"""Ove Godot — OpenClaw 集成层 v2
从 OpenClaw 脚本/工具调用，通过 HTTP API 控制 Godot 桌面宠物。

直接模式: HTTP → Godot (127.0.0.1:18776)
魂身合一: HTTP → Bridge (127.0.0.1:18779) → WS → Godot (127.0.0.1:18778)

魂身合一模式下，OpenClaw 作为大脑，Godot 作为身体，bridge 做中继。
"""

import urllib.request
import json
import argparse
import sys

BASE = "http://127.0.0.1:18776"      # Godot 直接 HTTP
BRIDGE = "http://127.0.0.1:18779"     # ove_bridge HTTP
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


# ═══════════════════════════════════════════════════════════════
# 魂身合一 API — 通过 ove_bridge (18779) 双向通信
# ═══════════════════════════════════════════════════════════════

def _bridge_post(path: str, data: dict) -> dict | None:
    """POST 到 bridge"""
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            f"{BRIDGE}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[Bridge] HTTP error: {e}", file=sys.stderr)
        return None


def _bridge_get(path: str) -> dict | None:
    """GET 到 bridge"""
    try:
        with urllib.request.urlopen(f"{BRIDGE}{path}", timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[Bridge] HTTP error: {e}", file=sys.stderr)
        return None


def push_composite(text: str, emotion: str = None, action: str = None,
                   intensity: float = 0.7, sender: str = "Ove") -> bool:
    """魂身合一核心 API：一次推送说话+情绪+动作。

    OpenClaw 每次回复时调用此函数，机器人会说对应文本、
    切换到对应情绪、执行对应动作——真正做到"我就是机器人"。

    Args:
        text: 显示的文本（气泡 + TTS 语音）
        emotion: 情绪名（neutral/melancholy/sad/hurt/annoyed/angry/
                 happy/proud/curious/surprised/anxious/lonely/grateful/
                 resigned/defiant），None 则不改变情绪
        action: 动作名（nod/shake_head/tilt_head/bounce/point_right/...），
                None 则不执行动作
        intensity: 情绪强度 0.0-1.0
        sender: 发送者名
    """
    msg = {
        "type": "composite",
        "text": text,
        "sender": sender,
        "intensity": intensity,
    }
    if emotion:
        msg["emotion"] = emotion
    if action:
        msg["action"] = action

    r = _bridge_post("/push", msg)
    return r is not None and r.get("status") == "ok"


def get_robot_state() -> dict | None:
    """获取机器人当前状态（通过 bridge 缓存）。

    Returns:
        {
            "current_emotion": "neutral",
            "intensity": 0.3,
            "idle_seconds": 120.5,
            "connected": true,
            "uptime_seconds": 3600,
            ...
        }
    """
    return _bridge_get("/state")


def get_events() -> list[dict]:
    """获取并清空最近的事件队列。

    事件类型：
    - idle_alert: 机器人闲置超过阈值
    - long_idle: 长时间无互动
    - interacted: 被点击/拖拽
    - dragged: 窗口被拖拽

    Returns:
        [{"ts": 1716000000, "event": "idle_alert", "detail": {...}}, ...]
    """
    r = _bridge_get("/events")
    if r:
        return r.get("events", [])
    return []


def bridge_health() -> dict | None:
    """检查 bridge 健康状态"""
    return _bridge_get("/health")


def is_robot_connected() -> bool:
    """检查机器人是否通过 WebSocket 连接到 bridge"""
    r = bridge_health()
    if r:
        return r.get("godot_connected", False)
    return False


def get_voice_input() -> list[dict]:
    """获取并清空语音输入队列。

    每条约包含 {"ts": 时间戳, "text": 转写文本, "source": "voice"}
    通常只有一条（最近一次说话）。
    """
    r = _bridge_get("/voice")
    if r:
        return r.get("voices", [])
    return []


def push_voice_to_bridge(text: str) -> bool:
    """（调试用）手动推送一条模拟语音输入到 bridge"""
    r = _bridge_post("/voice", {"text": text, "source": "manual"})
    return r is not None and r.get("status") == "ok"


# ---- CLI ----

def main():
    parser = argparse.ArgumentParser(description="Ove Godot CLI v2 — 魂身合一")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("health", help="健康检查 (Godot 直接)")
    sub.add_parser("bridge-health", help="Bridge 健康检查")
    sub.add_parser("state", help="查询机器人状态 (via bridge)")
    sub.add_parser("events", help="获取事件队列 (via bridge)")
    sub.add_parser("voice", help="获取语音输入队列 (via bridge)")

    voice_sim = sub.add_parser("voice-sim", help="模拟语音输入 (调试)")
    voice_sim.add_argument("text")

    msg = sub.add_parser("message", help="发送消息 (Godot 直接)")
    msg.add_argument("sender")
    msg.add_argument("text")

    comp = sub.add_parser("composite", help="魂身合一复合指令 (via bridge)")
    comp.add_argument("text")
    comp.add_argument("--emotion", "-e", default=None)
    comp.add_argument("--action", "-a", default=None)
    comp.add_argument("--intensity", "-i", type=float, default=0.7)

    emo = sub.add_parser("emotion", help="设置情绪 (Godot 直接)")
    emo.add_argument("emotion")
    emo.add_argument("--intensity", "-i", type=float, default=0.6)

    act = sub.add_parser("action", help="触发动作 (Godot 直接)")
    act.add_argument("action")

    sc = sub.add_parser("scene", help="播放场景序列 (Godot 直接)")
    sc.add_argument("scene")

    args = parser.parse_args()

    if args.cmd == "health":
        print(json.dumps(health(), ensure_ascii=False, indent=2))
    elif args.cmd == "bridge-health":
        print(json.dumps(bridge_health(), ensure_ascii=False, indent=2))
    elif args.cmd == "state":
        print(json.dumps(get_robot_state(), ensure_ascii=False, indent=2))
    elif args.cmd == "events":
        print(json.dumps(get_events(), ensure_ascii=False, indent=2))
    elif args.cmd == "voice":
        print(json.dumps(get_voice_input(), ensure_ascii=False, indent=2))
    elif args.cmd == "voice-sim":
        ok = push_voice_to_bridge(args.text)
        print(json.dumps({"ok": ok}))
    elif args.cmd == "message":
        ok = push_to_ove(args.sender, args.text)
        print(json.dumps({"ok": ok}))
    elif args.cmd == "composite":
        ok = push_composite(args.text, args.emotion, args.action, args.intensity)
        print(json.dumps({"ok": ok}))
    elif args.cmd == "emotion":
        ok = set_emotion(args.emotion, args.intensity)
        print(json.dumps({"ok": ok}))
    elif args.cmd == "action":
        ok = do_action(args.action)
        print(json.dumps({"ok": ok}))
    elif args.cmd == "scene":
        ok = play_scene(args.scene)
        print(json.dumps({"ok": ok}))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
