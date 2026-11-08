from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mgs4_remastered_importer.blender_import import (  # noqa: E402
    _legacy_specular,
    _texture_roles,
    game_transform_matrix,
    import_mdn_model,
    import_motion_archive,
)
from mgs4_remastered_importer.formats.common import hash_name  # noqa: E402
from mgs4_remastered_importer.formats.mdn import (  # noqa: E402
    MdnMaterial,
    parse_mdn,
)
from mgs4_remastered_importer.formats.mtar import parse_mtar  # noqa: E402
from mgs4_remastered_importer.formats.texture import TextureResolver  # noqa: E402
from mgs4_remastered_importer.formats.vpak import VpakArchive  # noqa: E402


install_root = Path(
    os.environ.get(
        "MGS4_ROOT",
        r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4",
    )
)
data_root = (
    install_root / "MGS4" if (install_root / "MGS4").is_dir() else install_root
)
archive = VpakArchive(
    data_root / "common" / "stage" / "stage_data_compressed.1.pak"
)
specular_level, roughness = _legacy_specular((1.0, 1.0, 1.0, 64.0))
assert specular_level == 0.12
assert abs(roughness - 1.6 * (2.0 / 66.0) ** 0.25) < 1e-8
shader_005 = MdnMaterial(
    flags=0x00110005,
    name_hash=0,
    texture_count=5,
    parameter_count=0,
    texture_indices=(0, 1, 2, 3, 4),
    parameters=(),
)
assert _texture_roles(shader_005) == (
    "diffuse",
    "normal",
    "layer",
    "environment",
    "unknown",
)
transform = game_transform_matrix(
    (1000.0, 2000.0, 3000.0),
    (0.0, 0.0, 0.0),
)
assert tuple(transform.translation) == (1.0, -3.0, 2.0)
model = parse_mdn(
    archive.fetch("common/stage/stage01/s01a00l/cache/007ec102.mdn")
)
motion = parse_mtar(
    archive.fetch("common/stage/stage01/s01a00l/cache/0005bb6e.mtar")
)

