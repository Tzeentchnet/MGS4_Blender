from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Final

from ..errors import FormatError
from .common import strcode
from .geom import GeomEffect, GeomEffects


_MAX_DEFINITIONS = 1_000_000
_MAX_SCRIPT_SIZE = 64 * 1024 * 1024

_NEW_PUT_OBJECT: Final = 0x07A516
_NEW_PUT_STAGE_MODEL_SET: Final = 0x7E641F
_MODEL_PLACEMENT_COMMANDS: Final = frozenset(
    {
        _NEW_PUT_OBJECT,
        _NEW_PUT_STAGE_MODEL_SET,
        0xCE2D24,
        0x7187BE,
        0x9396D5,
        0x07E1F9,
        0x2808D7,
        0x192604,
        0x9EF3C9,
        0x3EF709,
        0x4D1F6D,
        0x71EF12,
        0xF213EE,
        0x656B68,
        0x9ADA66,
    }
)

_MODEL_HASH: Final = 0x091D13
_POSITION_HASH: Final = 0x01CE53
_DIRECTION_HASH: Final = 0x019D92
_EFFECT_HASH: Final = 0x01A134
_MODEL_GLASS_HASH: Final = strcode("model_glass")
_PROPERTY_POSITION_HASH: Final = strcode("prop_pos")
_PROPERTY_HASH: Final = strcode("prop")
_DATA_HASH: Final = strcode("data")
_ARGUMENT_COUNT_HASH: Final = strcode("argc")


@dataclass(frozen=True, slots=True)
class GcxValue:
    kind: str
    value: int


@dataclass(frozen=True, slots=True)
class GcxParameter:
    letter: int
    name_hash: int
    values: tuple[GcxValue, ...]


@dataclass(frozen=True, slots=True)
class GcxCommand:
    name_hash: int
    parameters: tuple[GcxParameter, ...]
    parent_index: int | None


@dataclass(frozen=True, slots=True)
class GcxScript:
    commands: tuple[GcxCommand, ...]


@dataclass(frozen=True, slots=True)
class GcxDocument:
    scripts: tuple[GcxScript, ...]


@dataclass(frozen=True, slots=True)
class StagePlacement:
    model_hash: int
    position: tuple[float, float, float] | None
    rotation: tuple[float, float, float] | None
    command_hash: int
    effect_hash: int | None = None
    property_hash: int | None = None


@dataclass(frozen=True, slots=True)
class StageLayout:
    stage_model_hashes: tuple[int, ...]
    placements: tuple[StagePlacement, ...]

    @property
    def model_hashes(self) -> tuple[int, ...]:
        return tuple(
            dict.fromkeys(
                (*self.stage_model_hashes, *(item.model_hash for item in self.placements))
            )
        )


@dataclass(slots=True)
class _ParameterBuilder:
    letter: int
    name_hash: int
    values: list[GcxValue]


@dataclass(slots=True)
class _CommandBuilder:
    name_hash: int
    parameters: list[_ParameterBuilder]
    parent_index: int | None


