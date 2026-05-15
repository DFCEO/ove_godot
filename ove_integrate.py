"""Ove Godot — OpenClaw 集成层
从 OpenClaw 脚本/工具调用，通过 HTTP API 控制 Godot 桌面宠物。

端口：127.0.0.1:18776（与 Godot http_server.gd 对应）
"""

import urllib.request
import json
import argparse
import sys

BASE = "http://127.0.0.1:18776"
TIMEOUT = 3


def _post(path: str, data: dict) -> dict | None:
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[Ove] HTTP error: {e}", file=sys.stderr)
        return None


def _get(path: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[Ove] HTTP error: {e}", file=sys.stderr)
        return None


# ---- OpenClaw 集成 API ----

def push_to_ove(sender: str, text: str) -> bool:
    """推送消息到 Ove（显示气泡 + TTS）"""
    r = _post("/message", {"sender": sender, "text": text})
    return r is not None


def set_emotion(emotion: str, intensity: float = 0.6) -> bool:
    """设置 Ove 情绪状态。
    emotion: neutral|melancholy|sad|hurt|annoyed|angry|happy|proud|curious|surprised|anxious|lonely|grateful|resigned|defiant
    intensity: 0.0 - 1.0
    """
    r = _post("/emotion", {"emotion": emotion, "intensity": intensity})
    return r is not None


def do_action(action: str) -> bool:
    """触发 Ove 动作。
    action: point_right|point_left|both_forward|both_back|spread|right_side|left_side|nod|lookup|shake_head|tilt_head|bounce
    """
    r = _post("/action", {"action": action})
    return r is not None


def tweak(data: dict) -> bool:
    """实时调参（模型位置/相机/动画参数）"""
    r = _post("/tweak", data)
    return r is not None


def play_scene(scene_name: str) -> bool:
    """播放场景动作序列。
    scene_name: 葬花|听戏入神|拌嘴扭头|怔住（惊喜）|冷笑|摇头晃脑
    """
    r = _post("/scene", {"scene": scene_name})
    return r is not None


def health() -> dict | None:
    """健康检查"""
    return _get("/health")


# ---- CLI ----

def main():
    parser = argparse.ArgumentParser(description="Ove Godot CLI")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("health", help="健康检查")

    msg = sub.add_parser("message", help="发送消息")
    msg.add_argument("sender")
    msg.add_argument("text")

    emo = sub.add_parser("emotion", help="设置情绪")
    emo.add_argument("emotion")
    emo.add_argument("--intensity", "-i", type=float, default=0.6)

    act = sub.add_parser("action", help="触发动作")
    act.add_argument("action")

    sc = sub.add_parser("scene", help="播放场景序列")
    sc.add_argument("scene")

    args = parser.parse_args()

    if args.cmd == "health":
        print(health())
    elif args.cmd == "message":
        ok = push_to_ove(args.sender, args.text)
        print(f"Message sent: {ok}")
    elif args.cmd == "emotion":
        ok = set_emotion(args.emotion, args.intensity)
        print(f"Emotion set: {ok}")
    elif args.cmd == "action":
        ok = do_action(args.action)
        print(f"Action triggered: {ok}")
    elif args.cmd == "scene":
        ok = play_scene(args.scene)
        print(f"Scene played: {ok}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
