"""
ove_core.py — Ove 一体化核心
=============================
替代 stt_service + bridge + responder + agent 四个进程。
架构: 耳朵(mic→VAD→Whisper) + 嘴巴(Godot WS) + 大脑接口(OpenClaw)

启动参数:
  --device N   输入设备索引 (默认: 系统默认麦克风)
"""

import asyncio
import json
import time
import sys
import os
import threading
import queue
import argparse
import urllib.request

import numpy as np

try:
    import websockets
    import sounddevice as sd
except ImportError as e:
    print(f"[Core] Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

# ═══════════════════════════ 配置 ═══════════════════════════
SAMPLE_RATE = 16000
CHUNK_MS = 30
CHUNK_SAMPLES = 480  # 30ms @ 16kHz
SILENCE_DURATION = 1.0
SILENCE_FRAMES = int(SILENCE_DURATION * 1000 / CHUNK_MS)
MIN_SPEECH_DURATION = 0.8
MIN_SPEECH_FRAMES = int(MIN_SPEECH_DURATION * 1000 / CHUNK_MS)
VAD_THRESHOLD = 0.05  # Silero VAD 概率阈值，低到 0.05 以适应当前麦克风
AUDIO_GAIN = 5.0     # 音频增益倍数（避免 30x 削波失真）
VAD_RMS_THRESHOLD = 0.15  # RMS 能量阈值：VAD 概率低但 RMS 够高时也判为语音
WHISPER_MODEL = "small"

# ═══════════════════════════ 唤醒词配置 ═══════════════════════════
WAKE_WORDS = ["你好黛玉", "你好戴玉", "hi黛玉", "hi戴玉"]
AWAKE_TIMEOUT = 120  # 唤醒后无人说话自动休眠秒数

WS_PORT = 18778           # Godot WebSocket 连接
HTTP_PORT = 18779          # OpenClaw HTTP 调用
BRIDGE_VOICE_URL = f"http://127.0.0.1:{HTTP_PORT}/voice"

_whisper_model = None
_whisper_lock = threading.Lock()

# 唤醒词状态机
WAKE_STATES = {"idle": "idle", "awake": "awake"}
_wake_state = WAKE_STATES["idle"]
_wake_ts = 0.0  # 最后一次唤醒时间
_muted_until = 0.0  # 静音截止时间戳（防自激反馈）
_detector = None  # SpeechDetector 实例引用，供 mute 时清缓存

# 跨线程 asyncio 调度
_main_loop = None

# ═══════════════════════════ 日志 ═══════════════════════════
def log(msg: str):
    print(f"[Core {time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ═══════════════════════════ 唤醒词检测 ═══════════════════════════

def is_wake_word(text: str) -> bool:
    """检查文本是否包含唤醒词（含模糊匹配：编辑距离 ≤ 2）
    应对 Whisper 对"大/戴→黛、衣/鱼/月→玉"等常见识别偏差。
    阈值 2 覆盖大多数 1 字偏差场景，极少误触发。
    """
    t = text.lower().replace(" ", "")
    for w in WAKE_WORDS:
        wc = w.lower().replace(" ", "")
        # 精确子串匹配
        if wc in t:
            return True
        # 模糊匹配：允许最多 2 字差异
        for i in range(len(t) - len(wc) + 1):
            substr = t[i:i+len(wc)]
            diffs = sum(1 for a, b in zip(substr, wc) if a != b)
            if diffs <= 2:
                return True
    return False


def wake_state() -> str:
    global _wake_state
    return _wake_state


def set_wake_state(state: str):
    global _wake_state, _wake_ts
    _wake_state = state
    _wake_ts = time.time()
    log(f"Wake state → {state}")


def is_awake() -> bool:
    return _wake_state == WAKE_STATES["awake"]


# ═══════════════════════════ 耳朵: Whisper ═══════════════════════════

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        log(f"Loading Whisper '{WHISPER_MODEL}'...")
        _whisper_model = whisper.load_model(WHISPER_MODEL)
        log("Whisper ready")
    return _whisper_model

def transcribe(audio: np.ndarray) -> str:
    with _whisper_lock:
        model = get_whisper()
        result = model.transcribe(audio, language="zh", fp16=False)
    return result.get("text", "").strip()


# ═══════════════════════════ 耳朵: Silero VAD ═══════════════════════════

_vad_model = None

def load_vad():
    global _vad_model
    import torch
    model, _ = torch.hub.load("snakers4/silero-vad", "silero_vad", force_reload=False, trust_repo=True)
    _vad_model = model
    log("Silero VAD loaded")

def vad_prob(chunk: np.ndarray) -> float:
    import torch
    tensor = torch.from_numpy(chunk.astype(np.float32)).unsqueeze(0)
    with torch.no_grad():
        return _vad_model(tensor, SAMPLE_RATE).item()


# ═══════════════════════════ 耳朵: 麦克风 ═══════════════════════════

class MicCapture:
    def __init__(self, target_rate: int, device_index: int = None):
        self.rate = target_rate
        self.device = device_index
        self._q = queue.Queue()
        self._running = False

    def start(self):
        if self.device is None:
            deft = sd.default.device
            log(f"Default device: {deft} (type={type(deft).__name__})")
            if isinstance(deft, (list, tuple)):
                self.device = deft[0]
            elif isinstance(deft, int):
                self.device = deft
            else:
                self.device = sd.default.device
            log(f"Initial preferred device: {self.device}")
        # 验证设备是否为输入设备，并尝试查找最佳麦克风
        self.device = self._resolve_input_device(self.device)
        info = sd.query_devices(self.device)
        log(f"Mic: [{self.device}] {info['name']} @ {self.rate}Hz")
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _resolve_input_device(self, preferred: int) -> int:
        """解析麦克风设备：优先使用指定的，如果失败则自动查找。"""
        import sounddevice as sd

        # 检查指定设备是否为有效的输入设备
        if preferred is not None:
            try:
                info = sd.query_devices(preferred)
                if info["max_input_channels"] > 0:
                    # 尝试打开确认可用
                    sd.check_input_settings(device=preferred, samplerate=self.rate, channels=1)
                    return preferred
            except Exception:
                log(f"Device [{preferred}] not usable, scanning for mic...")

        # 自动扫描：按优先级查找麦克风
        # 优先级：Realtek 阵列 > 其他输入设备 > 默认输入
        preferred_names = ["阵列", "Array", "Realtek", "麦克风", "Microphone", "mic"]
        candidates = []

        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] == 0:
                continue
            try:
                sd.check_input_settings(device=i, samplerate=self.rate, channels=1)
                priority = 0
                name = d["name"].lower()
                for pn in preferred_names:
                    if pn.lower() in name:
                        priority += 1
                candidates.append((priority, i, d["name"]))
            except Exception:
                pass

        if candidates:
            # 选优先级最高的（优先 Realtek 阵列）
            candidates.sort(key=lambda x: (-x[0], x[1]))
            best = candidates[0][1]
            log(f"Auto-selected mic: [{best}] {sd.query_devices(best)['name']}")
            return best

        # 最后兜底：默认输入设备
        log(f"WARN: falling back to default input device")
        return sd.default.device[0]

    def _run(self):
        try:
            with sd.InputStream(samplerate=self.rate, device=self.device,
                               channels=1, blocksize=CHUNK_SAMPLES,
                               dtype='float32', callback=self._callback):
                while self._running:
                    sd.sleep(100)
        except Exception as e:
            log(f"Mic stream error: {e}")

    def _callback(self, indata, _frames, _time, _status):
        self._q.put(indata.copy().flatten())

    def get(self, timeout=0.1) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._running = False


# ═══════════════════════════ 耳朵: VAD + 录音 ═══════════════════════════

class SpeechDetector:
    def __init__(self):
        self._vad_buf: list[float] = []
        self._speech_buf: list[np.ndarray] = []
        self._speaking = False
        self._silence_cnt = 0
        self._speech_frames = 0
        self._on_text = None
        self._debug_cnt = 0
        self._responder = None  # injected later

    def on_text(self, cb):
        self._on_text = cb

    def feed(self, chunk: np.ndarray):
        # 静音期间跳过所有音频处理
        if time.time() < _muted_until:
            return
        # 增益放大后裁剪到 [-1, 1]，防止 VAD 模型收到异常值
        chunk = np.clip(chunk * AUDIO_GAIN, -1.0, 1.0)
        self._vad_buf.extend(chunk.tolist())
        while len(self._vad_buf) >= 512:
            vc = np.array(self._vad_buf[:512], dtype=np.float32)
            self._vad_buf = self._vad_buf[512:]
            self._process(vc)

    def clear_buffers(self):
        """清空 VAD 和语音缓存（静音时调用，防止已缓冲音频继续处理）"""
        self._vad_buf.clear()
        self._speech_buf.clear()
        self._speaking = False
        self._silence_cnt = 0
        self._speech_frames = 0

    def _process(self, chunk: np.ndarray):
        prob = vad_prob(chunk)
        rms_energy = float(np.sqrt(np.mean(chunk ** 2)))
        # 混合判决：VAD 模型概率，或 RMS 能量够高且 VAD 不完全是零
        is_speech = prob > VAD_THRESHOLD or (rms_energy > VAD_RMS_THRESHOLD and prob > 0.001)
        self._debug_cnt += 1
        if self._debug_cnt >= 33:  # 约每秒输出一次
            self._debug_cnt = 0
            log(f"Audio: rms={rms_energy:.4f} vad={prob:.3f} speech={is_speech}")

        if is_speech:
            if not self._speaking:
                self._speaking = True
                self._speech_buf = []
                self._speech_frames = 1
                self._silence_cnt = 0
            else:
                self._speech_frames += 1
                self._silence_cnt = 0
            self._speech_buf.append(chunk)
        elif self._speaking:
            self._speech_buf.append(chunk)
            self._silence_cnt += 1
            if self._silence_cnt >= SILENCE_FRAMES:
                self._speaking = False
                if self._speech_frames >= MIN_SPEECH_FRAMES:
                    self._finalize()
                else:
                    self._speech_buf = []

    def _finalize(self):
        if not self._speech_buf:
            return
        audio = np.concatenate(self._speech_buf)
        dur = len(audio) / SAMPLE_RATE
        log(f"Speech: {dur:.1f}s ({self._speech_frames} frames)")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio: np.ndarray):
        text = transcribe(audio)
        if text and text.strip():
            text = text.strip()
            log(f"→ \"{text}\"")
            handled = self._handle_text(text)
            if handled and self._on_text:
                self._on_text(text)

    def _handle_text(self, text: str) -> bool:
        """
        唤醒词门控：
        - IDLE 状态：只有检测到唤醒词才推送
        - AWAKE 状态：所有语音都推送（约 60s 无人说话后自动休眠）
        Returns: True if text was pushed to queue
        """
        if is_awake():
            # 唤醒状态：正常推送，重置计时
            # 用模块级函数替代直接 global，避免与类耦合
            set_wake_state(WAKE_STATES["awake"])  # 重置 _wake_ts
            self._text_to_queue(text, source="voice_awake")
            log(f"[Awake] → queued")
            return True
        elif is_wake_word(text):
            # 检测到唤醒词：切换到唤醒状态，推送唤醒提示
            set_wake_state(WAKE_STATES["awake"])
            log(f"[Wake] '{WAKE_WORDS[0]}' detected!")
            self._text_to_queue(WAKE_WORDS[0], source="wake")
            return True
        else:
            log(f"[Idle] ignored '{text}' (no wake word)")
            return False

    def _text_to_queue(self, text: str, source: str = "voice"):
        """推送到 HTTP voice 端点（给自己，供 OpenClaw 查询）"""
        try:
            body = json.dumps({"text": text, "source": source}).encode()
            req = urllib.request.Request(BRIDGE_VOICE_URL, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=2)
        except Exception as e:
            log(f"_text_to_queue FAILED: {e}")