class _ScriptDecoder:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0
        self.commands: list[_CommandBuilder] = []
        self.command_stack: list[int] = []
        self.active_parameter: _ParameterBuilder | None = None
        self.header_command: _CommandBuilder | None = None

    def decode(self) -> GcxScript:
        while self.position < len(self.data):
            previous = self.position
            self._process()
            if self.position <= previous:
                raise FormatError(
                    f"GCX decoder stopped advancing at 0x{self.position:X}"
                )
        return GcxScript(
            tuple(
                GcxCommand(
                    command.name_hash,
                    tuple(
                        GcxParameter(
                            parameter.letter,
                            parameter.name_hash,
                            tuple(parameter.values),
                        )
                        for parameter in command.parameters
                    ),
                    command.parent_index,
                )
                for command in self.commands
                if command.name_hash
            )
        )

    def _require(self, size: int, label: str) -> None:
        if (
            size < 0
            or self.position < 0
            or self.position > len(self.data)
            or size > len(self.data) - self.position
        ):
            raise FormatError(f"Truncated GCX {label} at 0x{self.position:X}")

    def _read_u8(self) -> int:
        self._require(1, "byte")
        value = self.data[self.position]
        self.position += 1
        return value

    def _read_int(self, size: int, *, signed: bool = False) -> int:
        self._require(size, "integer")
        value = int.from_bytes(
            self.data[self.position : self.position + size],
            "little",
            signed=signed,
        )
        self.position += size
        return value

    def _record(self, kind: str, value: int) -> None:
        if self.active_parameter is not None:
            self.active_parameter.values.append(GcxValue(kind, value))

    def _read_strcode(self, *, has_letter: bool = False) -> int:
        if has_letter:
            letter = self._read_u8()
        else:
            letter = 0
        value = self._read_int(3)
        if self.header_command is not None and not has_letter:
            if self.header_command.name_hash == 0:
                self.header_command.name_hash = value
        elif not has_letter:
            self._record("strcode", value)
        return (letter << 24) | value if has_letter else value

    def _get_size(self) -> int:
        value = self._read_u8() & 0x0F
        if value == 0x0D:
            return self._read_u8()
        if value == 0x0E:
            return self._read_int(2)
        return value

    def _get_short_size(self) -> int:
        value = self._read_u8()
        if value > 0x7F:
            value = ((value << 8) | self._read_u8()) & 0x7FFF
        return value

    def _process_for(self, start: int, size: int) -> None:
        end = start + size
        if end < start or end > len(self.data):
            raise FormatError(
                f"GCX block at 0x{start:X} extends beyond its script"
            )
        while self.position < end:
            self._process()
        if self.position != end:
            raise FormatError(
                f"GCX block at 0x{start:X} ended at 0x{self.position:X}, "
                f"expected 0x{end:X}"
            )

    def _read_command_header(self, command: _CommandBuilder) -> None:
        size = self._get_short_size()
        start = self.position
        previous = self.header_command
        self.header_command = command
        try:
            self._process_for(start, size)
        finally:
            self.header_command = previous

    def _read_command(self) -> None:
        size = self._get_size()
        start = self.position
        outer_hash = self._read_strcode()
        if outer_hash == 0x0D86:
            body_size = self._get_short_size()
            body_start = self.position
            self._read_expression()
            self._process_for(body_start, body_size)
            return

        parent = self.command_stack[-1] if self.command_stack else None
        command = _CommandBuilder(0, [], parent)
        command_index = len(self.commands)
        self.commands.append(command)
        self.command_stack.append(command_index)
        try:
            self._read_command_header(command)
            self._process_for(start, size)
        finally:
            self.command_stack.pop()

    def _read_parameter(self) -> None:
        size = self._get_size()
        start = self.position
        packed_name = self._read_strcode(has_letter=True)
        parameter = _ParameterBuilder(
            letter=(packed_name >> 24) & 0xFF,
            name_hash=packed_name & 0xFFFFFF,
            values=[],
        )
        if self.command_stack:
            self.commands[self.command_stack[-1]].parameters.append(parameter)
        previous = self.active_parameter
        self.active_parameter = parameter
        try:
            self._process_for(start, size)
        finally:
            self.active_parameter = previous

    def _read_expression(self) -> None:
        size = self._get_size()
        start = self.position
        end = start + size
        if end > len(self.data):
            raise FormatError("GCX expression extends beyond its script")
        while self.position < end:
            if self.data[self.position] & 0xE0 == 0xA0:
                self.position += 1
            else:
                self._process()

    def _read_variable(self) -> None:
        self._require(4, "variable")
        raw = self.data[self.position : self.position + 4]
        self.position += 4
        varcode = int.from_bytes(raw, "big")
        if (varcode & 0xF0000000) >> 24 == 0x20:
            self._process()
            self._process()

    def _read_procedure(self) -> None:
        size = self._get_size()
        start = self.position
        self._process_for(start, size)

    def _read_argument(self) -> None:
        value = self._read_u8() & 0x0F
        if value == 0x0F:
            value += self._read_u8()
        self._record("argument", value)

    def _read_type(self) -> None:
        value_type = self._read_u8()
        if value_type == 0x00:
            return
        if value_type == 0x01:
            self._record("literal", self._read_int(2, signed=True))
        elif value_type in {0x02, 0x03, 0x04}:
            self._record("literal", self._read_u8())
        elif value_type == 0x06:
            self._read_strcode()
        elif value_type == 0x07:
            length = self._read_u8()
            self._require(length, "string")
            self.position += length
        elif value_type == 0x08:
            self._record("literal", self._read_int(2))
        elif value_type == 0x09:
            self._record("literal", self._read_int(4, signed=True))
        elif value_type == 0x0A:
            self._record("literal", self._read_int(4))
        elif value_type == 0x0D:
            self._read_strcode()
            self._read_u8()
        elif value_type == 0x0E:
            self._record("literal", self._read_int(2))

    def _process(self) -> None:
        self._require(1, "value")
        tag = self.data[self.position] & 0xF0
        if tag in {0xC0, 0xD0, 0xE0, 0xF0}:
            self._record("literal", (self._read_u8() & 0x3F) - 1)
        elif tag == 0x90:
            self._read_u8()
            self._record("local", 0)
        elif tag == 0x80:
            self._read_procedure()
        elif tag == 0x70:
            size = self._get_size()
            start = self.position
            self._record("literal", self._read_int(2, signed=True))
            self._process_for(start, size)
        elif tag == 0x60:
            self._read_command()
        elif tag == 0x50:
            self._read_parameter()
        elif tag == 0x40:
            self._read_argument()
        elif tag == 0x30:
            self._read_expression()
        elif tag in {0x10, 0x20}:
            self._read_variable()
        elif tag:
            self.position += 1
        else:
            self._read_type()


