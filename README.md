# Ove 🐚 — Godot 桌面宠物

> Godot 4.4 实现的桌面宠物，林黛玉角色，3D 骨骼动画 + 情绪系统 + TTS 语音

## 技术栈

| 层 | 技术 | 版本 | 作用 |
|---|---|---|---|
| 引擎 | Godot | 4.4 | 3D 渲染、透明窗口、事件循环 |
| 3D 模型 | GLB (eve_ah.glb) | — | Blender 导出，4 骨骼 + 蒙皮 |
| 语音 | edge-tts (晓晓) | 7.2.8 | 在线中文语音合成 |
| 音频 | pygame | 2.6.1 | MP3 播放 |
| 集成 | Python CLI | 3.10+ | OpenClaw 工具调用的 HTTP 客户端 |

## 魂身合一架构 (v2)

```
┌─────────────┐     HTTP (18779)     ┌──────────────┐     WebSocket (18778)     ┌──────────────┐
│  OpenClaw    │ ──────────────────> │  ove_bridge   │ <─────────────────────> │  Godot 宠物   │
│  (大脑/Ove)  │ <────────────────── │  (中继/缓存)   │                         │  (身体/表现)  │
└─────────────┘     GET /state        └──────────────┘                         └──────────────┘
                          ↑                                                          │
                          │                             HTTP (18776，兼容旧接口)      │
                          └──────────────────────────────────────────────────────────┘
```

**核心能力：**
- OpenClaw 每次回复 → `push_composite(text, emotion, action)` → 机器人同步说话+表情+动作
- 机器人每 2 秒上报状态（情绪、闲置时长）→ bridge 缓存 → OpenClaw 随时查询
- Godot 事件（闲置预警、被戳、拖拽）→ 事件队列 → OpenClaw 拉取后决策

## 情绪系统架构

```
外部 API (POST /emotion)        关键词匹配 (_infer_emotion)
        │                              │
        ▼                              ▼
   set_emotion(name, intensity,    set_emotion(name, 0.6,
              "external")                  "scene")
        │                              │
        └──────────┬───────────────────┘
                   ▼
            _emotion_age 开始计时
                   │
      hold 12s(外部) / 8s(场景)
                   │
                   ▼
            强度线性衰减 20s → neutral
                   │
                   ▼
        触发场景规则：闲置 5min → lonely
                      21:00 后 → melancholy
```

- **防反馈循环**：外源推送的情绪优先，场景/关键词不覆盖；`_last_pushed_emotion` 防止关键词重复触发
- **眼睛颜色联动**：切换情绪时自动改眼睛颜色和亮度（从 persona.json glowColor 读取）
- **眨眼**：随情绪改变频率（blinkFreq），缩放眼球 Y 轴模拟

## 项目结构

```
ove-godot/
├── project.godot          # Godot 项目配置（透明窗口、置顶、无边框）
├── main.tscn              # 主场景（Camera + Lights + HTTP Server）
├── scripts/
│   ├── pet.gd             # 核心：3D 模型加载、骨骼动画、情绪系统、气泡消息
│   └── http_server.gd     # HTTP API + WebSocket 客户端（端口 18776 + WS 18778）
├── models/
│   ├── eve_ah.glb         # 3D 模型（~1.9MB）
│   └── eve_ah_Image_*.png # 模型纹理
├── persona.json           # 角色配置：15 种情绪参数 + 场景触发规则 + 动作卡片
├── ove_bridge.py          # 🆕 WebSocket 中继（端口 18778/18779，魂身合一核心）
├── ove_integrate.py       # OpenClaw 集成层 v2（含 push_composite / get_robot_state）
├── tts_bridge.py          # TTS 桥接微服务（端口 18777，→ GSV Server 9881）
├── test_all.py            # 自动化测试脚本
├── TEST_PLAN.md           # 详细测试计划
├── start_all.bat          # 一键启动（GSV + TTS 桥 + Bridge + Godot）
├── stop.ps1               # 停止脚本
├── ROADMAP.md             # 开发路线图
└── README.md
```

## 启动方式

```bash
# 方式 A：一键启动（start_all.bat 自动开 GSV + TTS 桥 + Godot）
start_all.bat

# 方式 B：手动启动
python tts_bridge.py              # 1. 先开 TTS 桥
# 2. 用 Godot 编辑器打开项目，按 F5 运行
```

启动后会听到林黛玉风格的问候语（根据时段）。渲染驱动用 OpenGL3（透明窗口兼容性最好）。

## HTTP API

### Godot 直接 (端口 18776)

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| `GET` | `/health` | — | 健康检查 |
| `POST` | `/message` | `{"sender":"...","text":"..."}` | 发消息（气泡 + TTS） |
| `POST` | `/emotion` | `{"emotion":"happy","intensity":0.6}` | 设情绪（含强度） |
| `POST` | `/action` | `{"action":"wave_right"}` | 触发动作 |
| `POST` | `/tweak` | `{"model":{"y":-0.5},"camera":{"z":2},"targets":{...}}` | 实时调参 |
| `POST` | `/scene` | `{"scene":"葬花"}` | 播放场景动作序列 |

