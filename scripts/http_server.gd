extends Node
## HTTP API + WebSocket 客户端 — 魂身合一
## HTTP 服务器 (18776) + WS 连接 ove_bridge (18778)

@export var port := 18776
@export var bind_address := "127.0.0.1"

var _server: TCPServer = null
var _pet: Node = null
var _running := false
var _last_pushed_emotion := ""   # 防反馈循环：记录上次外源推送的情绪

# ---- WebSocket 客户端 (→ ove_bridge) ----
var _ws = null  # WebSocketPeer (untyped to avoid GDScript strict-mode issues)
var _ws_url := "ws://127.0.0.1:18778"
var _ws_connected := false
var _ws_reconnect_timer := 0.0
var _ws_state_timer := 0.0
var _ws_startup_ts := 0.0
const WS_RECONNECT_INTERVAL := 3.0
const WS_STATE_INTERVAL := 2.0  # 每 2 秒上报状态


func _ready():
	# 查找 pet 节点
	_pet = get_parent()
	_ws_startup_ts = Time.get_ticks_msec() / 1000.0
	_start_server()
	_ws_connect()


func _start_server():
	_server = TCPServer.new()
	var err := _server.listen(port, bind_address)
	if err != OK:
		printerr("[HTTP] Failed to bind ", bind_address, ":", port, " error=", err)
		return
	_running = true
	print("[HTTP] Listening on http://", bind_address, ":", port)
	
	# 自动启动 TTS 桥接（非阻塞）
	_start_tts_bridge()


func _process(delta: float):
	if not _running:
		return
	
	# ---- HTTP 服务器轮询 ----
	while _server.is_connection_available():
		var conn := _server.take_connection()
		if conn == null:
			continue
		_handle_connection(conn)
	
	# ---- WebSocket 客户端轮询 ----
	_ws_poll(delta)


