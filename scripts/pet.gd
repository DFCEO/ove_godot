extends Node3D
## Ove 桌面宠物 — 3D 模型加载 + 骨骼动画 + 情绪表达

@onready var camera: Camera3D = $Camera3D
@onready var model_root: Node3D = null
@onready var anim_player: AnimationPlayer = null
@onready var emotion_light: OmniLight3D = $OmniLight3D

# 角色数据
var persona_data: Dictionary = {}

# 骨骼引用
var head_bone: int = -1
var arm_l_bone: int = -1
var arm_r_bone: int = -1
var body_bone: int = -1

# 骨骼 rest pose 基线（手臂动画以此为起点）
var _forced_targets := {}  # force 值存储（不被任何系统覆盖）
var _arm_l_rest := Vector3.ZERO   # rest 欧拉角
var _arm_r_rest := Vector3.ZERO
var _base_rot_x := 0.0
var _base_rot_y := 0.0
var _base_rot_z := 0.0

# 情绪参数（当前 → 目标 lerp）
var emotion_params := {}
var target_params := {}
var current_emotion := "neutral"
var emotion_intensity := 0.3

# ---- 情绪衰减 ----
var _emotion_source := ""        # "external" | "scene" | "_decay"
var _emotion_age := 0.0          # 当前情绪持续秒数
var _emotion_original_intensity := 0.3
const DECAY_HOLD_EXTERNAL := 12.0  # 外部推送保持时间
const DECAY_HOLD_SCENE := 8.0      # 场景触发保持时间
const DECAY_DURATION := 20.0       # 衰减过渡时长
const NEUTRAL_INTENSITY := 0.3     # 中性基线强度

# ---- 场景触发 ----
var _last_interaction_time := 0.0  # 上次交互时间（OS.get_ticks_msec）
var _last_scene_emotion := ""      # 上次场景触发的情绪名，防重复
const IDLE_TRIGGER_SEC := 300.0     # 闲置 5 分钟触发 lonely
const NIGHT_HOUR := 21              # 21:00 后触发 melancholy

# ---- 眨眼 ----
var _blink_timer := 0.0
var _blink_visible := true

# ---- 闲置微动
var idle_timer := 0.0
var _idle_busy := false
# 场景动作序列
var _sequence_busy := false
# 窗口拖拽
var _dragging := false
var _drag_start_pos := Vector2.ZERO
# 气泡 UI
var _bubble_layer: CanvasLayer = null
var _bubble_panel: Panel = null
var _bubble_label: Label = null
var _bubble_timer: float = 0.0
# 眼睛
var _eye_l: Node3D = null
var _eye_r: Node3D = null
var _lens_l: MeshInstance3D = null
var _lens_r: MeshInstance3D = null

func _input(event: InputEvent):
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_dragging = true
				_drag_start_pos = event.global_position - Vector2(get_window().position)
			else:
				_dragging = false
	if event is InputEventMouseMotion and _dragging:
		get_window().position = event.global_position - _drag_start_pos

func _ready():
	# 视图透明
	get_viewport().transparent_bg = true
	
	# 允许系统息屏（不抢占焦点）
	DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_NO_FOCUS, true)
	Engine.max_fps = 30
	
	# 窗口定位右下角
	var screen_size := DisplayServer.screen_get_size()
	var win_size := get_window().size
	get_window().position = Vector2i(screen_size.x - win_size.x - 20, screen_size.y - win_size.y - 80)
	
	# 相机对准原点
	if camera:
		camera.look_at(Vector3(0, 0, 0), Vector3.UP)
	
	# 加载 3D 模型
	var glb_path := "res://models/eve_ah.glb"
	if not FileAccess.file_exists(glb_path):
		printerr("Model not found: ", glb_path)
		return
	
	var glb := load(glb_path) as PackedScene
	if glb == null:
		printerr("Failed to load GLB")
		return
	
	model_root = glb.instantiate()
	print("Model loaded: ", model_root.get_class(), " children=", model_root.get_child_count())
	
	# 强制所有 mesh 材质可见
	_force_visible(model_root)
	
	model_root.scale = Vector3(35, 35, 35)
	model_root.position = Vector3(0, 1.0, 0)
	_base_rot_x = PI  # 补偿骨骼 body_2 的 180° Z旋转
	_base_rot_y = PI  # 转向面对相机
	model_root.rotation.y = _base_rot_y
	add_child(model_root)
	
	# 扫描骨骼
	_scan_skeleton(model_root)
	
	# 记录手臂 rest pose 基线
	var sk := _find_skeleton(model_root)
	if sk and arm_l_bone >= 0:
		_arm_l_rest = sk.get_bone_rest(arm_l_bone).basis.get_euler()
	if sk and arm_r_bone >= 0:
		_arm_r_rest = sk.get_bone_rest(arm_r_bone).basis.get_euler()
	
	# 查找/设置眼睛
	_add_eyes()
	
	# 加载角色配置
	_load_persona()
	
	# 创建气泡消息 UI
	_create_bubble()
	
	_last_interaction_time = Time.get_ticks_msec() / 1000.0
	
	# 设初始情绪
	set_emotion("neutral", NEUTRAL_INTENSITY, "_decay")
	
	print("Ove ready. Eyes: L=", _eye_l, " R=", _eye_r)


