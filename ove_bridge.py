"""
ove_bridge.py — 魂身合一 WebSocket 桥接
========================================
OpenClaw (大脑) ←HTTP→ Bridge (中继) ←WebSocket→ Godot (身体)
           :18779              :18778            :18776

职责：
- Godot 启动后 WS 连接到此，持久在线
- OpenClaw HTTP POST /push → 转发到 Godot WS
- Godot 定期上报状态 → 缓存 → OpenClaw GET /state 查询
"""

import asyncio
import json
import time
import sys
import os

try:
    import websockets
except ImportError:
    print("[Bridge] ERROR: pip install websockets")
    sys.exit(1)

# ---- 全局状态 ----
_godot_ws = None
_godot_connected_since = 0.0

_latest_state = {
    "idle_seconds": 0,
    "current_emotion": "neutral",
    "intensity": 0.3,
    "last_interaction": time.time(),
    "connected": False,
    "uptime_seconds": 0,
}

# 最近的事件队列（OpenClaw 可能错过，缓存最近 N 条）
_event_queue: list[dict] = []
MAX_EVENTS = 20

# 语音输入队列
_voice_queue: list[dict] = []
MAX_VOICE = 10


# ═══════════════════════════ WebSocket 服务 (port 18778) ═══════════════════════════

async def handle_ws(websocket):
    """Godot 连接到此 WebSocket"""
    global _godot_ws, _godot_connected_since
    _godot_ws = websocket
    _godot_connected_since = time.time()
    _latest_state["connected"] = True
    print(f"[Bridge] Godot connected @ {time.strftime('%H:%M:%S')}")

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[Bridge] Invalid JSON from Godot: {raw[:100]}")
                continue

            msg_type = data.get("type", "")

            if msg_type == "state":
                # Godot 定期状态上报
                _latest_state.update(data)
                _latest_state["connected"] = True

            elif msg_type == "event":
                # Godot 事件：idle_alert / long_idle / interacted / dragged
                event = {
                    "ts": time.time(),
                    "event": data.get("event", "unknown"),
                    "detail": data.get("detail", {}),
                }
                _event_queue.append(event)
                if len(_event_queue) > MAX_EVENTS:
                    _event_queue.pop(0)
                print(f"[Bridge] Event from Godot: {event['event']}")

            elif msg_type == "pong":
                pass  # 心跳回复

            else:
                print(f"[Bridge] Unknown msg type from Godot: {msg_type}")

    except websockets.exceptions.ConnectionClosed as e:
        print(f"[Bridge] Godot disconnected: {e}")
    except Exception as e:
        print(f"[Bridge] WS error: {e}")
    finally:
        _godot_ws = None
        _latest_state["connected"] = False


# ═══════════════════════════ HTTP 服务 (port 18779) ═══════════════════════════