func _handle_connection(stream: StreamPeerTCP):
	# 读 HTTP 请求（分两阶段：header → body）
	var req_bytes := PackedByteArray()
	
	# Phase 1: 读 header 直到 \r\n\r\n
	var header_end := -1
	var deadline_ms := Time.get_ticks_msec() + 5000
	while Time.get_ticks_msec() < deadline_ms:
		while stream.get_available_bytes() > 0:
			var chunk := stream.get_partial_data(4096)
			if chunk[0] != OK:
				break
			req_bytes.append_array(chunk[1])
			if req_bytes.size() > 65536:
				break
		# 找 \r\n\r\n 位置
		for i in range(req_bytes.size() - 3):
			if req_bytes[i] == 13 and req_bytes[i+1] == 10 and req_bytes[i+2] == 13 and req_bytes[i+3] == 10:
				header_end = i + 4
				break
		if header_end != -1:
			break
		if req_bytes.size() > 65536:
			break
	
	if header_end == -1:
		return
	
	# 解析 header 部分找 Content-Length
	var header_text := req_bytes.slice(0, header_end).get_string_from_utf8()
	var header_lines := header_text.split("\r\n")
	if header_lines.size() < 1:
		return
	var parts := header_lines[0].split(" ")
	if parts.size() < 2:
		return
	var method := parts[0]
	var path := parts[1]
	
	var content_length := 0
	for line in header_lines:
		if line.begins_with("Content-Length:") or line.begins_with("content-length:"):
			var cl := line.split(":")[1].strip_edges()
			content_length = cl.to_int()
	
	# Phase 2: 读 body（如果有 Content-Length）
	var body_start := header_end
	while (req_bytes.size() - body_start) < content_length:
		if Time.get_ticks_msec() > deadline_ms:
			break
		while stream.get_available_bytes() > 0:
			var chunk := stream.get_partial_data(4096)
			if chunk[0] != OK:
				break
			req_bytes.append_array(chunk[1])
			if req_bytes.size() > 65536:
				break
	
	if req_bytes.size() == 0:
		return
	
	# 提取 body 文本
	var body_bytes := req_bytes.slice(body_start, body_start + content_length)
	var body := body_bytes.get_string_from_utf8()
	
	# 路由
	var resp_body := ""
	var resp_code := 200
	var route_data: Variant = {}
	
	if method == "GET" and path.begins_with("/health"):
		resp_body = '{"status":"ok","pet":"Ove Godot"}'
	
	elif method == "POST" and path == "/message":
		route_data = _parse_json(body)
		if route_data is Dictionary and route_data.has("text"):
			resp_body = '{"status":"ok"}'
			call_deferred("_on_message", route_data.get("sender", "Ove"), route_data.get("text", ""))
		else:
			resp_code = 400
			resp_body = '{"error":"invalid json"}'
	
	elif method == "POST" and path == "/progress":
		# TTS bridge callback: update bubble to current sentence
		route_data = _parse_json(body)
		if route_data is Dictionary and route_data.has("text"):
			resp_body = '{"status":"ok"}'
			call_deferred("_on_progress", route_data.get("text", ""))
		else:
			resp_code = 400
			resp_body = '{"error":"missing text"}'
	
	elif method == "POST" and path == "/action":
		route_data = _parse_json(body)
		if route_data is Dictionary and route_data.has("action"):
			resp_body = '{"status":"ok","action":"%s"}' % route_data.get("action", "")
			call_deferred("_on_action", route_data.get("action", ""))
		else:
			resp_code = 400
			resp_body = '{"error":"missing action"}'
	
	elif method == "POST" and path == "/emotion":
		route_data = _parse_json(body)
		if route_data is Dictionary and route_data.has("emotion"):
			resp_body = '{"status":"ok","emotion":"%s"}' % route_data.get("emotion", "")
			call_deferred("_on_emotion", route_data)
		else:
			resp_code = 400
			resp_body = '{"error":"invalid json"}'
	
	elif method == "POST" and path == "/tweak":
		resp_body = '{"status":"ok"}'
		route_data = _parse_json(body)
		if route_data is Dictionary:
			call_deferred("_on_tweak", route_data)
	
	elif method == "POST" and path == "/scene":
		route_data = _parse_json(body)
		if route_data is Dictionary and route_data.has("scene"):
			resp_body = '{"status":"ok","scene":"%s"}' % route_data.get("scene", "")
			call_deferred("_on_scene", route_data.get("scene", ""))
		else:
			resp_code = 400
			resp_body = '{"error":"missing scene"}'
	
	elif method == "GET" and path == "/inspect":
		if _pet and _pet.has_method("get_inspect"):
			resp_body = JSON.stringify(_pet.get_inspect())
		else:
			resp_body = '{"error":"pet not ready"}'
	
	elif method == "GET" and path == "/debug":
		if _pet and _pet.has_method("get_debug_info"):
			resp_body = JSON.stringify(_pet.get_debug_info())
		else:
			resp_body = '{"error":"pet not ready"}'
	
	elif method == "GET" and path == "/ws-status":
		var ws_state := -1
		if _ws:
			ws_state = _ws.get_ready_state()
		var d := {
			"ws_obj": _ws != null,
			"ws_state": ws_state,
			"ws_connected": _ws_connected,
			"ws_url": _ws_url,
			"reconnect_timer": _ws_reconnect_timer,
			"has_ws_class": ClassDB.class_exists("WebSocketPeer"),
		}
		resp_body = JSON.stringify(d)
	
	elif method == "POST" and path == "/force":
		# 强制设置骨骼参数（绕过所有系统，直接控制）
		if _pet:
			route_data = _parse_json(body)
			if route_data is Dictionary and route_data.has("targets"):
				_pet.force_targets(route_data.targets)
				resp_body = '{"status":"ok"}'
			else:
				resp_code = 400
				resp_body = '{"error":"missing targets"}'
		else:
			resp_body = '{"error":"pet not ready"}'
	
	else:
		resp_code = 404
		resp_body = '{"error":"not found"}'
	
	# 发送响应
	_send_response(stream, resp_code, resp_body)


