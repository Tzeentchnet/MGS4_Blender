from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Final

from ..errors import FormatError


MTAR_BIG_MAGIC: Final = b"ratM"
MTAR_LITTLE_MAGIC: Final = b"Mtar"
MAX_MOTIONS: Final = 100_000
MAX_BONES: Final = 100_000


@dataclass(frozen=True, slots=True)
class VectorKey:
    frame: int
    value: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class QuaternionKey:
    frame: int
    value: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class BoneTrack:
    bone_hash: int
    translations: tuple[VectorKey, ...]
    rotations: tuple[QuaternionKey, ...]


@dataclass(frozen=True, slots=True)
class MotionClip:
    source_index: int
    name_hash: int
    flags: int
    base_tick: int
    frame_count: int
    tracks: tuple[BoneTrack, ...]


@dataclass(frozen=True, slots=True)
class MotionArchive:
    max_joint_count: int
    max_effect_position_count: int
    flags: int
    bone_hashes: tuple[int, ...]
    clips: tuple[MotionClip, ...]
    source_motion_count: int


@dataclass(frozen=True, slots=True)
class _MotionTableEntry:
    mtcm_offset: int
    mtcm_size: int
    mtex_offset: int
    mtex_size: int


class _WordBitReader:
    def __init__(self, data: bytes, endian: str):
        if len(data) % 2:
            raise FormatError("Motion bitstream has an odd byte count")
        self._data = data
        self._endian = endian
        self.position = 0
        self.bit_count = len(data) * 8

    @property
    def remaining(self) -> int:
        return self.bit_count - self.position

    def read(self, bit_count: int) -> int:
        if bit_count < 0 or bit_count > 32:
            raise FormatError(f"Invalid bit read size: {bit_count}")
        if self.position + bit_count > self.bit_count:
            raise FormatError("Motion bitstream ended unexpectedly")

        result = 0
        result_shift = 0
        remaining = bit_count
        while remaining:
            word_index = self.position // 16
            bit_offset = self.position % 16
            take = min(remaining, 16 - bit_offset)
            word = struct.unpack_from(
                f"{self._endian}H", self._data, word_index * 2
            )[0]
            mask = (1 << take) - 1
            result |= ((word >> bit_offset) & mask) << result_shift
            self.position += take
            result_shift += take
            remaining -= take
        return result


