from __future__ import annotations

import os
from pathlib import Path
import tempfile

import bpy


install_root = Path(
    os.environ.get(
        "MGS4_ROOT",
        r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4",
    )
)
data_root = (
    install_root / "MGS4" if (install_root / "MGS4").is_dir() else install_root
)
addon_key = next(
    key
    for key in bpy.context.preferences.addons.keys()
    if key.endswith("mgs4_remastered_importer")
)
preferences = bpy.context.preferences.addons[addon_key].preferences
settings = bpy.context.scene.mgs4_importer
assert preferences.highlight_missing_textures
assert settings.material_layer_mode == "CONSERVATIVE"
assert settings.use_vertex_color
assert not settings.use_vertex_alpha


def choose_result(virtual_path: str) -> None:
    settings.result_index = next(
        index
        for index, item in enumerate(settings.results)
        if item.virtual_path == virtual_path
    )


preferences.game_root = r"Z:\MGS4_EXTENSION_SMOKE_MISSING"
preferences.highlight_missing_textures = False
loose_path = data_root / "common" / "mesh" / "pc" / "0051b7f4.mdn"
assert bpy.ops.import_scene.mgs4_mdn(
    filepath=str(loose_path),
    import_materials=False,
    material_layer_mode="LAYER_PREVIEW",
    use_vertex_color=False,
    use_vertex_alpha=True,
) == {"FINISHED"}
loose_collection = next(
    collection
    for collection in bpy.data.collections
    if collection.get("mgs4_source_file") == str(loose_path)
)
assert loose_collection["mgs4_layer_mode"] == "LAYER_PREVIEW"
assert not loose_collection["mgs4_use_vertex_color"]
assert loose_collection["mgs4_use_vertex_alpha"]
assert not loose_collection["mgs4_highlight_missing_textures"]