func _add_eyes():
	"""用 BoneAttachment3D 挂到 eye_l_2/eye_r_2 骨骼上"""
	var sk: Skeleton3D = _find_skeleton(model_root)
	if sk == null:
		return
	
	# 找到眼睛骨骼（bone 4=eye_l_2, bone 5=eye_r_2）
	var eye_bone_l := -1
	var eye_bone_r := -1
	for i in range(sk.get_bone_count()):
		var bn := sk.get_bone_name(i).to_lower()
		if bn.contains("eye") and bn.contains("l"):
			eye_bone_l = i
		elif bn.contains("eye") and bn.contains("r"):
			eye_bone_r = i
	
	if eye_bone_l < 0 or eye_bone_r < 0:
		print("Eye bones not found!")
		return
	
	print("Eye bones: L=", sk.get_bone_name(eye_bone_l), "(", eye_bone_l, ") R=", sk.get_bone_name(eye_bone_r), "(", eye_bone_r, ")")
	
	# 隐藏模型自带的 eye mesh
	var all_m: Array[MeshInstance3D] = []
	_find_meshes(model_root, all_m)
	for mi in all_m:
		if mi.name.to_lower().contains("eye"):
			mi.visible = false
	
	# 镜片材质
	var lens_mat := StandardMaterial3D.new()
	lens_mat.albedo_color = Color(0.3, 0.6, 1.0)
	lens_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	lens_mat.emission = Color(0.3, 0.6, 1.0)
	lens_mat.emission_energy_multiplier = 2.0
	
	# 为每个眼睛骨骼创建 BoneAttachment3D + 蓝色球
	var offsets := {eye_bone_l: Vector3(-0.0005, 0.0025, 0.001), eye_bone_r: Vector3(-0.0005, 0.0005, 0.001)}
	for bone_idx in [eye_bone_l, eye_bone_r]:
		var bone_attach := BoneAttachment3D.new()
		bone_attach.name = "EyeAttach_" + sk.get_bone_name(bone_idx)
		bone_attach.bone_name = sk.get_bone_name(bone_idx)
		sk.add_child(bone_attach)
		
		var sphere := MeshInstance3D.new()
		sphere.name = "EyeLens_" + sk.get_bone_name(bone_idx)
		var sm := SphereMesh.new()
		sm.radius = 0.0015
		sm.height = 0.0030  # height > 2*radius → 纵向椭圆
		sphere.mesh = sm
		sphere.set_surface_override_material(0, lens_mat)
		sphere.position = offsets[bone_idx]  # 微调偏移
		bone_attach.add_child(sphere)
		
		if bone_idx == eye_bone_l:
			_eye_l = sphere; _lens_l = sphere
		else:
			_eye_r = sphere; _lens_r = sphere
	
	print("Eyes on bones: L=", _eye_l, " R=", _eye_r)
	
	print("Eyes: L=", _eye_l, " pos=", str(_eye_l.position) if _eye_l else "?", " R=", _eye_r, " pos=", str(_eye_r.position) if _eye_r else "?")


func _find_meshes(node: Node, out_list: Array):
	if node is MeshInstance3D:
		out_list.append(node)
	for child in node.get_children():
		_find_meshes(child, out_list)


func _force_visible(node: Node):
	"""只给缺失材质的 mesh 补默认材质，保留已有纹理"""
	if node is MeshInstance3D:
		var mi: MeshInstance3D = node
		var mesh = mi.mesh
		if mesh:
			var sc := mesh.get_surface_count()
			for si in range(sc):
				var cur := mi.get_surface_override_material(si)
				if cur == null and mesh:
					cur = mesh.surface_get_material(si)
				# 只补缺失的，不覆盖已有的
				if cur == null:
					var m := StandardMaterial3D.new()
					m.albedo_color = Color(0.65, 0.7, 0.8)
					m.roughness = 0.7
					mi.set_surface_override_material(si, m)
	for child in node.get_children():
		_force_visible(child)

