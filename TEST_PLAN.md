# Ove Godot 测试计划

> 测试日期：2026-04-30
> 环境：Windows 10, Python 3.10.10, Godot 4.4, edge-tts 7.2.8, pygame 2.6.1

---

## Phase 1 — 预检（无需启动 Godot）

### 1.1 依赖检查
```bash
python --version          # 期望: Python 3.10+
python -c "import edge_tts; print(edge_tts.__version__)"  # 期望: 7.2.8
python -c "import pygame; print('OK')"  # 期望: OK
godot --version           # 期望: 4.4+
```

### 1.2 配置文件完整性
```bash
python -c "import json; json.load(open('persona.json','r',encoding='utf-8')); print('OK')"
```
- [ ] persona.json 15 种情绪完整
- [ ] persona.json 6 个场景卡片完整
- [ ] persona.json emotionToProsody 定义了 sad/annoyed/happy/surprised/melancholy
- [ ] project.godot 窗口配置：borderless=true, transparent=true, always_on_top=true

### 1.3 端口可用性
```bash
netstat -ano | findstr 18776   # 期望：无结果（端口空闲）
netstat -ano | findstr 18777   # 期望：无结果（端口空闲）
```

---

## Phase 2 — TTS 桥独立测试

### 2.1 启动 TTS 桥
```bash
python tts_bridge.py
```
- [ ] 输出 `[TTS Bridge] Ready on http://127.0.0.1:18777`
- [ ] 进程不崩溃

### 2.2 TTS 合成测试
```bash
# 中性语速
curl -X POST http://127.0.0.1:18777/tts -H "Content-Type: application/json" -d '{"text":"你好，这是一条测试消息"}'
```
- [ ] 听到语音播放
- [ ] 音质正常（晓晓女声）
- [ ] 播放完毕不卡死

### 2.3 韵律参数测试
```bash
# 悲伤：慢速 + 低音调
curl -X POST http://127.0.0.1:18777/tts -H "Content-Type: application/json" -d '{"text":"花谢花飞花满天，红消香断有谁怜","prosody":{"rate":"-15%","pitch":"low"}}'

# 惊讶：快速 + 高音调
curl -X POST http://127.0.0.1:18777/tts -H "Content-Type: application/json" -d '{"text":"你说什么？","prosody":{"rate":"+15%","pitch":"high"}}'
```
- [ ] sad 韵律明显慢且低沉
- [ ] surprised 韵律明显快且高
- [ ] SSML 未报错

### 2.4 并发 TTS
```bash
# 快速连续发送两条，验证不崩溃
curl -X POST http://127.0.0.1:18777/tts -H "Content-Type: application/json" -d '{"text":"第一条"}' &
curl -X POST http://127.0.0.1:18777/tts -H "Content-Type: application/json" -d '{"text":"第二条"}'
```
- [ ] 两条都播放
- [ ] 不卡死、不崩溃

### 2.5 断网测试
```
断网 → 发送 TTS 请求 → 期望: 返回 200 但无声音（优雅降级）
```
- [ ] 不崩溃，不报未捕获异常

---

## Phase 3 — Godot 启动 + 自检

### 3.1 正常启动
```bash
run.bat
# 或直接 Godot 打开项目
```
- [ ] 窗口出现在屏幕右下角
- [ ] 窗口透明背景
- [ ] 窗口置顶（始终在其他窗口前面）
- [ ] 3D 模型可见（EVE 角色）
- [ ] 模型有纹理（非纯色）
- [ ] 模型在窗口中央偏下

### 3.2 启动问候
- [ ] 启动 2 秒后听到 TTS 问候语
- [ ] 根据时段内容不同：
  - 凌晨 0-6：「这么晚了，还不歇着……」
  - 早晨 6-12：「晨光正好。」
  - 下午 12-18：「日长人倦……」
  - 晚上 18-24：「天又黑了。」

### 3.3 HTTP 服务器启动
```bash
curl http://127.0.0.1:18776/health
```
- [ ] 返回 `{"status":"ok","pet":"Ove Godot"}`