# ═══════════════════════════ 嘴巴: WebSocket 服务器 (Godot连) ═══════════════════════════

_godot_ws = None
_connected_since = 0.0

# 状态 + 队列
_latest_state = {"idle_seconds": 0, "current_emotion": "neutral", "intensity": 0.3,
                  "last_interaction": time.time(), "connected": False, "uptime_seconds": 0}
_voice_queue: list[dict] = []
_event_queue: list[dict] = []
MAX_VOICE = 20
MAX_EVENTS = 20
_startup_ts = time.time()

async def handle_ws(websocket):
    global _godot_ws, _connected_since
    _godot_ws = websocket
    _connected_since = time.time()
    _latest_state["connected"] = True
    log("Godot connected")

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = data.get("type", "")
            if t == "state":
                _latest_state.update(data)
                _latest_state["connected"] = True
            elif t == "event":
                _event_queue.append({"ts": time.time(), "event": data.get("event", ""),
                                      "detail": data.get("detail", {})})
                if len(_event_queue) > MAX_EVENTS:
                    _event_queue.pop(0)
            elif t == "pong":
                pass
    except websockets.exceptions.ConnectionClosed:
        log("Godot disconnected")
    finally:
        _godot_ws = None
        _latest_state["connected"] = False


# ═══════════════════════════ HTTP 服务 (OpenClaw调用) ═══════════════════════════

