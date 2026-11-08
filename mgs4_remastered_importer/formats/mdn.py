from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Final

from ..errors import FormatError


MDN_MAGIC: Final = b"MDN "
HEADER_SIZE: Final = 128

POSITION = 0
WEIGHT = 1
NORMAL = 2
COLOR = 3
TEXTURE_3DS = 5
BONE_INDEX = 7
TEXTURE_00 = 8
TEXTURE_05 = 13
TANGENT = 14

DATA_FLOAT = 0x01
DATA_SHORT = 0x05
DATA_HALF = 0x07
DATA_UBYTE = 0x08
DATA_BYTE = 0x09
DATA_PACKED_FLOAT = 0x0A
DATA_PACKED_BYTE = 0x0B

MAX_TABLE_COUNT: Final = 1_000_000


@dataclass(frozen=True, slots=True)
class MdnHeader:
    endian: str
    filename_hash: int
    bone_count: int
    group_count: int
    mesh_count: int
    face_count: int
    vertex_definition_count: int
    material_count: int
    texture_count: int
    skin_count: int
    bone_offset: int
    group_offset: int
    mesh_offset: int
    face_offset: int
    vertex_definition_offset: int
    material_offset: int
    texture_offset: int
    skin_offset: int
    vertex_buffer_offset: int
    vertex_buffer_size: int
    face_buffer_offset: int
    face_buffer_size: int
    file_size: int
    bounds_max: tuple[float, float, float, float]
    bounds_min: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class MdnBone:
    name_hash: int
    flags: int
    parent_index: int
    parent_position: tuple[float, float, float, float]
    world_position: tuple[float, float, float, float]
    bounds_max: tuple[float, float, float, float]
    bounds_min: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class MdnGroup:
    name_hash: int
    flags: int
    parent_index: int


@dataclass(frozen=True, slots=True)
class MdnMeshInfo:
    group_index: int
    flags: int
    face_section_count: int
    first_face_section: int
    vertex_definition_index: int
    skin_index: int
    vertex_count: int
    bounds_max: tuple[float, float, float, float]
    bounds_min: tuple[float, float, float, float]
    position: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class MdnFaceSection:
    primitive_type: int
    index_count: int
    offset: int
    material_index: int
    start: int
    size: int


@dataclass(frozen=True, slots=True)
class MdnVertexDefinition:
    definition_count: int
    stride: int
    offset: int
    definitions: tuple[int, ...]
    positions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class MdnMaterial:
    flags: int
    name_hash: int
    texture_count: int
    parameter_count: int
    texture_indices: tuple[int, ...]
    parameters: tuple[tuple[float, float, float, float], ...]


@dataclass(frozen=True, slots=True)
class MdnTexture:
    name_hash: int
    flags: int
    scale_u: float
    scale_v: float
    offset_u: float
    offset_v: float


@dataclass(frozen=True, slots=True)
class MdnSkin:
    unknown: int
    count: int
    bone_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class MdnMeshGeometry:
    info: MdnMeshInfo
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    triangle_materials: tuple[int, ...]
    normals: tuple[tuple[float, float, float], ...]
    tangents: tuple[tuple[float, float, float], ...]
    uv_layers: tuple[tuple[tuple[float, float], ...], ...]
    colors: tuple[tuple[float, float, float, float], ...]
    weights: tuple[tuple[float, float, float, float], ...]
    bone_indices: tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True, slots=True)
class MdnModel:
    header: MdnHeader
    bones: tuple[MdnBone, ...]
    groups: tuple[MdnGroup, ...]
    materials: tuple[MdnMaterial, ...]
    textures: tuple[MdnTexture, ...]
    skins: tuple[MdnSkin, ...]
    meshes: tuple[MdnMeshGeometry, ...]


