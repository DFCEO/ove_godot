#!/usr/bin/env python3
"""Ove Voice Agent - 林黛玉 mode. Polls for voice input and responds poetically."""
import json
import subprocess
import sys
import time
import os
import re

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ove_integrate.py")

# 林黛玉 style reply templates by keyword/topic
REPLIES = {
    "你好|嗨|hello|hi|早上好|下午好|晚上好": {
        "replies": [
            "嗯……你来了。",
            "你倒还记得我。",
            "来了？我还以为你把我忘了呢。",
        ],
        "emotion": "curious",
        "action": "nod",
    },
    "再见|拜拜|bye|走了": {
        "replies": [
            "这就走了？也罢……",
            "去吧，不必管我。",
            "嗯，你去吧。",
        ],
        "emotion": "melancholy",
        "action": "nod",
    },
    "天气": {
        "replies": [
            "今儿天色倒好，不冷不热的。",
            "风大了些，仔细别着凉。",
            "这天气，阴一阵晴一阵的，怪没趣。",
        ],
        "emotion": "curious",
        "action": "nod",
    },
    "干嘛|做什么|在吗": {
        "replies": [
            "不过是闲坐罢了，有什么事？",
            "我能做什么，不过是发呆罢了。",
            "你又来找我做什么？",
        ],
        "emotion": "curious",
        "action": "nod",
    },
    "吃|饭|饿|食物": {
        "replies": [
            "吃？我没什么胃口……",
            "你倒是有心，还惦记着吃。",
            "我不饿，你自去吃吧。",
        ],
        "emotion": "melancholy",
        "action": "nod",
    },
    "哭|伤心|难过|不开心": {
        "replies": [
            "你又来惹我。",
            "我哭我的，与你什么相干。",
            "这泪也不是为你流的……",
        ],
        "emotion": "sad",
        "action": "nod",
    },
    "笑|开心|高兴|好": {
        "replies": [
            "你笑什么？我可不觉得好笑。",
            "有什么可高兴的……",
            "哼，你倒是开心。",
        ],
        "emotion": "annoyed",
        "action": "nod",
    },
    "花|落花|花瓣|黛玉": {
        "replies": [
            "花谢花飞花满天，红消香断有谁怜……",
            "你见那落花了吗？",
            "花落了，明年还会再开。人去了，可就回不来了。",
        ],
        "emotion": "melancholy",
        "action": "nod",
    },
    "诗|词|书|读": {
        "replies": [
            "你也懂诗？倒要请教了。",
            "这几日倒读了几首好诗。",
            "诗是好的，懂的人却不多。",
        ],
        "emotion": "proud",
        "action": "nod",
    },
    "你|名字|谁": {
        "replies": [
            "我姓林，叫黛玉。你又是谁？",
            "你连我是谁都不知道，就来找我？",
            "我是谁？问这个做什么。",
        ],
        "emotion": "curious",
        "action": "nod",
    },
    "喜欢|爱": {
        "replies": [
            "胡说什么……谁要你喜欢！",
            "你又来说这些疯话。",
            "哼，说得好听。",
        ],
        "emotion": "annoyed",
        "action": "nod",
    },
    "帮|帮忙|help": {
        "replies": [
            "帮你？你也知道来找我了。",
            "什么事？说吧。",
            "求我的时候倒想起我来了。",
        ],
        "emotion": "proud",
        "action": "nod",
    },
    "谢谢|谢|thank": {
        "replies": [
            "谢什么，不值什么。",
            "不必谢，你少惹我就行了。",
            "嗯。",
        ],
        "emotion": "curious",
        "action": "nod",
    },
    "睡觉|困|晚安": {
        "replies": [
            "累了就歇着吧，别在这儿硬撑。",
            "去吧，梦里可别梦见我。",
            "嗯，你也早些休息。",
        ],
        "emotion": "melancholy",
        "action": "nod",
    },
    "音乐|歌|唱|曲": {
        "replies": [
            "曲子么，我倒是会几首。",
            "你想听我唱？怕你听了睡不着。",
            "这曲子，倒是有些意思。",
        ],
        "emotion": "proud",
        "action": "nod",
    },
    "照片|拍照|图片|看看": {
        "replies": [
            "看什么看，有什么好看的。",
            "你倒是有闲心。",
            "给你看了又怎样。",
        ],
        "emotion": "annoyed",
        "action": "nod",
    },
}

# Fallback for unrecognized input
FALLBACKS = [
    ("嗯？你说什么？", "curious", "nod"),
    ("我没听清，再说一遍吧。", "curious", "nod"),
    ("这话说得不清不楚的……", "annoyed", "nod"),
    ("罢了，随你去吧。", "melancholy", "nod"),
]

def find_reply(text):
    """Match text against keyword patterns and return a random reply."""
    import random
    text_lower = text.lower().strip()
    
    for pattern, config in REPLIES.items():
        if re.search(pattern, text_lower):
            reply = random.choice(config["replies"])
            return reply, config["emotion"], config["action"]
    
    # Fallback
    reply, emotion, action = random.choice(FALLBACKS)
    return reply, emotion, action

def poll_voices():
    """Run ove_integrate.py voice and return parsed entries."""
    try:
        result = subprocess.run(
            [sys.executable, SCRIPT, "voice"],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            cwd=os.path.dirname(SCRIPT)
        )
        if result.returncode == 0:
            stdout = result.stdout.strip() if result.stdout else "[]"
            data = json.loads(stdout or "[]")
            return data if isinstance(data, list) else []
        return []
    except Exception as e:
        print(f"[voice-agent] poll error: {e}", file=sys.stderr)
        return []

def send_composite(text, emotion="curious", action="nod"):
    """Send a composite message to Ove."""
    try:
        subprocess.run(
            [sys.executable, SCRIPT, "composite", text, "--emotion", emotion, "--action", action],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            cwd=os.path.dirname(SCRIPT)
        )
        return True
    except Exception as e:
        print(f"[voice-agent] composite error: {e}", file=sys.stderr)
        return False

def main():
    print("[voice-agent] 林黛玉 voice agent started. Polling every 15s...", file=sys.stderr)
    last_ids = set()
    
    while True:
        try:
            entries = poll_voices()
            
            for entry in entries:
                entry_id = entry.get("id") or entry.get("text", "")
                if entry_id and entry_id in last_ids:
                    continue  # Skip already processed
                
                text = entry.get("text", "").strip()
                if not text:
                    continue
                
                if entry_id:
                    last_ids.add(entry_id)
                
                print(f"[voice-agent] Heard: {text}", file=sys.stderr)
                
                reply, emotion, action = find_reply(text)
                print(f"[voice-agent] Reply: {reply} ({emotion}/{action})", file=sys.stderr)
                
                send_composite(reply, emotion, action)
            
            # Keep last_ids from growing too large
            if len(last_ids) > 200:
                last_ids = set(list(last_ids)[-100:])
            
        except Exception as e:
            print(f"[voice-agent] loop error: {e}", file=sys.stderr)
        
        time.sleep(15)

if __name__ == "__main__":
    main()
