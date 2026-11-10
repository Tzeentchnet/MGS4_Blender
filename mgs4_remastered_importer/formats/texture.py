from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
from typing import Final
import warnings

from ..errors import AssetNotFoundError, FormatError
from .vpak import VpakArchive, VpakRecord


FORMAT_RGBA32: Final = 0x03
FORMAT_DXT1: Final = 0x09
FORMAT_DXT3: Final = 0x0A
FORMAT_DXT5: Final = 0x0B

FORMAT_NAMES = {
    FORMAT_RGBA32: "RGBA32",
    FORMAT_DXT1: "DXT1",
    FORMAT_DXT3: "DXT3",
    FORMAT_DXT5: "DXT5",
}
FOURCC = {
    FORMAT_DXT1: b"DXT1",
    FORMAT_DXT3: b"DXT3",
    FORMAT_DXT5: b"DXT5",
}
TXN_SLOT_SIZE: Final = 256
TXN_SLOT_SIZE_OFFSET: Final = 0xFC
SOURCE_INDEX_CACHE_VERSION: Final = 1
SOURCE_INDEX_CACHE_NAME: Final = ".mgs4-texture-sources-v1.json"


@dataclass(frozen=True, slots=True)
class TextureSource:
    texture_hash: int
    width: int
    height: int
    format: int
    mip_flags: int
    declared_size: int
    blob_path: str
    region: str
    index_archive_path: Path
    data_archive_path: Path


@dataclass(frozen=True, slots=True)
class _TxnImage:
    width: int
    height: int
    format: int
    mip_flags: int
    start: int
    end: int
    blob_path: str
    blob_size: int