func _scan_skeleton(node: Node):
	"""递归扫描 Skeleton3D，打印所有骨骼名 + 匹配"""
	if node is Skeleton3D:
		var sk: Skeleton3D = node
		var logf := FileAccess.open("user://bones.log", FileAccess.WRITE)
		logf.store_line("Skeleton: " + str(sk.get_bone_count()) + " bones")
		for i in range(sk.get_bone_count()):
			var orig := sk.get_bone_name(i)
			var lower := orig.to_lower()
			# 获取骨骼全局位姿
			var rest := sk.get_bone_rest(i)
			var pose := sk.get_bone_pose(i)
			logf.store_line("  [" + str(i) + "] '" + orig + "'")
			logf.store_line("    rest: " + str(rest.origin) + " rot=" + str(rest.basis.get_euler()))
			logf.store_line("    pose: " + str(pose.origin) + " rot=" + str(pose.basis.get_euler()))
			# Godot 导入器给骨骼加后缀：body_2, arm_r_2, head_ctrl, head_2
			if lower == "head" or lower.contains("head") and not lower.contains("ctrl"):
				head_bone = i
			if lower == "body" or lower.contains("body"):
				body_bone = i
			if lower.contains("arm_l") and not lower.contains("ctrl"):
				arm_l_bone = i
			if lower.contains("arm_r") and not lower.contains("ctrl"):
				arm_r_bone = i
		logf.store_line("Matched: head=" + str(head_bone) + " arm_l=" + str(arm_l_bone) + " arm_r=" + str(arm_r_bone) + " body=" + str(body_bone))
		logf.close()
		print("Bone log written to user://bones.log")
		return
	for child in node.get_children():
		_scan_skeleton(child)


func _load_persona():
	var f := FileAccess.open("res://persona.json", FileAccess.READ)
	if f == null:
		return
	var data = JSON.parse_string(f.get_as_text())
	if data == null or not data is Dictionary or not data.has("emotions"):
		return
	
	persona_data = data
	_init_params({})
	print("Persona loaded: ", persona_data.get("name", "unknown"))


func _init_params(_preset: Dictionary):
	emotion_params = {
		head_tilt=0.0, head_nod=0.0, head_turn=0.0,
		arm_lift_l=0.0, arm_lift_r=0.0, arm_swing=0.0,
		body_lean_x=0.0, body_lean_z=0.0, body_bob=0.0,
		body_twist=0.0, body_roll=0.0, body_pitch=0.0,
		bounce=0.0,
	}
	target_params = emotion_params.duplicate()


func _camel_to_snake(key: String) -> String:
	var result := ""
	for ch in key:
		if ch >= 'A' and ch <= 'Z':
			if result.length() > 0:
				result += "_"
			result += ch.to_lower()
		else:
			result += ch
	return result

func _build_skip_rect() -> PackedVector2Array:
	# 窗口大部分区域可穿透点击（仅模型区域不可）
	return PackedVector2Array()