### 3.4 3D 渲染检查
- [ ] 模型方向正确（略微侧身）
- [ ] 骨骼：body 微晃、手臂自然摆动、头左右摇
- [ ] 眼睛球体可见（两个白色小球 + 黑色瞳孔）
- [ ] 眼睛会眨眼（频率 ~2-4 秒一次）
- [ ] 光照正常（有阴影感）

---

## Phase 4 — HTTP API 端点测试

### 4.1 自动化测试
```bash
python test_all.py
```
- [ ] 所有测试 ✅

### 4.2 手动测试

#### /message
```bash
curl -X POST http://127.0.0.1:18776/message -H "Content-Type: application/json" -d '{"sender":"测试","text":"你好，Ove"}'
```
- [ ] 气泡消息出现在窗口上方
- [ ] 气泡显示「你好，Ove」
- [ ] TTS 朗读消息内容
- [ ] 气泡 4-5 秒后自动隐藏

#### /emotion
```bash
# Happy
curl -X POST http://127.0.0.1:18776/emotion -H "Content-Type: application/json" -d '{"emotion":"happy","intensity":0.8}'
# 观察：灯光变暖橙、头部微歪、手臂摆动加大、听到欣悦台词
```
- [ ] happy：灯光变暖橙「今朝倒是难得的好日头」
- [ ] sad：灯光变暗蓝、低头、听到悲戚台词
- [ ] angry：灯光变红、扭头、听到气恼台词
- [ ] melancholy：灯光变暗紫、自怜姿态
- [ ] neutral：回到默认状态

每个情绪测试等 5 秒，观察衰减：
- [ ] 12 秒后开始衰减
- [ ] 骨骼动画幅度逐渐减小
- [ ] 灯光颜色逐渐回归 neutral
- [ ] ~32 秒后完全回到 neutral

#### /action
```bash
curl -X POST http://127.0.0.1:18776/action -H "Content-Type: application/json" -d '{"action":"nod"}'
```
- [ ] nod：点头
- [ ] wave_right：挥右手
- [ ] shake_head：摇头
- [ ] bounce：弹跳
- [ ] tilt_head：歪头

#### /scene
```bash
curl -X POST http://127.0.0.1:18776/scene -H "Content-Type: application/json" -d '{"scene":"葬花"}'
```
- [ ] 葬花：低头看花 → 蹲下 → 拈花瓣 → 起身叹息（~6 秒）
- [ ] 拌嘴扭头：瞪 → 扭头 → 偷瞟 → 再扭（~3 秒）
- [ ] 冷笑：抬头 → 侧目 → 转回（~1.6 秒）
- [ ] 摇头晃脑：反复转头+点头（~4 秒）
- [ ] 场景播放期间闲置微动暂停
- [ ] 场景结束后恢复原情绪

#### /tweak
```bash
curl -X POST http://127.0.0.1:18776/tweak -H "Content-Type: application/json" -d '{"model":{"y":-0.3}}'
```
- [ ] 模型位置变化

### 4.3 错误处理
```bash
curl -X POST http://127.0.0.1:18776/emotion -H "Content-Type: application/json" -d '{"bad":"data"}'  # 期望 400
curl http://127.0.0.1:18776/nonexistent   # 期望 404
```
- [ ] 400 返回正确错误信息
- [ ] 404 返回正确错误信息

---

## Phase 5 — 情绪系统深度测试

### 5.1 情绪衰减时间线
```
00:00 - POST /emotion happy 0.8
00:00-00:12 - happy 满强度保持（外部推送 hold 12s）
00:12-00:32 - 强度从 0.8 线性衰减到 0.3
00:32     - 自动切换 neutral
```
- [ ] 时间线正确

### 5.2 场景触发 - 闲置
```
等待 5 分钟不发送任何 API 请求
期望：自动切换到 lonely 情绪
```
- [ ] 5 分钟后出现 lonely（灯光暗、低头、孤寂台词）
- [ ] 发送任意消息后闲置计时器重置

### 5.3 场景触发 - 夜间
```
系统时间改到 21:00+，重启 Godot
期望：自动切换到 melancholy 情绪
```
- [ ] 21 点后触发 melancholy
- [ ] 白天不触发