def parse_gcx(data: bytes) -> GcxDocument:
    if len(data) < 28:
        raise FormatError("GCX file is truncated")

    position = 4
    definitions = []
    while True:
        if len(definitions) >= _MAX_DEFINITIONS:
            raise FormatError("Implausible GCX script definition count")
        if position > len(data) - 4:
            raise FormatError("GCX script definition table is not terminated")
        packed = struct.unpack_from("<I", data, position)[0]
        position += 4
        if packed == 0xFFFFFFFF:
            break
        definitions.append((packed >> 24, packed & 0xFFFFFF))

    header_position = position
    if header_position > len(data) - 20:
        raise FormatError("GCX header is truncated")
    (
        script_section_offset,
        string_definitions_offset,
        string_section_offset,
        duplicate_script_offset,
        _crypto_seed,
    ) = struct.unpack_from("<iiiii", data, header_position)
    if (
        string_definitions_offset < 20
        or string_section_offset < string_definitions_offset
        or script_section_offset < string_section_offset
        or duplicate_script_offset != script_section_offset
    ):
        raise FormatError("GCX header contains inconsistent section offsets")

    section_position = header_position + script_section_offset
    if section_position < 0 or section_position > len(data) - 4:
        raise FormatError("GCX script section lies outside the file")
    section_size = struct.unpack_from("<i", data, section_position)[0]
    section_data_position = section_position + 4
    if (
        section_size < 0
        or section_size > _MAX_SCRIPT_SIZE
        or section_size > len(data) - section_data_position
    ):
        raise FormatError(f"Invalid GCX script section size: {section_size}")

    offsets = sorted({offset for _kind, offset in definitions})
    if offsets and offsets[0] != 0:
        raise FormatError("GCX script data does not start at offset zero")
    if any(offset < 0 or offset > section_size for offset in offsets):
        raise FormatError("GCX script definition lies outside the script section")

    scripts = []
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else section_size
        script = data[
            section_data_position + offset : section_data_position + end
        ]
        if script:
            scripts.append(_ScriptDecoder(script).decode())

    main_length_position = section_data_position + section_size
    if main_length_position > len(data) - 4:
        raise FormatError("GCX main script length is missing")
    main_size = struct.unpack_from("<i", data, main_length_position)[0]
    main_position = main_length_position + 4
    if (
        main_size < 0
        or main_size > _MAX_SCRIPT_SIZE
        or main_size > len(data) - main_position
    ):
        raise FormatError(f"Invalid GCX main script size: {main_size}")
    if main_size:
        scripts.insert(
            0,
            _ScriptDecoder(data[main_position : main_position + main_size]).decode(),
        )
    return GcxDocument(tuple(scripts))