def _require_span(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise FormatError(
            f"{label} lies outside the TXN descriptor "
            f"(offset 0x{offset:X}, size 0x{size:X})"
        )


def _parse_txn_sources(
    data: bytes,
    *,
    region: str,
    index_archive_path: Path,
    data_archive_path: Path,
) -> tuple[TextureSource, ...]:
    if len(data) < 32:
        raise FormatError("TXN descriptor is too small")
    (
        _unknown,
        _flags,
        image_count,
        image_offset,
        texture_count,
        texture_offset,
        _zero_1,
        _zero_2,
    ) = struct.unpack_from(">8I", data, 0)
    if image_count > 100_000 or texture_count > 100_000:
        raise FormatError("TXN descriptor has implausible table counts")

    _require_span(data, image_offset, image_count * 16, "TXN image table")
    _require_span(data, texture_offset, texture_count * 48, "TXN texture table")
    tail_offset = max(
        image_offset + image_count * 16,
        texture_offset + texture_count * 48,
    )
    _require_span(
        data,
        tail_offset,
        image_count * TXN_SLOT_SIZE,
        "TXN blob-path slots",
    )

    images = []
    for image_index in range(image_count):
        offset = image_offset + image_index * 16
        dimensions, format_word, start, end = struct.unpack_from(">4I", data, offset)
        if end < start:
            raise FormatError("TXN image has a negative payload size")
        slot = data[
            tail_offset
            + image_index * TXN_SLOT_SIZE : tail_offset
            + (image_index + 1) * TXN_SLOT_SIZE
        ]
        path_bytes = slot.split(b"\0", 1)[0]
        try:
            blob_path = path_bytes.decode("utf-8")
        except UnicodeDecodeError:
            blob_path = path_bytes.decode("latin-1")
        blob_size = struct.unpack_from("<I", slot, TXN_SLOT_SIZE_OFFSET)[0]
        declared_size = end - start
        if blob_path and blob_size != declared_size:
            raise FormatError(
                f"TXN blob size mismatch for {blob_path}: "
                f"image {declared_size}, slot {blob_size}"
            )
        images.append(
            _TxnImage(
                width=dimensions >> 16,
                height=dimensions & 0xFFFF,
                format=format_word >> 16,
                mip_flags=format_word & 0xFFFF,
                start=start,
                end=end,
                blob_path=blob_path.replace("\\", "/"),
                blob_size=blob_size,
            )
        )

    sources = []
    for texture_index in range(texture_count):
        offset = texture_offset + texture_index * 48
        values = struct.unpack_from(">12I", data, offset)
        texture_hash = values[1]
        image_reference = values[5]
        if image_reference < image_offset:
            raise FormatError("TXN texture image reference precedes the image table")
        relative = image_reference - image_offset
        if relative % 16:
            raise FormatError("TXN texture image reference is not record-aligned")
        image_index = relative // 16
        if image_index >= len(images):
            raise FormatError("TXN texture references a missing image descriptor")
        image = images[image_index]
        if not image.blob_path:
            continue
        sources.append(
            TextureSource(
                texture_hash=texture_hash,
                width=image.width,
                height=image.height,
                format=image.format,
                mip_flags=image.mip_flags,
                declared_size=image.end - image.start,
                blob_path=image.blob_path,
                region=region,
                index_archive_path=index_archive_path,
                data_archive_path=data_archive_path,
            )
        )
    return tuple(sources)


def _level_size(width: int, height: int, texture_format: int) -> int:
    if texture_format in (FORMAT_DXT1, FORMAT_DXT3, FORMAT_DXT5):
        block_bytes = 8 if texture_format == FORMAT_DXT1 else 16
        return (
            max(1, (width + 3) // 4)
            * max(1, (height + 3) // 4)
            * block_bytes
        )
    if texture_format == FORMAT_RGBA32:
        return width * height * 4
    raise FormatError(f"Unsupported MGS4 texture format 0x{texture_format:X}")


def _mip_count(
    width: int, height: int, total_size: int, texture_format: int
) -> int:
    count = 0
    accumulated = 0
    current_width = width
    current_height = height
    while accumulated < total_size:
        accumulated += _level_size(current_width, current_height, texture_format)
        count += 1
        if accumulated >= total_size or (
            current_width == 1 and current_height == 1
        ):
            break
        current_width = max(1, current_width // 2)
        current_height = max(1, current_height // 2)
    return max(1, count)


def _dds_header(
    width: int,
    height: int,
    texture_format: int,
    payload_size: int,
) -> bytes:
    fourcc = FOURCC[texture_format]
    mip_count = _mip_count(width, height, payload_size, texture_format)
    flags = 0x000A1007
    caps = 0x00001000
    if mip_count > 1:
        flags |= 0x00020000
        caps |= 0x00400008

    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into(
        "<7I",
        header,
        4,
        124,
        flags,
        height,
        width,
        _level_size(width, height, texture_format),
        0,
        mip_count,
    )
    struct.pack_into("<2I", header, 76, 32, 0x4)
    header[84:88] = fourcc
    struct.pack_into("<I", header, 108, caps)
    return bytes(header)


def _write_tga(path: Path, payload: bytes, width: int, height: int) -> None:
    required = width * height * 4
    if len(payload) < required:
        raise FormatError(
            f"RGBA texture is truncated: expected {required}, got {len(payload)}"
        )
    header = bytearray(18)
    header[2] = 2
    struct.pack_into("<HH", header, 12, width, height)
    header[16] = 32
    header[17] = 0x28

    source = payload[:required]
    pixels = bytearray(required)
    pixels[0::4] = source[3::4]
    pixels[1::4] = source[2::4]
    pixels[2::4] = source[1::4]
    pixels[3::4] = source[0::4]
    path.write_bytes(bytes(header) + pixels)


class TextureResolver:
    def __init__(self, game_root: str | Path, cache_root: str | Path):
        self.game_root = Path(game_root)
        self.cache_root = Path(cache_root)
        self._sources: dict[int, list[TextureSource]] | None = None
        self._archives: dict[Path, VpakArchive] = {}

    def _archive(self, path: Path) -> VpakArchive:
        archive = self._archives.get(path)
        if archive is None:
            archive = VpakArchive(path)
            self._archives[path] = archive
        return archive

    def _texture_trees(
        self,
    ) -> tuple[tuple[str, tuple[Path, ...], Path], ...]:
        trees = []
        for region in ("common", "ww"):
            base = self.game_root / region / "textures" / "PC_TXN_UP"
            data_archive_path = base / "paks" / "TextureData.pak"
            index_paths = tuple(
                path
                for path in (base / "txn_up.1.pak", base / "txn_up.2.pak")
                if path.is_file()
            )
            if not index_paths or not data_archive_path.is_file():
                continue
            trees.append((region, index_paths, data_archive_path))

        if not trees:
            raise AssetNotFoundError(
                f"No PC_TXN_UP texture archives were found under {self.game_root}"
            )
        return tuple(trees)

    def _source_index_fingerprint(
        self,
        trees: tuple[tuple[str, tuple[Path, ...], Path], ...],
    ) -> list[list[str | int]]:
        fingerprint = []
        for _region, index_paths, data_archive_path in trees:
            for path in (*index_paths, data_archive_path):
                stat = path.stat()
                fingerprint.append(
                    [
                        path.relative_to(self.game_root).as_posix(),
                        stat.st_size,
                        stat.st_mtime_ns,
                    ]
                )
        return fingerprint

    def _load_source_index(
        self,
        trees: tuple[tuple[str, tuple[Path, ...], Path], ...],
        fingerprint: list[list[str | int]],
    ) -> dict[int, list[TextureSource]] | None:
        cache_path = self.cache_root / SOURCE_INDEX_CACHE_NAME
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            warnings.warn(
                f"Ignoring unreadable texture source cache {cache_path}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

        if not isinstance(payload, dict):
            warnings.warn(
                f"Ignoring invalid texture source cache {cache_path}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None
        if (
            payload.get("version") != SOURCE_INDEX_CACHE_VERSION
            or payload.get("game_root")
            != os.path.normcase(str(self.game_root.resolve()))
            or payload.get("fingerprint") != fingerprint
        ):
            return None

        known_groups = {
            (region, index_path.name): (index_path, data_archive_path)
            for region, index_paths, data_archive_path in trees
            for index_path in index_paths
        }
        sources: dict[int, list[TextureSource]] = {}
        seen_groups = set()
        source_count = 0
        try:
            groups = payload["groups"]
            if not isinstance(groups, list):
                raise ValueError("groups must be a list")
            for group in groups:
                if not isinstance(group, list) or len(group) != 3:
                    raise ValueError("invalid archive group")
                region, index_name, records = group
                if not isinstance(region, str) or not isinstance(index_name, str):
                    raise ValueError("invalid archive group name")
                group_key = (region, index_name)
                if group_key not in known_groups or group_key in seen_groups:
                    raise ValueError("unknown or duplicate archive group")
                if not isinstance(records, list):
                    raise ValueError("archive records must be a list")
                seen_groups.add(group_key)
                index_archive_path, data_archive_path = known_groups[group_key]

                for record in records:
                    if not isinstance(record, list) or len(record) != 7:
                        raise ValueError("invalid texture source record")
                    (
                        texture_hash,
                        width,
                        height,
                        texture_format,
                        mip_flags,
                        declared_size,
                        blob_path,
                    ) = record
                    integers = (
                        texture_hash,
                        width,
                        height,
                        texture_format,
                        mip_flags,
                        declared_size,
                    )
                    if not all(isinstance(value, int) for value in integers):
                        raise ValueError("texture source values must be integers")
                    if (
                        not 0 <= texture_hash <= 0xFFFFFFFF
                        or width <= 0
                        or height <= 0
                        or texture_format < 0
                        or mip_flags < 0
                        or declared_size < 0
                        or not isinstance(blob_path, str)
                        or not blob_path
                    ):
                        raise ValueError("invalid texture source values")
                    source = TextureSource(
                        texture_hash=texture_hash,
                        width=width,
                        height=height,
                        format=texture_format,
                        mip_flags=mip_flags,
                        declared_size=declared_size,
                        blob_path=blob_path,
                        region=region,
                        index_archive_path=index_archive_path,
                        data_archive_path=data_archive_path,
                    )
                    sources.setdefault(texture_hash, []).append(source)
                    source_count += 1

            if seen_groups != set(known_groups):
                raise ValueError("texture source archive groups are incomplete")
            if (
                payload.get("source_count") != source_count
                or payload.get("hash_count") != len(sources)
            ):
                raise ValueError("texture source counts do not match")
        except (KeyError, TypeError, ValueError) as exc:
            warnings.warn(
                f"Ignoring invalid texture source cache {cache_path}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            return None
        return sources

    def _scan_sources(
        self,
        trees: tuple[tuple[str, tuple[Path, ...], Path], ...],
    ) -> dict[int, list[TextureSource]]:
        sources: dict[int, list[TextureSource]] = {}
        for region, index_paths, data_archive_path in trees:
            for index_path in index_paths:
                archive = self._archive(index_path)
                for record in archive.iter_extension(".txn"):
                    descriptor = archive.fetch(record)
                    for source in _parse_txn_sources(
                        descriptor,
                        region=region,
                        index_archive_path=index_path,
                        data_archive_path=data_archive_path,
                    ):
                        sources.setdefault(source.texture_hash, []).append(source)
        return sources

    def _write_source_index(
        self,
        trees: tuple[tuple[str, tuple[Path, ...], Path], ...],
        fingerprint: list[list[str | int]],
        sources: dict[int, list[TextureSource]],
    ) -> None:
        grouped = {
            (region, index_path.name): []
            for region, index_paths, _data_archive_path in trees
            for index_path in index_paths
        }
        source_count = 0
        for matches in sources.values():
            for source in matches:
                grouped[(source.region, source.index_archive_path.name)].append(
                    [
                        source.texture_hash,
                        source.width,
                        source.height,
                        source.format,
                        source.mip_flags,
                        source.declared_size,
                        source.blob_path,
                    ]
                )
                source_count += 1

        payload = {
            "version": SOURCE_INDEX_CACHE_VERSION,
            "game_root": os.path.normcase(str(self.game_root.resolve())),
            "fingerprint": fingerprint,
            "source_count": source_count,
            "hash_count": len(sources),
            "groups": [
                [region, index_name, records]
                for (region, index_name), records in grouped.items()
            ],
        }
        cache_path = self.cache_root / SOURCE_INDEX_CACHE_NAME
        temporary = cache_path.with_name(f"{cache_path.name}.tmp-{os.getpid()}")
        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(cache_path)
        except OSError as exc:
            warnings.warn(
                f"Could not write texture source cache {cache_path}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                warnings.warn(
                    f"Could not remove temporary texture source cache "
                    f"{temporary}: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

    def _build_sources(self) -> dict[int, list[TextureSource]]:
        trees = self._texture_trees()
        fingerprint = self._source_index_fingerprint(trees)
        sources = self._load_source_index(trees, fingerprint)
        if sources is not None:
            return sources

        sources = self._scan_sources(trees)
        self._write_source_index(trees, fingerprint, sources)
        return sources

    @property
    def sources(self) -> dict[int, list[TextureSource]]:
        if self._sources is None:
            self._sources = self._build_sources()
        return self._sources

    def resolve(self, texture_hash: int, preferred_region: str = "common") -> TextureSource:
        matches = self.sources.get(texture_hash)
        if not matches:
            raise AssetNotFoundError(f"Texture {texture_hash:08X} was not found")
        return next(
            (source for source in matches if source.region == preferred_region),
            matches[0],
        )

    def _fetch_payload(self, source: TextureSource) -> bytes:
        archive = self._archive(source.data_archive_path)
        try:
            record: VpakRecord = archive.get(source.blob_path)
        except AssetNotFoundError as exc:
            raise AssetNotFoundError(
                f"Texture blob {source.blob_path} was not found in "
                f"{source.data_archive_path.name}"
            ) from exc
        payload = archive.fetch(record)
        if len(payload) != source.declared_size:
            raise FormatError(
                f"Texture {source.texture_hash:08X} payload size mismatch: "
                f"expected {source.declared_size}, got {len(payload)}"
            )
        return payload

    def materialize(
        self, texture_hash: int, preferred_region: str = "common"
    ) -> tuple[Path, TextureSource]:
        source = self.resolve(texture_hash, preferred_region)
        extension = "dds" if source.format in FOURCC else "tga"
        target = (
            self.cache_root
            / source.region
            / (
                f"{texture_hash:08X}_{source.width}x{source.height}_"
                f"{source.format:02X}.{extension}"
            )
        )
        if target.is_file() and target.stat().st_size > 18:
            return target, source

        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self._fetch_payload(source)
        temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}")
        try:
            if source.format in FOURCC:
                temporary.write_bytes(
                    _dds_header(
                        source.width,
                        source.height,
                        source.format,
                        len(payload),
                    )
                    + payload
                )
            elif source.format == FORMAT_RGBA32:
                _write_tga(temporary, payload, source.width, source.height)
            else:
                raise FormatError(
                    f"Unsupported texture format 0x{source.format:X} "
                    f"for {texture_hash:08X}"
                )
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target, source