async def handle_http(reader, writer):
    try:
        raw = await asyncio.wait_for(reader.read(65536), timeout=10)
    except asyncio.TimeoutError:
        writer.close(); return

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\r\n")
    parts = lines[0].split(" ") if lines else ["GET", "/"]
    method, path = parts[0], parts[1] if len(parts) > 1 else "/"
    body = text[text.find("\r\n\r\n") + 4:] if "\r\n\r\n" in text else ""

    status, resp = 200, ""

    if method == "POST" and path == "/push":
        resp = await _http_push(body)
    elif method == "GET" and path == "/state":
        resp = json.dumps(_latest_state, ensure_ascii=False)
    elif method == "GET" and path == "/voice":
        vs = list(_voice_queue); _voice_queue.clear()
        resp = json.dumps({"voices": vs}, ensure_ascii=False)
    elif method == "POST" and path == "/voice":
        resp = _http_add_voice(body)
    elif method == "GET" and path == "/voice/flush":
        _voice_queue.clear()
        resp = json.dumps({"status": "ok", "flushed": True})
    elif method == "GET" and path == "/events":
        es = list(_event_queue); _event_queue.clear()
        resp = json.dumps({"events": es}, ensure_ascii=False)
    elif method == "GET" and path == "/health":
        resp = json.dumps({"status": "ok", "godot_connected": _godot_ws is not None,
                           "uptime": time.time() - _startup_ts})
    elif method == "GET" and path == "/wake":
        resp = json.dumps({"state": _wake_state,
                           "since": _wake_ts,
                           "idle_for": time.time() - _wake_ts if _wake_state == "idle" else 0})
    elif method == "GET" and path.startswith("/mute"):
        # ?seconds=15 — 静音麦克风 N 秒（防 TTS 自激反馈）
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(path).query)
        secs = float(qs.get("seconds", [15])[0])
        global _muted_until
        _muted_until = time.time() + secs
        # 同时清空 VAD/语音缓存，防止缓冲段继续处理
        if _detector:
            _detector.clear_buffers()
        resp = json.dumps({"muted": True, "until": _muted_until, "seconds": secs})
    elif method == "GET" and path == "/unmute":
        _muted_until = 0.0
        if _detector:
            _detector.clear_buffers()
        resp = json.dumps({"muted": False, "until": _muted_until})
    elif method == "GET" and path == "/reset_wake":
        # TTS 播放结束后：如果已休眠则重新唤醒，如果还醒着则重置计时
        set_wake_state(WAKE_STATES["awake"])
        resp = json.dumps({"state": _wake_state, "reset": True})
    else:
        status, resp = 404, json.dumps({"error": "not found"})

    st = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Error"}.get(status, "OK")
    payload = resp.encode()
    writer.write(f"HTTP/1.1 {status} {st}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n".encode() + payload)
    await writer.drain()
    writer.close()

