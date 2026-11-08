from __future__ import annotations

from pathlib import PurePath


def strcode(value: str) -> int:
    result = 0
    mask = 0x00FFFFFF
    for character in value:
        result = ((result >> 19) | ((result << 5) & mask))
        result = (result + ord(character)) & mask
    return result or 1


def strcode_from_name(name: str) -> int:
    stem = PurePath(name).stem
    if stem.lower().startswith("0x"):
        stem = stem[2:]
    if 1 <= len(stem) <= 8:
        try:
            return int(stem, 16)
        except ValueError:
            pass
    return strcode(stem)


def hash_name(value: int) -> str:
    return f"{value & 0xFFFFFFFF:08X}"
