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
assert preferences.hide_inferred_lods
assert settings.material_layer_mode == "CONSERVATIVE"
assert settings.use_vertex_color
assert not settings.use_vertex_alpha
assert not settings.import_inferred_lods


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

    handprint_path = (
        data_root / "common" / "mesh" / "pc" / "00b267e5.mdn"
    )
    existing_materials = {
        material.as_pointer() for material in bpy.data.materials
    }
    assert bpy.ops.import_scene.mgs4_mdn(
        filepath=str(handprint_path),
        import_materials=True,
    ) == {"FINISHED"}
    handprint_materials = {
        material["mgs4_name_hash"] & 0xFFFFFFFF: material
        for material in bpy.data.materials
        if material.as_pointer() not in existing_materials
    }
    assert len(handprint_materials) == 4
    expected_handprint_diffuse = {
        0x00EBB437: 0x00C3944E,
        0x00A1E9BF: 0x0076315A,
        0x007ACC3D: 0x0052A120,
    }
    for material_hash, texture_hash in expected_handprint_diffuse.items():
        material = handprint_materials[material_hash]
        assert material["mgs4_variant_50_color_presentation"]
        assert all(
            abs(component - expected) < 1e-6
            for component, expected in zip(
                material.diffuse_color,
                (0.8, 0.8, 0.8, 1.0),
            )
        )
        principled = next(
            node
            for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        base_color_link = principled.inputs["Base Color"].links[0]
        assert base_color_link.from_node.get("mgs4_role") == "diffuse"
        assert (
            base_color_link.from_node.get("mgs4_texture_hash")
            & 0xFFFFFFFF
            == texture_hash
        )

    settings.asset_kind = "MODEL"
    assert len(settings.results) > 6_000
    lod_paths = {
        "common/stage/stage02/s02a40l/cache/0051d172.mdn",
        "common/stage/stage02/s02a40l/cache/0051a3f7.mdn",
        "common/stage/stage02/s02a40l/cache/0051b7f4.mdn",
    }
    visible_lod_paths = {
        item.virtual_path
        for item in settings.results
        if item.virtual_path in lod_paths
    }
    assert visible_lod_paths == {
        "common/stage/stage02/s02a40l/cache/0051d172.mdn"
    }
    visible_model_count = len(settings.results)
    preferences.hide_inferred_lods = False
    assert len(settings.results) > visible_model_count
    assert {
        item.virtual_path
        for item in settings.results
        if item.virtual_path in lod_paths
    } == lod_paths
    preferences.hide_inferred_lods = True
    assert len(settings.results) == visible_model_count

    variant_50_vegetation_path = (
        "ww/stage/slot/00008d4d/cache/0078b3f0.mdn"
    )
    existing_materials = {
        material.as_pointer() for material in bpy.data.materials
    }
    settings.import_materials = True
    settings.import_animations = False
    choose_result(variant_50_vegetation_path)
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    variant_50_vegetation_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in existing_materials
    )
    assert len(variant_50_vegetation_materials) == 1
    variant_50_vegetation_material = variant_50_vegetation_materials[0]
    assert variant_50_vegetation_material[
        "mgs4_inferred_variant_50_vegetation"
    ]
    assert variant_50_vegetation_material[
        "mgs4_diffuse_alpha_mode"
    ] == "MASKED"
    assert (
        variant_50_vegetation_material.surface_render_method
        == "DITHERED"
    )
    variant_50_principled = next(
        node
        for node in variant_50_vegetation_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    variant_50_alpha_link = variant_50_principled.inputs["Alpha"].links[0]
    assert variant_50_alpha_link.from_node.get("mgs4_role") == "diffuse"
    assert (
        variant_50_alpha_link.from_node.get("mgs4_texture_hash")
        & 0xFFFFFFFF
        == 0x00BE7D8F
    )

    planar_variant_50_path = (
        "common/stage/slot/00000001/cache/0094b09f.mdn"
    )
    existing_materials = {
        material.as_pointer() for material in bpy.data.materials
    }
    choose_result(planar_variant_50_path)
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    planar_variant_50_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in existing_materials
    )
    assert len(planar_variant_50_materials) == 1
    planar_variant_50_material = planar_variant_50_materials[0]
    assert not planar_variant_50_material[
        "mgs4_inferred_variant_50_vegetation"
    ]
    assert not planar_variant_50_material["mgs4_diffuse_alpha_enabled"]
    planar_variant_50_principled = next(
        node
        for node in planar_variant_50_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    assert not planar_variant_50_principled.inputs["Alpha"].is_linked

    tree_path = "common/stage/slot/0000001e/cache/00f686ed.mdn"
    existing_materials = {
        material.as_pointer() for material in bpy.data.materials
    }
    choose_result(tree_path)
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    tree_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in existing_materials
    )
    assert len(tree_materials) == 1
    tree_material = tree_materials[0]
    assert tree_material["mgs4_name_hash"] & 0xFFFFFFFF == 0x003A99E2
    assert tree_material["mgs4_inferred_tree_mask"]
    assert tree_material["mgs4_diffuse_alpha_enabled"]
    assert tree_material["mgs4_diffuse_alpha_mode"] == "MASKED"
    assert tree_material.surface_render_method == "DITHERED"
    assert tree_material.use_transparency_overlap
    tree_principled = next(
        node
        for node in tree_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    tree_alpha_link = tree_principled.inputs["Alpha"].links[0]
    assert tree_alpha_link.from_node.get("mgs4_role") == "diffuse"
    assert (
        tree_alpha_link.from_node.get("mgs4_texture_hash") & 0xFFFFFFFF
        == 0x00DDEC65
    )

    glasses_path = "common/stage/stage05/s20a00l/cache/0033f269.mdn"
    existing_materials = {
        material.as_pointer() for material in bpy.data.materials
    }
    choose_result(glasses_path)
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    glasses_material = next(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in existing_materials
        and material.get("mgs4_name_hash") is not None
        and material["mgs4_name_hash"] & 0xFFFFFFFF == 0x00F31905
    )
    assert glasses_material["mgs4_variant_a5_translucency"]
    assert (
        abs(
            glasses_material["mgs4_variant_a5_opacity"]
            - 0.39990234375
        )
        < 1e-8
    )
    assert (
        abs(
            glasses_material["mgs4_variant_a5_roughness"]
            - 0.0999755859375
        )
        < 1e-8
    )
    assert glasses_material.surface_render_method == "BLENDED"
    assert not glasses_material.use_transparency_overlap
    glasses_principled = next(
        node
        for node in glasses_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    glasses_alpha_link = glasses_principled.inputs["Alpha"].links[0]
    assert glasses_alpha_link.from_node.bl_idname == "ShaderNodeValue"
    assert glasses_alpha_link.from_node.get("mgs4_role") == "opacity"
    assert (
        glasses_alpha_link.from_node.get("mgs4_parameter")
        == "parameter_2_w"
    )
    assert abs(
        glasses_alpha_link.from_socket.default_value - 0.39990234375
    ) < 1e-8

    hair_blood_path = (
        "common/stage/stage05/s05a50l/cache/00846b09.mdn"
    )
    existing_materials = {
        material.as_pointer() for material in bpy.data.materials
    }
    choose_result(hair_blood_path)
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    hair_blood_materials = {
        material["mgs4_name_hash"] & 0xFFFFFFFF: material
        for material in bpy.data.materials
        if material.as_pointer() not in existing_materials
        and material.get("mgs4_name_hash") is not None
    }
    expected_hair_blood = {
        0x006FF975: ("MASKED", 0x0028E963, True),
        0x009FDFB9: ("MASKED", 0x00D42573, True),
        0x00EDE4E4: ("BLENDED", 0x0012860F, False),
        0x001019E9: ("BLENDED", 0x007DAC4D, False),
    }
    assert set(expected_hair_blood).issubset(hair_blood_materials)
    for material_hash, (
        alpha_mode,
        diffuse_hash,
        hair_mask,
    ) in expected_hair_blood.items():
        imported_material = hair_blood_materials[material_hash]
        assert imported_material["mgs4_diffuse_alpha_enabled"]
        assert imported_material["mgs4_diffuse_alpha_mode"] == alpha_mode
        assert imported_material["mgs4_inferred_hair_mask"] == hair_mask
        assert imported_material[
            "mgs4_inferred_scripted_overlay"
        ] == (not hair_mask)
        assert imported_material.surface_render_method == (
            "DITHERED" if hair_mask else "BLENDED"
        )
        assert imported_material.use_transparency_overlap == hair_mask
        imported_principled = next(
            node
            for node in imported_material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        imported_alpha_link = imported_principled.inputs["Alpha"].links[0]
        assert imported_alpha_link.from_node.get("mgs4_role") == "diffuse"
        assert imported_alpha_link.from_socket.name == "Alpha"
        assert (
            imported_alpha_link.from_node.get("mgs4_texture_hash")
            & 0xFFFFFFFF
            == diffuse_hash
        )
    settings.import_animations = True

    alias_item = next(
        item
        for item in settings.results
        if item.virtual_path
        == "common/stage/stage01/s01a00l/cache/00e46846.mdn"
    )
    assert settings.catalog_names_version
    assert alias_item.resolved_name == "mem_def_b"
    assert alias_item.name.startswith("mem_def_b  (00e46846.mdn)")
    settings.search_text = "mem_def_b"
    assert "mem_def_b" in settings.results[
        settings.result_index
    ].searchable_names.lower()
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

    character_armature = bpy.context.view_layer.objects.active
    settings.search_text = "001a1f43.mdn"
    choose_result("common/stage/stage02/s02a95l/cache/001a1f43.mdn")
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    high_flag_collection = next(
        collection
        for collection in bpy.data.collections
        if collection.get("mgs4_virtual_path")
        == "common/stage/stage02/s02a95l/cache/001a1f43.mdn"
    )
    high_flag_mesh = next(
        obj for obj in high_flag_collection.all_objects if obj.type == "MESH"
    )
    assert high_flag_mesh["mgs4_flags"] == -0x80000000
    assert high_flag_mesh["mgs4_flags"] & 0xFFFFFFFF == 0x80000000
    high_flag_material = high_flag_mesh.data.materials[0]
    high_flag_principled = next(
        node
        for node in high_flag_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    assert high_flag_material.diffuse_color[3] == 1.0
    assert high_flag_principled.inputs["Alpha"].default_value == 1.0
    assert not high_flag_principled.inputs["Alpha"].is_linked
    settings.search_text = ""
    bpy.context.view_layer.objects.active = character_armature

    character_materials = {
        material
        for obj in model_collection.all_objects
        if obj.type == "MESH"
        for material in obj.data.materials
        if material is not None
    }
    assert character_materials
    assert all(
        not material["mgs4_vertex_color_available"]
        and not material["mgs4_vertex_color_enabled"]
        and not material["mgs4_vertex_alpha_enabled"]
        for material in character_materials
    )
    assert any(
        material["mgs4_vertex_alpha_available"]
        for material in character_materials
    )
    for material in character_materials:
        diffuse_nodes = [
            node
            for node in material.node_tree.nodes
            if node.get("mgs4_role") == "diffuse"
        ]
        if not diffuse_nodes:
            continue
        principled = next(
            node
            for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        assert (
            principled.inputs["Base Color"].links[0].from_node
            in diffuse_nodes
        )
    armature = bpy.context.view_layer.objects.active
    assert armature is not None and armature.type == "ARMATURE"
    assert not armature.show_in_front
    assert len(armature.data.bones) == 71
    assert bpy.data.images
    principled_nodes = [
        node
        for material in character_materials
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
        for material in character_materials
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

    settings.search_text = "00aee46b.mdn"
    settings.import_materials = False
    settings.import_animations = True
    settings.animation_limit = 3
    existing_armatures = {
        obj.as_pointer()
        for obj in bpy.data.objects
        if obj.type == "ARMATURE"
    }
    choose_result(
        "common/stage/stage03/s03a00l/cache/00aee46b.mdn"
    )
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    chapter_armature = next(
        obj
        for obj in bpy.data.objects
        if obj.type == "ARMATURE"
        and obj.as_pointer() not in existing_armatures
    )
    chapter_actions = {
        candidate.name: candidate
        for candidate in bpy.data.actions
        if candidate.name.startswith("0084802F_")
    }
    assert set(chapter_actions) == {
        "0084802F_0000_0048326B",
        "0084802F_0001_0055905D",
        "0084802F_0002_0070640B",
    }
    assert chapter_armature.animation_data is not None
    assert (
        chapter_armature.animation_data.action
        == chapter_actions["0084802F_0000_0048326B"]
    )
    assert not any(
        name.startswith("00815BAE_")
        for name in chapter_actions
    )
    chapter_mesh = max(
        (
            obj
            for obj in bpy.data.objects
            if obj.type == "MESH" and obj.parent == chapter_armature
        ),
        key=lambda obj: len(obj.data.vertices),
    )

    def evaluated_vertices(mesh_object):
        evaluated = mesh_object.evaluated_get(
            bpy.context.evaluated_depsgraph_get()
        )
        evaluated_mesh = evaluated.to_mesh()
        try:
            return tuple(
                tuple(vertex.co)
                for vertex in evaluated_mesh.vertices
            )
        finally:
            evaluated.to_mesh_clear()

    bpy.context.scene.frame_set(1)
    chapter_vertices_start = evaluated_vertices(chapter_mesh)
    bpy.context.scene.frame_set(60)
    chapter_vertices_middle = evaluated_vertices(chapter_mesh)
    assert max(
        abs(start_component - middle_component)
        for start_vertex, middle_vertex in zip(
            chapter_vertices_start,
            chapter_vertices_middle,
        )
        for start_component, middle_component in zip(
            start_vertex,
            middle_vertex,
        )
    ) > 0.01

    settings.search_text = ""
    settings.import_materials = False
    settings.import_animations = False
    settings.import_inferred_lods = True
    choose_result("common/stage/stage02/s02a40l/cache/0051d172.mdn")
    assert bpy.ops.mgs4.import_selected() == {"FINISHED"}
    lod_collections = {
        collection.get("mgs4_virtual_path"): collection
        for collection in bpy.data.collections
        if collection.get("mgs4_inferred_lod_high_hash") == 0x0051D172
    }
    assert set(lod_collections) == lod_paths
    for level, path in enumerate(
        (
            "common/stage/stage02/s02a40l/cache/0051d172.mdn",
            "common/stage/stage02/s02a40l/cache/0051a3f7.mdn",
            "common/stage/stage02/s02a40l/cache/0051b7f4.mdn",
        )
    ):
        collection = lod_collections[path]
        assert collection["mgs4_inferred_lod"]
        assert collection["mgs4_inferred_lod_level"] == level
        assert collection["mgs4_inferred_lod_count"] == 3
        assert collection.hide_viewport == (level > 0)
        assert collection.hide_render == (level > 0)
    settings.import_inferred_lods = False

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
