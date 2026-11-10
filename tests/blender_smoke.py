from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mgs4_remastered_importer.blender_import import (  # noqa: E402
    LAYER_MODE_PREVIEW,
    MaterialImportOptions,
    _diffuse_alpha_mode,
    _legacy_specular,
    _load_texture_image,
    _material_alpha_render_states,
    _material_inferred_hair_masks,
    _material_inferred_scripted_overlays,
    _material_inferred_tree_masks,
    _material_render_families,
    _texture_roles,
    _uses_diffuse_alpha,
    _variant_a5_translucency,
    game_transform_matrix,
    import_mdn_model,
    import_motion_archive,
)
from mgs4_remastered_importer.catalog import GameCatalog, MODEL  # noqa: E402
from mgs4_remastered_importer.errors import MGS4Error  # noqa: E402
from mgs4_remastered_importer.formats.common import hash_name  # noqa: E402
from mgs4_remastered_importer.formats.mdn import (  # noqa: E402
    MdnMaterial,
    parse_mdn,
)
from mgs4_remastered_importer.formats.mtar import parse_mtar  # noqa: E402
from mgs4_remastered_importer.formats.texture import (  # noqa: E402
    FORMAT_DXT1,
    FORMAT_DXT5,
    TextureResolver,
)
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

    high_flag_model = parse_mdn(
        VpakArchive(
            data_root
            / "common"
            / "stage"
            / "stage_data_compressed.2.pak"
        ).fetch("common/stage/stage02/s02a95l/cache/001a1f43.mdn")
    )
    high_flag_import = import_mdn_model(
        bpy.context,
        high_flag_model,
        name="MGS4_HIGH_FLAG",
        resolver=resolver,
    )
    assert not high_flag_import.warnings
    assert len(high_flag_import.objects) == 1
    high_flag_mesh = high_flag_import.objects[0]
    assert len(high_flag_mesh.data.vertices) == 36
    assert len(high_flag_mesh.data.polygons) == 42
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

    diffuse_alpha_model = parse_mdn(
        (
            data_root
            / "common"
            / "mesh"
            / "pc"
            / "00325067.mdn"
        ).read_bytes()
    )
    diffuse_alpha_hashes = {0x00097A97, 0x00069813}
    diffuse_alpha_source_indices = tuple(
        material_index
        for material_index, material in enumerate(
            diffuse_alpha_model.materials
        )
        if material.name_hash in diffuse_alpha_hashes
    )
    diffuse_alpha_sources = tuple(
        diffuse_alpha_model.materials[material_index]
        for material_index in diffuse_alpha_source_indices
    )
    assert len(diffuse_alpha_sources) == 2
    reduced_materials = []
    reduced_textures = []
    for source_material in diffuse_alpha_sources:
        assert _texture_roles(source_material) == ("diffuse", "normal")
        first_texture_index = len(reduced_textures)
        active_textures = tuple(
            diffuse_alpha_model.textures[texture_index]
            for texture_index in source_material.texture_indices[
                : source_material.texture_count
            ]
        )
        reduced_textures.extend(active_textures)
        texture_source = resolver.resolve(
            active_textures[0].name_hash,
            "common",
        )
        assert _uses_diffuse_alpha(source_material, texture_source)
        reduced_materials.append(
            replace(
                source_material,
                texture_indices=(
                    first_texture_index,
                    first_texture_index + 1,
                    *source_material.texture_indices[2:],
                ),
            )
        )
    diffuse_alpha_index_map = {
        source_index: reduced_index
        for reduced_index, source_index in enumerate(
            diffuse_alpha_source_indices
        )
    }
    diffuse_alpha_geometry = diffuse_alpha_model.meshes[50]
    assert diffuse_alpha_geometry.info.flags & 0x00001000
    diffuse_alpha_triangles = tuple(
        triangle
        for triangle, material_index in zip(
            diffuse_alpha_geometry.triangles,
            diffuse_alpha_geometry.triangle_materials,
        )
        if material_index in diffuse_alpha_index_map
    )
    diffuse_alpha_triangle_materials = tuple(
        diffuse_alpha_index_map[material_index]
        for material_index in diffuse_alpha_geometry.triangle_materials
        if material_index in diffuse_alpha_index_map
    )
    assert set(diffuse_alpha_triangle_materials) == {0, 1}
    reduced_diffuse_alpha_model = replace(
        diffuse_alpha_model,
        bones=(),
        groups=(),
        materials=tuple(reduced_materials),
        textures=tuple(reduced_textures),
        skins=(),
        meshes=(
            replace(
                diffuse_alpha_geometry,
                triangles=diffuse_alpha_triangles,
                triangle_materials=diffuse_alpha_triangle_materials,
            ),
        ),
    )
    diffuse_alpha_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    diffuse_alpha_import = import_mdn_model(
        bpy.context,
        reduced_diffuse_alpha_model,
        name="MGS4_DIFFUSE_ALPHA",
        resolver=resolver,
    )
    assert not diffuse_alpha_import.warnings
    diffuse_alpha_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in diffuse_alpha_existing
    )
    assert len(diffuse_alpha_materials) == 2
    for diffuse_alpha_material in diffuse_alpha_materials:
        assert diffuse_alpha_material["mgs4_alpha_render_state"]
        assert not diffuse_alpha_material[
            "mgs4_diffuse_alpha_suppressed"
        ]
        assert diffuse_alpha_material["mgs4_diffuse_alpha_enabled"]
        assert (
            diffuse_alpha_material["mgs4_diffuse_alpha_mode"]
            == "BLENDED"
        )
        assert diffuse_alpha_material.surface_render_method == "BLENDED"
        assert not diffuse_alpha_material.use_transparency_overlap
        principled = next(
            node
            for node in diffuse_alpha_material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        alpha_link = principled.inputs["Alpha"].links[0]
        assert alpha_link.from_node.get("mgs4_role") == "diffuse"
        assert alpha_link.from_socket.name == "Alpha"
        assert principled.inputs["Normal"].is_linked
        assert (
            principled.inputs["Normal"].links[0].from_node.bl_idname
            == "ShaderNodeNormalMap"
        )

    grime_source = diffuse_alpha_model.materials[8]
    assert grime_source.name_hash == 0x001E4C5B
    assert grime_source.flags == 0x00100010
    assert _texture_roles(grime_source) == ("diffuse",)
    grime_texture = diffuse_alpha_model.textures[
        grime_source.texture_indices[0]
    ]
    assert grime_texture.name_hash == 0x00E237D5
    assert _uses_diffuse_alpha(
        grime_source,
        resolver.resolve(grime_texture.name_hash, "common"),
    )
    reduced_grime_model = replace(
        diffuse_alpha_model,
        bones=(),
        groups=(),
        materials=(
            replace(
                grime_source,
                texture_indices=(
                    0,
                    *grime_source.texture_indices[1:],
                ),
            ),
        ),
        textures=(grime_texture,),
        skins=(),
        meshes=(),
    )
    grime_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    grime_import = import_mdn_model(
        bpy.context,
        reduced_grime_model,
        name="MGS4_GRIME_DECAL_ALPHA",
        resolver=resolver,
    )
    assert not grime_import.warnings
    grime_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in grime_existing
    )
    assert len(grime_materials) == 1
    grime_material = grime_materials[0]
    assert grime_material["mgs4_diffuse_alpha_enabled"]
    assert grime_material["mgs4_diffuse_alpha_mode"] == "BLENDED"
    assert not grime_material["mgs4_inferred_detail_decal"]
    assert grime_material.surface_render_method == "BLENDED"
    assert not grime_material.use_transparency_overlap
    grime_principled = next(
        node
        for node in grime_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    grime_alpha_link = grime_principled.inputs["Alpha"].links[0]
    assert grime_alpha_link.from_node.get("mgs4_role") == "diffuse"
    assert grime_alpha_link.from_socket.name == "Alpha"
    diffuse_alpha_families = _material_render_families(
        diffuse_alpha_model
    )
    diffuse_alpha_states = _material_alpha_render_states(
        diffuse_alpha_model
    )
    assert not _material_inferred_tree_masks(
        diffuse_alpha_model,
        diffuse_alpha_families,
        diffuse_alpha_states,
    )[8]

    tree_model = parse_mdn(
        archive.fetch(
            "common/stage/slot/0000001e/cache/00f686ed.mdn"
        )
    )
    assert len(tree_model.meshes) == 1
    assert tree_model.meshes[0].info.flags == 0x00001000
    assert len(tree_model.meshes[0].triangles) == 1080
    assert len(tree_model.materials) == 1
    tree_source = tree_model.materials[0]
    assert tree_source.name_hash == 0x003A99E2
    assert tree_source.flags == 0x00100010
    assert tree_source.texture_count == 1
    tree_diffuse = tree_model.textures[tree_source.texture_indices[0]]
    assert tree_diffuse.name_hash == 0x00DDEC65
    assert (
        resolver.resolve(tree_diffuse.name_hash, "common").format
        == FORMAT_DXT5
    )
    tree_families = _material_render_families(tree_model)
    tree_states = _material_alpha_render_states(tree_model)
    assert _material_inferred_tree_masks(
        tree_model,
        tree_families,
        tree_states,
    ) == (True,)
    tree_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    tree_import = import_mdn_model(
        bpy.context,
        tree_model,
        name="MGS4_TREE_MASK",
        resolver=resolver,
    )
    assert not tree_import.warnings
    tree_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in tree_existing
    )
    assert len(tree_materials) == 1
    tree_material = tree_materials[0]
    assert tree_material["mgs4_alpha_render_state"]
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
    assert tree_alpha_link.from_socket.name == "Alpha"

    stage_geometry_model = parse_mdn(
        (
            data_root
            / "common"
            / "mesh"
            / "pc"
            / "00416fe4.mdn"
        ).read_bytes()
    )
    opaque_alpha_geometry = stage_geometry_model.meshes[45]
    assert opaque_alpha_geometry.info.flags == 0x10000000
    opaque_alpha_source = stage_geometry_model.materials[219]
    assert opaque_alpha_source.name_hash == 0x00F9864C
    assert opaque_alpha_source.flags == 0x00120000
    assert _texture_roles(opaque_alpha_source) == ("diffuse", "normal")
    opaque_alpha_textures = tuple(
        stage_geometry_model.textures[index]
        for index in opaque_alpha_source.texture_indices[
            : opaque_alpha_source.texture_count
        ]
    )
    assert opaque_alpha_textures[0].name_hash == 0x000D1C01
    opaque_alpha_texture_source = resolver.resolve(
        opaque_alpha_textures[0].name_hash,
        "common",
    )
    assert opaque_alpha_texture_source.format == FORMAT_DXT5
    assert _uses_diffuse_alpha(
        opaque_alpha_source,
        opaque_alpha_texture_source,
    )
    opaque_alpha_triangles = tuple(
        triangle
        for triangle, material_index in zip(
            opaque_alpha_geometry.triangles,
            opaque_alpha_geometry.triangle_materials,
        )
        if material_index == 219
    )
    reduced_opaque_alpha_model = replace(
        stage_geometry_model,
        bones=(),
        groups=(),
        materials=(
            replace(
                opaque_alpha_source,
                texture_indices=(
                    0,
                    1,
                    *opaque_alpha_source.texture_indices[2:],
                ),
            ),
        ),
        textures=opaque_alpha_textures,
        skins=(),
        meshes=(
            replace(
                opaque_alpha_geometry,
                triangles=opaque_alpha_triangles,
                triangle_materials=(0,) * len(opaque_alpha_triangles),
            ),
        ),
    )
    opaque_alpha_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    opaque_alpha_import = import_mdn_model(
        bpy.context,
        reduced_opaque_alpha_model,
        name="MGS4_OPAQUE_PACKED_ALPHA",
        resolver=resolver,
    )
    assert not opaque_alpha_import.warnings
    opaque_alpha_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in opaque_alpha_existing
    )
    assert len(opaque_alpha_materials) == 1
    opaque_alpha_material = opaque_alpha_materials[0]
    assert not opaque_alpha_material["mgs4_alpha_render_state"]
    assert opaque_alpha_material["mgs4_diffuse_alpha_suppressed"]
    assert not opaque_alpha_material["mgs4_diffuse_alpha_enabled"]
    assert opaque_alpha_material["mgs4_diffuse_alpha_mode"] == "NONE"
    opaque_alpha_principled = next(
        node
        for node in opaque_alpha_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    assert opaque_alpha_principled.inputs["Alpha"].default_value == 1.0
    assert not opaque_alpha_principled.inputs["Alpha"].is_linked

    handprint_model = parse_mdn(
        (
            data_root
            / "common"
            / "mesh"
            / "pc"
            / "00b267e5.mdn"
        ).read_bytes()
    )
    assert len(handprint_model.materials) == 4
    assert _texture_roles(handprint_model.materials[0]) == (
        "diffuse",
        "normal",
        "specular",
    )
    handprint_sources = handprint_model.materials[1:]
    assert tuple(source.name_hash for source in handprint_sources) == (
        0x00EBB437,
        0x00A1E9BF,
        0x007ACC3D,
    )
    assert all(
        source.flags == 0x00000050
        and source.texture_count == 4
        and source.parameters[0] == (3.0, 0.0, 0.0, 1.0)
        and _texture_roles(source)
        == (
            "secondary_color",
            "normal",
            "diffuse",
            "specular_gradient",
        )
        for source in handprint_sources
    )
    handprint_expected_diffuse = {
        source.name_hash: handprint_model.textures[
            source.texture_indices[2]
        ].name_hash
        for source in handprint_sources
    }
    handprint_expected_secondary = {
        source.name_hash: handprint_model.textures[
            source.texture_indices[0]
        ].name_hash
        for source in handprint_sources
    }
    handprint_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    handprint_import = import_mdn_model(
        bpy.context,
        handprint_model,
        name="MGS4_HANDPRINT_COLOR",
        resolver=resolver,
    )
    assert not handprint_import.warnings
    handprint_materials = {
        material["mgs4_name_hash"] & 0xFFFFFFFF: material
        for material in bpy.data.materials
        if material.as_pointer() not in handprint_existing
    }
    assert len(handprint_materials) == 4
    for source in handprint_sources:
        handprint_material = handprint_materials[source.name_hash]
        assert handprint_material["mgs4_variant_50_color_presentation"]
        assert all(
            abs(component - expected) < 1e-6
            for component, expected in zip(
                handprint_material.diffuse_color,
                (0.8, 0.8, 0.8, 1.0),
            )
        )
        assert not handprint_material["mgs4_vertex_color_enabled"]
        handprint_principled = next(
            node
            for node in handprint_material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        handprint_base_link = handprint_principled.inputs[
            "Base Color"
        ].links[0]
        assert handprint_base_link.from_node.get("mgs4_role") == "diffuse"
        assert (
            handprint_base_link.from_node.get("mgs4_texture_hash")
            & 0xFFFFFFFF
            == handprint_expected_diffuse[source.name_hash]
        )
        assert (
            handprint_base_link.from_node.image.colorspace_settings.name
            == "sRGB"
        )
        secondary_node = next(
            node
            for node in handprint_material.node_tree.nodes
            if node.bl_idname == "ShaderNodeTexImage"
            and node.get("mgs4_role") == "secondary_color"
        )
        assert (
            secondary_node.get("mgs4_texture_hash") & 0xFFFFFFFF
            == handprint_expected_secondary[source.name_hash]
        )
        assert not secondary_node.outputs["Color"].is_linked

    vegetation_model = parse_mdn(
        (
            data_root
            / "common"
            / "mesh"
            / "pc"
            / "00517be4.mdn"
        ).read_bytes()
    )
    vegetation_source = vegetation_model.materials[87]
    assert vegetation_source.name_hash == 0x00EA7750
    assert _texture_roles(vegetation_source) == ("diffuse",)
    vegetation_texture = vegetation_model.textures[
        vegetation_source.texture_indices[0]
    ]
    assert _uses_diffuse_alpha(
        vegetation_source,
        resolver.resolve(vegetation_texture.name_hash, "common"),
    )
    reduced_vegetation_model = replace(
        vegetation_model,
        bones=(),
        groups=(),
        materials=(
            replace(
                vegetation_source,
                texture_indices=(
                    0,
                    *vegetation_source.texture_indices[1:],
                ),
            ),
        ),
        textures=(vegetation_texture,),
        skins=(),
        meshes=(),
    )
    vegetation_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    vegetation_import = import_mdn_model(
        bpy.context,
        reduced_vegetation_model,
        name="MGS4_VEGETATION_ALPHA",
        resolver=resolver,
    )
    assert not vegetation_import.warnings
    vegetation_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in vegetation_existing
    )
    assert len(vegetation_materials) == 1
    vegetation_material = vegetation_materials[0]
    assert vegetation_material["mgs4_diffuse_alpha_enabled"]
    assert vegetation_material["mgs4_diffuse_alpha_mode"] == "MASKED"
    assert vegetation_material.surface_render_method == "DITHERED"
    assert vegetation_material.use_transparency_overlap
    vegetation_principled = next(
        node
        for node in vegetation_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    vegetation_alpha_link = vegetation_principled.inputs["Alpha"].links[0]
    assert vegetation_alpha_link.from_node.get("mgs4_role") == "diffuse"
    assert vegetation_alpha_link.from_socket.name == "Alpha"

    planar_variant_50_model = parse_mdn(
        archive.fetch(
            "common/stage/slot/00000001/cache/0094b09f.mdn"
        )
    )
    assert len(planar_variant_50_model.meshes) == 1
    assert planar_variant_50_model.meshes[0].info.flags == 0x80001000
    assert planar_variant_50_model.materials[0].flags == 0x00100050
    planar_variant_50_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    planar_variant_50_import = import_mdn_model(
        bpy.context,
        planar_variant_50_model,
        name="MGS4_PLANAR_VARIANT_50",
        resolver=resolver,
    )
    assert not planar_variant_50_import.warnings
    planar_variant_50_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in planar_variant_50_existing
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

    ww_archive = VpakArchive(
        data_root / "ww" / "stage" / "stage_data_compressed.1.pak"
    )
    plant_model = parse_mdn(
        ww_archive.fetch(
            "ww/stage/slot/00008d4d/cache/0078b3f0.mdn"
        )
    )
    assert len(plant_model.meshes) == 1
    assert plant_model.meshes[0].info.flags == 0x80001000
    assert len(plant_model.materials) == 1
    plant_source = plant_model.materials[0]
    assert plant_source.name_hash == 0x0027F049
    assert plant_source.flags == 0x00100050
    assert plant_source.texture_count == 3
    plant_diffuse = plant_model.textures[plant_source.texture_indices[0]]
    assert plant_diffuse.name_hash == 0x00BE7D8F
    assert (
        resolver.resolve(plant_diffuse.name_hash, "ww").format
        == FORMAT_DXT5
    )
    plant_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    plant_import = import_mdn_model(
        bpy.context,
        plant_model,
        name="MGS4_VARIANT_50_VEGETATION",
        resolver=resolver,
        preferred_region="ww",
    )
    assert not plant_import.warnings
    plant_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in plant_existing
    )
    assert len(plant_materials) == 1
    plant_material = plant_materials[0]
    assert plant_material["mgs4_alpha_render_state"]
    assert plant_material["mgs4_inferred_variant_50_vegetation"]
    assert plant_material["mgs4_diffuse_alpha_enabled"]
    assert plant_material["mgs4_diffuse_alpha_mode"] == "MASKED"
    assert plant_material.surface_render_method == "DITHERED"
    assert plant_material.use_transparency_overlap
    plant_principled = next(
        node
        for node in plant_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    plant_alpha_link = plant_principled.inputs["Alpha"].links[0]
    assert plant_alpha_link.from_node.get("mgs4_role") == "diffuse"
    assert (
        plant_alpha_link.from_node.get("mgs4_texture_hash") & 0xFFFFFFFF
        == 0x00BE7D8F
    )
    assert plant_alpha_link.from_socket.name == "Alpha"

    grass_source = vegetation_model.materials[159]
    assert grass_source.name_hash == 0x000DA0C9
    grass_textures = tuple(
        vegetation_model.textures[texture_index]
        for texture_index in grass_source.texture_indices[
            : grass_source.texture_count
        ]
    )
    grass_geometry_source = vegetation_model.meshes[31]
    assert grass_geometry_source.info.flags == 0x10001000
    grass_triangles = tuple(
        triangle
        for triangle, material_index in zip(
            grass_geometry_source.triangles,
            grass_geometry_source.triangle_materials,
        )
        if material_index == 159
    )
    grass_opaque_geometry = vegetation_model.meshes[19]
    assert grass_opaque_geometry.info.flags == 0x10000000
    grass_opaque_triangles = tuple(
        triangle
        for triangle, material_index in zip(
            grass_opaque_geometry.triangles,
            grass_opaque_geometry.triangle_materials,
        )
        if material_index == 159
    )
    assert grass_opaque_triangles
    reduced_grass_model = replace(
        vegetation_model,
        bones=(),
        groups=(),
        materials=(
            replace(
                grass_source,
                texture_indices=(
                    0,
                    1,
                    *grass_source.texture_indices[2:],
                ),
            ),
        ),
        textures=grass_textures,
        skins=(),
        meshes=(
            replace(
                grass_opaque_geometry,
                triangles=grass_opaque_triangles,
                triangle_materials=(0,) * len(grass_opaque_triangles),
            ),
            replace(
                grass_geometry_source,
                triangles=grass_triangles,
                triangle_materials=(0,) * len(grass_triangles),
            ),
        ),
    )
    grass_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    grass_import = import_mdn_model(
        bpy.context,
        reduced_grass_model,
        name="MGS4_GRASS_ALPHA",
        resolver=resolver,
    )
    assert not grass_import.warnings
    grass_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in grass_existing
    )
    assert len(grass_materials) == 1
    grass_material = grass_materials[0]
    assert grass_material["mgs4_alpha_render_state"]
    assert not grass_material["mgs4_diffuse_alpha_suppressed"]
    assert grass_material["mgs4_diffuse_alpha_enabled"]
    assert grass_material["mgs4_diffuse_alpha_mode"] == "MASKED"
    assert grass_material.surface_render_method == "DITHERED"
    assert grass_material.use_transparency_overlap
    grass_principled = next(
        node
        for node in grass_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    grass_alpha_link = grass_principled.inputs["Alpha"].links[0]
    assert grass_alpha_link.from_node.get("mgs4_role") == "diffuse"
    assert grass_alpha_link.from_socket.name == "Alpha"

    reflective_model = parse_mdn(
        (
            data_root
            / "common"
            / "mesh"
            / "pc"
            / "00c3a92a.mdn"
        ).read_bytes()
    )
    reflective_geometry = reflective_model.meshes[12]
    assert reflective_geometry.info.flags == 0x10001110
    assert set(reflective_geometry.triangle_materials) == {44, 45}
    reflective_sources = tuple(
        reflective_model.materials[index] for index in (44, 45)
    )
    assert tuple(source.name_hash for source in reflective_sources) == (
        0x004E2EE5,
        0x0048747B,
    )
    assert all(
        source.flags == 0x00110000
        and source.texture_count == 4
        and _texture_roles(source)
        == ("diffuse", "normal", "specular", "environment")
        for source in reflective_sources
    )
    opaque_reflective_source = reflective_model.materials[42]
    assert opaque_reflective_source.flags == 0x00110000
    assert opaque_reflective_source.texture_count == 4
    opaque_reflective_texture = reflective_model.textures[
        opaque_reflective_source.texture_indices[0]
    ]
    opaque_texture_source = resolver.resolve(
        opaque_reflective_texture.name_hash,
        "common",
    )
    assert opaque_texture_source.format == FORMAT_DXT1
    assert (
        _diffuse_alpha_mode(
            opaque_reflective_source,
            opaque_texture_source,
            masked=True,
        )
        is None
    )
    reflective_textures = []
    reduced_reflective_sources = []
    for source in reflective_sources:
        first_texture_index = len(reflective_textures)
        active_textures = tuple(
            reflective_model.textures[index]
            for index in source.texture_indices[: source.texture_count]
        )
        reflective_textures.extend(active_textures)
        reduced_reflective_sources.append(
            replace(
                source,
                texture_indices=(
                    *range(
                        first_texture_index,
                        first_texture_index + source.texture_count,
                    ),
                    *source.texture_indices[source.texture_count :],
                ),
            )
        )
    reduced_reflective_model = replace(
        reflective_model,
        bones=(),
        groups=(),
        materials=tuple(reduced_reflective_sources),
        textures=tuple(reflective_textures),
        skins=(),
        meshes=(
            replace(
                reflective_geometry,
                triangle_materials=tuple(
                    0 if index == 44 else 1
                    for index in reflective_geometry.triangle_materials
                ),
            ),
        ),
    )
    reflective_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    reflective_import = import_mdn_model(
        bpy.context,
        reduced_reflective_model,
        name="MGS4_REFLECTIVE_GLASS",
        resolver=resolver,
    )
    assert not reflective_import.warnings
    reflective_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in reflective_existing
    )
    assert len(reflective_materials) == 2
    for reflective_material in reflective_materials:
        assert reflective_material["mgs4_diffuse_alpha_enabled"]
        assert reflective_material["mgs4_diffuse_alpha_mode"] == "BLENDED"
        assert reflective_material.surface_render_method == "BLENDED"
        assert not reflective_material.use_transparency_overlap
        reflective_principled = next(
            node
            for node in reflective_material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        reflective_alpha_link = reflective_principled.inputs["Alpha"].links[0]
        assert reflective_alpha_link.from_node.get("mgs4_role") == "diffuse"
        assert reflective_alpha_link.from_socket.name == "Alpha"

    glasses_archive = VpakArchive(
        data_root
        / "common"
        / "stage"
        / "stage_data_compressed.2.pak"
    )
    glasses_model = parse_mdn(
        glasses_archive.fetch(
            "common/stage/stage05/s20a00l/cache/0033f269.mdn"
        )
    )
    assert len(glasses_model.meshes) == 3
    assert glasses_model.meshes[1].info.flags == 0x05011010
    assert set(glasses_model.meshes[1].triangle_materials) == {2}
    glasses_source = glasses_model.materials[2]
    assert glasses_source.name_hash == 0x00F31905
    assert glasses_source.flags == 0x000000A5
    assert glasses_source.texture_count == 2
    assert _texture_roles(glasses_source) == ("diffuse", "environment")
    glasses_translucency = _variant_a5_translucency(glasses_source)
    assert glasses_translucency is not None
    assert abs(glasses_translucency[0] - 0.39990234375) < 1e-8
    assert abs(glasses_translucency[1] - 0.0999755859375) < 1e-8
    boundary_parameters = list(glasses_source.parameters)
    boundary_parameters[2] = (*boundary_parameters[2][:3], 1.0)
    assert (
        _variant_a5_translucency(
            replace(
                glasses_source,
                parameters=tuple(boundary_parameters),
            )
        )
        is None
    )
    boundary_parameters[2] = (*boundary_parameters[2][:3], 0.0)
    assert (
        _variant_a5_translucency(
            replace(
                glasses_source,
                parameters=tuple(boundary_parameters),
            )
        )
        is None
    )
    glasses_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    glasses_import = import_mdn_model(
        bpy.context,
        glasses_model,
        name="MGS4_VARIANT_A5_GLASSES",
        resolver=resolver,
    )
    assert not glasses_import.warnings
    glasses_material = next(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in glasses_existing
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
    assert not glasses_material["mgs4_diffuse_alpha_enabled"]
    assert glasses_material["mgs4_diffuse_alpha_mode"] == "NONE"
    assert glasses_material.surface_render_method == "BLENDED"
    assert not glasses_material.use_transparency_overlap
    assert abs(glasses_material.diffuse_color[3] - 0.39990234375) < 1e-6
    assert abs(glasses_material.roughness - 0.0999755859375) < 1e-8
    glasses_principled = next(
        node
        for node in glasses_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    assert abs(
        glasses_principled.inputs["Roughness"].default_value
        - 0.0999755859375
    ) < 1e-8
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
    glasses_base_link = glasses_principled.inputs["Base Color"].links[0]
    assert glasses_base_link.from_node.get("mgs4_role") == "diffuse"
    assert (
        glasses_base_link.from_node.get("mgs4_texture_hash") & 0xFFFFFFFF
        == 0x00EACE51
    )
    glasses_environment = next(
        node
        for node in glasses_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeTexImage"
        and node.get("mgs4_role") == "environment"
    )
    assert (
        glasses_environment.get("mgs4_texture_hash") & 0xFFFFFFFF
        == 0x0019FEA8
    )
    assert not glasses_environment.outputs["Color"].is_linked

    hair_blood_model = parse_mdn(
        glasses_archive.fetch(
            "common/stage/stage05/s05a50l/cache/00846b09.mdn"
        )
    )
    assert len(hair_blood_model.meshes) == 23
    expected_mesh_materials = {
        9: (0x05011010, 32),
        10: (0x05011010, 32),
        11: (0x05020010, 27),
        12: (0x05021010, 28),
        22: (0x050A1010, 30),
    }
    for mesh_index, (mesh_flags, material_index) in (
        expected_mesh_materials.items()
    ):
        source_mesh = hair_blood_model.meshes[mesh_index]
        assert source_mesh.info.flags == mesh_flags
        assert set(source_mesh.triangle_materials) == {material_index}
    expected_source_materials = {
        27: (0x006FF975, 0x000000A3, 3, 0x0028E963),
        28: (0x009FDFB9, 0x00000003, 4, 0x00D42573),
        30: (0x00EDE4E4, 0x00000060, 5, 0x0012860F),
        32: (0x001019E9, 0x00000060, 5, 0x007DAC4D),
    }
    for material_index, (
        material_hash,
        material_flags,
        texture_count,
        diffuse_hash,
    ) in expected_source_materials.items():
        source_material = hair_blood_model.materials[material_index]
        assert source_material.name_hash == material_hash
        assert source_material.flags == material_flags
        assert source_material.texture_count == texture_count
        diffuse_texture = hair_blood_model.textures[
            source_material.texture_indices[0]
        ]
        assert diffuse_texture.name_hash == diffuse_hash
        assert (
            resolver.resolve(diffuse_hash, "common").format
            == FORMAT_DXT5
        )
    hair_blood_families = _material_render_families(hair_blood_model)
    hair_blood_states = _material_alpha_render_states(hair_blood_model)
    assert {
        index
        for index, inferred in enumerate(
            _material_inferred_hair_masks(
                hair_blood_model,
                hair_blood_families,
                hair_blood_states,
            )
        )
        if inferred
    } == {5, 6, 25, 26, 27, 28, 29}
    assert {
        index
        for index, inferred in enumerate(
            _material_inferred_scripted_overlays(
                hair_blood_model,
                hair_blood_families,
                hair_blood_states,
            )
        )
        if inferred
    } == {30, 32}
    no_alpha_state_model = replace(
        hair_blood_model,
        meshes=tuple(
            replace(
                source_mesh,
                info=replace(
                    source_mesh.info,
                    flags=source_mesh.info.flags & ~0x00001000,
                ),
            )
            for source_mesh in hair_blood_model.meshes
        ),
    )
    no_alpha_state_families = _material_render_families(
        no_alpha_state_model
    )
    no_alpha_states = _material_alpha_render_states(no_alpha_state_model)
    assert {
        index
        for index, inferred in enumerate(
            _material_inferred_hair_masks(
                no_alpha_state_model,
                no_alpha_state_families,
                no_alpha_states,
            )
        )
        if inferred
    } == {5, 6, 25, 26, 27, 29}
    assert not any(
        _material_inferred_scripted_overlays(
            no_alpha_state_model,
            no_alpha_state_families,
            no_alpha_states,
        )
    )
    a3_source = hair_blood_model.materials[27]
    a3_dxt1_texture = hair_blood_model.textures[
        a3_source.texture_indices[1]
    ]
    assert (
        resolver.resolve(a3_dxt1_texture.name_hash, "common").format
        == FORMAT_DXT1
    )
    assert (
        _diffuse_alpha_mode(
            a3_source,
            resolver.resolve(a3_dxt1_texture.name_hash, "common"),
            inferred_hair_mask=True,
        )
        is None
    )

    reduced_material_indices = (27, 28, 30, 32)
    reduced_texture_indices = tuple(
        sorted(
            {
                texture_index
                for material_index in reduced_material_indices
                for texture_index in hair_blood_model.materials[
                    material_index
                ].texture_indices[
                    : hair_blood_model.materials[
                        material_index
                    ].texture_count
                ]
            }
        )
    )
    texture_index_map = {
        source_index: reduced_index
        for reduced_index, source_index in enumerate(
            reduced_texture_indices
        )
    }
    material_index_map = {
        source_index: reduced_index
        for reduced_index, source_index in enumerate(
            reduced_material_indices
        )
    }
    reduced_hair_blood_model = replace(
        hair_blood_model,
        bones=(),
        groups=(),
        materials=tuple(
            replace(
                hair_blood_model.materials[source_index],
                texture_indices=tuple(
                    texture_index_map.get(texture_index, 0)
                    for texture_index in hair_blood_model.materials[
                        source_index
                    ].texture_indices
                ),
            )
            for source_index in reduced_material_indices
        ),
        textures=tuple(
            hair_blood_model.textures[source_index]
            for source_index in reduced_texture_indices
        ),
        skins=(),
        meshes=tuple(
            replace(
                hair_blood_model.meshes[source_index],
                triangle_materials=tuple(
                    material_index_map[material_index]
                    for material_index in hair_blood_model.meshes[
                        source_index
                    ].triangle_materials
                ),
            )
            for source_index in (9, 10, 11, 12, 22)
        ),
    )
    hair_blood_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    hair_blood_import = import_mdn_model(
        bpy.context,
        reduced_hair_blood_model,
        name="MGS4_HAIR_BLOOD_MASKS",
        resolver=resolver,
    )
    assert not hair_blood_import.warnings
    hair_blood_materials = {
        material["mgs4_name_hash"] & 0xFFFFFFFF: material
        for material in bpy.data.materials
        if material.as_pointer() not in hair_blood_existing
        and material.get("mgs4_name_hash") is not None
    }
    assert set(hair_blood_materials) == {
        values[0] for values in expected_source_materials.values()
    }
    expected_presentations = {
        0x006FF975: ("MASKED", 0x0028E963, True),
        0x009FDFB9: ("MASKED", 0x00D42573, True),
        0x00EDE4E4: ("BLENDED", 0x0012860F, False),
        0x001019E9: ("BLENDED", 0x007DAC4D, False),
    }
    for material_hash, (
        alpha_mode,
        diffuse_hash,
        hair_mask,
    ) in expected_presentations.items():
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

    detail_geometry_source = vegetation_model.meshes[48]
    assert detail_geometry_source.info.flags >> 24 == 0x15
    assert set(detail_geometry_source.triangle_materials) == {346, 347}
    detail_source_indices = (346, 347)
    detail_sources = tuple(
        vegetation_model.materials[index]
        for index in detail_source_indices
    )
    assert tuple(source.name_hash for source in detail_sources) == (
        0x00E335D2,
        0x00E335D4,
    )
    detail_texture_indices = tuple(
        dict.fromkeys(
            texture_index
            for source in detail_sources
            for texture_index in source.texture_indices[
                : source.texture_count
            ]
        )
    )
    detail_texture_map = {
        texture_index: local_index
        for local_index, texture_index in enumerate(detail_texture_indices)
    }
    reduced_detail_model = replace(
        vegetation_model,
        bones=(),
        groups=(),
        materials=tuple(
            replace(
                source,
                texture_indices=tuple(
                    detail_texture_map[texture_index]
                    if slot < source.texture_count
                    else texture_index
                    for slot, texture_index in enumerate(
                        source.texture_indices
                    )
                ),
            )
            for source in detail_sources
        ),
        textures=tuple(
            vegetation_model.textures[index]
            for index in detail_texture_indices
        ),
        skins=(),
        meshes=(
            replace(
                detail_geometry_source,
                triangle_materials=tuple(
                    detail_source_indices.index(material_index)
                    for material_index in (
                        detail_geometry_source.triangle_materials
                    )
                ),
            ),
        ),
    )
    detail_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    detail_import = import_mdn_model(
        bpy.context,
        reduced_detail_model,
        name="MGS4_DETAIL_DECAL_ALPHA",
        resolver=resolver,
    )
    assert not detail_import.warnings
    detail_materials = {
        material["mgs4_name_hash"]: material
        for material in bpy.data.materials
        if material.as_pointer() not in detail_existing
    }
    assert set(detail_materials) == {0x00E335D2, 0x00E335D4}
    for detail_material in detail_materials.values():
        assert detail_material["mgs4_inferred_detail_decal"]
        assert detail_material["mgs4_vertex_alpha_available"]
        assert detail_material["mgs4_vertex_alpha_enabled"]
        assert detail_material.surface_render_method == "BLENDED"
        assert not detail_material.use_transparency_overlap

    textured_detail = detail_materials[0x00E335D2]
    assert textured_detail["mgs4_diffuse_alpha_enabled"]
    assert textured_detail["mgs4_diffuse_alpha_mode"] == "BLENDED"
    textured_principled = next(
        node
        for node in textured_detail.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    textured_alpha = textured_principled.inputs["Alpha"].links[0].from_node
    assert textured_alpha.name == "MGS4_Vertex_Alpha_Multiply"
    assert {
        link.from_node.bl_idname
        for input_socket in textured_alpha.inputs
        for link in input_socket.links
    } == {"ShaderNodeTexImage", "ShaderNodeVertexColor"}

    vertex_only_detail = detail_materials[0x00E335D4]
    assert not vertex_only_detail["mgs4_diffuse_alpha_enabled"]
    assert vertex_only_detail["mgs4_diffuse_alpha_mode"] == "NONE"
    vertex_only_principled = next(
        node
        for node in vertex_only_detail.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    vertex_only_alpha = vertex_only_principled.inputs["Alpha"].links[0]
    assert vertex_only_alpha.from_node.bl_idname == "ShaderNodeVertexColor"
    assert vertex_only_alpha.from_socket.name == "Alpha"

    texture_hash = model.textures[0].name_hash
    image_path, texture_source = resolver.materialize(texture_hash, "common")
    color_image = _load_texture_image(
        image_path,
        texture_source,
        texture_hash,
        "diffuse",
    )
    data_image = _load_texture_image(
        image_path,
        texture_source,
        texture_hash,
        "normal",
    )
    assert color_image != data_image
    assert color_image.colorspace_settings.name == "sRGB"
    assert data_image.colorspace_settings.name == "Non-Color"
    assert color_image["mgs4_interpretation"] == "COLOR"
    assert data_image["mgs4_interpretation"] == "DATA"

    character_model = parse_mdn(
        archive.fetch("common/stage/stage01/s01a00l/cache/00e46845.mdn")
    )
    character_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    character = import_mdn_model(
        bpy.context,
        character_model,
        name="MGS4_CHARACTER_MATERIALS",
        resolver=resolver,
    )
    assert not character.warnings
    character_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in character_existing
    )
    assert character_materials
    assert all(
        not material["mgs4_vertex_color_available"]
        and not material["mgs4_vertex_color_enabled"]
        and material["mgs4_vertex_alpha_available"]
        and not material["mgs4_vertex_alpha_enabled"]
        for material in character_materials
    )
    character_diffuse_materials = [
        material
        for material in character_materials
        if any(
            node.get("mgs4_role") == "diffuse"
            for node in material.node_tree.nodes
        )
    ]
    assert character_diffuse_materials
    for character_material in character_diffuse_materials:
        character_principled = next(
            node
            for node in character_material.node_tree.nodes
            if node.bl_idname == "ShaderNodeBsdfPrincipled"
        )
        assert (
            character_principled.inputs["Base Color"]
            .links[0]
            .from_node.get("mgs4_role")
            == "diffuse"
        )

    layer_model = parse_mdn(
        archive.fetch("common/stage/slot/00000001/cache/0006860b.mdn")
    )
    layer_material_index = next(
        index
        for index, source_material in enumerate(layer_model.materials)
        if "layer" in _texture_roles(source_material)
    )

    conservative_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    conservative = import_mdn_model(
        bpy.context,
        layer_model,
        name="MGS4_LAYER_CONSERVATIVE",
        resolver=resolver,
    )
    assert not conservative.warnings
    conservative_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in conservative_existing
    )
    layer_material_hash = layer_model.materials[
        layer_material_index
    ].name_hash
    conservative_material = next(
        material
        for material in conservative_materials
        if material["mgs4_name_hash"] == layer_material_hash
    )
    conservative_principled = next(
        node
        for node in conservative_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    conservative_multiply = conservative_principled.inputs[
        "Base Color"
    ].links[0].from_node
    assert conservative_multiply.name == "MGS4_Vertex_Color_Multiply"
    assert conservative_multiply.inputs[1].links[0].from_node[
        "mgs4_role"
    ] == "diffuse"
    assert conservative_material["mgs4_vertex_color_enabled"]
    assert not conservative_material["mgs4_vertex_alpha_enabled"]
    assert not conservative_principled.inputs["Alpha"].is_linked
    assert any(
        node.get("mgs4_role") == "layer"
        for node in conservative_material.node_tree.nodes
    )

    preview_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    preview = import_mdn_model(
        bpy.context,
        layer_model,
        name="MGS4_LAYER_PREVIEW",
        resolver=resolver,
        material_options=MaterialImportOptions(
            layer_mode=LAYER_MODE_PREVIEW,
            use_vertex_alpha=True,
        ),
    )
    assert not preview.warnings
    preview_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in preview_existing
    )
    preview_material = next(
        material
        for material in preview_materials
        if material["mgs4_name_hash"] == layer_material_hash
    )
    preview_principled = next(
        node
        for node in preview_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    preview_multiply = preview_principled.inputs[
        "Base Color"
    ].links[0].from_node
    preview_source = preview_multiply.inputs[1].links[0].from_node
    assert preview_source["mgs4_role"] == "layer"
    assert preview_source["mgs4_layer_preview_source"]
    assert preview_material["mgs4_layer_mode"] == LAYER_MODE_PREVIEW
    assert preview_material["mgs4_vertex_color_enabled"]
    assert preview_material["mgs4_vertex_alpha_enabled"]
    assert preview_principled.inputs["Alpha"].is_linked
    assert (
        preview_principled.inputs["Alpha"].links[0].from_node.bl_idname
        == "ShaderNodeVertexColor"
    )
    assert preview_material.surface_render_method == "BLENDED"

    class MissingTextureResolver:
        def materialize(self, texture_hash, preferred_region):
            raise MGS4Error(
                f"Test texture {texture_hash:08X} missing in {preferred_region}"
            )

    missing_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    missing = import_mdn_model(
        bpy.context,
        model,
        name="MGS4_MISSING_CHECKERS",
        resolver=MissingTextureResolver(),
    )
    missing_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in missing_existing
    )
    missing_nodes = [
        node
        for material in missing_materials
        for node in material.node_tree.nodes
        if (
            node.bl_idname == "ShaderNodeTexImage"
            and node.get("mgs4_missing_texture")
        )
    ]
    assert missing.warnings
    assert missing_nodes
    assert all(node.image["mgs4_missing_texture"] for node in missing_nodes)
    assert sum(
        material["mgs4_missing_texture_count"]
        for material in missing_materials
    ) > 0

    skipped_existing = {
        material.as_pointer() for material in bpy.data.materials
    }
    skipped = import_mdn_model(
        bpy.context,
        model,
        name="MGS4_MISSING_SKIPPED",
        resolver=MissingTextureResolver(),
        material_options=MaterialImportOptions(
            highlight_missing_textures=False,
        ),
    )
    skipped_materials = tuple(
        material
        for material in bpy.data.materials
        if material.as_pointer() not in skipped_existing
    )
    assert skipped.warnings
    assert not any(
        node.get("mgs4_missing_texture")
        for material in skipped_materials
        for node in material.node_tree.nodes
    )
    assert sum(
        material["mgs4_missing_texture_count"]
        for material in skipped_materials
    ) > 0

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

    chapter_catalog = GameCatalog(install_root)
    chapter_model_asset = next(
        asset
        for asset in chapter_catalog.search(
            MODEL,
            "00aee46b.mdn",
            limit=0,
        )
        if asset.virtual_path
        == "common/stage/stage03/s03a00l/cache/00aee46b.mdn"
    )
    chapter_model = parse_mdn(chapter_catalog.read(chapter_model_asset))
    chapter_motion_asset = chapter_catalog.default_motion_for_model(
        chapter_model_asset,
        tuple(bone.name_hash for bone in chapter_model.bones),
    )
    assert chapter_motion_asset is not None
    assert (
        chapter_motion_asset.virtual_path
        == "common/stage/stage03/s03a35l/cache/0084802f.mtar"
    )
    chapter_import = import_mdn_model(
        bpy.context,
        chapter_model,
        name="MGS4_CHAPTER_MOTION_FALLBACK",
        resolver=None,
    )
    assert chapter_import.armature is not None
    chapter_motion = parse_mtar(
        chapter_catalog.read(chapter_motion_asset),
        max_clips=3,
    )
    chapter_animated = import_motion_archive(
        bpy.context,
        chapter_import.armature,
        chapter_motion,
        max_actions=3,
        archive_name=Path(chapter_motion_asset.virtual_path).name,
    )
    assert len(chapter_animated.actions) == 3
    assert not chapter_animated.missing_bones
    assert chapter_animated.actions[0].name == "0084802F_0000_0048326B"
    chapter_mesh_object = max(
        (
            scene_object
            for scene_object in chapter_import.objects
            if scene_object.type == "MESH"
        ),
        key=lambda scene_object: len(scene_object.data.vertices),
    )

    def evaluated_vertices(scene_object):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = scene_object.evaluated_get(depsgraph)
        evaluated_mesh = evaluated.to_mesh()
        try:
            return tuple(
                tuple(vertex.co)
                for vertex in evaluated_mesh.vertices
            )
        finally:
            evaluated.to_mesh_clear()

    bpy.context.scene.frame_set(1)
    chapter_vertices_start = evaluated_vertices(chapter_mesh_object)
    bpy.context.scene.frame_set(60)
    chapter_vertices_middle = evaluated_vertices(chapter_mesh_object)
    chapter_deformation = max(
        abs(start_component - middle_component)
        for start_vertex, middle_vertex in zip(
            chapter_vertices_start,
            chapter_vertices_middle,
        )
        for start_component, middle_component in zip(
            start_vertex,
            middle_vertex,
        )
    )
    assert chapter_deformation > 0.01

print(
    "MGS4_BLENDER_SMOKE_OK",
    len(imported.objects),
    len(animated.actions),
    len(bpy.data.materials),
    len(bpy.data.images),
)
