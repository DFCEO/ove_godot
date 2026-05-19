"""
voice_responder.py — 实时语音响应器
====================================
每 15s 监测 voice_inbox.txt，有新语音 → 投递到 bridge，
等待 OpenClaw 心跳处理并回复。

不做 LLM 调用——由 OpenClaw（我）统一处理。
"""

import time
import os
import sys
import json
import urllib.request

INBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_inbox.txt")
BRIDGE_VOICE = "http://127.0.0.1:18779/voice"
BRIDGE_PUSH = "http://127.0.0.1:18779/push"
CHECK_INTERVAL = 15
PROCESSED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_processed.txt")

def get_inbox_text():
    try:
        with open(INBOX, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def get_last_processed():
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def mark_processed(text):
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        f.write(text)

def push_to_bridge_voice(text):
    """把语音文本推送到 bridge voice 队列"""
    try:
        body = json.dumps({"text": text, "source": "voice_responder"}).encode("utf-8")
        req = urllib.request.Request(
            BRIDGE_VOICE, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception as e:
        print(f"[Resp] Bridge voice error: {e}", flush=True)
        return False

def main():
    print(f"[Resp] Monitoring {INBOX} every {CHECK_INTERVAL}s", flush=True)
    while True:
        try:
            text = get_inbox_text()
            if text and text != get_last_processed():
                print(f"[Resp] New: \"{text}\"", flush=True)
                push_to_bridge_voice(text)
                mark_processed(text)
        except Exception as e:
            print(f"[Resp] Error: {e}", flush=True)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