func _send_response(stream: StreamPeerTCP, code: int, body: String):
	var status_text := "OK" if code == 200 else ("Bad Request" if code == 400 else "Not Found")
	var resp := "HTTP/1.1 %d %s\r\n" % [code, status_text]
	resp += "Content-Type: application/json\r\n"
	resp += "Content-Length: %d\r\n" % body.to_utf8_buffer().size()
	resp += "Access-Control-Allow-Origin: *\r\n"
	resp += "Connection: close\r\n"
	resp += "\r\n"
	resp += body
	stream.put_data(resp.to_utf8_buffer())


func _parse_json(text: String) -> Variant:
	if text.is_empty():
		return {}
	var json := JSON.new()
	var err := json.parse(text)
	if err != OK:
		return null
	return json.get_data()



# ---- 回调 ----

func _on_message(sender: String, text: String):
	if _pet and _pet.has_method("mark_interaction"):
		_pet.mark_interaction()
	# 关键词匹配已解耦，只说话不做情绪/动作推断
	_speak(text)


func _on_action(name: String):
	if _pet and _pet.has_method("do_action"):
		_pet.do_action(name)
	if _pet and _pet.has_method("mark_interaction"):
		_pet.mark_interaction()


func _on_emotion(data: Dictionary):
	if _pet and _pet.has_method("set_emotion"):
		var emo_name = data.get("emotion", "neutral")
		_pet.set_emotion(emo_name, data.get("intensity", 1.0), "external")
		_last_pushed_emotion = str(emo_name)
		_pet.mark_interaction()


func _on_progress(text: String):
	# Update bubble with current sentence (called by TTS bridge)
	if _pet and _pet.has_method("show_message"):
		_pet.show_message("Ove", text)


# ---- TTS + 韵律 ----

# 情绪自动台词已全部取消（解耦），只保留视觉表现


func _get_prosody_for(emotion_name: String) -> Dictionary:
	"""从 persona.json 读取情绪的韵律参数"""
	if _pet and _pet.has_method("get_persona"):
		var persona = _pet.get_persona() as Dictionary
		var vp = persona.get("voiceProfile", {}) as Dictionary
		var e2p = vp.get("emotionToProsody", {}) as Dictionary
		if e2p.has(emotion_name):
			return e2p[emotion_name]
	return {"rate": "+0%"}


func speak_emotion_phrase(_emotion_name: String):
	# 已解耦，不自动说话
	pass


func _speak(text: String, prosody_override: Dictionary = {}):
	print("[TTS] Speaking: ", text)
	var http := HTTPRequest.new()
	add_child(http)
	http.request_completed.connect(func(result, code, _h, _b):
		if code != 200:
			printerr("[TTS] HTTP ", code, " result=", result)
		else:
			print("[TTS] OK")
		http.queue_free())
	
	var prosody := prosody_override if not prosody_override.is_empty() else _get_prosody_for("neutral")
	# 附加当前宠物情绪，供本地 TTS 桥使用
	if _pet and _pet.has_method("get_current_emotion"):
		prosody["emotion"] = _pet.get_current_emotion()
	var body := JSON.stringify({"text": text, "prosody": prosody})
	var err := http.request_raw("http://127.0.0.1:18777/tts", [], HTTPClient.METHOD_POST, body.to_utf8_buffer())
	if err != OK:
		printerr("[TTS] Request failed: ", err)


func _on_tweak(data: Dictionary):
	if _pet and _pet.has_method("tweak"):
		_pet.tweak(data)
	if _pet and _pet.has_method("mark_interaction"):
		_pet.mark_interaction()


func _on_scene(scene_name: String):
	if _pet and _pet.has_method("play_scene"):
		_pet.play_scene(scene_name)
	if _pet and _pet.has_method("mark_interaction"):
		_pet.mark_interaction()


# ---- TTS 桥自动启动 ----
func _start_tts_bridge():
	"""TTS 桥由外部手动管理，这里只做启动问候"""
	await get_tree().create_timer(2.0).timeout
	_speak_greeting()


func _speak_greeting():
	"""启动问候"""
	var dt := Time.get_datetime_dict_from_system()
	var greeting := "嗯……"
	if dt.hour < 6:
		greeting = "这么晚了，还不歇着……"
	elif dt.hour < 12:
		greeting = "晨光正好。"
	elif dt.hour < 18:
		greeting = "日长人倦……"
	else:
		greeting = "天又黑了。"
	_speak(greeting, _get_prosody_for("neutral"))