def _parameter(
    command: GcxCommand,
    name_hash: int,
    letter: str | None = None,
) -> GcxParameter | None:
    letter_value = ord(letter) if letter is not None else None
    return next(
        (
            item
            for item in command.parameters
            if item.name_hash == name_hash
            and (letter_value is None or item.letter == letter_value)
        ),
        None,
    )


def _first_literal(parameter: GcxParameter | None) -> int | None:
    if parameter is None:
        return None
    return next(
        (value.value for value in parameter.values if value.kind == "literal"),
        None,
    )


def _foreach_data(
    script: GcxScript,
    command: GcxCommand,
) -> tuple[tuple[GcxValue, ...], int] | None:
    parent_index = command.parent_index
    while parent_index is not None:
        parent = script.commands[parent_index]
        data = _parameter(parent, _DATA_HASH)
        argument_count = _first_literal(
            _parameter(parent, _ARGUMENT_COUNT_HASH)
        )
        if data is not None and argument_count is not None and argument_count > 0:
            return data.values, argument_count
        parent_index = parent.parent_index
    return None


def _resolve_values(
    parameter: GcxParameter | None,
    row: int | None,
    foreach_data: tuple[tuple[GcxValue, ...], int] | None,
) -> tuple[GcxValue, ...]:
    if parameter is None:
        return ()
    result = []
    for value in parameter.values:
        if value.kind != "argument":
            result.append(value)
            continue
        if foreach_data is None or row is None:
            continue
        data, argument_count = foreach_data
        if not 1 <= value.value <= argument_count:
            continue
        index = row * argument_count + value.value - 1
        if 0 <= index < len(data):
            result.append(data[index])
    return tuple(result)


def _hash_value(values: tuple[GcxValue, ...], *, last: bool = False) -> int:
    hashes = [
        value.value & 0xFFFFFFFF
        for value in values
        if value.kind in {"strcode", "literal"}
    ]
    if not hashes:
        return 0
    return hashes[-1] if last else hashes[0]


def _vector_value(
    values: tuple[GcxValue, ...],
    *,
    swap_yz: bool = False,
) -> tuple[float, float, float] | None:
    components = [
        value.value for value in values if value.kind == "literal"
    ]
    if len(components) < 3:
        return None
    if swap_yz:
        return float(components[0]), float(components[2]), float(components[1])
    return float(components[0]), float(components[1]), float(components[2])


def _game_angle(value: float) -> float:
    signed = ((round(value) + 32768) & 0xFFFF) - 32768
    return signed * math.pi / 32768.0


def _effect_placements(
    model_hashes: tuple[int, ...],
    command_hash: int,
    effect_hash: int,
    effects_by_hash: dict[int, GeomEffect],
) -> tuple[StagePlacement, ...]:
    effect = effects_by_hash.get(effect_hash)
    if effect is None:
        return tuple(
            StagePlacement(
                model_hash,
                None,
                None,
                command_hash,
                effect_hash=effect_hash,
            )
            for model_hash in model_hashes
        )
    return tuple(
        StagePlacement(
            model_hash,
            effect.position,
            effect.rotation,
            command_hash,
            effect_hash=effect_hash,
        )
        for model_hash in model_hashes
    )