def _require_span(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise FormatError(
            f"{label} lies outside the MTAR file "
            f"(offset 0x{offset:X}, size 0x{size:X})"
        )


def _half_to_float(bits: int) -> float:
    value = struct.unpack(">e", struct.pack(">H", bits))[0]
    if not math.isfinite(value):
        raise FormatError("Non-finite translation value in motion bitstream")
    return value * 128.0


def _read_translation_keys(
    bitstream: bytes, frame_count: int, endian: str
) -> tuple[VectorKey, ...]:
    reader = _WordBitReader(bitstream, endian)
    keys = []
    frame = 0
    while frame < frame_count and reader.remaining >= 56:
        value = (
            _half_to_float(reader.read(16)),
            _half_to_float(reader.read(16)),
            _half_to_float(reader.read(16)),
        )
        frame += reader.read(8)
        keys.append(VectorKey(frame, value))
    return tuple(keys)


def _read_rotation_keys(
    bitstream: bytes,
    quaternion_bits: int,
    frame_count: int,
    endian: str,
) -> tuple[QuaternionKey, ...]:
    if quaternion_bits <= 0 or quaternion_bits > 20:
        raise FormatError(f"Unsupported motion quaternion width: {quaternion_bits}")

    reader = _WordBitReader(bitstream, endian)
    bits_per_key = 8 + quaternion_bits * 3 + 3
    keys = []
    frame = 0
    denominator = float(1 << quaternion_bits)

    while frame < frame_count and reader.remaining >= bits_per_key:
        frame += reader.read(8)
        angle = reader.read(quaternion_bits) / denominator * math.pi
        axis_x = reader.read(quaternion_bits) / denominator
        axis_y = reader.read(quaternion_bits) / denominator
        axis_z = 1.0 - axis_x - axis_y
        if reader.read(1):
            axis_x = -axis_x
        if reader.read(1):
            axis_y = -axis_y
        if reader.read(1):
            axis_z = -axis_z

        axis_length = math.sqrt(
            axis_x * axis_x + axis_y * axis_y + axis_z * axis_z
        )
        if axis_length > 1e-12:
            axis_x /= axis_length
            axis_y /= axis_length
            axis_z /= axis_length
        else:
            axis_x, axis_y, axis_z = 0.0, 0.0, 1.0

        sine = math.sin(angle * 0.5)
        cosine = math.cos(angle * 0.5)
        keys.append(
            QuaternionKey(
                frame,
                (
                    cosine,
                    axis_x * sine,
                    axis_y * sine,
                    axis_z * sine,
                ),
            )
        )
    return tuple(keys)


def _parse_mtcm(
    data: bytes,
    endian: str,
    table_entry: _MotionTableEntry,
    mtcm_base: int,
    bone_hashes: tuple[int, ...],
    source_index: int,
) -> MotionClip:
    base = mtcm_base + table_entry.mtcm_offset
    clip_limit = (
        base + table_entry.mtcm_size
        if table_entry.mtcm_size
        else len(data)
    )
    _require_span(data, base, 64, "MTCM header")
    if clip_limit > len(data) or clip_limit < base + 64:
        raise FormatError("MTCM table size is invalid")

    values = struct.unpack_from(f"{endian}11I2BH4I", data, base)
    (
        name_hash,
        flags,
        base_tick,
        frame_count,
        archive_offset,
        archive_size,
        joint_count,
        bit_check_0,
        bit_check_1,
        bit_check_2,
        bit_check_3,
        quaternion_low_bits,
        quaternion_high_bits,
        indices_offset,
        root_offset,
        _move_offset,
        _turn_offset,
        _fix_offset,
    ) = values

    if joint_count > MAX_BONES:
        raise FormatError(f"Implausible MTCM joint count: {joint_count}")
    archive_count = joint_count * 2 if flags >> 4 == 0x07 else joint_count
    quaternion_table_size = archive_count * 4
    if base + 64 + quaternion_table_size > clip_limit:
        raise FormatError("MTCM quaternion offset table is truncated")
    quaternion_offsets = (
        struct.unpack_from(
            f"{endian}{archive_count}I", data, base + 64
        )
        if archive_count
        else ()
    )

    if indices_offset + joint_count > clip_limit - base:
        raise FormatError("MTCM joint index table is truncated")
    joint_indices = data[base + indices_offset : base + indices_offset + joint_count]

    archive_start = base + archive_offset
    archive_byte_size = archive_size * 2
    if (
        archive_start > clip_limit
        or archive_byte_size > clip_limit - archive_start
    ):
        raise FormatError("MTCM bitstream archive is truncated")
    archive = data[archive_start : archive_start + archive_byte_size]
    archive_units = archive_size

    checks = (bit_check_0, bit_check_1, bit_check_2, bit_check_3)
    tracks = []
    for joint_index in range(joint_count):
        bone_table_index = joint_indices[joint_index]
        if bone_table_index >= len(bone_hashes):
            raise FormatError(
                f"MTCM joint references missing bone-table index {bone_table_index}"
            )
        if joint_index >= len(quaternion_offsets):
            raise FormatError("MTCM has fewer rotation offsets than joints")

        rotation_start = quaternion_offsets[joint_index]
        if joint_index + 1 < joint_count:
            rotation_end = quaternion_offsets[joint_index + 1]
        else:
            rotation_end = root_offset
        rotation_units = rotation_end - rotation_start
        if rotation_units <= 0:
            rotation_units = root_offset - rotation_start
        if (
            rotation_start > archive_units
            or rotation_units < 0
            or rotation_units > archive_units - rotation_start
        ):
            raise FormatError("MTCM rotation stream lies outside its archive")

        check_word = checks[joint_index // 32] if joint_index < 128 else 0
        high_precision = (check_word >> (joint_index % 32)) & 1
        quaternion_bits = (
            quaternion_high_bits if high_precision else quaternion_low_bits
        )
        rotation_bytes = archive[
            rotation_start * 2 : (rotation_start + rotation_units) * 2
        ]
        rotations = _read_rotation_keys(
            rotation_bytes, quaternion_bits, frame_count, endian
        )

        translations: tuple[VectorKey, ...] = ()
        if joint_index == 0 and quaternion_offsets:
            translation_units = quaternion_offsets[0]
            if translation_units > archive_units:
                raise FormatError("MTCM translation stream exceeds its archive")
            translations = _read_translation_keys(
                archive[: translation_units * 2], frame_count, endian
            )

        if translations or rotations:
            tracks.append(
                BoneTrack(
                    bone_hash=bone_hashes[bone_table_index],
                    translations=translations,
                    rotations=rotations,
                )
            )

    return MotionClip(
        source_index=source_index,
        name_hash=name_hash,
        flags=flags,
        base_tick=base_tick,
        frame_count=frame_count,
        tracks=tuple(tracks),
    )


def parse_mtar(
    data: bytes,
    *,
    clip_hashes: set[int] | None = None,
    max_clips: int | None = None,
) -> MotionArchive:
    if len(data) < 32:
        raise FormatError("MTAR is too small")
    if data[:4] == MTAR_BIG_MAGIC:
        endian = ">"
    elif data[:4] == MTAR_LITTLE_MAGIC:
        endian = "<"
    else:
        raise FormatError("Not an MTAR motion archive")

    (
        _magic,
        max_joint_count,
        max_effect_position_count,
        bone_count,
        motion_count,
        flags,
        mtcm_offset,
        _mtex_offset,
        bone_table_offset,
        data_table_offset,
    ) = struct.unpack_from(f"{endian}I4H5I", data, 0)

    if bone_count > MAX_BONES:
        raise FormatError(f"Implausible MTAR bone count: {bone_count}")
    if motion_count > MAX_MOTIONS:
        raise FormatError(f"Implausible MTAR motion count: {motion_count}")
    _require_span(data, bone_table_offset, bone_count * 4, "MTAR bone table")
    _require_span(data, data_table_offset, motion_count * 16, "MTAR motion table")
    if mtcm_offset > len(data):
        raise FormatError("MTAR MTCM section starts outside the file")

    bone_hashes = (
        tuple(
            struct.unpack_from(
                f"{endian}{bone_count}I", data, bone_table_offset
            )
        )
        if bone_count
        else ()
    )
    table = tuple(
        _MotionTableEntry(
            *struct.unpack_from(
                f"{endian}4I", data, data_table_offset + index * 16
            )
        )
        for index in range(motion_count)
    )

    clips = []
    for source_index, entry in enumerate(table):
        clip_header = mtcm_offset + entry.mtcm_offset
        _require_span(data, clip_header, 4, "MTCM clip")
        name_hash = struct.unpack_from(f"{endian}I", data, clip_header)[0]
        if clip_hashes is not None and name_hash not in clip_hashes:
            continue
        if max_clips is not None and max_clips > 0 and len(clips) >= max_clips:
            break
        clips.append(
            _parse_mtcm(
                data,
                endian,
                entry,
                mtcm_offset,
                bone_hashes,
                source_index,
            )
        )

    return MotionArchive(
        max_joint_count=max_joint_count,
        max_effect_position_count=max_effect_position_count,
        flags=flags,
        bone_hashes=bone_hashes,
        clips=tuple(clips),
        source_motion_count=motion_count,
    )
