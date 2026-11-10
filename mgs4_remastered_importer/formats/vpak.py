from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Iterable

from ..errors import AssetNotFoundError, FormatError


VPAK_HEADER = struct.Struct("<4sHHII")
MAX_ENTRY_COUNT = 2_000_000


@dataclass(frozen=True, slots=True)
class VpakRecord:
    name: str
    flags: int
    uncompressed_size: int
    compressed_size: int
    offset: int
    chunk_size: int
    chunk_offsets: tuple[int, ...]

    @property
    def stored_size(self) -> int:
        return self.compressed_size or self.uncompressed_size


def decompress_lz4_block(source: bytes, expected_size: int | None = None) -> bytes:
    if expected_size is not None and expected_size < 0:
        raise FormatError(f"Invalid LZ4 output size {expected_size}")

    output = bytearray(expected_size or 0)
    output_view = memoryview(output) if expected_size is not None else None
    output_position = 0
    position = 0
    source_size = len(source)
    source_view = memoryview(source)

    while position < source_size:
        token = source[position]
        position += 1

        literal_length = token >> 4
        if literal_length == 15:
            while True:
                if position >= source_size:
                    raise FormatError("Truncated LZ4 literal length")
                extension = source[position]
                position += 1
                literal_length += extension
                if extension != 255:
                    break

        literal_end = position + literal_length
        if literal_end > source_size:
            raise FormatError("LZ4 literal extends beyond the source block")
        next_output = output_position + literal_length
        if expected_size is not None and next_output > expected_size:
            raise FormatError("LZ4 output exceeds its declared size")
        if output_view is None:
            output[output_position:next_output] = source_view[
                position:literal_end
            ]
        else:
            output_view[output_position:next_output] = source_view[
                position:literal_end
            ]
        output_position = next_output
        position = literal_end

        if position == source_size:
            break
        if position + 2 > source_size:
            raise FormatError("Truncated LZ4 match offset")

        match_offset = source[position] | (source[position + 1] << 8)
        position += 2
        if match_offset == 0 or match_offset > output_position:
            raise FormatError(f"Invalid LZ4 match offset {match_offset}")

        match_length = token & 0x0F
        if match_length == 15:
            while True:
                if position >= source_size:
                    raise FormatError("Truncated LZ4 match length")
                extension = source[position]
                position += 1
                match_length += extension
                if extension != 255:
                    break
        match_length += 4

        next_output = output_position + match_length
        if expected_size is not None and next_output > expected_size:
            raise FormatError("LZ4 match exceeds its declared output size")
        if match_length <= match_offset:
            match_start = output_position - match_offset
            if output_view is None:
                output[output_position:next_output] = output[
                    match_start : match_start + match_length
                ]
            else:
                output_view[output_position:next_output] = output_view[
                    match_start : match_start + match_length
                ]
        else:
            pattern = bytes(
                output_view[
                    output_position - match_offset : output_position
                ]
                if output_view is not None
                else output[
                    output_position - match_offset : output_position
                ]
            )
            repeats = (match_length + match_offset - 1) // match_offset
            repeated = (pattern * repeats)[:match_length]
            if output_view is None:
                output[output_position:next_output] = repeated
            else:
                output_view[output_position:next_output] = repeated
        output_position = next_output

    if expected_size is not None and output_position != expected_size:
        raise FormatError(
            f"LZ4 size mismatch: expected {expected_size}, "
            f"decoded {output_position}"
        )
    return bytes(output)