func _process(delta: float):
	if model_root == null:
		return
	
	# ---- 情绪衰减 ----
	_decay_emotion(delta)
	
	# ---- 场景触发 ----
	_check_scene_triggers()
	
	# ---- 镜片表情 ----
	_lens_update(delta)
	
	# 参数 lerp
	var speed := 6.0
	for k: String in target_params:
		emotion_params[k] = lerpf(emotion_params[k], target_params[k], speed * delta)
	
	# 驱动骨骼
	var sk: Skeleton3D = _find_skeleton(model_root)
	if sk == null:
		return
	
	_apply_bone(sk, head_bone, emotion_params.head_turn, emotion_params.head_nod, emotion_params.head_tilt)
	# 手臂动画
	#   X 轴 = 前后摆动，范围 ±180°
	#     左臂: +X=向前, -X=向后
	#     右臂: +X=向后, -X=向前
	#     arm_swing 同号即可产生走路交替（左前右后）
	#   Z 轴 = 侧向摆动，范围 ±135°
	#     右臂: +Z=向右摆, -Z=向左摆（过身体）
	#     左臂: -Z=向左摆, +Z=向右摆（过身体）
	#   Y 轴 = 扭转（不可见）
	const ARM_X_MAX := PI      # 180°
	const ARM_Z_MAX := 2.356   # 135°
	# force 值优先级最高，直接覆盖 emotion_params
	for fk: String in _forced_targets:
		emotion_params[fk] = _forced_targets[fk]
	emotion_params["arm_l_rx"] = clampf(emotion_params.get("arm_l_rx", 0.0), -ARM_X_MAX, ARM_X_MAX)
	emotion_params["arm_r_rx"] = clampf(emotion_params.get("arm_r_rx", 0.0), -ARM_X_MAX, ARM_X_MAX)
	emotion_params["arm_l_rz"] = clampf(emotion_params.get("arm_l_rz", 0.0), -ARM_Z_MAX, ARM_Z_MAX)
	emotion_params["arm_r_rz"] = clampf(emotion_params.get("arm_r_rz", 0.0), -ARM_Z_MAX, ARM_Z_MAX)
	_apply_bone(sk, arm_l_bone,
		emotion_params.get("arm_l_ry", 0.0),
		emotion_params.get("arm_l_rx", emotion_params.arm_swing),
		-_arm_l_rest.z + emotion_params.arm_l_rz)
	_apply_bone(sk, arm_r_bone,
		emotion_params.get("arm_r_ry", 0.0),
		emotion_params.get("arm_r_rx", emotion_params.arm_swing),
		-_arm_r_rest.z + emotion_params.arm_r_rz)
	_apply_bone(sk, body_bone, emotion_params.body_twist, emotion_params.body_pitch, emotion_params.body_roll)
	
	# 眼睛由 BoneAttachment3D 自动跟随骨骼
	
	# 整体倾斜（加基础旋转偏移）
	model_root.rotation.x = _base_rot_x + emotion_params.body_lean_x + sin(Time.get_ticks_msec() * 0.0012) * max(emotion_params.body_bob, 0.003)
	model_root.rotation.z = _base_rot_z + emotion_params.body_lean_z
	
# 弹跳
	if emotion_params.bounce > 0.01:
		model_root.position.y = -0.8 + emotion_params.bounce * 0.15
	
	# 闲置微动
	if not _idle_busy:
		idle_timer += delta
		if idle_timer > 2.0 + randf() * 3.0:
			idle_timer = 0.0
			_do_idle_micro()
	
	# 气泡自动隐藏
	if _bubble_panel and _bubble_panel.visible and _bubble_timer > 0:
		_bubble_timer -= delta
		if _bubble_timer <= 0:
			_bubble_panel.visible = false


func _find_skeleton(node: Node) -> Skeleton3D:
	if node is Skeleton3D:
		return node
	for child in node.get_children():
		var found := _find_skeleton(child)
		if found:
			return found
	return null


func _apply_bone(sk: Skeleton3D, bone: int, ry: float, rx: float, rz: float):
	if bone < 0:
		return
	var pose := sk.get_bone_pose_rotation(bone)
	var target := Quaternion.from_euler(Vector3(rx, ry, rz))
	sk.set_bone_pose_rotation(bone, pose.slerp(target, 0.15))


func _do_idle_micro():
	if _idle_busy:
		return
	_idle_busy = true
	var r := randi() % 6
	match r:
		0: target_params.head_turn = 0.12
		1: target_params.head_tilt = 0.08
		2: target_params.head_nod = 0.06
		3: target_params.body_twist = 0.06
		4: target_params.body_roll = 0.03
		5: target_params.arm_swing = 0.1
	await get_tree().create_timer(1.2).timeout
	_restore_targets()
	_idle_busy = false


func _restore_targets():
	# 只重置标准参数，不碰 raw 骨骼参数
	for k: String in target_params:
		if k.begins_with("arm_l_r") or k.begins_with("arm_r_r"):
			continue
		target_params[k] = 0.0


# ---- 情绪衰减 ----
func _decay_emotion(delta: float):
	if current_emotion == "neutral" or _emotion_source == "_decay":
		# neutral 不衰减；已经是衰减中也不重复触发
		return
	
	_emotion_age += delta
	var hold_time := DECAY_HOLD_EXTERNAL if _emotion_source == "external" else DECAY_HOLD_SCENE
	
	if _emotion_age > hold_time:
		var t := clampf((_emotion_age - hold_time) / DECAY_DURATION, 0.0, 1.0)
		var decayed := lerpf(_emotion_original_intensity, NEUTRAL_INTENSITY, t)
		emotion_intensity = decayed
		
		# 同步缩放动画参数
		var emotions: Dictionary = persona_data.get("emotions", {})
		if emotions.has(current_emotion):
			var preset: Dictionary = emotions[current_emotion].get("params", {})
			for persona_key: String in preset:
				var godot_key := _camel_to_snake(persona_key)
				if godot_key in target_params:
					target_params[godot_key] = preset[persona_key] * emotion_intensity
		
		if t >= 1.0:
			# 衰减完毕，回归 neutral
			_emotion_source = "_decay"
			set_emotion("neutral", NEUTRAL_INTENSITY, "_decay")