with tempfile.TemporaryDirectory() as temporary:
    resolver = TextureResolver(data_root, temporary)
    imported = import_mdn_model(
        bpy.context,
        model,
        name="MGS4_SMOKE",
        resolver=resolver,
    )
    assert imported.armature is not None
    assert not imported.armature.show_in_front
    assert len(imported.armature.data.bones) == 70
    assert len([obj for obj in imported.objects if obj.type == "MESH"]) == 5
    assert sum(
        len(obj.vertex_groups) for obj in imported.objects if obj.type == "MESH"
    ) > 0
    assert not imported.warnings
    first_mesh = bpy.data.objects["MGS4_SMOKE_Mesh_000"].data
    first_source = model.meshes[0]
    assert len(first_mesh.vertices) == len(first_source.vertices)
    assert len(first_mesh.polygons) == len(first_source.triangles)
    assert len(first_mesh.loops) == len(first_source.triangles) * 3
    expected_vertex = (
        first_source.vertices[0][0] * 0.001,
        -first_source.vertices[0][2] * 0.001,
        first_source.vertices[0][1] * 0.001,
    )
    for actual, expected in zip(first_mesh.vertices[0].co, expected_vertex):
        assert abs(actual - expected) < 1e-6
    assert bpy.data.images
    assert any(
        image.source == "FILE"
        and Path(bpy.path.abspath(image.filepath)).suffix.lower() == ".dds"
        for image in bpy.data.images
    )
    principled_nodes = [
        node
        for material in bpy.data.materials
        for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    ]
    assert principled_nodes
    assert all(
        node.inputs["Metallic"].default_value == 0.0
        for node in principled_nodes
    )
    assert all(
        node.inputs["Roughness"].default_value >= 0.45
        for node in principled_nodes
    )
    assert all(
        not node.inputs["Alpha"].is_linked
        for node in principled_nodes
    )
    normal_nodes = [
        node
        for material in bpy.data.materials
        for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeNormalMap"
    ]
    assert normal_nodes
    assert all(
        node.inputs["Strength"].default_value == 0.5
        for node in normal_nodes
    )

    decal_model = parse_mdn(
        (
            data_root
            / "common"
            / "mesh"
            / "pc"
            / "00126129.mdn"
        ).read_bytes()
    )
    decal = import_mdn_model(
        bpy.context,
        decal_model,
        name="MGS4_DECAL",
        resolver=resolver,
    )
    assert not decal.warnings
    decal_mesh = bpy.data.objects["MGS4_DECAL_Mesh_009"]
    decal_materials = tuple(decal_mesh.data.materials)
    assert len(decal_materials) == 2
    assert all(
        material["mgs4_flags"] & 0xFF == 0xA6
        and material.surface_render_method == "BLENDED"
        and not material.use_transparency_overlap
        for material in decal_materials
    )
    opacity_nodes = [
        node
        for material in decal_materials
        for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeTexImage"
        and node.get("mgs4_role") == "opacity"
    ]
    assert len(opacity_nodes) == 2
    assert all(
        node.image.colorspace_settings.name == "Non-Color"
        for node in opacity_nodes
    )

    animated = import_motion_archive(
        bpy.context,
        imported.armature,
        motion,
        archive_name="0005bb6e.mtar",
    )
    assert len(animated.actions) == 3
    assert not animated.missing_bones
    assert imported.armature.animation_data.action in animated.actions
    assert animated.actions[0].name == "0005BB6E_0000_003542E7"
    assert all(action.use_frame_range for action in animated.actions)
    assert all(action.slots[0].select for action in animated.actions)
    assert all(action.slots[0].show_expanded for action in animated.actions)
    assert bpy.context.scene.frame_start == round(animated.actions[0].frame_start)
    assert bpy.context.scene.frame_end == round(animated.actions[0].frame_end)
    curves = [
        curve
        for action in animated.actions
        for layer in action.layers
        for strip in layer.strips
        for channel_bag in strip.channelbags
        for curve in channel_bag.fcurves
    ]
    assert curves
    assert all(
        point.interpolation == "LINEAR"
        for curve in curves
        for point in curve.keyframe_points
    )
    source_track = next(
        track for track in motion.clips[0].tracks if track.translations
    )
    source_translation = source_track.translations[0]
    expected_armature_translation = (
        source_translation.value[0] * 0.001,
        -source_translation.value[2] * 0.001,
        source_translation.value[1] * 0.001,
    )
    translation_path = (
        f'pose.bones["{hash_name(source_track.bone_hash)}"].location'
    )
    translation_curves = {
        curve.array_index: curve
        for layer in animated.actions[0].layers
        for strip in layer.strips
        for channel_bag in strip.channelbags
        for curve in channel_bag.fcurves
        if curve.data_path == translation_path
    }
    assert set(translation_curves) == {0, 1, 2}
    pose_bone = imported.armature.pose.bones[
        hash_name(source_track.bone_hash)
    ]
    expected_local_translation = (
        pose_bone.bone.matrix_local.to_3x3().inverted_safe()
        @ (
            Vector(expected_armature_translation)
            - pose_bone.bone.head_local
        )
    )
    for component, expected in enumerate(expected_local_translation):
        point = translation_curves[component].keyframe_points[0]
        assert abs(point.co[0] - source_translation.frame) < 1e-8
        assert abs(point.co[1] - expected) < 1e-8
    bpy.context.scene.frame_set(source_translation.frame)
    actual_translation = pose_bone.matrix.translation
    for actual, expected in zip(actual_translation, expected_armature_translation):
        assert abs(actual - expected) < 1e-7

print(
    "MGS4_BLENDER_SMOKE_OK",
    len(imported.objects),
    len(animated.actions),
    len(bpy.data.materials),
    len(bpy.data.images),
)