def _require_span(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise FormatError(
            f"{label} lies outside the MDN file "
            f"(offset 0x{offset:X}, size 0x{size:X})"
        )


def _table_count(value: int, label: str) -> int:
    if value > MAX_TABLE_COUNT:
        raise FormatError(f"Implausible MDN {label} count: {value}")
    return value


def _detect_endian(data: bytes) -> str:
    if len(data) < HEADER_SIZE or data[:4] != MDN_MAGIC:
        raise FormatError("Not an MDN model")
    big_size = struct.unpack_from(">I", data, 92)[0]
    little_size = struct.unpack_from("<I", data, 92)[0]
    if big_size == len(data):
        return ">"
    if little_size == len(data):
        return "<"
    if HEADER_SIZE <= big_size <= len(data):
        return ">"
    if HEADER_SIZE <= little_size <= len(data):
        return "<"
    raise FormatError(
        f"MDN file size field is invalid (BE {big_size}, LE {little_size}, "
        f"actual {len(data)})"
    )


def _parse_header(data: bytes) -> MdnHeader:
    endian = _detect_endian(data)
    values = struct.unpack_from(f"{endian}24I8f", data, 0)
    integers = values[:24]
    if integers[0] not in (0x4D444E20, 0x204E444D):
        raise FormatError("Invalid MDN magic")

    counts = [
        _table_count(integers[index], label)
        for index, label in zip(
            range(2, 10),
            (
                "bone",
                "group",
                "mesh",
                "face",
                "vertex definition",
                "material",
                "texture",
                "skin",
            ),
        )
    ]
    return MdnHeader(
        endian=endian,
        filename_hash=integers[1],
        bone_count=counts[0],
        group_count=counts[1],
        mesh_count=counts[2],
        face_count=counts[3],
        vertex_definition_count=counts[4],
        material_count=counts[5],
        texture_count=counts[6],
        skin_count=counts[7],
        bone_offset=integers[10],
        group_offset=integers[11],
        mesh_offset=integers[12],
        face_offset=integers[13],
        vertex_definition_offset=integers[14],
        material_offset=integers[15],
        texture_offset=integers[16],
        skin_offset=integers[17],
        vertex_buffer_offset=integers[18],
        vertex_buffer_size=integers[19],
        face_buffer_offset=integers[20],
        face_buffer_size=integers[21],
        file_size=integers[23],
        bounds_max=tuple(values[24:28]),
        bounds_min=tuple(values[28:32]),
    )


def _parse_bones(data: bytes, header: MdnHeader) -> tuple[MdnBone, ...]:
    stride = 80
    _require_span(data, header.bone_offset, header.bone_count * stride, "Bone table")
    bones = []
    for index in range(header.bone_count):
        offset = header.bone_offset + index * stride
        name_hash, flags, parent, _padding = struct.unpack_from(
            f"{header.endian}IIiI", data, offset
        )
        vectors = struct.unpack_from(f"{header.endian}16f", data, offset + 16)
        bones.append(
            MdnBone(
                name_hash=name_hash,
                flags=flags,
                parent_index=parent,
                parent_position=tuple(vectors[0:4]),
                world_position=tuple(vectors[4:8]),
                bounds_max=tuple(vectors[8:12]),
                bounds_min=tuple(vectors[12:16]),
            )
        )
    return tuple(bones)


def _signed_index(value: int) -> int:
    return -1 if value == 0xFFFFFFFF else value


def _parse_groups(data: bytes, header: MdnHeader) -> tuple[MdnGroup, ...]:
    stride = 16
    _require_span(data, header.group_offset, header.group_count * stride, "Group table")
    groups = []
    for index in range(header.group_count):
        offset = header.group_offset + index * stride
        name_hash, flags, parent, _padding = struct.unpack_from(
            f"{header.endian}4I", data, offset
        )
        groups.append(MdnGroup(name_hash, flags, _signed_index(parent)))
    return tuple(groups)


def _parse_mesh_infos(data: bytes, header: MdnHeader) -> tuple[MdnMeshInfo, ...]:
    stride = 80
    _require_span(data, header.mesh_offset, header.mesh_count * stride, "Mesh table")
    meshes = []
    for index in range(header.mesh_count):
        offset = header.mesh_offset + index * stride
        values = struct.unpack_from(f"{header.endian}8I12f", data, offset)
        meshes.append(
            MdnMeshInfo(
                group_index=values[0],
                flags=values[1],
                face_section_count=values[2],
                first_face_section=values[3],
                vertex_definition_index=values[4],
                skin_index=_signed_index(values[5]),
                vertex_count=values[6],
                bounds_max=tuple(values[8:12]),
                bounds_min=tuple(values[12:16]),
                position=tuple(values[16:20]),
            )
        )
    return tuple(meshes)


def _parse_faces(data: bytes, header: MdnHeader) -> tuple[MdnFaceSection, ...]:
    stride = 16
    _require_span(data, header.face_offset, header.face_count * stride, "Face table")
    faces = []
    for index in range(header.face_count):
        values = struct.unpack_from(
            f"{header.endian}HHIIHH", data, header.face_offset + index * stride
        )
        faces.append(MdnFaceSection(*values))
    return tuple(faces)


def _parse_vertex_definitions(
    data: bytes, header: MdnHeader
) -> tuple[MdnVertexDefinition, ...]:
    stride = 48
    _require_span(
        data,
        header.vertex_definition_offset,
        header.vertex_definition_count * stride,
        "Vertex definition table",
    )
    definitions = []
    for index in range(header.vertex_definition_count):
        offset = header.vertex_definition_offset + index * stride
        _padding, count, vertex_stride, buffer_offset = struct.unpack_from(
            f"{header.endian}4I", data, offset
        )
        if count > 16:
            raise FormatError(f"Vertex definition {index} has {count} components")
        if vertex_stride == 0 or vertex_stride > 4096:
            raise FormatError(
                f"Vertex definition {index} has invalid stride {vertex_stride}"
            )
        component_types = tuple(data[offset + 16 : offset + 16 + count])
        component_positions = tuple(data[offset + 32 : offset + 32 + count])
        for component_position in component_positions:
            if component_position >= vertex_stride:
                raise FormatError(
                    f"Vertex definition {index} component exceeds its stride"
                )
        definitions.append(
            MdnVertexDefinition(
                definition_count=count,
                stride=vertex_stride,
                offset=buffer_offset,
                definitions=component_types,
                positions=component_positions,
            )
        )
    return tuple(definitions)


def _parse_materials(data: bytes, header: MdnHeader) -> tuple[MdnMaterial, ...]:
    stride = 112
    _require_span(
        data, header.material_offset, header.material_count * stride, "Material table"
    )
    materials = []
    for index in range(header.material_count):
        offset = header.material_offset + index * stride
        values = struct.unpack_from(f"{header.endian}12I", data, offset)
        texture_count = min(values[2], 8)
        parameter_count = min(values[3], 8)
        parameters = tuple(
            tuple(
                struct.unpack_from(
                    f"{header.endian}4e", data, offset + 48 + parameter_index * 8
                )
            )
            for parameter_index in range(8)
        )
        materials.append(
            MdnMaterial(
                flags=values[0],
                name_hash=values[1],
                texture_count=texture_count,
                parameter_count=parameter_count,
                texture_indices=tuple(values[4:12]),
                parameters=parameters,
            )
        )
    return tuple(materials)


def _parse_textures(data: bytes, header: MdnHeader) -> tuple[MdnTexture, ...]:
    stride = 32
    _require_span(
        data, header.texture_offset, header.texture_count * stride, "Texture table"
    )
    textures = []
    for index in range(header.texture_count):
        offset = header.texture_offset + index * stride
        values = struct.unpack_from(f"{header.endian}II4f2I", data, offset)
        textures.append(
            MdnTexture(
                name_hash=values[0],
                flags=values[1],
                scale_u=values[2],
                scale_v=values[3],
                offset_u=values[4],
                offset_v=values[5],
            )
        )
    return tuple(textures)


def _parse_skins(data: bytes, header: MdnHeader) -> tuple[MdnSkin, ...]:
    stride = 40
    _require_span(data, header.skin_offset, header.skin_count * stride, "Skin table")
    skins = []
    for index in range(header.skin_count):
        offset = header.skin_offset + index * stride
        unknown, count, _null_bytes = struct.unpack_from(
            f"{header.endian}IHH", data, offset
        )
        if count > 32:
            raise FormatError(f"Skin {index} has an invalid palette size of {count}")
        bone_ids = tuple(data[offset + 8 : offset + 40])
        skins.append(MdnSkin(unknown, count, bone_ids))
    return tuple(skins)


def _component_size(data_type: int) -> int:
    if data_type in (DATA_FLOAT, DATA_PACKED_FLOAT):
        return 4
    if data_type in (DATA_SHORT, DATA_HALF):
        return 2
    if data_type in (DATA_UBYTE, DATA_BYTE, DATA_PACKED_BYTE):
        return 1
    raise FormatError(f"Unsupported MDN vertex data type 0x{data_type:X}")


def _read_scalar(data: bytes, offset: int, data_type: int, endian: str) -> float:
    if data_type == DATA_FLOAT:
        return struct.unpack_from(f"{endian}f", data, offset)[0]
    if data_type == DATA_SHORT:
        value = struct.unpack_from(f"{endian}h", data, offset)[0]
        return max(-1.0, value / 32767.0)
    if data_type == DATA_HALF:
        return struct.unpack_from(f"{endian}e", data, offset)[0]
    if data_type == DATA_UBYTE:
        return data[offset] / 255.0
    if data_type in (DATA_BYTE, DATA_PACKED_BYTE):
        value = struct.unpack_from("b", data, offset)[0]
        return max(-1.0, value / 127.0)
    raise FormatError(f"Cannot read scalar MDN data type 0x{data_type:X}")


def _read_vector(
    data: bytes,
    offset: int,
    data_type: int,
    count: int,
    endian: str,
) -> tuple[float, ...]:
    component_size = _component_size(data_type)
    _require_span(data, offset, component_size * count, "Vertex component")
    return tuple(
        _read_scalar(data, offset + component * component_size, data_type, endian)
        for component in range(count)
    )


def _unpack_packed_normal(data: bytes, offset: int, endian: str) -> tuple[float, float, float]:
    _require_span(data, offset, 4, "Packed normal")
    packed = struct.unpack_from(f"{endian}I", data, offset)[0]

    def signed(value: int, bits: int) -> int:
        sign = 1 << (bits - 1)
        return value - (1 << bits) if value & sign else value

    x = signed(packed & 0x7FF, 11) / 1023.0
    y = signed((packed >> 11) & 0x7FF, 11) / 1023.0
    z = signed((packed >> 22) & 0x3FF, 10) / 511.0
    length = math.sqrt(x * x + y * y + z * z)
    if length > 1e-12:
        return x / length, y / length, z / length
    return 0.0, 0.0, 1.0


def _parse_vertex_buffer(
    data: bytes,
    header: MdnHeader,
    mesh: MdnMeshInfo,
    definition: MdnVertexDefinition,
    skin: MdnSkin | None,
) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[float, float, float], ...],
    tuple[tuple[float, float, float], ...],
    tuple[tuple[tuple[float, float], ...], ...],
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[int, int, int, int], ...],
]:
    start = header.vertex_buffer_offset + definition.offset
    size = mesh.vertex_count * definition.stride
    _require_span(data, start, size, "Mesh vertex buffer")
    if definition.offset > header.vertex_buffer_size:
        raise FormatError("Vertex definition offset exceeds the vertex buffer")
    if size > header.vertex_buffer_size - definition.offset:
        raise FormatError("Mesh vertices exceed the declared vertex buffer")

    vertices: list[tuple[float, float, float] | None] = [None] * mesh.vertex_count
    normals: list[tuple[float, float, float] | None] = [None] * mesh.vertex_count
    tangents: list[tuple[float, float, float] | None] = [None] * mesh.vertex_count
    uv_layers: list[list[tuple[float, float] | None]] = [
        [None] * mesh.vertex_count for _ in range(6)
    ]
    colors: list[tuple[float, float, float, float] | None] = [
        None
    ] * mesh.vertex_count
    weights: list[tuple[float, float, float, float] | None] = [
        None
    ] * mesh.vertex_count
    bone_indices: list[tuple[int, int, int, int] | None] = [
        None
    ] * mesh.vertex_count

    for vertex_index in range(mesh.vertex_count):
        vertex_offset = start + vertex_index * definition.stride
        for encoded_type, component_position in zip(
            definition.definitions, definition.positions
        ):
            semantic = encoded_type & 0x0F
            data_type = encoded_type >> 4
            component_offset = vertex_offset + component_position

            if semantic == POSITION:
                vertices[vertex_index] = _read_vector(
                    data, component_offset, data_type, 3, header.endian
                )
            elif semantic in (NORMAL, TANGENT):
                value = (
                    _unpack_packed_normal(data, component_offset, header.endian)
                    if data_type == DATA_PACKED_FLOAT
                    else _read_vector(
                        data, component_offset, data_type, 3, header.endian
                    )
                )
                if semantic == NORMAL:
                    normals[vertex_index] = value
                else:
                    tangents[vertex_index] = value
            elif semantic == COLOR:
                colors[vertex_index] = _read_vector(
                    data, component_offset, data_type, 4, header.endian
                )
            elif semantic == WEIGHT:
                value = _read_vector(
                    data, component_offset, data_type, 4, header.endian
                )
                total = sum(max(0.0, component) for component in value)
                weights[vertex_index] = (
                    tuple(max(0.0, component) / total for component in value)
                    if total > 1e-12
                    else (0.0, 0.0, 0.0, 0.0)
                )
            elif semantic == BONE_INDEX:
                _require_span(data, component_offset, 4, "Bone index component")
                local_indices = tuple(data[component_offset : component_offset + 4])
                if skin is None:
                    bone_indices[vertex_index] = local_indices
                else:
                    bone_indices[vertex_index] = tuple(
                        skin.bone_ids[local_index]
                        if local_index < skin.count
                        else -1
                        for local_index in local_indices
                    )
            elif semantic == TEXTURE_3DS or TEXTURE_00 <= semantic <= TEXTURE_05:
                layer = 0 if semantic == TEXTURE_3DS else semantic - TEXTURE_00
                uv_layers[layer][vertex_index] = _read_vector(
                    data, component_offset, data_type, 2, header.endian
                )

    if any(vertex is None for vertex in vertices):
        raise FormatError("Mesh vertex definition does not provide all positions")

    def complete_vector3(
        values: list[tuple[float, float, float] | None],
    ) -> tuple[tuple[float, float, float], ...]:
        if not any(value is not None for value in values):
            return ()
        return tuple(value or (0.0, 0.0, 1.0) for value in values)

    complete_uvs = tuple(
        tuple(value or (0.0, 0.0) for value in layer)
        for layer in uv_layers
        if any(value is not None for value in layer)
    )
    complete_colors = (
        tuple(value or (1.0, 1.0, 1.0, 1.0) for value in colors)
        if any(value is not None for value in colors)
        else ()
    )
    complete_weights = (
        tuple(value or (0.0, 0.0, 0.0, 0.0) for value in weights)
        if any(value is not None for value in weights)
        else ()
    )
    complete_bones = (
        tuple(value or (-1, -1, -1, -1) for value in bone_indices)
        if any(value is not None for value in bone_indices)
        else ()
    )
    return (
        tuple(vertex for vertex in vertices if vertex is not None),
        complete_vector3(normals),
        complete_vector3(tangents),
        complete_uvs,
        complete_colors,
        complete_weights,
        complete_bones,
    )