# ---- 场景触发规则 ----
func _check_scene_triggers():
	# 外源推送的情绪正在保持中，不干扰
	if _emotion_source == "external" and _emotion_age < DECAY_HOLD_EXTERNAL:
		return
	
	var now_sec := Time.get_ticks_msec() / 1000.0
	var idle_sec := now_sec - _last_interaction_time
	var dt := Time.get_datetime_dict_from_system()
	
	# 闲置 > 5 分钟 → lonely
	if idle_sec > IDLE_TRIGGER_SEC and _last_scene_emotion != "lonely" and current_emotion != "lonely":
		_last_scene_emotion = "lonely"
		set_emotion("lonely", 0.5, "scene")
		return
	
	# 21:00 后 → melancholy
	if dt.hour >= NIGHT_HOUR and _last_scene_emotion != "melancholy_night" and current_emotion != "melancholy":
		_last_scene_emotion = "melancholy_night"
		set_emotion("melancholy", 0.6, "scene")
		return
	
	# 白天且不在 lonely/melancholy 中，清除场景标记
	if dt.hour < NIGHT_HOUR and idle_sec < IDLE_TRIGGER_SEC:
		_last_scene_emotion = ""


# ---- 镜片表情 ----
func _lens_update(_delta: float):
	if _lens_l == null or _lens_r == null:
		return
	
	# 从 persona 读取当前情绪眼部参数
	var brightness := 1.0
	var eye_size := 1.0
	if persona_data.has("emotions") and persona_data.emotions.has(current_emotion):
		var params: Dictionary = persona_data.emotions[current_emotion].get("params", {})
		brightness = params.get("eyeBright", 1.0)
		eye_size = params.get("eyeSize", 1.0)
	
	# ---- 眨眼 ----
	_blink_timer += _delta * 1000.0
	var blink_freq := 3000.0  # 3秒眨一次
	var blink_mul := 1.0
	if _blink_visible and _blink_timer > blink_freq:
		_blink_visible = false
		_blink_timer = 0.0
	elif not _blink_visible and _blink_timer > 150.0:
		_blink_visible = true
		_blink_timer = 0.0
	if not _blink_visible:
		blink_mul = 0.1
	
	# ---- 应用亮度 ----
	var energy := brightness
	var mat_l := _lens_l.get_surface_override_material(0)
	var mat_r := _lens_r.get_surface_override_material(0)
	if mat_l: mat_l.emission_energy_multiplier = energy
	if mat_r: mat_r.emission_energy_multiplier = energy
	
	# ---- 应用大小 ----
	const BASE_RADIUS := 0.0015
	const BASE_HEIGHT := 0.0030
	var r := BASE_RADIUS * eye_size
	var h := BASE_HEIGHT * eye_size
	var sl := _lens_l.mesh as SphereMesh
	var sr := _lens_r.mesh as SphereMesh
	if sl:
		sl.radius = r
		sl.height = h * blink_mul  # 眨眼缩高度
	if sr:
		sr.radius = r
		sr.height = h * blink_mul


# ---- 公开 API ----
func mark_interaction():
	_last_interaction_time = Time.get_ticks_msec() / 1000.0


func get_persona() -> Dictionary:
	return persona_data


func get_current_emotion() -> String:
	return current_emotion

func get_debug_info() -> Dictionary:
	"""返回当前骨骼参数状态（诊断用）"""
	var sk := _find_skeleton(model_root) if model_root else null
	var eyes_info := {}
	if _eye_l:
		eyes_info["left"] = {"name": _eye_l.name, "pos": var_to_str(_eye_l.position), "scale": var_to_str(_eye_l.scale), "global_pos": var_to_str(_eye_l.global_position)}
	if _eye_r:
		eyes_info["right"] = {"name": _eye_r.name, "pos": var_to_str(_eye_r.position), "scale": var_to_str(_eye_r.scale), "global_pos": var_to_str(_eye_r.global_position)}
	return {
		"model_root": model_root != null,
		"skeleton": sk != null,
		"persona_loaded": persona_data.has("emotions"),
		"emotion_params": emotion_params.duplicate(),
		"target_params": target_params.duplicate(),
		"current_emotion": current_emotion,
		"arm_l_rest_z": _arm_l_rest.z,
		"arm_r_rest_z": _arm_r_rest.z,
		"bones": {"head": head_bone, "arm_l": arm_l_bone, "arm_r": arm_r_bone, "body": body_bone},
		"eyes": eyes_info,
	}


