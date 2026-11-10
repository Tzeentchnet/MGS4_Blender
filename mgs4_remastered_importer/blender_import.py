from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

import bpy
from mathutils import Euler, Matrix, Vector
import numpy as np

from .errors import MGS4Error
from .formats.common import hash_name, uint32_to_int32
from .formats.mdn import MdnMaterial, MdnModel
from .formats.mtar import MotionArchive, QuaternionKey
from .formats.texture import (
    FORMAT_DXT3,
    FORMAT_DXT5,
    FORMAT_RGBA32,
    TextureResolver,
    TextureSource,
)


LAYER_MODE_CONSERVATIVE = "CONSERVATIVE"
LAYER_MODE_PREVIEW = "LAYER_PREVIEW"
LAYER_MODES = frozenset({LAYER_MODE_CONSERVATIVE, LAYER_MODE_PREVIEW})
_ALPHA_CAPABLE_TEXTURE_FORMATS = frozenset(
    {FORMAT_RGBA32, FORMAT_DXT3, FORMAT_DXT5}
)
_DIFFUSE_ALPHA_BLENDED = "BLENDED"
_DIFFUSE_ALPHA_MASKED = "MASKED"
_DETAIL_DECAL_SIGNATURES = frozenset(
    {
        (0x00120000, 2),
        (0x00120002, 3),
    }
)
_TREE_MASK_MIN_AXIS_RATIO = 0.4
_TREE_MASK_MIN_TRIANGLES = 512
_VARIANT_50_VEGETATION_MIN_AXIS_RATIO = 0.1


@dataclass(frozen=True, slots=True)
class MaterialImportOptions:
    layer_mode: str = LAYER_MODE_CONSERVATIVE
    highlight_missing_textures: bool = True
    use_vertex_color: bool = True
    use_vertex_alpha: bool = False

    def __post_init__(self) -> None:
        if self.layer_mode not in LAYER_MODES:
            raise ValueError(f"Unknown MGS4 layer mode: {self.layer_mode}")


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


@dataclass(slots=True)
class _MaterialTextureOutputs:
    base_color: bpy.types.NodeSocket | None = None
    opacity: bpy.types.NodeSocket | None = None
    diffuse_alpha: bool = False
    diffuse_alpha_mode: str | None = None
    missing_count: int = 0