async def handle_http(reader, writer):
    """OpenClaw 调用的 HTTP 端点"""
    try:
        raw = await asyncio.wait_for(reader.read(65536), timeout=10)
    except asyncio.TimeoutError:
        writer.close()
        return

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\r\n")
    if not lines:
        writer.close()
        return

    parts = lines[0].split(" ")
    method = parts[0] if len(parts) > 0 else "GET"
    path = parts[1] if len(parts) > 1 else "/"

    # 提取 body
    body = ""
    header_end = text.find("\r\n\r\n")
    if header_end != -1:
        body = text[header_end + 4:]

    status = 200
    resp_body = ""

    try:
        if method == "POST" and path == "/push":
            resp_body = await _handle_push(body)

        elif method == "GET" and path == "/state":
            resp_body = json.dumps(_latest_state, ensure_ascii=False)

        elif method == "GET" and path == "/events":
            # 获取并清空事件队列
            events = list(_event_queue)
            _event_queue.clear()
            resp_body = json.dumps({"events": events}, ensure_ascii=False)

        elif method == "POST" and path == "/voice":
            # STT 服务推送语音转写结果
            resp_body = await _handle_voice(body)

        elif method == "GET" and path == "/voice":
            # OpenClaw 拉取最新语音输入
            voices = list(_voice_queue)
            _voice_queue.clear()
            resp_body = json.dumps({"voices": voices}, ensure_ascii=False)

        elif method == "GET" and path == "/health":
            resp_body = json.dumps({
                "status": "ok",
                "godot_connected": _godot_ws is not None,
                "connected_since": _godot_connected_since,
                "bridge_uptime": time.time() - _godot_connected_since if _godot_ws else 0,
            })

        elif method == "POST" and path == "/raw":
            # 透传原始 JSON 到 Godot（高级调试用）
            resp_body = await _handle_raw(body)

        else:
            status = 404
            resp_body = json.dumps({"error": "not found"})

    except Exception as e:
        status = 500
        resp_body = json.dumps({"error": str(e)})

    status_text = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Error"}.get(status, "OK")
    payload = resp_body.encode("utf-8")
    header = (
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    writer.write(header.encode() + payload)
    await writer.drain()
    writer.close()


async def _handle_push(body: str) -> str:
    """处理 OpenClaw 推送消息"""
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid json"})

    msg_type = msg.get("type", "composite")

    # 验证并补全
    if msg_type == "composite":
        if "text" not in msg:
            return json.dumps({"error": "composite requires text field"})
        msg.setdefault("emotion", None)
        msg.setdefault("action", None)
        msg.setdefault("sender", "Ove")

    if _godot_ws is None:
        return json.dumps({"status": "error", "reason": "godot not connected"})

    try:
        await _godot_ws.send(json.dumps(msg, ensure_ascii=False))
        return json.dumps({"status": "ok", "delivered": True})
    except Exception as e:
        return json.dumps({"status": "error", "reason": str(e)})


async def _handle_raw(body: str) -> str:
    """透传原始 JSON"""
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid json"})

    if _godot_ws is None:
        return json.dumps({"status": "error", "reason": "godot not connected"})

    try:
        await _godot_ws.send(json.dumps(msg, ensure_ascii=False))
        return json.dumps({"status": "ok"})
    except Exception as e:
        return json.dumps({"status": "error", "reason": str(e)})


async def _handle_voice(body: str) -> str:
    """STT 服务推送语音转写文本 → 缓存 + 转发到 Godot 气泡"""
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid json"})

    text = msg.get("text", "").strip()
    if not text:
        return json.dumps({"status": "ignored", "reason": "empty text"})

    entry = {
        "ts": time.time(),
        "text": text,
        "source": msg.get("source", "voice"),
    }
    _voice_queue.append(entry)
    if len(_voice_queue) > MAX_VOICE:
        _voice_queue.pop(0)

    print(f"[Bridge] Voice: \"{text}\"")

    # 只有 STT 原始语音才写 inbox（防止循环）
    if msg.get("source") == "voice":
        _write_voice_inbox(text)

    # 只有 STT 原始语音才 ack 到 Godot
    if _godot_ws is not None and msg.get("source") == "voice":
        try:
            await _godot_ws.send(json.dumps({
                "type": "speak",
                "text": "嗯？",
                "sender": "",
            }, ensure_ascii=False))
        except Exception as e:
            print(f"[Bridge] Failed to ack voice to Godot: {e}")

    return json.dumps({"status": "ok"})


def _write_voice_inbox(text: str):
    """将语音文本写入 inbox，OpenClaw 心跳时检测并回复"""
    inbox_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_inbox.txt")
    try:
        with open(inbox_path, "w", encoding="utf-8") as f:
            f.write(text.strip())
    except Exception as e:
        print(f"[Bridge] Failed to write voice inbox: {e}")


# ═══════════════════════════ 心跳 ═══════════════════════════

async def heartbeat():
    """定期 ping Godot + 输出健康日志"""
    while True:
        await asyncio.sleep(30)
        if _godot_ws is not None:
            try:
                await _godot_ws.send(json.dumps({"type": "ping"}))
            except Exception:
                pass


# ═══════════════════════════ Main ═══════════════════════════

async def main():
    ws_server = await websockets.serve(
        handle_ws, "127.0.0.1", 18778,
        ping_interval=20, ping_timeout=10,
        max_size=2 ** 20,
    )
    http_server = await asyncio.start_server(handle_http, "127.0.0.1", 18779)

    print(f"[Bridge] WS  : ws://127.0.0.1:18778  (Godot connects here)")
    print(f"[Bridge] HTTP: http://127.0.0.1:18779 (OpenClaw calls this)")
    print(f"[Bridge] Ready. Waiting for Godot...")

    await asyncio.gather(
        heartbeat(),
        ws_server.wait_closed(),
        http_server.serve_forever(),
    )


if __name__ == "__main__":
    print("[Bridge] Ove Bridge v1.0 — 魂身合一")
    asyncio.run(main())