func get_inspect() -> Dictionary:
	"""完整模型诊断：节点树、mesh、骨骼、包围盒"""
	if model_root == null:
		return {"error": "no model"}
	
	var result := {}
	
	# 模型基础信息
	result["model"] = {
		"class": model_root.get_class(),
		"scale": var_to_str(model_root.scale),
		"position": var_to_str(model_root.position),
		"rotation": var_to_str(model_root.rotation),
		"global_pos": var_to_str(model_root.global_position),
		"child_count": model_root.get_child_count(),
	}
	
	# 节点树
	var tree := []
	_dump_tree(model_root, "", tree)
	result["node_tree"] = tree
	
	# 所有 mesh
	var all_m: Array[MeshInstance3D] = []
	_find_meshes(model_root, all_m)
	var meshes := []
	for mi in all_m:
		var aabb := mi.mesh.get_aabb() if mi.mesh else AABB()
		meshes.append({
			"name": mi.name,
			"visible": mi.visible,
			"local_pos": var_to_str(mi.position),
			"global_pos": var_to_str(mi.global_position),
			"scale": var_to_str(mi.scale),
			"aabb_size": var_to_str(aabb.size),
			"parent": str(mi.get_parent().name) if mi.get_parent() else "none",
		})
	result["meshes"] = meshes
	
	# 包围盒（整个模型的世界空间）
	var world_aabb := AABB()
	for mi in all_m:
		if mi.visible and mi.mesh:
			var mesh_aabb := mi.mesh.get_aabb()
			var t := mi.global_transform
			var corners := [
				t * mesh_aabb.position,
				t * (mesh_aabb.position + Vector3(mesh_aabb.size.x, 0, 0)),
				t * (mesh_aabb.position + Vector3(0, mesh_aabb.size.y, 0)),
				t * (mesh_aabb.position + Vector3(0, 0, mesh_aabb.size.z)),
				t * mesh_aabb.end,
			]
			for c in corners:
				world_aabb = world_aabb.expand(c)
	result["world_aabb"] = {
		"center": var_to_str(world_aabb.get_center()),
		"size": var_to_str(world_aabb.size),
	}
	
	return result


func _dump_tree(node: Node, indent: String, out_list: Array):
	if node == null:
		return
	var info := indent + node.name + " (" + node.get_class() + ")"
	if node is MeshInstance3D:
		info += " [mesh]"
	elif node is Skeleton3D:
		info += " [skeleton " + str(node.get_bone_count()) + " bones]"
	out_list.append(info)
	for child in node.get_children():
		_dump_tree(child, indent + "  ", out_list)


# ---- 场景动作序列 ----
func is_scene_busy() -> bool:
	return _sequence_busy


func play_scene(scene_name: String):
	"""播放 persona.json 中定义的经典瞬间动作卡片"""
	if _sequence_busy:
		return
	
	var cards: Array = persona_data.get("经典瞬间动作卡片", [])
	var card: Dictionary = {}
	for c: Variant in cards:
		if c is Dictionary and c.get("scene", "") == scene_name:
			card = c
			break
	if card.is_empty():
		print("[Scene] Not found: ", scene_name)
		return
	
	_sequence_busy = true
	_idle_busy = true
	
	var seq: Dictionary = card.sequence
	var stages: Array = seq.stages
	var emotion_track: Dictionary = seq.get("emotionTrack", {})
	var prev_emotion := current_emotion
	
	# 设情绪覆盖层
	if emotion_track.has("overlay"):
		var overlay := emotion_track.overlay as String
		if persona_data.has("emotions") and persona_data.emotions.has(overlay):
			set_emotion(overlay, 0.8, "scene")
	
	print("[Scene] Start: ", scene_name, " stages=", stages.size())
	
	# 逐阶段执行
	for stage: Variant in stages:
		if not stage is Dictionary:
			continue
		var params: Dictionary = stage.get("params", {})
		var duration: float = stage.get("duration", 1.0)
		var freeze: bool = stage.get("freeze", false)
		var action_name: String = stage.get("action", "")
		
		print("  [Stage] ", action_name, " (", duration, "s)")
		
		if freeze:
			for k: String in target_params:
				target_params[k] = 0.0
		else:
			for pk: String in params:
				var gk := _camel_to_snake(pk)
				if gk in target_params:
					target_params[gk] = params[pk]
		
		await get_tree().create_timer(duration).timeout
	
	# 结束：恢复
	_restore_targets()
	set_emotion(prev_emotion, emotion_intensity, "scene")
	_sequence_busy = false
	_idle_busy = false
	print("[Scene] End: ", scene_name)