def extract_stage_layout(
    document: GcxDocument,
    effects: GeomEffects | None = None,
) -> StageLayout:
    effects = effects or GeomEffects(())
    effects_by_hash = effects.by_hash()
    effect_children = effects.children_by_hash()
    stage_hashes: list[int] = []
    placements: list[StagePlacement] = []

    for script in document.scripts:
        for command in script.commands:
            if command.name_hash not in _MODEL_PLACEMENT_COMMANDS:
                continue
            foreach_data = _foreach_data(script, command)
            row_count = 1
            if foreach_data is not None:
                values, argument_count = foreach_data
                row_count = len(values) // argument_count

            model_parameter = _parameter(command, _MODEL_HASH, "m")
            glass_parameter = _parameter(command, _MODEL_GLASS_HASH)
            position_parameter = _parameter(command, _POSITION_HASH, "p")
            direction_parameter = _parameter(command, _DIRECTION_HASH, "d")
            effect_parameter = _parameter(command, _EFFECT_HASH, "e")
            property_parameter = _parameter(command, _PROPERTY_POSITION_HASH)
            property_uses_children = property_parameter is not None
            if property_parameter is None:
                property_parameter = _parameter(command, _PROPERTY_HASH)

            for row in range(row_count):
                row_value = row if foreach_data is not None else None
                model_hashes = tuple(
                    dict.fromkeys(
                        value
                        for value in (
                            _hash_value(
                                _resolve_values(
                                    model_parameter,
                                    row_value,
                                    foreach_data,
                                )
                            ),
                            _hash_value(
                                _resolve_values(
                                    glass_parameter,
                                    row_value,
                                    foreach_data,
                                )
                            ),
                        )
                        if value
                    )
                )
                if not model_hashes:
                    continue

                position = _vector_value(
                    _resolve_values(
                        position_parameter,
                        row_value,
                        foreach_data,
                    ),
                    swap_yz=True,
                )
                raw_rotation = _vector_value(
                    _resolve_values(
                        direction_parameter,
                        row_value,
                        foreach_data,
                    )
                )
                rotation = (
                    tuple(_game_angle(value) for value in raw_rotation)
                    if raw_rotation is not None
                    else None
                )
                effect_hash = _hash_value(
                    _resolve_values(
                        effect_parameter,
                        row_value,
                        foreach_data,
                    )
                )
                property_hash = _hash_value(
                    _resolve_values(
                        property_parameter,
                        row_value,
                        foreach_data,
                    ),
                    last=not property_uses_children,
                )

                has_transform = (
                    position is not None
                    or rotation is not None
                    or effect_hash != 0
                    or property_hash != 0
                )
                if (
                    command.name_hash == _NEW_PUT_STAGE_MODEL_SET
                    and not has_transform
                ):
                    stage_hashes.extend(model_hashes)
                    continue

                if effect_hash:
                    placements.extend(
                        _effect_placements(
                            model_hashes,
                            command.name_hash,
                            effect_hash,
                            effects_by_hash,
                        )
                    )
                    continue

                if property_hash:
                    property_effects = (
                        effect_children.get(property_hash, ())
                        if property_uses_children
                        else ()
                    )
                    if not property_effects:
                        direct = effects_by_hash.get(property_hash)
                        property_effects = (direct,) if direct is not None else ()
                    if property_effects:
                        for effect in property_effects:
                            for model_hash in model_hashes:
                                placements.append(
                                    StagePlacement(
                                        model_hash,
                                        effect.position,
                                        effect.rotation,
                                        command.name_hash,
                                        property_hash=property_hash,
                                    )
                                )
                        continue

                if command.name_hash == _NEW_PUT_OBJECT or has_transform:
                    for model_hash in model_hashes:
                        placements.append(
                            StagePlacement(
                                model_hash,
                                position,
                                rotation,
                                command.name_hash,
                                property_hash=property_hash or None,
                            )
                        )

    return StageLayout(
        tuple(dict.fromkeys(stage_hashes)),
        tuple(dict.fromkeys(placements)),
    )
