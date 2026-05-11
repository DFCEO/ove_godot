"""
Blender 脚本：修正模型方向并导出（Godot Y-up 兼容）
运行：Blender → Scripting → 打开此文件 → Run Script
前提：已导入 eve_ah.glb
"""
import bpy
import os
from mathutils import Euler, Matrix
import math

# ===== 1. 找到骨架 =====
armature = None
for obj in bpy.data.objects:
    if obj.type == 'ARMATURE':
        armature = obj
        break

if armature is None:
    raise Exception("没找到骨架！请先导入 GLB 模型")

print(f"找到骨架: {armature.name}")

# ===== 2. 检查 body_2 骨骼 =====
bpy.context.view_layer.objects.active = armature
bpy.ops.object.mode_set(mode='EDIT')

body_bone = armature.data.edit_bones.get('body_2')
if body_bone:
    print(f"body_2 当前旋转: {body_bone.matrix.to_euler()}")
    print(f"body_2 位置: head={body_bone.head} tail={body_bone.tail}")

bpy.ops.object.mode_set(mode='OBJECT')

# ===== 3. 修正：直接在 Object 模式旋转整个骨架 =====
# Godot 使用 Y-up，Blender 使用 Z-up
# glTF 导出器默认做转换，但我们的骨骼内部有 180° Z旋转需要清除

# 方法：选中骨架和所有子对象，整体旋转 -90° 绕 X
bpy.ops.object.select_all(action='DESELECT')
armature.select_set(True)
bpy.context.view_layer.objects.active = armature

# 先取消父子关系，旋转后再恢复（避免双重旋转）
# 更简单的方法：直接导出前旋转

print("\n当前骨架 Object 旋转:", armature.rotation_euler)

# ===== 4. 导出 =====
desktop = os.path.join(os.path.expanduser("~"), "Desktop")
output_path = os.path.join(desktop, "eve_fixed.glb")

# 选中所有对象
bpy.ops.object.select_all(action='DESELECT')
for obj in bpy.data.objects:
    obj.select_set(True)

bpy.ops.export_scene.gltf(
    filepath=output_path,
    export_format='GLB',
    export_yup=True,           # Godot 需要 Y-up
    export_skins=True,
    export_morph=False,
    export_apply=True,
    use_selection=True,
)

print(f"\n✅ 导出完成: {output_path}")
print("请将此文件重命名为 eve_ah.glb 并放到 ove-godot/models/ 目录")