@dataclass(frozen=True, slots=True)
class _MaterialVertexColorSupport:
    rgb: bool
    alpha: bool
    alpha_fade: bool


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
    return (
        max(0.0, min(1.0, value[0])),
        max(0.0, min(1.0, value[1])),
        max(0.0, min(1.0, value[2])),
        1.0,
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


def _set_masked(material: bpy.types.Material) -> None:
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
        if hasattr(material, "use_transparency_overlap"):
            material.use_transparency_overlap = True
    elif hasattr(material, "blend_method"):
        material.blend_method = "CLIP"
        if hasattr(material, "alpha_threshold"):
            material.alpha_threshold = 0.5


def _texture_roles(material: MdnMaterial) -> tuple[str, ...]:
    if _uses_variant_50_color_presentation(material):
        return (
            "secondary_color",
            "normal",
            "diffuse",
            "specular_gradient",
        )
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


def _uses_variant_50_color_presentation(material: MdnMaterial) -> bool:
    return (
        material.flags == 0x00000050
        and material.texture_count == 4
        and bool(material.parameters)
        and all(
            abs(component - expected) < 1e-6
            for component, expected in zip(
                material.parameters[0],
                (3.0, 0.0, 0.0, 1.0),
            )
        )
    )


def _variant_a5_translucency(
    material: MdnMaterial,
) -> tuple[float, float] | None:
    if (
        material.flags != 0x000000A5
        or material.texture_count != 2
        or len(material.parameters) < 4
    ):
        return None
    opacity = material.parameters[2][3]
    roughness = material.parameters[3][3]
    if (
        not math.isfinite(opacity)
        or not math.isfinite(roughness)
        or not 0.0 < opacity < 1.0
    ):
        return None
    return opacity, max(0.0, min(1.0, roughness))


def _variant_e1_tint(
    material: MdnMaterial,
) -> tuple[float, float, float] | None:
    if (
        material.flags & 0xFF != 0xE1
        or material.texture_count != 1
        or not material.parameters
    ):
        return None
    color = material.parameters[0][:3]
    if not all(math.isfinite(component) for component in color):
        return None
    return tuple(max(0.0, min(1.0, component)) for component in color)


def _uses_diffuse_alpha(
    material: MdnMaterial,
    texture_source: TextureSource,
    *,
    detail_decal: bool = False,
) -> bool:
    return (
        _diffuse_alpha_mode(
            material,
            texture_source,
            detail_decal=detail_decal,
        )
        is not None
    )


def _diffuse_alpha_mode(
    material: MdnMaterial,
    texture_source: TextureSource,
    *,
    masked: bool = False,
    suppress_generic: bool = False,
    detail_decal: bool = False,
    inferred_tree_mask: bool = False,
    variant_50_vegetation: bool = False,
    inferred_hair_mask: bool = False,
    inferred_scripted_overlay: bool = False,
) -> str | None:
    if texture_source.format not in _ALPHA_CAPABLE_TEXTURE_FORMATS:
        return None
    shader = material.flags & 0xFF
    no_specular = bool((material.flags >> 17) & 1)
    has_environment = bool((material.flags >> 20) & 1)
    if shader == 0x30 and material.texture_count == 1:
        return _DIFFUSE_ALPHA_MASKED
    if inferred_tree_mask or variant_50_vegetation or inferred_hair_mask:
        return _DIFFUSE_ALPHA_MASKED
    if inferred_scripted_overlay:
        return _DIFFUSE_ALPHA_BLENDED
    if shader in {0xE0, 0xE1} and material.texture_count == 1:
        return _DIFFUSE_ALPHA_BLENDED
    if material.flags == 0x00100010 and material.texture_count == 1:
        return _DIFFUSE_ALPHA_BLENDED
    if (
        masked
        and material.flags == 0x00110000
        and material.texture_count == 4
    ):
        return _DIFFUSE_ALPHA_BLENDED
    if (
        detail_decal
        and shader == 0x02
        and no_specular
        and has_environment
        and material.texture_count == 3
    ):
        return _DIFFUSE_ALPHA_BLENDED
    if (
        shader == 0x00
        and no_specular
        and has_environment
        and material.texture_count == 2
        and not suppress_generic
    ):
        return (
            _DIFFUSE_ALPHA_MASKED
            if masked
            else _DIFFUSE_ALPHA_BLENDED
        )
    return None


_COLOR_TEXTURE_ROLES = frozenset(
    {"diffuse", "layer", "environment", "secondary_color"}
)


def _texture_interpretation(role: str) -> str:
    return "COLOR" if role in _COLOR_TEXTURE_ROLES else "DATA"


def _configure_image_color_space(image: bpy.types.Image, role: str) -> None:
    image.colorspace_settings.name = (
        "sRGB" if role in _COLOR_TEXTURE_ROLES else "Non-Color"
    )


def _load_texture_image(
    image_path: Path,
    source: TextureSource,
    texture_hash: int,
    role: str,
) -> bpy.types.Image:
    interpretation = _texture_interpretation(role)
    image_name = (
        f"MGS4_{source.region.upper()}_{texture_hash:08X}_"
        f"{source.width}x{source.height}_{interpretation}"
    )
    image = bpy.data.images.get(image_name)
    if image is None:
        image = bpy.data.images.load(str(image_path), check_existing=False)
        image.name = image_name
    _configure_image_color_space(image, role)
    image["mgs4_texture_hash"] = uint32_to_int32(texture_hash)
    image["mgs4_region"] = source.region
    image["mgs4_interpretation"] = interpretation
    return image


def _missing_texture_image(
    texture_hash: int,
    role: str,
) -> bpy.types.Image:
    interpretation = _texture_interpretation(role)
    image_name = f"MGS4_MISSING_{texture_hash:08X}_{interpretation}"
    image = bpy.data.images.get(image_name)
    if image is None:
        image = bpy.data.images.new(
            image_name,
            width=8,
            height=8,
            alpha=True,
        )
        pixels = []
        for y in range(8):
            for x in range(8):
                pixels.extend(
                    (1.0, 0.0, 1.0, 1.0)
                    if ((x // 2) + (y // 2)) % 2 == 0
                    else (0.03, 0.0, 0.03, 1.0)
                )
        image.pixels[:] = pixels
        image.update()
    _configure_image_color_space(image, role)
    image["mgs4_missing_texture"] = True
    image["mgs4_texture_hash"] = uint32_to_int32(texture_hash)
    image["mgs4_interpretation"] = interpretation
    return image


def _new_texture_slot_frame(
    nodes,
    parent,
    *,
    slot: int,
    role: str,
    texture_hash: int,
    missing: bool,
):
    frame = nodes.new("NodeFrame")
    frame.name = f"MGS4_Slot_{slot:02d}_{role}"
    frame.label = (
        f"Slot {slot}: {role.replace('_', ' ').title()} "
        f"{texture_hash:08X}"
        + (" (Missing)" if missing else "")
    )
    frame.parent = parent
    frame.location = (0.0, -slot * 260.0)
    frame.use_custom_color = True
    if missing:
        frame.color = (0.55, 0.04, 0.08)
    elif role in _COLOR_TEXTURE_ROLES:
        frame.color = (0.12, 0.28, 0.48)
    else:
        frame.color = (0.28, 0.16, 0.42)
    frame["mgs4_slot"] = slot
    frame["mgs4_role"] = role
    frame["mgs4_texture_hash"] = uint32_to_int32(texture_hash)
    frame["mgs4_missing_texture"] = missing
    return frame


def _new_texture_nodes(
    material: bpy.types.Material,
    model: MdnModel,
    source_material: MdnMaterial,
    resolver: TextureResolver,
    preferred_region: str,
    specular_level: float,
    warnings: list[str],
    options: MaterialImportOptions,
    masked_diffuse_alpha: bool,
    suppressed_diffuse_alpha: bool,
    inferred_detail_decal: bool,
    inferred_tree_mask: bool,
    inferred_variant_50_vegetation: bool,
    inferred_hair_mask: bool,
    inferred_scripted_overlay: bool,
) -> _MaterialTextureOutputs:
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    principled = next(
        node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    roles = _texture_roles(source_material)
    use_uv_1 = bool((source_material.flags >> 16) & 1)
    outputs = _MaterialTextureOutputs()
    layer_preview_selected = False
    texture_frame = nodes.new("NodeFrame")
    texture_frame.name = f"MGS4_Texture_Roles_{source_material.name_hash:08X}"
    texture_frame.label = (
        f"MGS4 Texture Roles | Shader 0x{source_material.flags & 0xFF:02X} "
        f"| Flags 0x{source_material.flags:08X}"
    )
    texture_frame.location = (-1050.0, 420.0)
    texture_frame.use_custom_color = True
    texture_frame.color = (0.08, 0.12, 0.18)

    for slot, role in enumerate(roles):
        texture_index = source_material.texture_indices[slot]
        if texture_index == 0xFFFFFFFF or texture_index >= len(model.textures):
            warnings.append(
                f"Material {source_material.name_hash:08X} has invalid "
                f"texture index {texture_index}"
            )
            continue
        texture = model.textures[texture_index]
        missing = False
        try:
            image_path, texture_source = resolver.materialize(
                texture.name_hash, preferred_region
            )
            image = _load_texture_image(
                image_path,
                texture_source,
                texture.name_hash,
                role,
            )
        except MGS4Error as exc:
            warnings.append(str(exc))
            outputs.missing_count += 1
            if not options.highlight_missing_textures:
                continue
            image = _missing_texture_image(texture.name_hash, role)
            missing = True

        slot_frame = _new_texture_slot_frame(
            nodes,
            texture_frame,
            slot=slot,
            role=role,
            texture_hash=texture.name_hash,
            missing=missing,
        )

        image_node = nodes.new("ShaderNodeTexImage")
        image_node.name = f"{role}_{texture.name_hash:08X}"
        image_node.label = f"{role.replace('_', ' ').title()} {texture.name_hash:08X}"
        image_node.image = image
        image_node.parent = slot_frame
        image_node.location = (400.0, 0.0)
        image_node["mgs4_texture_hash"] = uint32_to_int32(texture.name_hash)
        image_node["mgs4_texture_flags"] = uint32_to_int32(texture.flags)
        image_node["mgs4_role"] = role
        image_node["mgs4_interpretation"] = _texture_interpretation(role)
        image_node["mgs4_missing_texture"] = missing

        uv_node = nodes.new("ShaderNodeUVMap")
        uv_node.uv_map = "UV1" if use_uv_1 else "UV0"
        uv_node.parent = slot_frame
        uv_node.location = (0.0, 0.0)
        mapping = nodes.new("ShaderNodeMapping")
        mapping.parent = slot_frame
        mapping.location = (200.0, 0.0)
        mapping.inputs["Scale"].default_value[0] = texture.scale_u
        mapping.inputs["Scale"].default_value[1] = texture.scale_v
        mapping.inputs["Location"].default_value[0] = texture.offset_u
        mapping.inputs["Location"].default_value[1] = texture.offset_v
        links.new(uv_node.outputs["UV"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], image_node.inputs["Vector"])

        if role == "diffuse":
            if outputs.base_color is None:
                outputs.base_color = image_node.outputs["Color"]
            diffuse_alpha_mode = (
                None
                if missing
                else _diffuse_alpha_mode(
                    source_material,
                    texture_source,
                    masked=masked_diffuse_alpha,
                    suppress_generic=suppressed_diffuse_alpha,
                    detail_decal=inferred_detail_decal,
                    inferred_tree_mask=inferred_tree_mask,
                    variant_50_vegetation=inferred_variant_50_vegetation,
                    inferred_hair_mask=inferred_hair_mask,
                    inferred_scripted_overlay=inferred_scripted_overlay,
                )
            )
            if outputs.opacity is None and diffuse_alpha_mode is not None:
                outputs.opacity = image_node.outputs["Alpha"]
                outputs.diffuse_alpha = True
                outputs.diffuse_alpha_mode = diffuse_alpha_mode
        elif (
            role == "layer"
            and options.layer_mode == LAYER_MODE_PREVIEW
            and not layer_preview_selected
        ):
            outputs.base_color = image_node.outputs["Color"]
            image_node["mgs4_layer_preview_source"] = True
            layer_preview_selected = True
        elif role == "opacity":
            if outputs.opacity is None:
                outputs.opacity = image_node.outputs["Color"]
        elif role in {"normal", "detail_normal", "layer_normal"}:
            separate = nodes.new("ShaderNodeSeparateColor")
            combine = nodes.new("ShaderNodeCombineColor")
            normal_map = nodes.new("ShaderNodeNormalMap")
            normal_map.inputs["Strength"].default_value = 0.5
            separate.parent = slot_frame
            combine.parent = slot_frame
            normal_map.parent = slot_frame
            separate.location = (620.0, 0.0)
            combine.location = (820.0, 0.0)
            normal_map.location = (1020.0, 0.0)
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
            multiply.parent = slot_frame
            multiply.location = (620.0, 0.0)
            links.new(
                image_node.outputs["Color"],
                multiply.inputs[0],
            )
            links.new(
                multiply.outputs["Value"],
                principled.inputs["Specular IOR Level"],
            )
    return outputs


def _material_vertex_color_support(
    model: MdnModel,
) -> tuple[_MaterialVertexColorSupport, ...]:
    usage: list[list[_MaterialVertexColorSupport]] = [
        [] for _material in model.materials
    ]
    for geometry in model.meshes:
        material_triangles: dict[int, list[tuple[int, int, int]]] = {}
        for triangle, material_index in zip(
            geometry.triangles,
            geometry.triangle_materials,
        ):
            if 0 <= material_index < len(usage):
                material_triangles.setdefault(material_index, []).append(
                    triangle
                )

        has_vertex_colors = (
            bool(geometry.colors)
            and len(geometry.colors) == len(geometry.vertices)
        )
        for material_index, triangles in material_triangles.items():
            if not has_vertex_colors:
                support = _MaterialVertexColorSupport(False, False, False)
            else:
                vertex_indices = {
                    vertex_index
                    for triangle in triangles
                    for vertex_index in triangle
                }
                colors = (
                    geometry.colors[vertex_index]
                    for vertex_index in vertex_indices
                )
                color_values = tuple(colors)
                support = _MaterialVertexColorSupport(
                    rgb=any(
                        any(abs(component) > 1e-6 for component in color[:3])
                        for color in color_values
                    ),
                    alpha=any(abs(color[3]) > 1e-6 for color in color_values),
                    alpha_fade=any(
                        max(geometry.colors[index][3] for index in triangle)
                        - min(geometry.colors[index][3] for index in triangle)
                        > 1e-6
                        for triangle in triangles
                    ),
                )
            usage[material_index].append(support)
    return tuple(
        _MaterialVertexColorSupport(
            rgb=bool(values) and all(value.rgb for value in values),
            alpha=bool(values) and all(value.alpha for value in values),
            alpha_fade=bool(values) and any(
                value.alpha_fade for value in values
            ),
        )
        for values in usage
    )


def _material_render_families(
    model: MdnModel,
) -> tuple[frozenset[int], ...]:
    usage: list[set[int]] = [set() for _material in model.materials]
    for geometry in model.meshes:
        render_family = geometry.info.flags >> 24
        for material_index in set(geometry.triangle_materials):
            if 0 <= material_index < len(usage):
                usage[material_index].add(render_family)
    return tuple(frozenset(values) for values in usage)


def _material_alpha_render_states(
    model: MdnModel,
) -> tuple[bool, ...]:
    states = [False] * len(model.materials)
    for geometry in model.meshes:
        if not geometry.info.flags & 0x00001000:
            continue
        for material_index in set(geometry.triangle_materials):
            if 0 <= material_index < len(states):
                states[material_index] = True
    return tuple(states)


def _material_masked_diffuse_alpha(
    render_families: tuple[frozenset[int], ...],
    alpha_render_states: tuple[bool, ...],
) -> tuple[bool, ...]:
    return tuple(
        families == frozenset({0x10}) and alpha_render_state
        for families, alpha_render_state in zip(
            render_families,
            alpha_render_states,
        )
    )


def _material_suppressed_diffuse_alpha(
    render_families: tuple[frozenset[int], ...],
    alpha_render_states: tuple[bool, ...],
) -> tuple[bool, ...]:
    return tuple(
        families == frozenset({0x10}) and not alpha_render_state
        for families, alpha_render_state in zip(
            render_families,
            alpha_render_states,
        )
    )


def _material_geometry_metrics(
    model: MdnModel,
    candidates: set[int],
) -> Iterable[tuple[int, int, tuple[float, float, float]]]:
    for geometry in model.meshes:
        material_vertices: dict[int, set[int]] = {}
        material_triangle_counts: dict[int, int] = {}
        for triangle, material_index in zip(
            geometry.triangles,
            geometry.triangle_materials,
        ):
            if material_index not in candidates:
                continue
            material_vertices.setdefault(material_index, set()).update(
                triangle
            )
            material_triangle_counts[material_index] = (
                material_triangle_counts.get(material_index, 0) + 1
            )
        for material_index, vertex_indices in material_vertices.items():
            spans = tuple(
                max(geometry.vertices[index][axis] for index in vertex_indices)
                - min(
                    geometry.vertices[index][axis]
                    for index in vertex_indices
                )
                for axis in range(3)
            )
            yield (
                material_index,
                material_triangle_counts[material_index],
                spans,
            )


def _material_inferred_tree_masks(
    model: MdnModel,
    render_families: tuple[frozenset[int], ...],
    alpha_render_states: tuple[bool, ...],
) -> tuple[bool, ...]:
    candidates = {
        material_index
        for material_index, material in enumerate(model.materials)
        if material.flags == 0x00100010
        and material.texture_count == 1
        and render_families[material_index] == frozenset({0x00})
        and alpha_render_states[material_index]
    }
    inferred = [False] * len(model.materials)
    for material_index, triangle_count, spans in _material_geometry_metrics(
        model,
        candidates,
    ):
        largest_span = max(spans)
        if (
            triangle_count >= _TREE_MASK_MIN_TRIANGLES
            and largest_span > 0.0
            and min(spans) >= largest_span * _TREE_MASK_MIN_AXIS_RATIO
        ):
            inferred[material_index] = True
    return tuple(inferred)


def _material_inferred_variant_50_vegetation(
    model: MdnModel,
    render_families: tuple[frozenset[int], ...],
    alpha_render_states: tuple[bool, ...],
) -> tuple[bool, ...]:
    candidates = {
        material_index
        for material_index, material in enumerate(model.materials)
        if material.flags == 0x00100050
        and material.texture_count == 3
        and render_families[material_index] == frozenset({0x80})
        and alpha_render_states[material_index]
    }
    inferred = [False] * len(model.materials)
    for material_index, _triangle_count, spans in _material_geometry_metrics(
        model,
        candidates,
    ):
        largest_span = max(spans)
        if (
            largest_span > 0.0
            and min(spans)
            >= largest_span * _VARIANT_50_VEGETATION_MIN_AXIS_RATIO
        ):
            inferred[material_index] = True
    return tuple(inferred)


def _material_inferred_hair_masks(
    model: MdnModel,
    render_families: tuple[frozenset[int], ...],
    alpha_render_states: tuple[bool, ...],
) -> tuple[bool, ...]:
    return tuple(
        render_families[material_index] == frozenset({0x05})
        and (
            (
                material.flags == 0x000000A3
                and material.texture_count == 3
            )
            or (
                material.flags == 0x00000003
                and material.texture_count == 4
                and alpha_render_states[material_index]
            )
        )
        for material_index, material in enumerate(model.materials)
    )


def _material_inferred_scripted_overlays(
    model: MdnModel,
    render_families: tuple[frozenset[int], ...],
    alpha_render_states: tuple[bool, ...],
) -> tuple[bool, ...]:
    return tuple(
        material.flags == 0x00000060
        and material.texture_count == 5
        and render_families[material_index] == frozenset({0x05})
        and alpha_render_states[material_index]
        for material_index, material in enumerate(model.materials)
    )


def _material_inferred_detail_decals(
    model: MdnModel,
    render_families: tuple[frozenset[int], ...],
    vertex_support: tuple[_MaterialVertexColorSupport, ...],
) -> tuple[bool, ...]:
    return tuple(
        (material.flags, material.texture_count) in _DETAIL_DECAL_SIGNATURES
        and render_families[material_index] == frozenset({0x15})
        and vertex_support[material_index].alpha
        and vertex_support[material_index].alpha_fade
        for material_index, material in enumerate(model.materials)
    )


def _connect_material_presentation(
    material: bpy.types.Material,
    principled,
    diffuse: tuple[float, float, float, float],
    texture_outputs: _MaterialTextureOutputs,
    options: MaterialImportOptions,
    vertex_support: _MaterialVertexColorSupport,
    inferred_detail_decal: bool,
    variant_e1_tint: tuple[float, float, float] | None,
) -> None:
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    use_vertex_color = options.use_vertex_color and vertex_support.rgb
    use_vertex_alpha = (
        options.use_vertex_alpha or inferred_detail_decal
    ) and vertex_support.alpha
    vertex_node = None
    presentation_frame = None

    if (
        use_vertex_color
        or use_vertex_alpha
        or variant_e1_tint is not None
    ):
        presentation_frame = nodes.new("NodeFrame")
        presentation_frame.name = "MGS4_Material_Presentation"
        presentation_frame.label = "MGS4 Material Presentation"
        presentation_frame.location = (-250.0, -500.0)
        presentation_frame.use_custom_color = True
        presentation_frame.color = (0.18, 0.34, 0.16)

    base_color_output = texture_outputs.base_color
    if variant_e1_tint is not None and base_color_output is not None:
        tint = nodes.new("ShaderNodeMixRGB")
        tint.name = "MGS4_Variant_E1_Tint"
        tint.label = "Variant E1 Texture x Parameter Tint"
        tint.blend_type = "MULTIPLY"
        tint.inputs[0].default_value = 1.0
        tint.inputs[2].default_value = (*variant_e1_tint, 1.0)
        tint.parent = presentation_frame
        tint.location = (0.0, 180.0)
        tint["mgs4_role"] = "material_tint"
        tint["mgs4_parameter"] = "parameter_0_rgb"
        links.new(base_color_output, tint.inputs[1])
        base_color_output = tint.outputs["Color"]

    if use_vertex_color or use_vertex_alpha:
        vertex_node = nodes.new("ShaderNodeVertexColor")
        vertex_node.name = "MGS4_Vertex_Color"
        vertex_node.label = "MGS4 Vertex Color"
        vertex_node.layer_name = "Color"
        vertex_node.parent = presentation_frame
        vertex_node.location = (0.0, 0.0)

    if use_vertex_color and vertex_node is not None:
        multiply = nodes.new("ShaderNodeMixRGB")
        multiply.name = "MGS4_Vertex_Color_Multiply"
        multiply.label = "Base Color x Vertex Color"
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1.0
        multiply.parent = presentation_frame
        multiply.location = (220.0, 0.0)
        if base_color_output is not None:
            links.new(base_color_output, multiply.inputs[1])
        else:
            multiply.inputs[1].default_value = diffuse
        links.new(vertex_node.outputs["Color"], multiply.inputs[2])
        links.new(multiply.outputs["Color"], principled.inputs["Base Color"])
    elif base_color_output is not None:
        links.new(base_color_output, principled.inputs["Base Color"])

    alpha_output = texture_outputs.opacity
    if use_vertex_alpha and vertex_node is not None:
        if alpha_output is not None:
            multiply_alpha = nodes.new("ShaderNodeMath")
            multiply_alpha.name = "MGS4_Vertex_Alpha_Multiply"
            multiply_alpha.label = "Opacity x Vertex Alpha"
            multiply_alpha.operation = "MULTIPLY"
            multiply_alpha.use_clamp = True
            multiply_alpha.parent = presentation_frame
            multiply_alpha.location = (220.0, -180.0)
            links.new(alpha_output, multiply_alpha.inputs[0])
            links.new(vertex_node.outputs["Alpha"], multiply_alpha.inputs[1])
            alpha_output = multiply_alpha.outputs["Value"]
        else:
            alpha_output = vertex_node.outputs["Alpha"]

    if alpha_output is not None:
        links.new(alpha_output, principled.inputs["Alpha"])
        if texture_outputs.diffuse_alpha_mode == _DIFFUSE_ALPHA_MASKED:
            _set_masked(material)
        else:
            _set_transparent(material)

    material["mgs4_layer_mode"] = options.layer_mode
    material["mgs4_vertex_color_available"] = vertex_support.rgb
    material["mgs4_vertex_alpha_available"] = vertex_support.alpha
    material["mgs4_inferred_detail_decal"] = inferred_detail_decal
    material["mgs4_vertex_color_enabled"] = use_vertex_color
    material["mgs4_vertex_alpha_enabled"] = use_vertex_alpha
    material["mgs4_diffuse_alpha_enabled"] = texture_outputs.diffuse_alpha
    material["mgs4_diffuse_alpha_mode"] = (
        texture_outputs.diffuse_alpha_mode or "NONE"
    )
    material["mgs4_missing_texture_count"] = texture_outputs.missing_count


def _build_materials(
    model: MdnModel,
    resolver: TextureResolver | None,
    preferred_region: str,
    warnings: list[str],
    options: MaterialImportOptions,
) -> tuple[bpy.types.Material, ...]:
    materials = []
    vertex_color_support = _material_vertex_color_support(model)
    render_families = _material_render_families(model)
    alpha_render_states = _material_alpha_render_states(model)
    masked_diffuse_alpha = _material_masked_diffuse_alpha(
        render_families,
        alpha_render_states,
    )
    suppressed_diffuse_alpha = _material_suppressed_diffuse_alpha(
        render_families,
        alpha_render_states,
    )
    inferred_tree_masks = _material_inferred_tree_masks(
        model,
        render_families,
        alpha_render_states,
    )
    inferred_variant_50_vegetation = (
        _material_inferred_variant_50_vegetation(
            model,
            render_families,
            alpha_render_states,
        )
    )
    inferred_hair_masks = _material_inferred_hair_masks(
        model,
        render_families,
        alpha_render_states,
    )
    inferred_scripted_overlays = _material_inferred_scripted_overlays(
        model,
        render_families,
        alpha_render_states,
    )
    inferred_detail_decals = _material_inferred_detail_decals(
        model,
        render_families,
        vertex_color_support,
    )
    for material_index, source in enumerate(model.materials):
        variant_50_color_presentation = (
            _uses_variant_50_color_presentation(source)
        )
        variant_a5_translucency = _variant_a5_translucency(source)
        variant_e1_tint = _variant_e1_tint(source)
        name = f"MGS4_MAT_{source.name_hash:08X}"
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        material["mgs4_flags"] = uint32_to_int32(source.flags)
        material["mgs4_name_hash"] = uint32_to_int32(source.name_hash)
        material["mgs4_texture_count"] = source.texture_count
        material["mgs4_alpha_render_state"] = alpha_render_states[
            material_index
        ]
        material["mgs4_diffuse_alpha_suppressed"] = (
            suppressed_diffuse_alpha[material_index]
        )
        material["mgs4_inferred_tree_mask"] = inferred_tree_masks[
            material_index
        ]
        material["mgs4_inferred_variant_50_vegetation"] = (
            inferred_variant_50_vegetation[material_index]
        )
        material["mgs4_inferred_hair_mask"] = inferred_hair_masks[
            material_index
        ]
        material["mgs4_inferred_scripted_overlay"] = (
            inferred_scripted_overlays[material_index]
        )
        material["mgs4_variant_50_color_presentation"] = (
            variant_50_color_presentation
        )
        material["mgs4_variant_a5_translucency"] = (
            variant_a5_translucency is not None
        )
        material["mgs4_variant_e1_tint"] = variant_e1_tint is not None
        if variant_e1_tint is not None:
            material["mgs4_variant_e1_tint_color"] = variant_e1_tint
        if variant_a5_translucency is not None:
            material["mgs4_variant_a5_opacity"] = (
                variant_a5_translucency[0]
            )
            material["mgs4_variant_a5_roughness"] = (
                variant_a5_translucency[1]
            )

        nodes = material.node_tree.nodes
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        output.location = (500.0, 200.0)
        principled.location = (200.0, 200.0)
        material.node_tree.links.new(
            principled.outputs["BSDF"], output.inputs["Surface"]
        )

        diffuse = (
            (0.8, 0.8, 0.8, 1.0)
            if variant_50_color_presentation
            else _safe_diffuse_color(source.parameters[0])
        )
        if variant_a5_translucency is not None:
            diffuse = (*diffuse[:3], variant_a5_translucency[0])
        specular_level, roughness = _legacy_specular(source.parameters[1])
        if variant_a5_translucency is not None:
            roughness = variant_a5_translucency[1]
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
        texture_outputs = _MaterialTextureOutputs()
        if resolver is not None and source.texture_count:
            texture_outputs = _new_texture_nodes(
                material,
                model,
                source,
                resolver,
                preferred_region,
                specular_level,
                warnings,
                options,
                masked_diffuse_alpha[material_index],
                suppressed_diffuse_alpha[material_index],
                inferred_detail_decals[material_index],
                inferred_tree_masks[material_index],
                inferred_variant_50_vegetation[material_index],
                inferred_hair_masks[material_index],
                inferred_scripted_overlays[material_index],
            )
        if variant_a5_translucency is not None:
            opacity_node = nodes.new("ShaderNodeValue")
            opacity_node.name = "MGS4_Variant_A5_Opacity"
            opacity_node.label = "Variant A5 Parameter 2 W Opacity"
            opacity_node.location = (-200.0, -500.0)
            opacity_node["mgs4_role"] = "opacity"
            opacity_node["mgs4_parameter"] = "parameter_2_w"
            opacity_node.outputs["Value"].default_value = (
                variant_a5_translucency[0]
            )
            texture_outputs.opacity = opacity_node.outputs["Value"]
        _connect_material_presentation(
            material,
            principled,
            diffuse,
            texture_outputs,
            options,
            vertex_color_support[material_index],
            inferred_detail_decals[material_index],
            variant_e1_tint,
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
    armature_object["mgs4_filename_hash"] = uint32_to_int32(
        model.header.filename_hash
    )

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
        bone["mgs4_name_hash"] = uint32_to_int32(source.name_hash)
        bone["mgs4_flags"] = uint32_to_int32(source.flags)
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
        default_hidden = group.flags == 0x0000000F
        collection["mgs4_group_index"] = group_index
        collection["mgs4_name_hash"] = uint32_to_int32(group.name_hash)
        collection["mgs4_flags"] = uint32_to_int32(group.flags)
        collection["mgs4_default_hidden"] = default_hidden
        collection.hide_viewport = default_hidden
        collection.hide_render = default_hidden
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
    material_options: MaterialImportOptions | None = None,
) -> ModelImportResult:
    material_options = material_options or MaterialImportOptions()
    warnings: list[str] = []
    root = bpy.data.collections.new(name)
    (parent_collection or context.collection).children.link(root)
    root["mgs4_filename_hash"] = uint32_to_int32(model.header.filename_hash)
    root["mgs4_scale"] = scale
    root["mgs4_layer_mode"] = material_options.layer_mode
    root["mgs4_use_vertex_color"] = material_options.use_vertex_color
    root["mgs4_use_vertex_alpha"] = material_options.use_vertex_alpha
    root["mgs4_highlight_missing_textures"] = (
        material_options.highlight_missing_textures
    )

    materials = _build_materials(
        model,
        resolver,
        preferred_region,
        warnings,
        material_options,
    )
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
        mesh_object["mgs4_flags"] = uint32_to_int32(geometry.info.flags)
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
        action["mgs4_name_hash"] = uint32_to_int32(clip.name_hash)
        action["mgs4_flags"] = uint32_to_int32(clip.flags)
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
