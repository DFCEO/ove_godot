extends Node
## HTTP API 服务器 — 接收 OpenClaw 消息

@export var port := 18776
@export var bind_address := "127.0.0.1"

var _server: TCPServer = null
var _pet: Node = null
var _running := false
var _last_pushed_emotion := ""   # 防反馈循环：记录上次外源推送的情绪


func _ready():
	# 查找 pet 节点
	_pet = get_parent()
	_start_server()


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


func _process(_delta: float):
	if not _running:
		return
	while _server.is_connection_available():
		var conn := _server.take_connection()
		if conn == null:
			continue
		_handle_connection(conn)


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
	_infer_emotion(text)
	_infer_scene(text)
	_infer_action(text)
	_speak(text)  # 消息本身正常语速说


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

# 情绪 → 随机台词（林黛玉风格）
const EMOTION_PHRASES := {
	"neutral": ["嗯。"],
	"melancholy": ["花谢花飞花满天，红消香断有谁怜……", "一年三百六十日，风刀霜剑严相逼。", "花开易见落难寻，阶前闷杀葬花人。"],
	"sad": ["泪眼问花花不语，乱红飞过秋千去……", "侬今葬花人笑痴，他年葬侬知是谁？"],
	"hurt": ["你又来说这些话……", "我说了也无用……"],
	"annoyed": ["哼，你又来了。", "谁理你。"],
	"angry": ["你走！我不想看见你。"],
	"happy": ["嗯……", "今日倒是难得的好日头。"],
	"proud": ["一从陶令平章后，千古高风说到今。"],
	"curious": ["咦？", "这是什么？"],
	"surprised": ["啊……", "你说什么？"],
	"anxious": ["急死了……", "怎么还不来……"],
	"lonely": ["热闹是他们的，我什么也没有……"],
	"grateful": ["多谢你……", "难为你想得到。"],
	"resigned": ["罢了……一切随它去罢。"],
	"defiant": ["质本洁来还洁去，强于污淖陷渠沟。"]
}


func _get_prosody_for(emotion_name: String) -> Dictionary:
	"""从 persona.json 读取情绪的韵律参数"""
	if _pet and _pet.has_method("get_persona"):
		var persona = _pet.get_persona() as Dictionary
		var vp = persona.get("voiceProfile", {}) as Dictionary
		var e2p = vp.get("emotionToProsody", {}) as Dictionary
		if e2p.has(emotion_name):
			return e2p[emotion_name]
	return {"rate": "+0%"}


func speak_emotion_phrase(emotion_name: String):
	"""情绪切换时说一句对应台词（由 pet.gd 调用）"""
	var phrases = EMOTION_PHRASES.get(emotion_name, ["嗯。"]) as Array
	var text: String = phrases[randi() % phrases.size()]
	var prosody := _get_prosody_for(emotion_name)
	_speak(text, prosody)


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
	"举起右手": "raise_right", "举右手": "raise_right",
	"举起左手": "raise_left", "举左手": "raise_left",
	"挥手": "wave_right", "招手": "wave_right",
	"举手": "both_up", "双手": "both_up",
	"点头": "nod", "嗯": "nod",
	"摇头": "shake_head",
	"歪头": "tilt_head",
	"扭腰": "twist",
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
