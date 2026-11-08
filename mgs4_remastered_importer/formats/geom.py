from __future__ import annotations

from dataclasses import dataclass
import math
import struct

from ..errors import FormatError


_HEADER_SIZE = 32
_CHUNK_SIZE = 12
_EFFECT_RECORD_SIZE = 16
_EFFECT_SLOT_MASK = 0x3FF
_MAX_CHUNKS = 256
_MAX_EFFECTS = 1_000_000


@dataclass(frozen=True, slots=True)
class GeomChunk:
    type: int
    size: int
    offset: int


@dataclass(frozen=True, slots=True)
class GeomEffect:
    name_hash: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    parent_index: int | None


@dataclass(frozen=True, slots=True)
class GeomEffects:
    effects: tuple[GeomEffect, ...]

    def by_hash(self) -> dict[int, GeomEffect]:
        result: dict[int, GeomEffect] = {}
        for effect in self.effects:
            result.setdefault(effect.name_hash, effect)
        return result

    def children_by_hash(self) -> dict[int, tuple[GeomEffect, ...]]:
        children: dict[int, list[GeomEffect]] = {}
        for effect in self.effects:
            if effect.parent_index is None:
                continue
            parent = self.effects[effect.parent_index]
            children.setdefault(parent.name_hash, []).append(effect)
        return {key: tuple(value) for key, value in children.items()}


def _detect_endian(data: bytes) -> str:
    if len(data) < _HEADER_SIZE:
        raise FormatError("GEOM file is truncated")
    for endian in (">", "<"):
        declared_size = struct.unpack_from(f"{endian}I", data, 4)[0]
        if declared_size == len(data):
            return endian
    raise FormatError("GEOM file size does not match its header")


def _read_chunks(data: bytes, endian: str) -> tuple[GeomChunk, ...]:
    chunk_count = struct.unpack_from(f"{endian}i", data, 8)[0]
    if not 0 <= chunk_count <= _MAX_CHUNKS:
        raise FormatError(f"Implausible GEOM chunk count: {chunk_count}")
    table_end = _HEADER_SIZE + chunk_count * _CHUNK_SIZE
    if table_end > len(data):
        raise FormatError("GEOM chunk table is truncated")

    chunks = []
    for index in range(chunk_count):
        offset = _HEADER_SIZE + index * _CHUNK_SIZE
        chunk_type, _padding, size, data_offset = struct.unpack_from(
            f"{endian}HHii", data, offset
        )
        if (
            size < 0
            or data_offset < 0
            or data_offset > len(data)
            or size > len(data) - data_offset
        ):
            raise FormatError(
                f"GEOM chunk {index} lies outside the file "
                f"(offset 0x{data_offset:X}, size 0x{size:X})"
            )
        chunks.append(GeomChunk(chunk_type, size, data_offset))
    return tuple(chunks)


def _decode_angle(value: int) -> float:
    return value * math.pi / 32768.0


def parse_geom_effects(data: bytes) -> GeomEffects:
    endian = _detect_endian(data)
    chunks = _read_chunks(data, endian)
    properties = next((chunk for chunk in chunks if chunk.type == 6), None)
    if properties is None or properties.size == 0:
        return GeomEffects(())

    chunk = memoryview(data)[
        properties.offset : properties.offset + properties.size
    ]
    effects: list[GeomEffect] = []
    visited: set[int] = set()

    def read_effect(offset: int, parent_index: int | None) -> int:
        if len(effects) >= _MAX_EFFECTS:
            raise FormatError("Implausible GEOM effect count")
        if offset in visited:
            raise FormatError(f"GEOM effect list loops at 0x{offset:X}")
        if offset < 0 or offset > len(chunk) - _EFFECT_RECORD_SIZE:
            raise FormatError(f"GEOM effect record lies outside chunk 6 at 0x{offset:X}")
        visited.add(offset)

        next_offset, child_offset, name, packed_index = struct.unpack_from(
            f"{endian}iiii", chunk, offset
        )
        packed_index &= 0xFFFFFFFF

        position = (0.0, 0.0, 0.0)
        position_slot = packed_index & _EFFECT_SLOT_MASK
        if position_slot:
            payload_offset = offset + position_slot * 8
            if payload_offset < 0 or payload_offset > len(chunk) - 16:
                raise FormatError(
                    f"GEOM effect {name & 0xFFFFFFFF:08X} position "
                    f"lies outside chunk 6 at 0x{payload_offset:X}"
                )
            x, y, z, _w = struct.unpack_from(
                f"{endian}ffff", chunk, payload_offset
            )
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise FormatError(
                    f"GEOM effect {name & 0xFFFFFFFF:08X} has "
                    "a non-finite position"
                )
            position = (x, y, z)

        rotation = (0.0, 0.0, 0.0)
        rotation_slot = (packed_index >> 10) & _EFFECT_SLOT_MASK
        if rotation_slot:
            payload_offset = offset + rotation_slot * 8
            if payload_offset < 0 or payload_offset > len(chunk) - 6:
                raise FormatError(
                    f"GEOM effect {name & 0xFFFFFFFF:08X} rotation "
                    f"lies outside chunk 6 at 0x{payload_offset:X}"
                )
            rotation = tuple(
                _decode_angle(value)
                for value in struct.unpack_from(
                    f"{endian}hhh", chunk, payload_offset
                )
            )

        effect_index = len(effects)
        effects.append(
            GeomEffect(
                name_hash=name & 0xFFFFFFFF,
                position=position,
                rotation=rotation,
                parent_index=parent_index,
            )
        )

        if child_offset:
            child = offset + child_offset
            while True:
                child_next = read_effect(child, effect_index)
                if child_next == 0:
                    break
                child += child_next
        return next_offset

    root_offset = 0
    while root_offset < len(chunk):
        next_offset = read_effect(root_offset, None)
        if next_offset == 0:
            break
        root_offset += next_offset

    return GeomEffects(tuple(effects))