### Bridge 中继 (端口 18779) — 魂身合一

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| `GET` | `/health` | — | Bridge 健康 + Godot 连接状态 |
| `GET` | `/state` | — | 机器人当前状态（情绪/闲置/运行时间） |
| `GET` | `/events` | — | 获取并清空事件队列 |
| `POST` | `/push` | `{"type":"composite","text":"...","emotion":"happy","action":"nod"}` | 复合指令推送 |

## 动作列表

| 分类 | 指令 | 效果 |
|------|------|------|
| 手臂 | `point_right` | 右臂前伸 |
| 手臂 | `point_left` | 左臂前伸 |
| 手臂 | `both_forward` | 双臂前伸 |
| 手臂 | `both_back` | 双臂后摆 |
| 手臂 | `spread` | 双臂展开 |
| 手臂 | `right_side` | 右臂右摆 |
| 手臂 | `left_side` | 左臂左摆 |
| 头部 | `nod` | 点头 |
| 头部 | `lookup` | 抬头 |
| 头部 | `shake_head` | 摇头 |
| 头部 | `tilt_head` | 歪头 |
| 身体 | `bounce` | 弹跳 |

## 场景序列

`葬花` `听戏入神` `拌嘴扭头` `怔住（惊喜）` `冷笑` `摇头晃脑`

## 情绪列表（15 种，林黛玉角色）

| 情绪 | 中文标签 | 说明 |
|------|----------|------|
| neutral | 静默 | 默认状态，微愁底色 |
| melancholy | 自怜 | 葬花时的底色情绪 |
| sad | 悲戚 | 深切的伤感 |
| hurt | 心伤 | 被无心之言刺痛 |
| annoyed | 薄恼 | 和宝玉闹小脾气 |
| angry | 气恼 | 真生气、冷笑 |
| happy | 欣悦 | 黛玉式的开心——不外放 |
| proud | 暗喜 | 内心得意但表面淡然 |
| curious | 入神 | 被某事深深吸引 |
| surprised | 怔住 | 意外之事、脸红退步 |
| anxious | 急切 | 焦急关切 |
| lonely | 孤寂 | 长期无互动触发 |
| grateful | 感念 | 被人善意关爱时的动容 |
| resigned | 认命 | 绝望后的平静 |
| defiant | 决绝 | 焚稿时的决绝 |

## 骨骼结构

```
模型 (GLB) — 8 骨骼
├─ root
├─ body_2
├─ head_ctrl
├─ head_2
├─ eye_l_2
├─ eye_r_2
├─ arm_r_2
└─ arm_l_2
```

## OpenClaw 集成

### 魂身合一模式（推荐）

```python
from ove_integrate import push_composite, get_robot_state, get_events, is_robot_connected

# 一次推送：说话 + 情绪 + 动作
push_composite(
    text="长官，今儿来得早。",
    emotion="happy",
    action="nod"
)

# 查询机器人状态
state = get_robot_state()
print(state["current_emotion"], state["idle_seconds"])

# 获取事件（闲置预警等）
for event in get_events():
    if event["event"] == "idle_alert":
        push_composite("哼，没人理我……", emotion="lonely")
```

### 直接模式（兼容旧接口）

```python
from ove_integrate import push_to_ove, set_emotion, do_action

push_to_ove("Ove", "你好长官")
set_emotion("happy", 0.7)
do_action("wave_right")
```

命令行：

```bash
# 魂身合一
python ove_integrate.py composite "你好长官" --emotion happy --action nod
python ove_integrate.py state
python ove_integrate.py events

# 直接模式
python ove_integrate.py message "Ove" "你好"
python ove_integrate.py emotion happy --intensity 0.8
python ove_integrate.py action bounce
```

## 功能状态

| 功能 | 状态 |
|------|:--:|
| 透明桌面窗口 | ✅ |
| 3D 模型加载 + 骨骼动画 | ✅ |
| HTTP API 全部端点 | ✅ |
| 关键词 → 情绪匹配 | ✅ |
| 关键词 → 动作匹配 | ✅ |
| 气泡消息 | ✅ |
| TTS 语音（edge-tts） | ✅ |
| 15 情绪配置系统 | ✅ |
| 3D 眼睛 | ✅ |
| 闲置微动 | ✅ |
| 窗口拖拽 | ✅ |
| 情绪衰减（自然回归 neutral） | ✅ |
| 场景触发规则（时间/闲置） | ✅ |
| 情绪 → 眼睛颜色联动 | ✅ |
| 情绪 → TTS 韵律联动 | ✅ |
| 场景动作卡片序列 | ✅ |
| 情绪切换 → 自动语音 | ✅ |
| 眨眼模拟 | ✅ |
| 防反馈循环 | ✅ |

## 已知问题

- TTS 依赖网络（edge-tts 在线合成），断网时优雅降级（无声音不崩溃）
- 模型无面部骨骼，表情通过眼睛颜色/亮度/大小表达（不驱动身体骨骼）
- 眼球追踪（视线跟随鼠标/消息来源）尚未实现
- 头眼联动已通过 BoneAttachment3D 解决
- 透明窗口在某些 GPU 上需要关闭 per-pixel transparency
- 未做 Linux/macOS 兼容测试