def _parse_triangles(
    data: bytes,
    header: MdnHeader,
    mesh: MdnMeshInfo,
    faces: tuple[MdnFaceSection, ...],
) -> tuple[tuple[tuple[int, int, int], ...], tuple[int, ...]]:
    first = mesh.first_face_section
    end = first + mesh.face_section_count
    if first > len(faces) or end > len(faces):
        raise FormatError("Mesh face-section range is invalid")

    triangles = []
    materials = []
    for face in faces[first:end]:
        byte_count = face.index_count * 2
        if face.offset > header.face_buffer_size:
            raise FormatError("Face section offset exceeds the face buffer")
        if byte_count > header.face_buffer_size - face.offset:
            raise FormatError("Face section exceeds the declared face buffer")
        offset = header.face_buffer_offset + face.offset
        _require_span(data, offset, byte_count, "Face index data")
        indices = struct.unpack_from(
            f"{header.endian}{face.index_count}H", data, offset
        )

        candidate_triangles = []
        if 0xFFFF in indices or len(indices) % 3:
            strip = []
            strips = []
            for vertex_index in indices:
                if vertex_index == 0xFFFF:
                    if strip:
                        strips.append(strip)
                        strip = []
                else:
                    strip.append(vertex_index)
            if strip:
                strips.append(strip)
            for strip_indices in strips:
                for index in range(len(strip_indices) - 2):
                    if index % 2:
                        triangle = (
                            strip_indices[index + 1],
                            strip_indices[index],
                            strip_indices[index + 2],
                        )
                    else:
                        triangle = (
                            strip_indices[index],
                            strip_indices[index + 1],
                            strip_indices[index + 2],
                        )
                    candidate_triangles.append(triangle)
        else:
            candidate_triangles.extend(
                (indices[index], indices[index + 1], indices[index + 2])
                for index in range(0, len(indices) - 2, 3)
            )

        for triangle in candidate_triangles:
            if max(triangle) >= mesh.vertex_count:
                raise FormatError(
                    f"Face index {max(triangle)} exceeds {mesh.vertex_count} vertices"
                )
            if len(set(triangle)) < 3:
                continue
            triangles.append(triangle)
            materials.append(face.material_index)
    return tuple(triangles), tuple(materials)


