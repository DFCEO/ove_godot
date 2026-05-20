"""
TTS Bridge — edge-tts 云端合成
================================
替换原来的 GPT-SoVITS 本地推理，用微软免费 TTS API：
- 合成快（1-2s/句），不需要 GPU
- 支持中文女声 zh-CN-XiaoxiaoNeural
- 保持句子拆分 + 顺序播放 + done 回调
"""

import sys, os
if os.name == 'nt':
    try:
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    except Exception:
        pass
    sys.stdin = open(os.devnull, 'r')

import http.server, json, threading, queue, tempfile, time, asyncio

GODOT_PROGRESS = "http://127.0.0.1:18776/progress"
CORE_MUTE_URL = "http://127.0.0.1:18779/mute?seconds=30"
CORE_UNMUTE_URL = "http://127.0.0.1:18779/unmute"
CORE_RESET_WAKE_URL = "http://127.0.0.1:18779/reset_wake"

VOICE = "zh-CN-XiaoxiaoNeural"  # 微软中文女声
RATE = "+0%"
VOLUME = "+0%"

try:
    import pygame
except ImportError:
    pygame = None

_queue = queue.Queue()
_stop_event = threading.Event()


class TTSHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ('/', '/tts'):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw)
            text = data.get("text", "")
            if text:
                _queue.put(text)
                print(f"[TTS] queued: {text[:50]}", flush=True)
        except Exception as e:
            print(f"[TTS] ERROR: {e}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
    def log_message(self, *a): pass


def _mute_core():
    try:
        import urllib.request
        urllib.request.urlopen(CORE_MUTE_URL, timeout=2)
    except Exception:
        pass

def _unmute_core():
    try:
        import urllib.request
        urllib.request.urlopen(CORE_UNMUTE_URL, timeout=2)
    except Exception:
        pass

def _reset_wake_core():
    try:
        import urllib.request
        urllib.request.urlopen(CORE_RESET_WAKE_URL, timeout=2)
    except Exception:
        pass

def _signal_speak_done():
    try:
        import urllib.request
        urllib.request.urlopen(GODOT_PROGRESS, data=json.dumps({"done": True}).encode(), timeout=3)
    except Exception:
        pass


async def _synthesize_async(text: str) -> bytes:
    """使用 edge-tts 合成语音，返回 WAV 字节"""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=VOICE, rate=RATE, volume=VOLUME)
    # 存到临时文件
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        await communicate.save(tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _synthesize(text: str) -> bytes:
    """同步包装 _synthesize_async"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_synthesize_async(text))
    finally:
        loop.close()


def _speak(text: str):
    t0 = time.time()
    print(f"[TTS] Synthesizing: {text[:30]}", flush=True)
    try:
        audio = _synthesize(text)
    except Exception as e:
        print(f"[TTS] FAILED: {e}", flush=True)
        return
    dt = time.time() - t0
    print(f"[TTS] Done in {dt:.1f}s, {len(audio)} bytes", flush=True)

    # 更新 Godot 气泡
    try:
        import urllib.request
        urllib.request.urlopen(GODOT_PROGRESS, data=json.dumps({"text": text}).encode(), timeout=2)
    except Exception:
        pass

    if pygame:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(audio)
            tmp_path = tmp.name
        try:
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def worker():
    if pygame:
        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"[TTS] pygame init failed: {e}")
    while not _stop_event.is_set():
        try:
            text = _queue.get(timeout=1.0)
            _mute_core()
            try:
                _speak(text)
            finally:
                # 播完后等回音消散再恢复麦克风
                _signal_speak_done()
                print(f"[TTS] Waiting 2s for echo decay...", flush=True)
                time.sleep(0.5)
                _unmute_core()
                _reset_wake_core()
        except queue.Empty:
            continue


def main():
    server = http.server.HTTPServer(("127.0.0.1", 18777), TTSHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    w = threading.Thread(target=worker, daemon=True)
    w.start()
    print(f"[TTS] edge-tts ready on http://127.0.0.1:18777 (voice={VOICE})", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _stop_event.set()
        print("[TTS] Stopped", flush=True)

if __name__ == "__main__":
    main()