with tempfile.TemporaryDirectory() as temporary:
    preferences.game_root = str(install_root)
    preferences.cache_root = temporary
    preferences.highlight_missing_textures = True

    settings.asset_kind = "MODEL"
    assert len(settings.results) > 6_000
    settings.search_text = "00e46846.mdn"
    assert settings.search_text in settings.results[
        settings.result_index
    ].virtual_path
    choose_result("common/stage/stage01/s01a00l/cache/00e46846.mdn")
    settings.import_materials = True
    assert settings.import_animations
    settings.animation_limit = 3
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    model_collection = next(
        collection
        for collection in bpy.data.collections
        if collection.get("mgs4_virtual_path")
        == "common/stage/stage01/s01a00l/cache/00e46846.mdn"
    )
    assert model_collection["mgs4_layer_mode"] == "CONSERVATIVE"
    assert model_collection["mgs4_use_vertex_color"]
    assert not model_collection["mgs4_use_vertex_alpha"]
    assert model_collection["mgs4_highlight_missing_textures"]
    armature = bpy.context.view_layer.objects.active
    assert armature is not None and armature.type == "ARMATURE"
    assert not armature.show_in_front
    assert len(armature.data.bones) == 71
    assert bpy.data.images
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

    assert armature.animation_data is not None
    assert armature.animation_data.action is not None
    action = bpy.data.actions["00815BAE_0000_009A5583"]
    assert {
        candidate.name for candidate in bpy.data.actions
    } >= {
        "00815BAE_0000_009A5583",
        "00815BAE_0001_00C90559",
        "00815BAE_0002_009864D6",
    }
    assert action.use_frame_range
    assert tuple(action.frame_range) == (1.0, 120.0)
    assert action.slots[0].select
    assert action.slots[0].show_expanded
    assert action["mgs4_archive_name"] == "00815bae"
    assert action["mgs4_clip_index"] == 0
    assert action["mgs4_fps"] == 60
    assert bpy.context.scene.frame_start == 1
    assert bpy.context.scene.frame_end == 120
    channel_bag = action.layers[0].strips[0].channelbags[0]
    assert len(channel_bag.groups) == 39
    assert len(channel_bag.fcurves) == 159
    rotation_curve = next(
        curve
        for layer in action.layers
        for strip in layer.strips
        for channel_bag in strip.channelbags
        for curve in channel_bag.fcurves
        if curve.data_path
        == 'pose.bones["00A89233"].rotation_quaternion'
        and curve.array_index == 1
    )
    assert abs(rotation_curve.keyframe_points[0].co[1] + 0.0053752277) < 1e-7
    bpy.context.scene.frame_set(1)
    root = armature.pose.bones["00A89233"].matrix.translation
    assert abs(root.x) < 1e-7
    assert abs(root.y + 0.026734375) < 1e-7
    assert abs(root.z - 1.067) < 1e-7
    meshes = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH" and obj.parent == armature
    ]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for frame in (1, 60, 120):
        bpy.context.scene.frame_set(frame)
        minimum_z = float("inf")
        for mesh_object in meshes:
            evaluated = mesh_object.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                minimum_z = min(
                    minimum_z,
                    *(
                        (evaluated.matrix_world @ vertex.co).z
                        for vertex in mesh.vertices
                    ),
                )
            finally:
                evaluated.to_mesh_clear()
        assert -0.003 <= minimum_z <= 0.003

    settings.asset_kind = "MISSION"
    assert len(settings.results) > 5
    settings.search_text = "stage05/s20"
    settings.import_materials = False
    settings.stage_bone_limit = 32
    settings.stage_model_limit = 0
    settings.stage_use_layout = True
    settings.stage_include_auxiliary = True
    settings.stage_include_exterior = True
    assert settings.search_text in settings.results[
        settings.result_index
    ].virtual_path
    choose_result("common/stage/stage05/s20")
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    mission_collection = next(
        collection
        for collection in bpy.data.collections
        if collection.get("mgs4_mission_path")
        == "common/stage/stage05/s20"
    )
    stage_collections = {
        collection.get("mgs4_stage_path"): collection
        for collection in mission_collection.children
    }
    assert set(stage_collections) == {
        "common/stage/stage05/s20a00l",
        "common/stage/stage05/s20a10l",
    }
    environment = stage_collections["common/stage/stage05/s20a10l"]
    imported_stage_paths = {
        collection.get("mgs4_virtual_path")
        for collection in environment.children
        if collection.get("mgs4_virtual_path")
    }
    assert imported_stage_paths == {
        "common/stage/stage05/s20a10l/cache/00fb6416.mdn",
        "common/stage/stage05/s20a10l/cache/00931c63.mdn",
        "common/stage/stage05/s20a10l/cache/002ea1c9.mdn",
        "common/stage/stage05/s20a10l/cache/00c3447c.mdn",
        "common/stage/stage05/s20a10l/cache/00b53074.mdn",
        "common/stage/stage05/s20a10l/cache/00b53874.mdn",
        "common/stage/stage05/s20a10l/cache/00b54074.mdn",
    }
    auxiliary = next(
        collection
        for collection in environment.children
        if collection.get("mgs4_auxiliary_environment")
    )
    assert environment["mgs4_auxiliary_model_count"] == 3
    assert environment["mgs4_manifest_model_count"] == 104
    assert environment["mgs4_manifest_environment_count"] == 2
    assert {
        collection.get("mgs4_virtual_path")
        for collection in auxiliary.children
    } == {
        "common/stage/stage04/s04a75l/cache/00674471.mdn",
        "common/stage/stage05/s20a10l/cache/00374816.mdn",
        "common/stage/stage05/s20a10l/cache/00f7458e.mdn",
    }
    assert all(
        collection.get("mgs4_stage_role") == "AUXILIARY_ENVIRONMENT"
        and collection.get("mgs4_inferred_stage_model")
        for collection in auxiliary.children
    )
    assert {
        collection.get("mgs4_virtual_path")
        for collection in auxiliary.children
        if collection.get("mgs4_manifest_stage_model")
    } == {
        "common/stage/stage04/s04a75l/cache/00674471.mdn",
    }
    exterior = next(
        collection
        for collection in environment.children
        if collection.get("mgs4_exterior_environment")
    )
    assert environment["mgs4_exterior_model_count"] == 2
    assert exterior.hide_viewport and exterior.hide_render
    assert {
        collection.get("mgs4_virtual_path")
        for collection in exterior.children
    } == {
        "common/stage/stage04/s04a75l/cache/00174953.mdn",
        "common/stage/stage05/s20a10l/cache/00374492.mdn",
    }
    assert all(
        collection.get("mgs4_stage_role") == "EXTERIOR_LOD"
        and collection.get("mgs4_exterior_stage_model")
        for collection in exterior.children
    )
    assert {
        collection.get("mgs4_virtual_path")
        for collection in exterior.children
        if collection.get("mgs4_manifest_stage_model")
    } == {
        "common/stage/stage04/s04a75l/cache/00174953.mdn",
    }
    approach = stage_collections["common/stage/stage05/s20a00l"]
    instances = [
        obj
        for obj in approach.objects
        if obj.instance_type == "COLLECTION"
        and obj.get("mgs4_model_hash") == 0x00FF5F4B
    ]
    assert len(instances) == 4
    assert {
        tuple(round(value, 4) for value in instance.matrix_world.translation)
        for instance in instances
    } == {
        (2.3, 1.4375, 2.85),
        (2.3, 4.5625, 2.85),
        (6.05, 4.5625, 2.85),
        (6.05, 1.4375, 2.85),
    }

print(
    "MGS4_EXTENSION_SMOKE_OK",
    addon_key,
    len(bpy.data.objects),
    len(bpy.data.actions),
    len(bpy.data.images),
)