async def _http_push(body: str) -> str:
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid json"})
    if _godot_ws is None:
        return json.dumps({"status": "error", "reason": "godot not connected"})
    try:
        msg.setdefault("sender", "Ove")
        await _godot_ws.send(json.dumps(msg, ensure_ascii=False))
        return json.dumps({"status": "ok", "delivered": True})
    except Exception as e:
        return json.dumps({"status": "error", "reason": str(e)})

def _http_add_voice(body: str) -> str:
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid json"})
    text = msg.get("text", "").strip()
    if not text:
        return json.dumps({"status": "ignored"})
    entry = {"ts": time.time(), "text": text, "source": msg.get("source", "voice")}
    _voice_queue.append(entry)
    if len(_voice_queue) > MAX_VOICE:
        _voice_queue.pop(0)

    # 语音不再即时 ack（改为 Heartbeat AI 生成回复后 composite 推送）

    return json.dumps({"status": "ok"})


# ═══════════════════════════ 大脑接口: 语音→回复 ═══════════════════════════

_last_voice_id = None

# 语音 → 回复 → 推 Godot（通过线程安全队列）
def on_voice_text(text: str):
    """收到语音文本（voice agent 通过 HTTP 轮询，无需额外信号）"""
    log(f"→ Voice queued ({len(_voice_queue)} pending)")