class VpakArchive:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.version = (0, 0)
        self.flags = 0
        self._records: dict[str, VpakRecord] | None = None

    @property
    def records(self) -> dict[str, VpakRecord]:
        if self._records is None:
            self._records = self._read_toc()
        return self._records

    def _read_toc(self) -> dict[str, VpakRecord]:
        try:
            file_size = self.path.stat().st_size
        except OSError as exc:
            raise AssetNotFoundError(f"Cannot open VPAK: {self.path}") from exc

        if file_size < VPAK_HEADER.size:
            raise FormatError(f"VPAK is too small: {self.path}")

        with self.path.open("rb") as stream:
            header_data = stream.read(VPAK_HEADER.size)
            magic, major, minor, entry_count, toc_size = VPAK_HEADER.unpack(header_data)
            if magic != b"VPAK":
                raise FormatError(f"Not a VPAK archive: {self.path}")
            if entry_count > MAX_ENTRY_COUNT:
                raise FormatError(f"Implausible VPAK entry count: {entry_count}")
            if toc_size > file_size - VPAK_HEADER.size:
                raise FormatError("VPAK table of contents lies outside the file")

            toc_offset = file_size - toc_size
            stream.seek(toc_offset)
            toc_data = stream.read(toc_size)
            if len(toc_data) != toc_size:
                raise FormatError("Could not read the complete VPAK table of contents")

        self.version = (major, minor)
        self.flags = minor
        records: dict[str, VpakRecord] = {}
        position = 0

        for record_index in range(entry_count):
            if position + 6 > len(toc_data):
                raise FormatError(f"Truncated VPAK record {record_index}")
            name_length, record_flags = struct.unpack_from("<IH", toc_data, position)
            name_start = position + 6
            name_end = name_start + name_length
            fixed_end = name_end + 40
            if name_length == 0 or fixed_end > len(toc_data):
                raise FormatError(f"Invalid VPAK name length in record {record_index}")

            name_bytes = toc_data[name_start:name_end].rstrip(b"\0")
            try:
                name = name_bytes.decode("utf-8")
            except UnicodeDecodeError:
                name = name_bytes.decode("latin-1")

            (
                reserved,
                uncompressed_size,
                compressed_size,
                data_offset,
                chunk_size,
                chunk_count,
            ) = struct.unpack_from("<QQQQII", toc_data, name_end)
            if reserved != 0:
                raise FormatError(f"Unexpected VPAK reserved value in {name}")
            if chunk_count > MAX_ENTRY_COUNT:
                raise FormatError(f"Implausible VPAK chunk count in {name}")

            chunks_end = fixed_end + chunk_count * 8
            if chunks_end > len(toc_data):
                raise FormatError(f"Truncated VPAK chunk table in {name}")
            chunks = (
                struct.unpack_from(f"<{chunk_count}Q", toc_data, fixed_end)
                if chunk_count
                else ()
            )

            stored_size = compressed_size or uncompressed_size
            if data_offset > toc_offset or stored_size > toc_offset - data_offset:
                raise FormatError(f"VPAK entry data lies outside the body: {name}")
            if chunks:
                if chunks[0] != 0:
                    raise FormatError(f"VPAK chunk table does not start at zero: {name}")
                if any(left >= right for left, right in zip(chunks, chunks[1:])):
                    raise FormatError(f"VPAK chunk offsets are not increasing: {name}")
                if stored_size and chunks[-1] >= stored_size:
                    raise FormatError(f"VPAK chunk offset exceeds entry size: {name}")

            normalized_name = name.replace("\\", "/")
            records[normalized_name] = VpakRecord(
                name=normalized_name,
                flags=record_flags,
                uncompressed_size=uncompressed_size,
                compressed_size=compressed_size,
                offset=data_offset,
                chunk_size=chunk_size,
                chunk_offsets=tuple(chunks),
            )
            position = chunks_end

        if position != len(toc_data):
            trailing = toc_data[position:]
            if any(trailing):
                raise FormatError(
                    f"VPAK table has {len(trailing)} unexpected trailing bytes"
                )
        return records

    def get(self, name: str) -> VpakRecord:
        normalized = name.replace("\\", "/")
        try:
            return self.records[normalized]
        except KeyError as exc:
            raise AssetNotFoundError(
                f"{normalized} is not present in {self.path.name}"
            ) from exc

    def fetch(self, record_or_name: VpakRecord | str) -> bytes:
        record = (
            self.get(record_or_name)
            if isinstance(record_or_name, str)
            else record_or_name
        )
        with self.path.open("rb") as stream:
            stream.seek(record.offset)
            raw = stream.read(record.stored_size)
        if len(raw) != record.stored_size:
            raise FormatError(f"Truncated VPAK entry: {record.name}")
        if record.compressed_size == 0:
            return raw

        chunks = record.chunk_offsets
        if len(chunks) <= 1:
            return decompress_lz4_block(raw, record.uncompressed_size)
        if record.chunk_size <= 0:
            raise FormatError(f"Missing VPAK chunk size for {record.name}")

        output = bytearray()
        chunk_ends = (*chunks[1:], len(raw))
        for chunk_start, chunk_end in zip(chunks, chunk_ends):
            wanted = min(
                record.chunk_size,
                record.uncompressed_size - len(output),
            )
            if wanted <= 0:
                raise FormatError(f"Too many VPAK chunks for {record.name}")
            output.extend(
                decompress_lz4_block(raw[chunk_start:chunk_end], wanted)
            )

        if len(output) != record.uncompressed_size:
            raise FormatError(
                f"VPAK size mismatch for {record.name}: "
                f"expected {record.uncompressed_size}, decoded {len(output)}"
            )
        return bytes(output)

    def iter_extension(self, *extensions: str) -> Iterable[VpakRecord]:
        normalized = tuple(
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in extensions
        )
        for record in self.records.values():
            if record.name.lower().endswith(normalized):
                yield record
