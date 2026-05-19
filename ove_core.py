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
import struct
import threading
import queue
import re
import random
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
VAD_THRESHOLD = 0.95
WHISPER_MODEL = "small"

WS_PORT = 18778           # Godot WebSocket 连接
HTTP_PORT = 18779          # OpenClaw HTTP 调用
BRIDGE_VOICE_URL = f"http://127.0.0.1:{HTTP_PORT}/voice"

_whisper_model = None
_whisper_lock = threading.Lock()

# 跨线程 asyncio 调度
_main_loop = None

# ═══════════════════════════ 日志 ═══════════════════════════
def log(msg: str):
    print(f"[Core {time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ═══════════════════════════ 林黛玉回复模板 ═══════════════════════════
VOICE_REPLIES = {
    "你好|嗨|hello|hi|早上好|下午好|晚上好|来了": {
        "replies": ["嗯……你来了。", "你倒还记得我。", "来了？我还以为你把我忘了呢。"],
        "emotion": "curious", "action": "nod",
    },
    "天气": {
        "replies": ["今儿天色倒好，不冷不热的。", "风大了些，仔细别着凉。", "阴一阵晴一阵的，怪没趣。"],
        "emotion": "curious", "action": "nod",
    },
    "干嘛|做什么|在吗|在干嘛": {
        "replies": ["不过是闲坐罢了，有什么事？", "我能做什么，发呆罢了。", "你又来找我做什么？"],
        "emotion": "curious", "action": "nod",
    },
    "再见|拜拜|bye|走了|去吧": {
        "replies": ["这就走了？也罢……", "去吧，不必管我。", "嗯，你去吧。"],
        "emotion": "melancholy", "action": "nod",
    },
    "吃|饭|饿": {
        "replies": ["我没什么胃口……", "你倒是有心，还惦记着吃。", "不饿，你自去吃吧。"],
        "emotion": "melancholy", "action": "nod",
    },
    "哭|伤心|难过|不开心": {
        "replies": ["你又来惹我。", "我哭我的，与你什么相干。", "这泪也不是为你流的……"],
        "emotion": "sad", "action": "nod",
    },
    "笑|开心|高兴|好": {
        "replies": ["你笑什么？我可不觉得好笑。", "有什么可高兴的……", "哼，你倒是开心。"],
        "emotion": "annoyed", "action": "nod",
    },
    "花|落花|黛玉": {
        "replies": ["花谢花飞花满天，红消香断有谁怜……", "你见那落花了吗？"],
        "emotion": "melancholy", "action": "nod",
    },
    "诗|词|书|读": {
        "replies": ["你也懂诗？倒要请教了。", "这几日倒读了几首好诗。", "诗是好的，懂的人却不多。"],
        "emotion": "proud", "action": "nod",
    },
    "谁|名字|你": {
        "replies": ["我姓林，叫黛玉。你又是谁？", "你连我是谁都不知道？"],
        "emotion": "curious", "action": "nod",
    },
    "喜欢|爱": {
        "replies": ["胡说什么……谁要你喜欢！", "你又来说这些疯话。", "哼，说得好听。"],
        "emotion": "annoyed", "action": "nod",
    },
    "帮|帮忙|help": {
        "replies": ["你也知道来找我了。", "什么事？说吧。", "求我的时候倒想起我来了。"],
        "emotion": "proud", "action": "nod",
    },
    "谢谢|谢": {
        "replies": ["谢什么，不值什么。", "不必谢。", "嗯。"],
        "emotion": "curious", "action": "nod",
    },
    "困|睡觉|晚安|休息": {
        "replies": ["累了就歇着吧。", "去吧，梦里可别梦见我。", "你也早些休息。"],
        "emotion": "melancholy", "action": "nod",
    },
    "歌|唱|曲|音乐": {
        "replies": ["曲子么，我倒是会几首。", "你想听我唱？怕你听了睡不着。"],
        "emotion": "proud", "action": "nod",
    },
}

FALLBACKS = [
    ("嗯？你说什么？", "curious", "nod"),
    ("我没听清，再说一遍吧。", "curious", "nod"),
    ("这话说得不清不楚的……", "annoyed", "nod"),
]

def find_voice_reply(text: str) -> tuple[str, str, str]:
    """关键词匹配生成回复 → (文本, 情绪, 动作)"""
    # 繁→简 简易映射（覆盖常见繁体）
    trans = str.maketrans('氣樣麼樣麼', '气样么样么')
    text = text.translate(trans)
    for pattern, cfg in VOICE_REPLIES.items():
        if re.search(pattern, text.lower()):
            return random.choice(cfg["replies"]), cfg["emotion"], cfg["action"]
    return random.choice(FALLBACKS)


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
            self.device = sd.default.device[0]
        info = sd.query_devices(self.device)
        log(f"Mic: [{self.device}] {info['name']} @ {self.rate}Hz")
        self._running = True
        threading.Thread(target=self._run, daemon=True).start()

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
        self._vad_buf.extend(chunk.tolist())
        while len(self._vad_buf) >= 512:
            vc = np.array(self._vad_buf[:512], dtype=np.float32)
            self._vad_buf = self._vad_buf[512:]
            self._process(vc)

    def _process(self, chunk: np.ndarray):
        prob = vad_prob(chunk)
        self._debug_cnt += 1
        if self._debug_cnt >= 33:
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            self._debug_cnt = 0
            # Quiet debug - only log if speaking
            if prob > 0.5:
                log(f"Audio: rms={rms:.4f} vad={prob:.3f}")

        if prob > VAD_THRESHOLD:
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
            self._text_to_queue(text)
            if self._on_text:
                self._on_text(text)

    def _text_to_queue(self, text: str):
        """推送到 HTTP voice 端点（给自己，供 OpenClaw 查询）"""
        try:
            body = json.dumps({"text": text, "source": "voice"}).encode()
            req = urllib.request.Request(BRIDGE_VOICE_URL, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass


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
    elif method == "GET" and path == "/events":
        es = list(_event_queue); _event_queue.clear()
        resp = json.dumps({"events": es}, ensure_ascii=False)
    elif method == "GET" and path == "/health":
        resp = json.dumps({"status": "ok", "godot_connected": _godot_ws is not None,
                           "uptime": time.time() - _startup_ts})
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
_pending_replies = queue.Queue()

def on_voice_text(text: str):
    """收到语音文本 → 存入队列，由 OpenClaw 心跳处理"""
    global _last_voice_id
    vid = f"{text}_{time.time():.0f}"
    if vid == _last_voice_id:
        return
    _last_voice_id = vid
    log(f"→ OpenClaw will respond")


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
    detector.on_text(on_voice_text)
    mic.start()

    # 3. 启动 WebSocket + HTTP 服务器
    ws_srv = await websockets.serve(handle_ws, "127.0.0.1", WS_PORT,
                                     ping_interval=20, ping_timeout=10, max_size=2**20)
    http_srv = await asyncio.start_server(handle_http, "127.0.0.1", HTTP_PORT)

    log(f"WS:  ws://127.0.0.1:{WS_PORT}")
    log(f"HTTP: http://127.0.0.1:{HTTP_PORT}")
    log("Listening... (Ctrl+C to stop)")

    # 4. 麦克风采集 → VAD 处理 循环
    async def mic_loop():
        while True:
            chunk = mic.get(timeout=0.1)
            if chunk is not None:
                detector.feed(chunk)
            await asyncio.sleep(0)

    await asyncio.gather(
        mic_loop(),
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