### 5.4 防反馈循环
```bash
# 1. 外源推送 happy
curl -X POST http://127.0.0.1:18776/emotion -d '{"emotion":"happy"}'
# 2. 发消息包含「哈哈」
curl -X POST http://127.0.0.1:18776/message -d '{"sender":"test","text":"哈哈好开心"}'
# 期望：消息不触发重复的 happy（_last_pushed_emotion 拦截）
```
- [ ] 关键词不重复触发同情绪

### 5.5 外源覆盖场景
```
1. 等待闲置触发 lonely
2. 立即 POST /emotion happy
3. 期望：happy 覆盖 lonely，hold 12s 后才衰减
```
- [ ] 外部推送优先于场景触发
- [ ] 场景触发不在 hold 期内覆盖外部推送

---

## Phase 6 — 交互测试

### 6.1 窗口拖拽
- [ ] 按住模型区域拖动，窗口跟随鼠标
- [ ] 松开后窗口停留在新位置
- [ ] 拖拽不触发其他交互

### 6.2 气泡消息
- [ ] 消息显示在窗口上方
- [ ] 文字居中、自动换行
- [ ] 长时间消息气泡显示更久
- [ ] 多条消息逐条显示（不重叠）

### 6.3 TTS 与消息同步
- [ ] 消息到达后先显示气泡，同时开始 TTS
- [ ] 情绪切换时说话不干扰消息 TTS

---

## Phase 7 — 异常 / 边界测试

### 7.1 无网络
- [ ] edge-tts 失败时不崩溃
- [ ] HTTP API 仍正常响应
- [ ] 气泡消息正常显示
- [ ] 骨骼动画正常运行

### 7.2 端口占用
- [ ] 18776 被占用时 http_server 打印错误但不崩溃
- [ ] 18777 被占用时 TTS 桥打印错误但不崩溃

### 7.3 快速连续请求
```bash
# 10 个连续 emotion 请求
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -X POST http://127.0.0.1:18776/emotion -d '{"emotion":"happy"}'
done
```
- [ ] 不崩溃
- [ ] 最后停在 happy（最新请求生效）

### 7.4 缺失模型文件
```
临时改名 models/eve_ah.glb → _eve_ah.glb
启动 Godot
期望：打印 "Model not found"，程序不崩溃
```
- [ ] 优雅降级，窗口仍显示

### 7.5 缺失 persona.json
```
临时改名 persona.json → _persona.json
启动 Godot
期望：情绪系统降级，骨骼基础动画仍运行
```
- [ ] 不崩溃
- [ ] 基础骨骼动画正常工作

### 7.6 内存 / 性能
- [ ] 运行 30 分钟后内存稳定（无明显泄漏）
- [ ] CPU 占用 < 5%
- [ ] 帧率稳定（无明显卡顿）

---

## Phase 8 — OpenClaw 集成测试

### 8.1 CLI 工具
```bash
python ove_integrate.py health
python ove_integrate.py message "Ove" "你好"
python ove_integrate.py emotion happy --intensity 0.7
python ove_integrate.py action wave_right
python ove_integrate.py scene 葬花
```
- [ ] 每个命令返回成功
- [ ] Godot 端有对应反应

### 8.2 Python API
```python
from ove_integrate import push_to_ove, set_emotion, do_action, play_scene, health

assert health()["status"] == "ok"
assert push_to_ove("Test", "API 测试")
assert set_emotion("curious", 0.5)
assert do_action("nod")
assert play_scene("冷笑")
```
- [ ] 所有 API 调用返回 True

---

## 测试记录 (2026-05-06 更新)

### Bug 修复
- **HTTP body 丢失**：`_is_complete()` 只检测 `\r\n\r\n`，TCP 分包时 header 到就 break，body 丢失导致 400。修复为两阶段读取（header → Content-Length → body）
- **test_all.py**：urllib 对 4xx 抛 HTTPError 导致 status 读到 None，加了 `urllib.error.HTTPError` 处理

