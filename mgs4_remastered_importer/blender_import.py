from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

import bpy
from mathutils import Euler, Matrix, Vector
import numpy as np

from .errors import MGS4Error
from .formats.common import hash_name
from .formats.mdn import MdnMaterial, MdnModel
from .formats.mtar import MotionArchive, QuaternionKey
from .formats.texture import TextureResolver


@dataclass(frozen=True, slots=True)
class ModelImportResult:
    collection: bpy.types.Collection
    armature: bpy.types.Object | None
    objects: tuple[bpy.types.Object, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MotionImportResult:
    actions: tuple[bpy.types.Action, ...]
    skipped_clips: int
    missing_bones: tuple[str, ...]


def _game_vector(
    value: tuple[float, float, float] | tuple[float, float, float, float],
    scale: float = 1.0,
) -> tuple[float, float, float]:
    return value[0] * scale, -value[2] * scale, value[1] * scale


def _game_vectors(
    values: Iterable[tuple[float, ...]],
    scale: float = 1.0,
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if source.size == 0:
        return np.empty((0, 3), dtype=np.float32)
    if source.ndim != 2 or source.shape[1] < 3:
        raise ValueError("MGS4 vectors must have at least three components")
    converted = np.empty((source.shape[0], 3), dtype=np.float32)
    converted[:, 0] = source[:, 0] * scale
    converted[:, 1] = -source[:, 2] * scale
    converted[:, 2] = source[:, 1] * scale
    return converted


def game_transform_matrix(
    position: tuple[float, float, float] | None,
    rotation: tuple[float, float, float] | None,
    scale: float = 0.001,
) -> Matrix:
    translation = Matrix.Translation(
        Vector(_game_vector(position, scale)) if position is not None else Vector()
    )
    if rotation is None:
        return translation
    conversion = Matrix(
        (
            (1.0, 0.0, 0.0),
            (0.0, 0.0, -1.0),
            (0.0, 1.0, 0.0),
        )
    )
    game_rotation = Euler(rotation, "XYZ").to_matrix()
    blender_rotation = conversion @ game_rotation @ conversion.transposed()
    return translation @ blender_rotation.to_4x4()


def _safe_diffuse_color(
    value: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if not all(math.isfinite(component) for component in value):
        return 0.8, 0.8, 0.8, 1.0
    if max(abs(component) for component in value) < 1e-8:
        return 0.8, 0.8, 0.8, 1.0
    alpha = value[3] if 0.0 <= value[3] <= 1.0 else 1.0
    return (
        max(0.0, min(1.0, value[0])),
        max(0.0, min(1.0, value[1])),
        max(0.0, min(1.0, value[2])),
        alpha,
    )


def _legacy_specular(
    value: tuple[float, float, float, float],
) -> tuple[float, float]:
    if not all(math.isfinite(component) for component in value):
        return 0.25, 0.5
    red, green, blue = (
        max(0.0, min(1.0, component)) for component in value[:3]
    )
    luminance = red * 0.2126 + green * 0.7152 + blue * 0.0722
    specular_level = min(0.25, luminance * 0.12)
    shininess = max(0.0, value[3])
    # Principled exposes perceptual roughness, whose square is the GGX alpha.
    roughness = 1.6 * (
        (2.0 / (shininess + 2.0)) ** 0.25
        if shininess > 0.0
        else 1.0
    )
    return specular_level, max(0.45, min(1.0, roughness))


def _set_transparent(material: bpy.types.Material) -> None:
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
        if hasattr(material, "use_transparency_overlap"):
            material.use_transparency_overlap = False
    elif hasattr(material, "blend_method"):
        material.blend_method = "BLEND"


def _texture_roles(material: MdnMaterial) -> tuple[str, ...]:
    shader = material.flags & 0xFF
    no_specular = bool((material.flags >> 17) & 1)
    has_environment = bool((material.flags >> 20) & 1)
    no_normal = shader in {0x30, 0x31, 0xA3, 0xA5, 0xA8, 0xE0, 0xE1}
    no_specular = no_specular or shader in {
        0x05,
        0xA5,
        0xA8,
        0xAB,
        0xE0,
        0xE1,
        0xF3,
    }
    roles: list[str] = []

    if shader == 0xA6:
        roles.append("opacity")
        if not no_specular:
            roles.append("specular")
        if not no_normal:
            roles.append("normal")
        if has_environment:
            roles.append("environment")
    elif shader == 0x00 and no_specular and has_environment:
        roles.extend(("diffuse", "unknown"))
    else:
        roles.append("diffuse")
        if not no_normal:
            roles.append("normal")
        if not no_specular:
            roles.append("specular")

        extra_roles: dict[int, tuple[str, ...]] = {
        0x02: ("detail_normal",),
        0x03: ("layer",),
        0x05: ("layer",),
        0x06: ("detail_normal", "layer"),
        0x0A: ("layer", "detail_normal"),
        0x0C: ("layer",),
        0x10: ("environment",),
        0x13: ("layer", "unknown", "unknown"),
        0x14: ("environment", "layer"),
        0x21: ("detail_normal",),
        0x24: ("detail_normal", "layer"),
        0x2A: ("incidence", "layer", "detail_normal"),
        0x30: ("detail_normal",),
        0x31: ("detail_normal",),
        0x41: ("incidence", "unknown"),
        0x50: ("specular_gradient",),
        0x51: ("specular_gradient",),
        0x52: ("specular_gradient", "environment"),
        0x60: ("specular_gradient", "incidence"),
        0x61: ("specular_gradient", "incidence"),
        0xA0: ("unknown", "unknown", "unknown"),
        0xA2: ("environment",),
        0xA3: ("layer",),
        0xA4: ("detail_normal", "layer"),
        0xA5: ("environment",),
        0xAB: ("environment", "specular_gradient"),
        0xF0: (
            "specular_gradient",
            "layer",
            "layer_normal",
            "incidence",
            "unknown",
        ),
        0xF1: ("specular_gradient", "incidence", "unknown"),
        0xF5: ("incidence", "layer", "detail_normal", "detail_normal"),
        }
        roles.extend(extra_roles.get(shader, ()))
        if has_environment and shader not in {0x30, 0x31}:
            roles.append("environment")

    while len(roles) < material.texture_count:
        roles.append("unknown")
    return tuple(roles[: material.texture_count])


def _new_texture_nodes(
    material: bpy.types.Material,
    model: MdnModel,
    source_material: MdnMaterial,
    resolver: TextureResolver,
    preferred_region: str,
    specular_level: float,
    warnings: list[str],
) -> None:
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = next(
        node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    roles = _texture_roles(source_material)
    use_uv_1 = bool((source_material.flags >> 16) & 1)

    for slot, role in enumerate(roles):
        texture_index = source_material.texture_indices[slot]
        if texture_index == 0xFFFFFFFF or texture_index >= len(model.textures):
            warnings.append(
                f"Material {source_material.name_hash:08X} has invalid "
                f"texture index {texture_index}"
            )
            continue
        texture = model.textures[texture_index]
        try:
            image_path, texture_source = resolver.materialize(
                texture.name_hash, preferred_region
            )
        except MGS4Error as exc:
            warnings.append(str(exc))
            continue

        image_name = (
            f"MGS4_{texture.name_hash:08X}_{texture_source.width}x"
            f"{texture_source.height}"
        )
        image = bpy.data.images.get(image_name)
        if image is None:
            image = bpy.data.images.load(str(image_path), check_existing=True)
            image.name = image_name
        if role != "diffuse":
            image.colorspace_settings.name = "Non-Color"

        image_node = nodes.new("ShaderNodeTexImage")
        image_node.name = f"{role}_{texture.name_hash:08X}"
        image_node.label = f"{role.replace('_', ' ').title()} {texture.name_hash:08X}"
        image_node.image = image
        image_node.location = (-500.0, 360.0 - slot * 240.0)
        image_node["mgs4_texture_hash"] = texture.name_hash
        image_node["mgs4_texture_flags"] = texture.flags
        image_node["mgs4_role"] = role

        uv_node = nodes.new("ShaderNodeUVMap")
        uv_node.uv_map = "UV1" if use_uv_1 else "UV0"
        uv_node.location = (-900.0, image_node.location.y)
        mapping = nodes.new("ShaderNodeMapping")
        mapping.location = (-700.0, image_node.location.y)
        mapping.inputs["Scale"].default_value[0] = texture.scale_u
        mapping.inputs["Scale"].default_value[1] = texture.scale_v
        mapping.inputs["Location"].default_value[0] = texture.offset_u
        mapping.inputs["Location"].default_value[1] = texture.offset_v
        links.new(uv_node.outputs["UV"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], image_node.inputs["Vector"])

        if role == "diffuse":
            links.new(image_node.outputs["Color"], principled.inputs["Base Color"])
        elif role == "opacity":
            links.new(image_node.outputs["Color"], principled.inputs["Alpha"])
            _set_transparent(material)
        elif role in {"normal", "detail_normal", "layer_normal"}:
            separate = nodes.new("ShaderNodeSeparateColor")
            combine = nodes.new("ShaderNodeCombineColor")
            normal_map = nodes.new("ShaderNodeNormalMap")
            normal_map.inputs["Strength"].default_value = 0.5
            separate.location = (-280.0, image_node.location.y)
            combine.location = (-80.0, image_node.location.y)
            normal_map.location = (100.0, image_node.location.y)
            combine.inputs["Blue"].default_value = 1.0
            links.new(image_node.outputs["Color"], separate.inputs["Color"])
            links.new(image_node.outputs["Alpha"], combine.inputs["Red"])
            links.new(separate.outputs["Green"], combine.inputs["Green"])
            links.new(combine.outputs["Color"], normal_map.inputs["Color"])
            if role == "normal" and not principled.inputs["Normal"].is_linked:
                links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
        elif role == "specular":
            multiply = nodes.new("ShaderNodeMath")
            multiply.operation = "MULTIPLY"
            multiply.use_clamp = True
            multiply.inputs[1].default_value = specular_level
            multiply.location = (-100.0, image_node.location.y)
            links.new(
                image_node.outputs["Color"],
                multiply.inputs[0],
            )
            links.new(
                multiply.outputs["Value"],
                principled.inputs["Specular IOR Level"],
            )


def _build_materials(
    model: MdnModel,
    resolver: TextureResolver | None,
    preferred_region: str,
    warnings: list[str],
) -> tuple[bpy.types.Material, ...]:
    materials = []
    for source in model.materials:
        name = f"MGS4_MAT_{source.name_hash:08X}"
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        material["mgs4_flags"] = source.flags
        material["mgs4_name_hash"] = source.name_hash
        material["mgs4_texture_count"] = source.texture_count

        nodes = material.node_tree.nodes
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        output.location = (500.0, 200.0)
        principled.location = (200.0, 200.0)
        material.node_tree.links.new(
            principled.outputs["BSDF"], output.inputs["Surface"]
        )

        diffuse = _safe_diffuse_color(source.parameters[0])
        specular_level, roughness = _legacy_specular(source.parameters[1])
        principled.inputs["Base Color"].default_value = diffuse
        principled.inputs["Alpha"].default_value = diffuse[3]
        principled.inputs["Metallic"].default_value = 0.0
        principled.inputs["Specular IOR Level"].default_value = specular_level
        principled.inputs["Roughness"].default_value = roughness
        material.diffuse_color = diffuse
        material.metallic = 0.0
        material.roughness = roughness
        if (source.flags >> 17) & 1:
            principled.inputs["Specular IOR Level"].default_value = 0.0
            principled.inputs["Roughness"].default_value = 1.0
            material.roughness = 1.0
        if resolver is not None and source.texture_count:
            _new_texture_nodes(
                material,
                model,
                source,
                resolver,
                preferred_region,
                specular_level,
                warnings,
            )
        materials.append(material)
    return tuple(materials)


def _ensure_object_mode() -> None:
    active = bpy.context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _create_armature(
    model: MdnModel,
    name: str,
    scale: float,
    collection: bpy.types.Collection,
) -> tuple[bpy.types.Object, tuple[str, ...]] | tuple[None, tuple[()]]:
    if not model.bones:
        return None, ()

    _ensure_object_mode()
    armature_data = bpy.data.armatures.new(f"{name}_Armature")
    armature_object = bpy.data.objects.new(f"{name}_Armature", armature_data)
    collection.objects.link(armature_object)
    armature_object.show_in_front = False
    armature_object["mgs4_filename_hash"] = model.header.filename_hash

    bpy.context.view_layer.objects.active = armature_object
    armature_object.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = []
    bone_names = []
    used_names: set[str] = set()
    display_length = max(0.01, abs(scale) * 50.0)
    for bone_index, source in enumerate(model.bones):
        base_name = hash_name(source.name_hash)
        bone_name = (
            base_name
            if base_name not in used_names
            else f"{base_name}_{bone_index:03d}"
        )
        used_names.add(bone_name)
        bone_names.append(bone_name)
        edit_bone = armature_data.edit_bones.new(bone_name)
        edit_bone.head = _game_vector(source.world_position, scale)
        edit_bone.tail = (
            edit_bone.head.x,
            edit_bone.head.y,
            edit_bone.head.z + display_length,
        )
        edit_bone.roll = 0.0
        edit_bone.use_connect = False
        edit_bones.append(edit_bone)

    for bone_index, source in enumerate(model.bones):
        if 0 <= source.parent_index < len(edit_bones):
            edit_bones[bone_index].parent = edit_bones[source.parent_index]

    bpy.ops.object.mode_set(mode="OBJECT")
    for bone_index, bone_name in enumerate(bone_names):
        bone = armature_data.bones[bone_name]
        source = model.bones[bone_index]
        bone["mgs4_bone_index"] = bone_index
        bone["mgs4_name_hash"] = source.name_hash
        bone["mgs4_flags"] = source.flags
    return armature_object, tuple(bone_names)


def _create_group_collections(
    model: MdnModel,
    root: bpy.types.Collection,
) -> tuple[bpy.types.Collection, ...]:
    groups = [
        bpy.data.collections.new(f"Group_{hash_name(group.name_hash)}")
        for group in model.groups
    ]
    for group_index, (group, collection) in enumerate(zip(model.groups, groups)):
        collection["mgs4_group_index"] = group_index
        collection["mgs4_name_hash"] = group.name_hash
        collection["mgs4_flags"] = group.flags
        if 0 <= group.parent_index < len(groups) and group.parent_index != group_index:
            groups[group.parent_index].children.link(collection)
        else:
            root.children.link(collection)
    return tuple(groups)


def _assign_skin(
    mesh_object: bpy.types.Object,
    geometry,
    bone_names: tuple[str, ...],
    armature_object: bpy.types.Object | None,
) -> None:
    if (
        armature_object is None
        or not geometry.weights
        or not geometry.bone_indices
    ):
        return

    groups: dict[int, bpy.types.VertexGroup] = {}
    for vertex_index, (weights, bone_indices) in enumerate(
        zip(geometry.weights, geometry.bone_indices)
    ):
        valid = [
            (bone_index, max(0.0, weight))
            for bone_index, weight in zip(bone_indices, weights)
            if 0 <= bone_index < len(bone_names) and weight > 0.0
        ]
        total = sum(weight for _bone_index, weight in valid)
        if total <= 1e-12:
            continue
        for bone_index, weight in valid:
            group = groups.get(bone_index)
            if group is None:
                group = mesh_object.vertex_groups.new(name=bone_names[bone_index])
                groups[bone_index] = group
            group.add((vertex_index,), weight / total, "REPLACE")

    if not groups:
        return
    modifier = mesh_object.modifiers.new("MGS4 Armature", "ARMATURE")
    modifier.object = armature_object
    mesh_object.parent = armature_object


def _apply_mesh_attributes(
    mesh: bpy.types.Mesh,
    geometry,
) -> None:
    if mesh.polygons:
        mesh.polygons.foreach_set(
            "use_smooth",
            np.ones(len(mesh.polygons), dtype=np.bool_),
        )

    if geometry.normals and len(geometry.normals) == len(mesh.vertices):
        normals = _game_vectors(geometry.normals)
        mesh.normals_split_custom_set_from_vertices(normals)

    loop_vertex_indices = np.empty(len(mesh.loops), dtype=np.int32)
    if mesh.loops:
        mesh.loops.foreach_get("vertex_index", loop_vertex_indices)

    for layer_index, source_uvs in enumerate(geometry.uv_layers):
        uv_layer = mesh.uv_layers.new(name=f"UV{layer_index}")
        source = np.asarray(source_uvs, dtype=np.float32)
        corner_uvs = np.ascontiguousarray(source[loop_vertex_indices])
        corner_uvs[:, 1] = 1.0 - corner_uvs[:, 1]
        uv_layer.data.foreach_set("uv", corner_uvs.ravel())

    if geometry.colors:
        color_attribute = mesh.color_attributes.new(
            name="Color",
            type="FLOAT_COLOR",
            domain="CORNER",
        )
        source_colors = np.asarray(geometry.colors, dtype=np.float32)
        corner_colors = np.ascontiguousarray(
            source_colors[loop_vertex_indices]
        )
        color_attribute.data.foreach_set("color", corner_colors.ravel())

    if geometry.tangents:
        tangent_attribute = mesh.attributes.new(
            name="MGS4_Tangent",
            type="FLOAT_VECTOR",
            domain="POINT",
        )
        tangents = _game_vectors(geometry.tangents)
        tangent_attribute.data.foreach_set("vector", tangents.ravel())


def _create_mesh_data(
    name: str,
    geometry,
    scale: float,
) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(name)
    vertices = _game_vectors(geometry.vertices, scale)
    triangles = np.asarray(
        geometry.triangles,
        dtype=np.int32,
    ).reshape((-1, 3))

    mesh.vertices.add(len(vertices))
    if len(vertices):
        mesh.vertices.foreach_set("co", vertices.ravel())

    loop_count = triangles.size
    mesh.loops.add(loop_count)
    if loop_count:
        mesh.loops.foreach_set("vertex_index", triangles.ravel())

    polygon_count = len(triangles)
    mesh.polygons.add(polygon_count)
    if polygon_count:
        mesh.polygons.foreach_set(
            "loop_start",
            np.arange(0, loop_count, 3, dtype=np.int32),
        )
        mesh.polygons.foreach_set(
            "loop_total",
            np.full(polygon_count, 3, dtype=np.int32),
        )

    mesh.validate(clean_customdata=False)
    mesh.update(calc_edges=True)
    _apply_mesh_attributes(mesh, geometry)
    return mesh


def import_mdn_model(
    context: bpy.types.Context,
    model: MdnModel,
    *,
    name: str,
    scale: float = 0.001,
    resolver: TextureResolver | None = None,
    preferred_region: str = "common",
    parent_collection: bpy.types.Collection | None = None,
) -> ModelImportResult:
    warnings: list[str] = []
    root = bpy.data.collections.new(name)
    (parent_collection or context.collection).children.link(root)
    root["mgs4_filename_hash"] = model.header.filename_hash
    root["mgs4_scale"] = scale

    materials = _build_materials(model, resolver, preferred_region, warnings)
    armature_object, bone_names = _create_armature(model, name, scale, root)
    group_collections = _create_group_collections(model, root)
    objects = []

    for mesh_index, geometry in enumerate(model.meshes):
        mesh_name = f"{name}_Mesh_{mesh_index:03d}"
        mesh = _create_mesh_data(mesh_name, geometry, scale)

        used_materials = sorted(
            {
                material_index
                for material_index in geometry.triangle_materials
                if 0 <= material_index < len(materials)
            }
        )
        local_materials = {}
        for material_index in used_materials:
            local_materials[material_index] = len(mesh.materials)
            mesh.materials.append(materials[material_index])
        if mesh.polygons:
            material_indices = np.zeros(len(mesh.polygons), dtype=np.int32)
            source_count = min(
                len(geometry.triangle_materials),
                len(mesh.polygons),
            )
            material_indices[:source_count] = np.fromiter(
                (
                    local_materials.get(source_material_index, 0)
                    for source_material_index in geometry.triangle_materials[
                        :source_count
                    ]
                ),
                dtype=np.int32,
                count=source_count,
            )
            mesh.polygons.foreach_set("material_index", material_indices)

        mesh_object = bpy.data.objects.new(mesh_name, mesh)
        target_collection = (
            group_collections[geometry.info.group_index]
            if 0 <= geometry.info.group_index < len(group_collections)
            else root
        )
        target_collection.objects.link(mesh_object)
        mesh_object["mgs4_mesh_index"] = mesh_index
        mesh_object["mgs4_flags"] = geometry.info.flags
        mesh_object["mgs4_group_index"] = geometry.info.group_index
        mesh_object["mgs4_skin_index"] = geometry.info.skin_index
        mesh_object["mgs4_position"] = list(geometry.info.position)
        _assign_skin(mesh_object, geometry, bone_names, armature_object)
        objects.append(mesh_object)

    if armature_object is not None:
        objects.insert(0, armature_object)
    return ModelImportResult(
        collection=root,
        armature=armature_object,
        objects=tuple(objects),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _set_fcurve_keys(
    channel_bag,
    data_path: str,
    values: Iterable[tuple[float, tuple[float, ...]]],
    component_count: int,
    group_name: str,
) -> int:
    rows = tuple(values)
    if not rows:
        return 0
    for component in range(component_count):
        curve = channel_bag.fcurves.new(
            data_path,
            index=component,
            group_name=group_name,
        )
        curve.keyframe_points.add(len(rows))
        coordinates = []
        for frame, value in rows:
            coordinates.extend((frame, value[component]))
        curve.keyframe_points.foreach_set("co", coordinates)
        for point in curve.keyframe_points:
            point.interpolation = "LINEAR"
        curve.update()
    return component_count


def _continuous_quaternions(
    keys: tuple[QuaternionKey, ...],
) -> tuple[tuple[float, tuple[float, float, float, float]], ...]:
    output = []
    previous = None
    for key in keys:
        quaternion = key.value
        if previous is not None:
            dot = sum(left * right for left, right in zip(previous, quaternion))
            if dot < 0.0:
                quaternion = tuple(-component for component in quaternion)
        output.append((float(key.frame), quaternion))
        previous = quaternion
    return tuple(output)


def _pose_translation(
    pose_bone: bpy.types.PoseBone,
    value: tuple[float, float, float],
    scale: float,
) -> tuple[float, float, float]:
    target = Vector(_game_vector(value, scale))
    delta = target - pose_bone.bone.head_local
    local = pose_bone.bone.matrix_local.to_3x3().inverted_safe() @ delta
    return tuple(local)


def _action_name(archive_name: str | None, clip) -> str:
    archive_label = (
        Path(archive_name).stem.upper()
        if archive_name
        else "MGS4"
    )
    return (
        f"{archive_label}_{clip.source_index:04d}_"
        f"{clip.name_hash:08X}"
    )


def _clip_frame_range(clip) -> tuple[float, float]:
    frames = [
        key.frame
        for track in clip.tracks
        for keys in (track.translations, track.rotations)
        for key in keys
    ]
    start = float(min(frames)) if frames else 0.0
    end = max(float(clip.frame_count), float(max(frames)) if frames else 0.0)
    return start, end


def import_motion_archive(
    context: bpy.types.Context,
    armature_object: bpy.types.Object,
    archive: MotionArchive,
    *,
    scale: float = 0.001,
    max_actions: int = 0,
    archive_name: str | None = None,
) -> MotionImportResult:
    if armature_object.type != "ARMATURE":
        raise ValueError("MGS4 motions require an armature object")

    animation_data = armature_object.animation_data_create()
    available_bones = set(armature_object.pose.bones.keys())
    missing_bones: set[str] = set()
    actions = []
    skipped_clips = 0
    animated_bones: set[str] = set()

    for clip in archive.clips:
        if max_actions > 0 and len(actions) >= max_actions:
            skipped_clips += 1
            continue

        frame_start, frame_end = _clip_frame_range(clip)
        action = bpy.data.actions.new(_action_name(archive_name, clip))
        action.use_fake_user = True
        action.use_frame_range = True
        action.frame_start = frame_start
        action.frame_end = frame_end
        action["mgs4_archive_name"] = Path(archive_name).stem if archive_name else ""
        action["mgs4_clip_index"] = clip.source_index
        action["mgs4_name_hash"] = clip.name_hash
        action["mgs4_flags"] = clip.flags
        action["mgs4_base_tick"] = clip.base_tick
        action["mgs4_frame_start"] = frame_start
        action["mgs4_frame_count"] = clip.frame_count
        action["mgs4_fps"] = 60
        slot = action.slots.new(id_type="OBJECT", name=armature_object.name)
        slot.select = True
        slot.show_expanded = True
        layer = action.layers.new("MGS4 Motion")
        strip = layer.strips.new(type="KEYFRAME")
        channel_bag = strip.channelbags.new(slot)
        curve_count = 0

        for track in clip.tracks:
            bone_name = hash_name(track.bone_hash)
            if bone_name not in available_bones:
                missing_bones.add(bone_name)
                continue
            pose_bone = armature_object.pose.bones[bone_name]
            pose_bone.rotation_mode = "QUATERNION"
            animated_bones.add(bone_name)
            escaped_name = bpy.utils.escape_identifier(bone_name)
            base_path = f'pose.bones["{escaped_name}"]'

            translations = tuple(
                (
                    float(key.frame),
                    _pose_translation(pose_bone, key.value, scale),
                )
                for key in track.translations
            )
            curve_count += _set_fcurve_keys(
                channel_bag,
                f"{base_path}.location",
                translations,
                3,
                bone_name,
            )
            curve_count += _set_fcurve_keys(
                channel_bag,
                f"{base_path}.rotation_quaternion",
                _continuous_quaternions(track.rotations),
                4,
                bone_name,
            )

        if curve_count == 0:
            bpy.data.actions.remove(action)
            skipped_clips += 1
            continue
        for group in channel_bag.groups:
            group.show_expanded = True
        actions.append(action)

    if actions:
        animation_data.action = actions[0]
        animation_data.action_slot = actions[0].slots[0]
        context.scene.render.fps = 60
        context.scene.frame_start = round(actions[0].frame_start)
        context.scene.frame_end = round(actions[0].frame_end)
        context.scene.frame_set(context.scene.frame_start)
        for pose_bone in armature_object.pose.bones:
            pose_bone.select = pose_bone.name in animated_bones

    return MotionImportResult(
        actions=tuple(actions),
        skipped_clips=skipped_clips,
        missing_bones=tuple(sorted(missing_bones)),
    )
