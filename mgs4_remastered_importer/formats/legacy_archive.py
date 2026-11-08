from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

from ..errors import AssetNotFoundError, FormatError


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _read_cstring(stream, file_size: int) -> str:
    value = bytearray()
    while stream.tell() < file_size:
        byte = stream.read(1)
        if byte == b"\0":
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.decode("latin-1")
        if not byte:
            break
        value.extend(byte)
        if len(value) > 32_768:
            raise FormatError("Archive filename is unreasonably long")
    raise FormatError("Unterminated archive filename")


@dataclass(frozen=True, slots=True)
class LegacyArchiveEntry:
    name: str
    offset: int
    size: int
    file_info: int = 0


class DarArchive:
    """Read-only parser for the big-endian DAR layout documented by Solideye."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries: tuple[LegacyArchiveEntry, ...] | None = None

    @property
    def entries(self) -> tuple[LegacyArchiveEntry, ...]:
        if self._entries is None:
            self._entries = self._read_entries()
        return self._entries

    def _read_entries(self) -> tuple[LegacyArchiveEntry, ...]:
        try:
            file_size = self.path.stat().st_size
        except OSError as exc:
            raise AssetNotFoundError(f"Cannot open DAR: {self.path}") from exc
        if file_size < 4:
            raise FormatError("DAR is too small")

        entries = []
        with self.path.open("rb") as stream:
            count = struct.unpack(">I", stream.read(4))[0]
            if count > 1_000_000:
                raise FormatError(f"Implausible DAR entry count: {count}")
            for _ in range(count):
                name = _read_cstring(stream, file_size).replace("\\", "/")
                stream.seek(_align(stream.tell(), 4))
                size_data = stream.read(4)
                if len(size_data) != 4:
                    raise FormatError("Truncated DAR entry size")
                size = struct.unpack(">I", size_data)[0]
                data_offset = _align(stream.tell(), 16)
                if data_offset > file_size or size > file_size - data_offset:
                    raise FormatError(f"DAR entry lies outside the file: {name}")
                entries.append(LegacyArchiveEntry(name, data_offset, size))
                stream.seek(data_offset + size + 1)
        return tuple(entries)

    def fetch(self, entry: LegacyArchiveEntry) -> bytes:
        with self.path.open("rb") as stream:
            stream.seek(entry.offset)
            data = stream.read(entry.size)
        if len(data) != entry.size:
            raise FormatError(f"Truncated DAR entry: {entry.name}")
        return data


class QarArchive:
    """Read-only parser for the big-endian QAR layout documented by Solideye."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries: tuple[LegacyArchiveEntry, ...] | None = None

    @property
    def entries(self) -> tuple[LegacyArchiveEntry, ...]:
        if self._entries is None:
            self._entries = self._read_entries()
        return self._entries

    def _read_entries(self) -> tuple[LegacyArchiveEntry, ...]:
        try:
            file_size = self.path.stat().st_size
        except OSError as exc:
            raise AssetNotFoundError(f"Cannot open QAR: {self.path}") from exc
        if file_size < 8:
            raise FormatError("QAR is too small")

        with self.path.open("rb") as stream:
            stream.seek(file_size - 4)
            table_offset = struct.unpack(">I", stream.read(4))[0]
            if table_offset > file_size - 8:
                raise FormatError("QAR table lies outside the file")
            stream.seek(table_offset)
            count_data = stream.read(4)
            if len(count_data) != 4:
                raise FormatError("Truncated QAR header")
            count, _padding = struct.unpack(">HH", count_data)

            metadata = []
            for _ in range(count):
                row = stream.read(8)
                if len(row) != 8:
                    raise FormatError("Truncated QAR entry table")
                metadata.append(struct.unpack(">II", row))
            names = [
                _read_cstring(stream, file_size).replace("\\", "/")
                for _ in range(count)
            ]

        entries = []
        data_offset = 0
        for (file_info, size), name in zip(metadata, names):
            if data_offset > table_offset or size > table_offset - data_offset:
                raise FormatError(f"QAR entry lies outside the data region: {name}")
            entries.append(
                LegacyArchiveEntry(name, data_offset, size, file_info=file_info)
            )
            data_offset = _align(data_offset + size, 0x80)
        return tuple(entries)

    def fetch(self, entry: LegacyArchiveEntry) -> bytes:
        with self.path.open("rb") as stream:
            stream.seek(entry.offset)
            data = stream.read(entry.size)
        if len(data) != entry.size:
            raise FormatError(f"Truncated QAR entry: {entry.name}")
        return data