func do_action(action_name: String, auto: bool = false):
	# 动作库（基于确认的轴映射）
	#   X轴: 左+X=前/-X=后  右+X=后/-X=前
	#   Z轴: 左-Z=左/+Z=右  右+Z=右/-Z=左
	const ACTIONS := {
		# --- 单臂前伸（指向）---
		"point_right": {"arm_r_rx": -PI/2},    # 右臂前伸 90°
		"point_left":  {"arm_l_rx":  PI/2},    # 左臂前伸 90°
		# --- 双臂动作 ---
		"both_forward":  {"arm_l_rx":  PI/2, "arm_r_rx": -PI/2},  # 双臂前伸
		"both_back":     {"arm_l_rx": -PI/2, "arm_r_rx":  PI/2},  # 双臂后摆
		"walk_pose":     {"arm_l_rx":  0.5,  "arm_r_rx":  0.5},   # 走路交替
		# --- 侧向动作 ---
		"spread":   {"arm_l_rz": -PI/2, "arm_r_rz":  PI/2},  # 张开双臂
		"hug":      {"arm_l_rz":  PI/2, "arm_r_rz": -PI/2},  # 环抱
		"right_side":  {"arm_r_rz":  PI/2},    # 右臂右摆
		"left_side":   {"arm_l_rz": -PI/2},    # 左臂左摆
		# --- 头部 ---
		"nod":        {"head_nod": 0.25},
		"shake_head": {"head_turn": 0.35},
		"tilt_head":  {"head_tilt": 0.25},
		# --- 身体 ---
		"bounce": {"bounce": 0.4},
	}
	if not ACTIONS.has(action_name):
		return
	
	_idle_busy = true
	var a: Dictionary = ACTIONS[action_name]
	
	# 保存当前原始骨骼值（用于恢复）
	var saved := {}
	for k: String in a:
		if k in emotion_params:
			saved[k] = emotion_params[k]
	
	# 应用动作
	for k: String in a:
		emotion_params[k] = a[k]
	
	var delay := 1.5 if auto else 2.0
	await get_tree().create_timer(delay).timeout
	
	# 恢复
	for k: String in saved:
		emotion_params[k] = saved[k]
	set_emotion(current_emotion, emotion_intensity)
	_idle_busy = false
	print("[Action] ", action_name)


func tweak(data: Dictionary):
	if data.has("model") and model_root:
		var m: Dictionary = data.model
		if m.has("x"): model_root.position.x = m.x
		if m.has("y"): model_root.position.y = m.y
		if m.has("z"): model_root.position.z = m.z
		if m.has("ry"): model_root.rotation.y = m.ry
	if data.has("camera") and camera:
		var cam: Dictionary = data.camera
		if cam.has("x"): camera.position.x = cam.x
		if cam.has("y"): camera.position.y = cam.y
		if cam.has("z"): camera.position.z = cam.z
	if data.has("fov") and camera:
		camera.fov = data.fov
	if data.has("targets"):
		var t: Dictionary = data.targets
		for k: String in t:
			target_params[k] = t[k]
	# 实时调整眼睛偏移（直觉方向：left=X- right=X+ up=Y+ down=Y-）
	if data.has("eye_l"):
		var v: Array = data.eye_l
		if _eye_l: _eye_l.position = Vector3(v[0], v[1], v[2])
	if data.has("eye_r"):
		var v: Array = data.eye_r
		if _eye_r: _eye_r.position = Vector3(v[0], v[1], v[2])
	# 直觉API：{"eye":"left","dir":"up","amt":0.001}
	if data.has("eye") and data.has("dir") and data.has("amt"):
		var target: Node3D = _eye_l if data.eye == "left" else _eye_r
		if target:
			var d: String = data.dir
			var a: float = data.amt
			var p: Vector3 = target.position
			if d == "left": p.x -= a
			elif d == "right": p.x += a
			elif d == "up": p.y += a
			elif d == "down": p.y -= a
			elif d == "forward": p.z += a
			elif d == "back": p.z -= a
			target.position = p
	if data.has("eye_size"):
		_eye_r.scale = Vector3.ONE * data.eye_size
		_eye_l.scale = Vector3.ONE * data.eye_size
	print("[Tweak] ", data)