# ═══════════════════════════ 心跳 ═══════════════════════════

async def ws_heartbeat():
    while True:
        await asyncio.sleep(30)
        if _godot_ws is not None:
            try:
                await _godot_ws.send(json.dumps({"type": "ping"}))
            except Exception:
                pass


# ═══════════════════════════ Main ═══════════════════════════

async def main(args):
    global _startup_ts
    _startup_ts = time.time()
    _main_loop = asyncio.get_event_loop()

    # 1. 加载 VAD
    load_vad()

    # 2. 启动麦克风 + 语音检测
    mic = MicCapture(SAMPLE_RATE, device_index=args.device)
    detector = SpeechDetector()
    global _detector
    _detector = detector
    detector.on_text(on_voice_text)
    mic.start()

    # 3. 启动 WebSocket + HTTP 服务器
    ws_srv = await websockets.serve(handle_ws, "127.0.0.1", WS_PORT,
                                     ping_interval=20, ping_timeout=10, max_size=2**20)
    http_srv = await asyncio.start_server(handle_http, "127.0.0.1", HTTP_PORT)

    log(f"WS:  ws://127.0.0.1:{WS_PORT}")
    log(f"HTTP: http://127.0.0.1:{HTTP_PORT}")
    log("Listening... (Ctrl+C to stop)")
    log(f"Wake word: {WAKE_WORDS[0]}, timeout: {AWAKE_TIMEOUT}s")
    log(f"Initial state: {_wake_state}")

    # 4. 麦克风采集 → VAD 处理 循环
    async def mic_loop():
        while True:
            chunk = mic.get(timeout=0.1)
            if chunk is not None:
                detector.feed(chunk)
            await asyncio.sleep(0)

    # 5. 唤醒超时检查（每 1s，实际超时精度 ±1s）
    async def wake_timeout_check():
        global _wake_state
        while True:
            await asyncio.sleep(1)
            if _wake_state == WAKE_STATES["awake"]:
                idle_for = time.time() - _wake_ts
                if idle_for > AWAKE_TIMEOUT:
                    log(f"[Timeout] idle {idle_for:.0f}s > {AWAKE_TIMEOUT}s → sleep")
                    set_wake_state(WAKE_STATES["idle"])

    await asyncio.gather(
        mic_loop(),
        wake_timeout_check(),
        ws_heartbeat(),
        ws_srv.wait_closed(),
        http_srv.serve_forever(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ove Core")
    parser.add_argument("--device", "-d", type=int, default=None)
    parser.add_argument("--list", "-l", action="store_true", help="List mics and exit")
    args = parser.parse_args()

    if args.list:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"  [{i}] {d['name']}")
    else:
        asyncio.run(main(args))
