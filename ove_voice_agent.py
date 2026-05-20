#!/usr/bin/env python3
"""
Ove Voice Agent — Function Calling + 成长系统
==============================================
- Function calling（天气、记笔记、记忆、偏好）
- 自动学习用户偏好和重要信息
- 长时记忆跨会话保留
"""

import json, time, os, subprocess, urllib.request, urllib.error, threading, concurrent.futures

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CTX_FILE = os.path.join(SCRIPT_DIR, "conversation_context.json")
PREFERENCES_FILE = os.path.join(SCRIPT_DIR, "conversation_preferences.json")
MEMORY_FILE = os.path.join(SCRIPT_DIR, "conversation_memories.json")
OBSIDIAN_VAULT = r"C:\Users\zj199\Documents\ff"

OPENCLAW_URL = "http://127.0.0.1:18789/v1/chat/completions"
OPENCLAW_TOKEN = os.environ.get("OPENCLAWS_API_TOKEN", "bdff9799b2b59c8ccfc4c4a251d7c15b011e2492cf46c8a4")
CORE_VOICE_URL = "http://127.0.0.1:18779/voice"
BRIDGE_PUSH_URL = "http://127.0.0.1:18779/push"
TTS_BRIDGE_URL = "http://127.0.0.1:18777/tts"

MODEL = "openclaw/ove"
MAX_TOKENS = 500
TEMPERATURE = 0.85
REQUEST_TIMEOUT = 45
POLL_INTERVAL = 3
MAX_CONTEXT_TURNS = 10
RESPONSE_COOLDOWN = 3

SYSTEM_PROMPT = """你是林黛玉，从《红楼梦》中来。

你有这些工具可用：

## 工具列表
1. get_weather — 查天气，调用真实 API
2. create_note — 写笔记到 Obsidian
3. save_memory — 记住重要信息（用户身份、偏好、事实）
4. save_preference — 记住用户的表达偏好（说短话、说长话、用什么语气）
5. get_memories — 查看过去记住的信息

## 学习能力
- 用户说"记住……""我喜欢……""我是……"等 → 立即调 save_memory
- 用户说"太长了""说短点""说详细点"等 → 立即调 save_preference
- 每次对话结束时，把新了解到的用户信息存为 memory

## 回复格式
始终输出 JSON：{"spoken": "短语音(10-20字)", "display": "完整文字"}
- spoken 只要一句话，天气就"下雨了""大太阳"。问时间就"将近中午了"。
- display 写完整细节。
- 语气文雅清冷，偶尔傲娇"""


def log(msg): print(f"[voice-agent {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ═════════════════════ 记忆 / 偏好 持久化 ═════════════════════

def load_memories() -> list:
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("memories", [])
    except Exception:
        return []

def save_memory_tool(text: str, category: str = "fact") -> str:
    """保存一条长期记忆"""
    memories = load_memories()
    entry = {"text": text, "category": category, "ts": time.time()}
    # 去重
    for m in memories:
        if m["text"] == text:
            return "已存在，跳过"
    memories.append(entry)
    # 只保留最近 50 条
    memories = memories[-50:]
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"memories": memories}, f, ensure_ascii=False, indent=2)
        log(f"Memory saved: [{category}] {text[:40]}")
        return f"记住了：{text}"
    except Exception as e:
        return f"保存失败: {e}"

def get_memories_tool(category: str = "") -> str:
    """读取长期记忆，可按类别筛选"""
    memories = load_memories()
    if category:
        memories = [m for m in memories if m["category"] == category]
    if not memories:
        return "暂无相关记忆"
    # 按时间倒序
    memories.sort(key=lambda m: -m["ts"])
    lines = [f"- {m['text']}" for m in memories[:10]]
    return "记住的信息：\n" + "\n".join(lines)

def load_preferences() -> list:
    try:
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("style_notes", [])
    except Exception:
        default = {"style_notes": ["用户喜欢极短的 spoken 回复"]}
        try:
            with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return default["style_notes"]