# ---- 自然语言 → 动作 ----
const ACTION_TRIGGERS := {
	"伸手": "point_right", "右臂": "point_right",
	"左手": "point_left", "左臂": "point_left",
	"双手前伸": "both_forward", "前伸": "both_forward",
	"双手后摆": "both_back", "后摆": "both_back",
	"展开": "spread", "张开": "spread",
	"右摆": "right_side", "右手": "point_right",
	"左摆": "left_side",
	"点头": "nod", "嗯": "nod",
	"抬头": "lookup",
	"摇头": "shake_head",
	"歪头": "tilt_head",
	"跳": "bounce", "弹跳": "bounce", "蹦": "bounce",
}


func _infer_action(text: String):
	for kw: String in ACTION_TRIGGERS:
		if kw in text:
			var action: String = ACTION_TRIGGERS[kw]
			if _pet and _pet.has_method("do_action"):
				_pet.do_action(action)
			return

const SCENE_TRIGGERS := {
	"葬花": "葬花", "落花": "葬花",
	"戏文": "听戏入神", "听戏": "听戏入神",
	"拌嘴": "拌嘴扭头", "扭头": "拌嘴扭头", "宝玉": "拌嘴扭头",
	"怔住": "怔住（惊喜）", "什么？": "怔住（惊喜）", "什么！": "怔住（惊喜）",
	"冷笑": "冷笑", "不以为然": "冷笑",
	"摇头晃脑": "摇头晃脑",
}

func _infer_scene(text: String):
	# 场景正在播放时不重复触发
	if _pet and _pet.has_method("is_scene_busy") and _pet.is_scene_busy():
		return
	for kw: String in SCENE_TRIGGERS:
		if kw in text:
			if _pet and _pet.has_method("play_scene"):
				_pet.play_scene(SCENE_TRIGGERS[kw])
			return


const EMOTION_TRIGGERS := {
	"开心": "happy", "高兴": "happy", "哈哈": "happy", "好棒": "happy",
	"难过": "sad", "伤心": "sad", "唉": "sad", "哭": "sad",
	"花": "melancholy", "落花": "melancholy", "秋天": "melancholy",
	"生气": "angry", "烦": "annoyed", "讨厌": "annoyed",
	"惊讶": "surprised", "哇": "surprised",
	"好奇": "curious", "咦": "curious",
	"诗": "proud", "寂寞": "lonely", "孤独": "lonely",
	"谢谢": "grateful", "感谢": "grateful",
	"急": "anxious", "算了": "resigned", "随便": "resigned",
}

func _infer_emotion(text: String):
	for kw: String in EMOTION_TRIGGERS:
		if kw in text:
			var emo: String = EMOTION_TRIGGERS[kw]
			# 防反馈：如果外源刚推过同样的情绪，跳过
			if emo == _last_pushed_emotion:
				return
			if _pet and _pet.has_method("set_emotion"):
				_pet.set_emotion(emo, 0.6, "scene")
			return


# ═══════════════════════════════════════════════════════════════
# WebSocket 客户端 — 魂身合一：连接 ove_bridge
# ═══════════════════════════════════════════════════════════════

func _ws_connect():
	_ws = WebSocketPeer.new()
	var err: Error = _ws.connect_to_url(_ws_url)
	if err != OK:
		printerr("[WS] Failed to connect: ", err, " -> ", _ws_url)
		_ws = null
	else:
		print("[WS] Connecting to ", _ws_url, "...")


func _ws_poll(delta: float):
	if _ws == null:
		# 重连
		_ws_reconnect_timer += delta
		if _ws_reconnect_timer > WS_RECONNECT_INTERVAL:
			_ws_reconnect_timer = 0.0
			_ws_connect()
		return
	
	_ws.poll()
	
	var state: int = _ws.get_ready_state()
	
	match state:
		WebSocketPeer.STATE_OPEN:
			if not _ws_connected:
				_ws_connected = true
				_ws_reconnect_timer = 0.0
				print("[WS] Connected to ove_bridge — 魂身合一")
			# 收发消息
			_ws_receive()
			_ws_send_state(delta)
		
		WebSocketPeer.STATE_CLOSED:
			if _ws_connected:
				print("[WS] Disconnected, will reconnect...")
				_ws_connected = false
			_ws = null
		
		WebSocketPeer.STATE_CLOSING:
			pass  # 等待关闭完成


