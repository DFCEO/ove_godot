"""
Blender 脚本：给 Eve 模型添加眼睛并绑定到头部骨骼
运行方式：Blender → Scripting 工作区 → 粘贴 → Run Script
前提：已用 File → Import → glTF 2.0 导入 eve_ah.glb
"""
import bpy
import math
from mathutils import Vector

# ===== 1. 找到骨架和 head_2 骨骼 =====
armature = None
head_bone = None

for obj in bpy.data.objects:
    if obj.type == 'ARMATURE':
        armature = obj
        for bone in obj.data.bones:
            print(f"  Bone: {bone.name}  head={bone.head_local}  tail={bone.tail_local}")
            if 'head' in bone.name.lower() and 'ctrl' not in bone.name.lower():
                head_bone = bone
                break
        break

if armature is None:
    raise Exception("没找到骨架(Armature)，请先导入 GLB 模型")

if head_bone is None:
    # 找不到 head_2，用最后一个含 head 的骨骼
    for bone in armature.data.bones:
        if 'head' in bone.name.lower():
            head_bone = bone
    if head_bone is None:
        raise Exception("没找到头部骨骼")

print(f"骨架: {armature.name}")
print(f"头部骨骼: {head_bone.name}, head={head_bone.head_local}, tail={head_bone.tail_local}")

# ===== 2. 计算眼睛位置 =====
# Godot 校准值（model_root 空间）：
#   eye_L: (-0.0023, 0.033, 0.008)
#   eye_R: ( 0.0023, 0.033, 0.008)
# 
# 骨骼 head_2 的 global pose 在 model_root 空间约为 (0, 10.026, 0)
# 所以眼睛在骨骼局部空间 = 校准值 - 骨骼位置
# 但 Blender 里骨骼位置可能不同，我们直接用骨骼的 head 位置做参考

bone_head = head_bone.head_local  # 骨骼在 armature 空间的位置
print(f"head_2 head 位置: {bone_head}")

# 眼睛在 head_2 前方的偏移（基于校准值，从骨骼 head 往外）
# 由于 armature 已缩放，偏移量用骨骼长度参考
bone_length = (head_bone.tail_local - head_bone.head_local).length
if bone_length < 0.001:
    bone_length = 1.0  # head_2 可能没有长度，用父骨骼的长度

print(f"骨骼长度参考: {bone_length:.4f}")

# 眼睛位置：骨骼 head 前方 bone_length*0.5 处
# X 间距按骨骼长度比例缩放
eye_spacing = bone_length * 0.07  # 大约 7% 的间距
eye_forward = bone_length * 0.25   # 前方 25%
eye_height = bone_length * 0.1     # 稍微上方

eye_pos_L = Vector((bone_head.x - eye_spacing, bone_head.y + eye_height, bone_head.z + eye_forward))
eye_pos_R = Vector((bone_head.x + eye_spacing, bone_head.y + eye_height, bone_head.z + eye_forward))

print(f"眼睛 L 位置: {eye_pos_L}")
print(f"眼睛 R 位置: {eye_pos_R}")

# ===== 3. 创建 UV 球（眼睛） =====
eye_radius = bone_length * 0.04  # 眼睛大小 = 骨骼长度的 4%

for name, pos in [("eye_L", eye_pos_L), ("eye_R", eye_pos_R)]:
    # 创建 UV 球
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=eye_radius,
        location=pos,
        segments=16,
        ring_count=8
    )
    eye_obj = bpy.context.active_object
    eye_obj.name = name
    
    print(f"创建 {name}: 位置={pos}, 半径={eye_radius:.4f}")

# ===== 4. 绑定眼睛到 head_2 骨骼 =====
# 先把所有眼睛选为子对象
bpy.ops.object.select_all(action='DESELECT')

eye_L = bpy.data.objects.get('eye_L')
eye_R = bpy.data.objects.get('eye_R')

if eye_L and eye_R:
    eye_L.select_set(True)
    eye_R.select_set(True)
    bpy.context.view_layer.objects.active = eye_L  # 活动对象
    
    # Shift+选骨架
    armature.select_set(True)
    
    # 设为骨架的子对象（先不用 bone parent，导出后在 Godot 里处理）
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.parent_set(type='ARMATURE')
    
    # 现在设置具体的骨骼父级
    # 用 vertex parent 方式挂到 head_2
    for obj in [eye_L, eye_R]:
        obj.parent = armature
        obj.parent_type = 'BONE'
        obj.parent_bone = head_bone.name
        print(f"{obj.name} 已绑定到骨骼 {head_bone.name}")
else:
    print("WARNING: 找不到创建的 eye_L/eye_R 对象")

# ===== 5. 导出 =====
# 获取桌面路径
import os
desktop = os.path.join(os.path.expanduser("~"), "Desktop")
output_path = os.path.join(desktop, "eve_with_eyes.glb")

# 选中所有要导出的对象
bpy.ops.object.select_all(action='DESELECT')
for obj in bpy.data.objects:
    obj.select_set(True)

bpy.ops.export_scene.gltf(
    filepath=output_path,
    export_format='GLB',
    export_skins=True,
    export_morph=False,
    export_apply=True,
    use_selection=True
)

print(f"\n✅ 导出完成: {output_path}")
print("请把这个文件拷贝到 ove-godot/models/ 目录下")