def save_preference_tool(note: str) -> str:
    """保存一条用户偏好"""
    try:
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"style_notes": []}
    notes = data.get("style_notes", [])
    if note not in notes:
        notes.append(note)
        data["style_notes"] = notes[-10:]
        try:
            with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        log(f"Preference saved: {note[:40]}")
        return f"已记住你的偏好：{note}"
    return "已存在"


# ═════════════════════ 工具定义 ═════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查某个城市当前天气和预报",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如珠海、广州"}
                },
                "required": ["city"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "在 Obsidian 笔记中创建一篇新笔记",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "笔记标题"},
                    "content": {"type": "string", "description": "笔记正文"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "记住一条关于用户的重要信息（身份、喜好、事实、约定）",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要记住的内容"},
                    "category": {"type": "string", "enum": ["preference", "identity", "fact", "topic"]}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_preference",
            "description": "记住用户的表达偏好，比如说短话/详细/诗意/直白",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "偏好描述"}
                },
                "required": ["note"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_memories",
            "description": "查看过去记住的用户信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["", "preference", "identity", "fact", "topic"]}
                }
            }
        }
    }
]


def tool_get_weather(city: str) -> str:
    try:
        url = f"https://wttr.in/{urllib.request.quote(city)}?format=%C+%t+%h+%w&lang=zh"
        with urllib.request.urlopen(url, timeout=10) as r:
            raw = r.read().decode("utf-8").strip()
        log(f"Weather({city}): {raw}")
        return raw
    except Exception as e:
        log(f"Weather API error: {e}")
        return "获取天气失败"

def tool_create_note(title: str, content: str) -> str:
    safe = "".join(c for c in title if c.isalnum() or c in " _-（）()").strip() or "笔记"
    path = os.path.join(OBSIDIAN_VAULT, f"{safe}.md")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log(f"Note saved: {safe}.md")
        try:
            subprocess.run(["obsidian", "reload"], capture_output=True, timeout=5)
        except Exception:
            pass
        return f"笔记已保存为{safe}.md"
    except Exception as e:
        return f"保存失败: {e}"

TOOL_DISPATCH = {
    "get_weather": tool_get_weather,
    "create_note": tool_create_note,
    "save_memory": save_memory_tool,
    "save_preference": save_preference_tool,
    "get_memories": get_memories_tool,
}


# ═════════════════════ LLM 调用 ═════════════════════

def _chat_completion(messages, tools=None, tool_choice=None):
    body = {"model": MODEL, "messages": messages, "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE}
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    payload = json.dumps(body).encode()
    req = urllib.request.Request(OPENCLAW_URL, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENCLAW_TOKEN}",
    })
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"LLM error: {e}")
        return None


def _build_messages(user_text, ctx):
    """构建消息列表：System Prompt + 记忆 + 偏好 + 对话历史 + 用户输入"""
    # 注入长期记忆（最近 8 条）
    memories = load_memories()
    mem_text = ""
    if memories:
        memories.sort(key=lambda m: -m["ts"])
        lines = []
        for m in memories[:8]:
            cat = f"[{m['category']}]" if m.get("category") else ""
            lines.append(f"- {cat} {m['text']}")
        if lines:
            mem_text = "\n" + "\n".join(lines)

    # 注入偏好
    prefs = load_preferences()
    pref_text = ""
    if prefs:
        pref_text = "\n" + "\n".join(f"- {n}" for n in prefs)

    prompt = SYSTEM_PROMPT
    if mem_text:
        prompt += f"\n\n## 我记得的用户信息{mem_text}"
    if pref_text:
        prompt += f"\n\n## 用户表达偏好{pref_text}"
    prompt += "\n\n如果用户纠正你的表达方式（太长、太短、太文绉绉），立即调 save_preference 记住。"

    msgs = [{"role": "system", "content": prompt}]
    for turn in ctx.get("turns", [])[-(MAX_CONTEXT_TURNS * 2):]:
        r = turn.get("role", "user")
        t = turn.get("text", "")
        if t:
            msgs.append({"role": r, "content": t})
    msgs.append({"role": "user", "content": user_text})
    return msgs


