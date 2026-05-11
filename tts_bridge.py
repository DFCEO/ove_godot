"""TTS Bridge — Godot -> GPT-SoVITS, with sentence splitting, sequential playback, and emotion-driven speed"""

import http.server
import json
import threading
import queue
import tempfile
import os
import re
import time
import requests

GSV_API = "http://127.0.0.1:9881"
GODOT_PROGRESS = "http://127.0.0.1:18776/progress"

# Emotion -> speed factor (0.6~1.65, GSV range)
EMOTION_SPEED = {
    "neutral":    1.0,
    "happy":      1.0,
    "proud":      1.0,
    "curious":    1.0,
    "grateful":   1.0,
    "surprised":  1.15,
    "anxious":    1.3,
    "angry":      1.2,
    "annoyed":    1.2,
    "defiant":    1.1,
    "resigned":   0.95,
    "lonely":     0.9,
    "hurt":       0.9,
    "melancholy": 0.85,
    "sad":        0.85,
}

try:
    import pygame
except ImportError:
    pygame = None

_queue = queue.Queue()
_running = True

SENTENCE_SPLIT = re.compile(r'[,.;、，。；：:?？!！\n]')


class TTSHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw)
            text = data.get("text", "")
            prosody = data.get("prosody", {})
            if text:
                _queue.put((text, prosody))
                print(f"[TTS Bridge] queued: {text[:50]}", flush=True)
        except Exception as e:
            print(f"[TTS Bridge] ERROR: {e}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


def _get_speed(prosody: dict) -> float:
    emotion = prosody.get("emotion", "neutral")
    base = EMOTION_SPEED.get(emotion, 1.0)
    # Godot may also send a rate modifier like "+10%" or "-10%"
    rate_str = prosody.get("rate", "+0%")
    try:
        rate_mod = float(rate_str.strip("%").lstrip("+")) / 100.0
    except ValueError:
        rate_mod = 0.0
    speed = base + rate_mod
    return max(0.6, min(1.65, speed))


def _split_sentences(text: str):
    parts = SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _synthesize(text: str, speed: float = 1.0) -> bytes:
    resp = requests.post(GSV_API, json={"text": text, "speed": speed}, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"GSV error: {resp.text[:200]}")
    return resp.content


def _speak(text: str, prosody: dict):
    speed = _get_speed(prosody)
    emotion = prosody.get("emotion", "neutral")
    print(f"[TTS Bridge] Speaking ({emotion}, speed={speed:.2f}): {text[:50]}", flush=True)

    sentences = _split_sentences(text)
    if not sentences:
        return

    print(f"[TTS Bridge] Split into {len(sentences)} sentences", flush=True)

    for i, sentence in enumerate(sentences):
        t0 = time.time()
        print(f"[TTS Bridge] [{i+1}/{len(sentences)}] Synthesizing: {sentence[:30]}", flush=True)
        try:
            wav = _synthesize(sentence, speed)
        except Exception as e:
            print(f"[TTS Bridge] [{i+1}] FAILED: {e}", flush=True)
            continue

        dt = time.time() - t0
        print(f"[TTS Bridge] [{i+1}] Done in {dt:.1f}s, {len(wav)} bytes", flush=True)

        # Update Godot bubble
        try:
            requests.post(GODOT_PROGRESS, json={"text": sentence}, timeout=2)
        except Exception:
            pass

        if pygame:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav)
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
    while _running:
        try:
            text, prosody = _queue.get(timeout=1.0)
            _speak(text, prosody)
        except queue.Empty:
            continue


def main():
    server = http.server.HTTPServer(("127.0.0.1", 18777), TTSHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    w = threading.Thread(target=worker, daemon=True)
    w.start()
    print("[TTS Bridge] Ready on http://127.0.0.1:18777", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        global _running
        _running = False


if __name__ == "__main__":
    main()