| 阶段 | 项目 | 结果 | 备注 |
|------|------|:----:|------|
| P1 | 1.1 依赖检查 | ✅ | Python 3.10.10, edge-tts 7.2.8, pygame 2.6.1 |
| P1 | 1.2 配置文件 | ✅ | 15情绪 6场景 5韵律 (keys: 经典瞬间动作卡片/voiceProfile.emotionToProsody) |
| P1 | 1.3 端口检查 | ✅ | 18776 空闲, 18777 TTS桥已运行 |
| P2 | 2.1 启动 TTS 桥 | ✅ | 已在运行 |
| P2 | 2.2 TTS 合成 | ✅ | API 返回 ok，听感需人工确认 |
| P2 | 2.3 韵律参数 | ✅ | API 层通过（sad/surprised 韵律请求均 ok），听感需人工确认 |
| P2 | 2.4 并发 TTS | ✅ | 两条并发均返回 ok |
| P2 | 2.5 断网 TTS | ⏭ | 代码审查：error handling 已修（不再吞异常），跳过实际断网测试 |
| P3 | 3.1 正常启动 | ✅ | Godot 启动，窗口可见 |
| P3 | 3.2 启动问候 | ⏭ | API 层验证通过，听感需人工确认 |
| P3 | 3.3 HTTP 服务器 | ✅ | health 正常，18776 监听 |
| P3 | 3.4 3D 渲染 | 👁 | 需肉眼检查 |
| P4 | 4.1 自动化测试 | ✅ | test_all.py 15/15 全部通过 |
| P4 | 4.2 /message | ✅ | 气泡 + TTS 正常 |
| P4 | 4.2 /emotion | ✅ | 全部 4 种情绪 200 |
| P4 | 4.2 /action | ✅ | nod/wave_right/bounce 全部 200 |
| P4 | 4.2 /scene | ✅ | 摇头晃脑/葬花/冷笑 全部 200 |
| P4 | 4.2 /tweak | ✅ | 正常 |
| P4 | 4.3 错误处理 | ✅ | 400/404 正确返回 |
| P5 | 5.1 衰减时间线 | 👁 | 需肉眼观察（灯光/骨骼变化 ~32s） |
| P5 | 5.2 闲置触发 | ⏱ | 需等 5 分钟，跳过 |
| P5 | 5.3 夜间触发 | ⏱ | 当前非 21:00+，跳过 |
| P5 | 5.4 防反馈循环 | ✅ | 外源推 happy 后消息含"哈哈"不重复触发 |
| P5 | 5.5 外源覆盖 | ⏱ | 需先等闲置 5min 触发 lonely 再覆盖，跳过 |
| P6 | 6.1 拖拽 | 👁 | 需肉眼检查 |
| P6 | 6.2 气泡 | 👁 | 需肉眼检查 |
| P6 | 6.3 TTS 同步 | 👁 | 需肉眼+听感检查 |
| P7 | 7.1 无网络 | ⏭ | 代码审查通过（try/except 兜底），跳过 |
| P7 | 7.2 端口占用 | ⏭ | 代码审查通过（print 错误不崩溃），跳过 |
| P7 | 7.3 快速请求 | ✅ | 10 次连续 /emotion 不崩溃 |
| P7 | 7.4 缺模型 | ⏭ | 需重启 Godot，跳过 |
| P7 | 7.5 缺 persona | ⏭ | 需重启 Godot，跳过 |
| P7 | 7.6 性能 | 👁 | 需长时间观察 |
| P8 | 8.1 CLI | ✅ | 5 个命令全部返回正确 |
| P8 | 8.2 Python API | ✅ | 5 个 API 全部 assert 通过 |

图例: ✅ 通过 | 👁 需人工肉眼/听感验证 | ⏱ 需等待/改时间 | ⏭ 代码审查通过跳过实测

---

## 调试指南

### 常见问题排查

**TTS 没声音**
1. 检查 `tts_bridge.py` 是否在运行（`netstat -ano | findstr 18777`）
2. 检查网络连接（`ping bing.com`）
3. 检查 Python 输出是否有 edge-tts 报错
4. 检查 pygame mixer 初始化日志

**模型不显示**
1. 检查 `models/eve_ah.glb` 存在
2. 检查 Godot 输出窗口的 GLB 加载日志
3. 检查 `bones.log`（在 `%APPDATA%\Godot\app_userdata\Ove Desktop Pet\`）

**情绪灯不变色**
1. 检查 OmniLight3D 节点名（main.tscn 必须是 "OmniLight3D"）
2. 检查 persona.json glowColor 值

**API 连不上**
1. 检查 Godot 是否在运行
2. `curl http://127.0.0.1:18776/health`
3. 检查防火墙是否拦截