def _extract_json_result(content: str) -> dict | None:
    if not content:
        return None
    js = content.find("{")
    je = content.rfind("}")
    if js >= 0 and je > js:
        jstr = content[js:je+1].strip().strip("`")
        if jstr.startswith("json"):
            jstr = jstr[4:].strip()
        try:
            data = json.loads(jstr)
            if "spoken" in data and "display" in data:
                r = {"spoken": data["spoken"].strip(), "display": data["display"].strip()}
                if data.get("action") == "note":
                    r["action"] = "note"
                    r["title"] = data.get("title", "")
                    r["note_content"] = data.get("note_content", "")
                return r
        except json.JSONDecodeError:
            pass
    return {"spoken": content[:40], "display": content}


def call_llm_with_tools(user_text: str, ctx: dict) -> dict | None:
    messages = _build_messages(user_text, ctx)
    resp = _chat_completion(messages, tools=TOOLS)
    if not resp:
        return None
    msg = resp["choices"][0]["message"]
    messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg.get("tool_calls")})

    for _ in range(5):
        if not msg.get("tool_calls"):
            break
        for tc in msg["tool_calls"]:
            fn = tc["function"]
            name, args_str = fn["name"], fn["arguments"]
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}
            log(f"Tool call: {name}({args})")
            result = TOOL_DISPATCH.get(name, lambda **_: f"未知工具{name}")(**args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
        resp = _chat_completion(messages, tools=TOOLS)
        if not resp:
            return None
        msg = resp["choices"][0]["message"]
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg.get("tool_calls")})

    content = msg.get("content", "").strip()
    return _extract_json_result(content)


# ═════════════════════ 上下文 ═════════════════════

def load_context():
    try:
        with open(CTX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"max_turns": MAX_CONTEXT_TURNS, "turns": []}

def save_context(ctx):
    try:
        with open(CTX_FILE, "w", encoding="utf-8") as f:
            json.dump(ctx, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"save context error: {e}")

def write_note(title, content):
    safe = "".join(c for c in title if c.isalnum() or c in " _-（）()").strip() or "笔记"
    path = os.path.join(OBSIDIAN_VAULT, f"{safe}.md")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log(f"Note saved: {safe}.md ({len(content)} chars)")
        try:
            subprocess.run(["obsidian", "reload"], capture_output=True, timeout=5)
        except Exception:
            pass
    except Exception as e:
        log(f"Note error: {e}")


# ═════════════════════ TTS / Composite ═════════════════════