func force_targets(t: Dictionary):
	# 强制设置骨骼参数（只写 emotion_params，不进 target/lerp）
	const ARM_X_MAX := PI
	const ARM_Z_MAX := 2.356
	_idle_busy = true
	for k: String in t:
		var v = t[k]
		if k.begins_with("arm_l_rx") or k.begins_with("arm_r_rx"):
			v = clampf(v, -ARM_X_MAX, ARM_X_MAX)
		elif k.begins_with("arm_l_rz") or k.begins_with("arm_r_rz"):
			v = clampf(v, -ARM_Z_MAX, ARM_Z_MAX)
		emotion_params[k] = v
	print("[Force] ", t)

func show_message(sender: String, text: String):
	if _bubble_label:
		_bubble_label.text = text
		_bubble_panel.visible = true
		_bubble_timer = 3.0 + len(text) * 0.08
	print("[Message] ", sender, ": ", text)


func _create_bubble():
	_bubble_layer = CanvasLayer.new()
	add_child(_bubble_layer)
	
	_bubble_panel = Panel.new()
	_bubble_panel.position = Vector2(40, 10)
	_bubble_panel.size = Vector2(220, 60)
	_bubble_panel.visible = false
	
	var style := StyleBoxFlat.new()
	style.bg_color = Color(1, 1, 1, 0.92)
	style.border_width_left = 2
	style.border_width_right = 2
	style.border_width_top = 2
	style.border_width_bottom = 2
	style.border_color = Color(0.85, 0.85, 0.85, 1)
	style.corner_radius_top_left = 12
	style.corner_radius_top_right = 12
	style.corner_radius_bottom_left = 12
	style.corner_radius_bottom_right = 12
	_bubble_panel.add_theme_stylebox_override("panel", style)
	
	_bubble_label = Label.new()
	_bubble_label.position = Vector2(12, 8)
	_bubble_label.size = Vector2(196, 44)
	_bubble_label.add_theme_color_override("font_color", Color.BLACK)
	_bubble_label.add_theme_font_size_override("font_size", 14)
	_bubble_label.autowrap_mode = TextServer.AUTOWRAP_WORD
	_bubble_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_bubble_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_bubble_panel.add_child(_bubble_label)
	
	_bubble_layer.add_child(_bubble_panel)


func set_emotion(emotion_name: String, intensity: float = 1.0, source: String = "external"):
	if not persona_data.has("emotions"):
		return
	var emotions: Dictionary = persona_data.get("emotions", {})
	if not emotions.has(emotion_name):
		emotion_name = "neutral"
	
	# 防反馈循环：场景触发和外源推送同一情绪时跳过
	if source == "scene" and emotion_name == current_emotion:
		return
	
	current_emotion = emotion_name
	emotion_intensity = clampf(intensity, 0.0, 1.0)
	_emotion_source = source
	_emotion_age = 0.0
	_emotion_original_intensity = emotion_intensity
	
	var preset: Dictionary = emotions[emotion_name].get("params", {})
	for persona_key: String in preset:
		var godot_key := _camel_to_snake(persona_key)
		if godot_key in target_params:
			target_params[godot_key] = preset[persona_key] * emotion_intensity
	
	# ---- 情绪 → 灯光颜色 ----
	if emotion_light and preset.has("glowColor"):
		var color_hex: int = preset["glowColor"]
		emotion_light.light_color = Color(
			float(color_hex >> 16 & 0xFF) / 255.0,
			float(color_hex >> 8 & 0xFF) / 255.0,
			float(color_hex & 0xFF) / 255.0
		)
		emotion_light.light_energy = preset.get("glowIntensity", 0.25) * 2.0
	
	print("[Emotion] ", emotion_name, " @ ", intensity, " source=", source)
	
	# ---- 情绪切换 → 自动语音（衰减中不触发） ----
	if source != "_decay":
		var http_svr := get_node_or_null("HTTPServer")
		if http_svr and http_svr.has_method("speak_emotion_phrase"):
			http_svr.speak_emotion_phrase(emotion_name)
