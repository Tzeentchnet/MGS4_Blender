from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath

from .errors import MGS4Error
from .formats.common import strcode


USER_DICTIONARY_SCHEMA_VERSION = 1
MAX_CUSTOM_NAME_LENGTH = 256


class AssetNameError(MGS4Error):
    """Raised when an asset-name dictionary is invalid or cannot be saved."""


@dataclass(frozen=True, slots=True)
class NameImportResult:
    added: int
    updated: int
    skipped: int


def asset_hash_from_path(virtual_path: str) -> int | None:
    stem = PurePosixPath(virtual_path.replace("\\", "/")).stem
    if not 1 <= len(stem) <= 8:
        return None
    try:
        return int(stem, 16)
    except ValueError:
        return None


def format_asset_hash(value: int) -> str:
    return f"{value & 0xFFFFFFFF:08X}"


def _validate_name(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AssetNameError(f"{label} must be a string")
    name = value.strip()
    if not name:
        raise AssetNameError(f"{label} cannot be empty")
    if len(name) > MAX_CUSTOM_NAME_LENGTH:
        raise AssetNameError(
            f"{label} exceeds {MAX_CUSTOM_NAME_LENGTH} characters"
        )
    if not all(character.isprintable() for character in name):
        raise AssetNameError(f"{label} contains control characters")
    return name


def _parse_hash_key(value: object) -> int:
    if not isinstance(value, str):
        raise AssetNameError("Asset-name dictionary hashes must be strings")
    key = value.strip()
    if key.lower().startswith("0x"):
        key = key[2:]
    if not 1 <= len(key) <= 8:
        raise AssetNameError(f"Invalid asset hash: {value!r}")
    try:
        return int(key, 16)
    except ValueError as exc:
        raise AssetNameError(f"Invalid asset hash: {value!r}") from exc


def load_name_wordlist(path: Path) -> dict[int, tuple[str, ...]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AssetNameError(f"Cannot read asset-name wordlist {path}: {exc}") from exc

    candidates: dict[int, list[str]] = {}
    for line_number, value in enumerate(lines, start=1):
        if not value.strip():
            continue
        name = _validate_name(value, f"{path.name} line {line_number}")
        name_hash = strcode(name)
        names = candidates.setdefault(name_hash, [])
        if name not in names:
            names.append(name)
    return {
        name_hash: tuple(names)
        for name_hash, names in candidates.items()
    }


def load_user_names(
    path: Path,
    *,
    missing_ok: bool = False,
) -> dict[int, str]:
    if missing_ok and not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise AssetNameError(f"Cannot read asset-name dictionary {path}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AssetNameError(f"Invalid asset-name dictionary {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise AssetNameError("Asset-name dictionary root must be an object")
    if payload.get("schema_version") != USER_DICTIONARY_SCHEMA_VERSION:
        raise AssetNameError(
            "Unsupported asset-name dictionary schema_version; "
            f"expected {USER_DICTIONARY_SCHEMA_VERSION}"
        )
    values = payload.get("names")
    if not isinstance(values, dict):
        raise AssetNameError("Asset-name dictionary 'names' must be an object")

    names: dict[int, str] = {}
    for raw_hash, raw_name in values.items():
        name_hash = _parse_hash_key(raw_hash)
        if name_hash in names:
            raise AssetNameError(
                f"Duplicate normalized asset hash: {format_asset_hash(name_hash)}"
            )
        names[name_hash] = _validate_name(
            raw_name,
            f"Name for {format_asset_hash(name_hash)}",
        )
    return names


def write_user_names(path: Path, names: dict[int, str]) -> None:
    values = {
        format_asset_hash(name_hash): _validate_name(
            name,
            f"Name for {format_asset_hash(name_hash)}",
        )
        for name_hash, name in sorted(names.items())
    }
    payload = {
        "schema_version": USER_DICTIONARY_SCHEMA_VERSION,
        "names": values,
    }
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path.exists():
            temporary_path.unlink()
        raise AssetNameError(f"Cannot write asset-name dictionary {path}: {exc}") from exc


@dataclass(slots=True)
class AssetNameRegistry:
    bundled_names: dict[int, tuple[str, ...]]
    custom_names: dict[int, str]
    user_path: Path

    @classmethod
    def load(cls, bundled_path: Path, user_path: Path) -> AssetNameRegistry:
        return cls(
            bundled_names=load_name_wordlist(bundled_path),
            custom_names=load_user_names(user_path, missing_ok=True),
            user_path=user_path,
        )

    def bundled_candidates(self, name_hash: int) -> tuple[str, ...]:
        return self.bundled_names.get(name_hash & 0xFFFFFFFF, ())

    def custom_name(self, name_hash: int) -> str | None:
        return self.custom_names.get(name_hash & 0xFFFFFFFF)

    def preferred_name(self, name_hash: int) -> str:
        normalized_hash = name_hash & 0xFFFFFFFF
        custom = self.custom_names.get(normalized_hash)
        if custom is not None:
            return custom
        candidates = self.bundled_names.get(normalized_hash, ())
        return candidates[0] if len(candidates) == 1 else ""

    def display_name(self, name_hash: int) -> str:
        normalized_hash = name_hash & 0xFFFFFFFF
        custom = self.custom_names.get(normalized_hash)
        if custom is not None:
            return custom
        return " | ".join(self.bundled_names.get(normalized_hash, ()))

    def searchable_names(self, name_hash: int) -> tuple[str, ...]:
        normalized_hash = name_hash & 0xFFFFFFFF
        values = []
        custom = self.custom_names.get(normalized_hash)
        if custom is not None:
            values.append(custom)
        values.extend(
            name
            for name in self.bundled_names.get(normalized_hash, ())
            if name not in values
        )
        return tuple(values)

    def set_custom_name(self, name_hash: int, name: str) -> None:
        updated = dict(self.custom_names)
        updated[name_hash & 0xFFFFFFFF] = _validate_name(name, "Asset name")
        write_user_names(self.user_path, updated)
        self.custom_names = updated

    def remove_custom_name(self, name_hash: int) -> bool:
        normalized_hash = name_hash & 0xFFFFFFFF
        if normalized_hash not in self.custom_names:
            return False
        updated = dict(self.custom_names)
        del updated[normalized_hash]
        write_user_names(self.user_path, updated)
        self.custom_names = updated
        return True

    def import_custom_names(
        self,
        path: Path,
        *,
        overwrite_existing: bool,
    ) -> NameImportResult:
        imported = load_user_names(path)
        updated_names = dict(self.custom_names)
        added = 0
        updated = 0
        skipped = 0
        for name_hash, name in imported.items():
            existing = updated_names.get(name_hash)
            if existing is None:
                updated_names[name_hash] = name
                added += 1
            elif existing == name:
                skipped += 1
            elif overwrite_existing:
                updated_names[name_hash] = name
                updated += 1
            else:
                skipped += 1
        if added or updated:
            write_user_names(self.user_path, updated_names)
            self.custom_names = updated_names
        return NameImportResult(added=added, updated=updated, skipped=skipped)

    def export_custom_names(self, path: Path) -> int:
        write_user_names(path, self.custom_names)
        return len(self.custom_names)