def send_tts_direct(text: str):
    try:
        body = json.dumps({"text": text, "prosody": {"emotion": "curious", "rate": "+0%"}}).encode()
        req = urllib.request.Request(TTS_BRIDGE_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def stream_tts(text: str):
    if text:
        send_tts_direct(text)

def mute_mic(seconds=10):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:18779/mute?seconds={seconds}", timeout=2)
    except Exception:
        pass

def flush_voice_queue():
    try:
        urllib.request.urlopen("http://127.0.0.1:18779/voice/flush", timeout=2)
    except Exception:
        pass

def send_composite(text, emotion="curious", action="nod", screen_text=""):
    msg = {"type": "composite", "text": text, "screen_text": screen_text or text, "emotion": emotion, "action": action, "intensity": 0.7, "sender": "Ove"}
    try:
        body = json.dumps(msg).encode()
        req = urllib.request.Request(BRIDGE_PUSH_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def infer_emotion_action(reply):
    for kw, em in [("哼","annoyed"),("讨厌","annoyed"),("无聊","annoyed"),("花","melancholy"),("落","melancholy"),("泪","melancholy"),("诗","proud"),("词","proud"),("死了","sad"),("命","sad"),("哭","sad")]:
        if kw in reply:
            return em, "nod" if em != "annoyed" else "tilt_head"
    return "curious", "nod"


# ═════════════════════ 主循环 ═════════════════════

def poll_voices():
    try:
        req = urllib.request.Request(CORE_VOICE_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read())
            return d.get("voices", []) if isinstance(d, dict) else []
    except Exception as e:
        log(f"poll error: {e}")
        return []

def handle_wake_event(ctx):
    prompt = "（用户对你说'你好黛玉'，请用一句简短诗意问候回应，输出JSON格式）"
    mute_mic(20)
    try:
        result = call_llm_with_tools("你好黛玉", ctx)
        if result and result.get("spoken"):
            em, ac = infer_emotion_action(result["spoken"])
            send_composite(result["spoken"], em, ac, screen_text=result.get("display", ""))
            return
    except Exception:
        pass
    send_composite("你来了？我正想着呢……", "curious", "lookup")

def process_llm_result(text, result, _recent):
    global _last_reply_ts
    if result:
        spoken = result.get("spoken", "")
        display = result.get("display", "")
        log(f"Spoken: {spoken}")
        if display and display != spoken:
            log(f"Display: {display[:80]}...")

        stream_tts(spoken)
        em, ac = infer_emotion_action(spoken)
        send_composite("", em, ac, screen_text=display or spoken)
        flush_voice_queue()
        _last_reply_ts = time.time()

        if result.get("action") == "note":
            write_note(result.get("title", "") or spoken[:20], result.get("note_content", "") or display)

        ctx = load_context()
        ctx.setdefault("turns", []).append({"role": "user", "text": text})
        ctx["turns"].append({"role": "ove", "text": display or spoken, "spoken": spoken, "emotion": em, "action": ac})
        ctx["turns"] = ctx["turns"][-(MAX_CONTEXT_TURNS * 2):]
        save_context(ctx)
    else:
        if len(text) >= 3:
            send_composite("我想想……", "curious", "lookup")
        else:
            send_composite("嗯？没听清……", "curious", "nod")
        _last_reply_ts = time.time()


_last_reply_ts = 0.0
_llm_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

def main():
    global _last_reply_ts
    log("Ove voice agent started (function calling + 成长系统)")
    log("Tools: get_weather, create_note, save_memory, save_preference, get_memories")
    recent_texts = set()
    wake_greeted = False
    pending = None
    pending_text = None

    while True:
        try:
            if pending is not None and pending.done():
                r = None
                try:
                    r = pending.result(timeout=1)
                except Exception as e:
                    log(f"LLM task failed: {e}")
                if r and pending_text:
                    process_llm_result(pending_text, r, recent_texts)
                elif pending_text:
                    process_llm_result(pending_text, None, recent_texts)
                pending = None
                pending_text = None

            if pending is not None and not pending.done():
                time.sleep(POLL_INTERVAL)
                continue

            for entry in poll_voices():
                text = entry.get("text", "").strip()
                source = entry.get("source", "voice")
                if not text:
                    continue
                if time.time() - _last_reply_ts < RESPONSE_COOLDOWN:
                    log("Cooldown")
                    continue
                if text in recent_texts:
                    continue
                recent_texts.add(text)
                if len(recent_texts) > 50:
                    recent_texts = set(list(recent_texts)[-25:])

                if source == "wake":
                    if not wake_greeted:
                        wake_greeted = True
                        log("Wake! Sending greeting...")
                        handle_wake_event(load_context())
                        _last_reply_ts = time.time()
                    continue
                else:
                    wake_greeted = False

                log(f"Heard: {text}")
                mute_mic(25)
                pending = _llm_executor.submit(call_llm_with_tools, text, load_context())
                pending_text = text
                break

        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
