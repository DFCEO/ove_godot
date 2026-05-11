"""Ove Godot API 全端点测试
Usage: python test_all.py
"""

import urllib.request
import urllib.error
import json
import time
import sys

BASE = "http://127.0.0.1:18776"
TIMEOUT = 3

def _post(path, data):
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(f"{BASE}{path}", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return None, str(e)

def _get(path):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return None, str(e)

def test(name, ok):
    print(f"  {'✅' if ok else '❌'} {name}")

def run():
    print("Ove Godot API Test\n" + "=" * 40)

    # 1. Health
    code, resp = _get("/health")
    test(f"GET /health", code == 200 and resp and resp.get("status") == "ok")

    # 2. Message
    code, resp = _post("/message", {"sender": "Test", "text": "你好，测试消息"})
    test(f"POST /message", code == 200)
    time.sleep(0.5)

    # 3. Emotion
    for emo in ["happy", "sad", "curious", "neutral"]:
        code, resp = _post("/emotion", {"emotion": emo, "intensity": 0.6})
        test(f"POST /emotion {emo}", code == 200)
        time.sleep(0.3)

    # 4. Action
    for act in ["nod", "wave_right", "bounce"]:
        code, resp = _post("/action", {"action": act})
        test(f"POST /action {act}", code == 200)
        time.sleep(0.3)

    # 5. Tweak
    code, resp = _post("/tweak", {"model": {"y": -0.5}, "camera": {"z": 2.5}})
    test(f"POST /tweak", code == 200)

    # 6. Scene
    for scene_name in ["摇头晃脑", "葬花", "冷笑"]:
        code, resp = _post("/scene", {"scene": scene_name})
        test(f"POST /scene {scene_name}", code == 200)
        time.sleep(0.5)

    # 7. Error cases
    code, resp = _post("/message", {"bad": "data"})
    test(f"POST /message (bad json)", code == 400)

    code, resp = _get("/nonexistent")
    test(f"GET /nonexistent", code == 404)

    print("\nDone.")


if __name__ == "__main__":
    run()