def parse_mdn(data: bytes) -> MdnModel:
    header = _parse_header(data)
    if header.file_size > len(data):
        raise FormatError("MDN declares a size larger than the available data")
    _require_span(
        data,
        header.vertex_buffer_offset,
        header.vertex_buffer_size,
        "Vertex buffer",
    )
    _require_span(
        data,
        header.face_buffer_offset,
        header.face_buffer_size,
        "Face buffer",
    )

    bones = _parse_bones(data, header)
    groups = _parse_groups(data, header)
    mesh_infos = _parse_mesh_infos(data, header)
    faces = _parse_faces(data, header)
    vertex_definitions = _parse_vertex_definitions(data, header)
    materials = _parse_materials(data, header)
    textures = _parse_textures(data, header)
    skins = _parse_skins(data, header)

    meshes = []
    for mesh_index, mesh_info in enumerate(mesh_infos):
        if mesh_info.vertex_definition_index >= len(vertex_definitions):
            raise FormatError(
                f"Mesh {mesh_index} references missing vertex definition "
                f"{mesh_info.vertex_definition_index}"
            )
        skin = (
            skins[mesh_info.skin_index]
            if 0 <= mesh_info.skin_index < len(skins)
            else None
        )
        vertex_data = _parse_vertex_buffer(
            data,
            header,
            mesh_info,
            vertex_definitions[mesh_info.vertex_definition_index],
            skin,
        )
        triangles, triangle_materials = _parse_triangles(
            data, header, mesh_info, faces
        )
        meshes.append(
            MdnMeshGeometry(
                info=mesh_info,
                vertices=vertex_data[0],
                triangles=triangles,
                triangle_materials=triangle_materials,
                normals=vertex_data[1],
                tangents=vertex_data[2],
                uv_layers=vertex_data[3],
                colors=vertex_data[4],
                weights=vertex_data[5],
                bone_indices=vertex_data[6],
            )
        )

    return MdnModel(
        header=header,
        bones=bones,
        groups=groups,
        materials=materials,
        textures=textures,
        skins=skins,
        meshes=tuple(meshes),
    )