func _ws_receive():
	while _ws.get_available_packet_count() > 0:
		var pkt: PackedByteArray = _ws.get_packet()
		var text := pkt.get_string_from_utf8()
		_ws_handle_message(text)


func _ws_handle_message(raw: String):
	var json := JSON.new()
	var err := json.parse(raw)
	if err != OK:
		printerr("[WS] Invalid JSON: ", raw.left(200))
		return
	
	var data = json.get_data()
	if not data is Dictionary:
		return
	
	var msg_type: String = data.get("type", "")
	
	match msg_type:
		"composite":
			# 魂身合一核心：一次指令 = 说话 + 情绪 + 动作
			call_deferred("_ws_on_composite", data)
		"speak":
			call_deferred("_on_message", data.get("sender", "Ove"), data.get("text", ""))
		"emotion":
			call_deferred("_on_emotion", data)
		"action":
			call_deferred("_on_action", data.get("action", ""))
		"ping":
			# 回复 pong
			_ws_send({"type": "pong"})
		"raw":
			# 透传任意 Godot HTTP 端点
			call_deferred("_ws_on_raw", data)
		_:
			print("[WS] Unknown message type: ", msg_type)


func _ws_on_composite(data: Dictionary):
	"""复合指令：说话 + 情绪 + 动作 一次完成"""
	var text: String = data.get("text", "")
	var emotion: String = data.get("emotion", "")
	var action: String = data.get("action", "")
	var sender: String = data.get("sender", "Ove")
	
	# 1. 设情绪（先设，语音用对应韵律）
	if not emotion.is_empty():
		_on_emotion({"emotion": emotion, "intensity": data.get("intensity", 0.7)})
	
	# 2. 做动作
	if not action.is_empty():
		_on_action(action)
	
	# 3. 说话（气泡 + TTS）
	if not text.is_empty():
		_on_message(sender, text)


func _ws_on_raw(data: Dictionary):
	"""透传原始指令到现有 HTTP 处理器"""
	var path: String = data.get("path", "")
	var payload: Dictionary = data.get("payload", {})
	
	match path:
		"/message":
			_on_message(payload.get("sender", "Ove"), payload.get("text", ""))
		"/emotion":
			_on_emotion(payload)
		"/action":
			_on_action(payload.get("action", ""))
		"/scene":
			_on_scene(payload.get("scene", ""))
		"/tweak":
			_on_tweak(payload)


func _ws_send_state(delta: float):
	"""定期上报机器人状态到 bridge"""
	_ws_state_timer += delta
	if _ws_state_timer < WS_STATE_INTERVAL:
		return
	_ws_state_timer = 0.0
	
	var emo := "neutral"
	var intensity := 0.3
	var idle_sec := 0.0
	
	if _pet:
		if _pet.has_method("get_current_emotion"):
			emo = _pet.get_current_emotion()
		intensity = _pet.get("emotion_intensity") if _pet.get("emotion_intensity") != null else 0.3
		var now := Time.get_ticks_msec() / 1000.0
		var raw_last = _pet.get("_last_interaction_time")
		var last: float = raw_last if raw_last != null else now
		idle_sec = now - last
	
	var state := {
		"type": "state",
		"current_emotion": emo,
		"intensity": intensity,
		"idle_seconds": idle_sec,
		"last_interaction": Time.get_ticks_msec() / 1000.0 - idle_sec,
		"uptime_seconds": Time.get_ticks_msec() / 1000.0 - _ws_startup_ts,
		"connected": true,
	}
	_ws_send(state)


func _ws_send(data: Dictionary):
	if _ws == null or _ws.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return
	var text := JSON.stringify(data)
	_ws.put_packet(text.to_utf8_buffer())
