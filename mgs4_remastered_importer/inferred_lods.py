from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Final

from .errors import FormatError


INFERRED_LOD_DATA_VERSION: Final = 1
INFERRED_LOD_RESOURCE: Final = (
    Path(__file__).resolve().parent
    / "resources"
    / "inferred_lod_chains.json"
)


def _parse_inferred_lod_chains(payload: object) -> tuple[tuple[int, ...], ...]:
    if not isinstance(payload, dict):
        raise FormatError("Inferred LOD data must be a JSON object")
    if payload.get("schema_version") != INFERRED_LOD_DATA_VERSION:
        raise FormatError(
            "Unsupported inferred LOD data schema: "
            f"{payload.get('schema_version')!r}"
        )
    raw_chains = payload.get("chains")
    if not isinstance(raw_chains, list):
        raise FormatError("Inferred LOD data is missing its chains list")

    chains = []
    seen_hashes = set()
    for chain_index, raw_chain in enumerate(raw_chains):
        if not isinstance(raw_chain, list) or not 2 <= len(raw_chain) <= 3:
            raise FormatError(
                f"Inferred LOD chain {chain_index} must contain 2 or 3 hashes"
            )
        chain = []
        for raw_hash in raw_chain:
            if (
                not isinstance(raw_hash, str)
                or len(raw_hash) != 8
                or any(
                    character not in "0123456789abcdefABCDEF"
                    for character in raw_hash
                )
            ):
                raise FormatError(
                    f"Inferred LOD chain {chain_index} contains an invalid hash"
                )
            model_hash = int(raw_hash, 16)
            if model_hash in seen_hashes:
                raise FormatError(
                    f"Inferred LOD hash {model_hash:08X} appears more than once"
                )
            seen_hashes.add(model_hash)
            chain.append(model_hash)
        chains.append(tuple(chain))
    return tuple(chains)


@lru_cache(maxsize=1)
def inferred_lod_chains() -> tuple[tuple[int, ...], ...]:
    try:
        payload = json.loads(INFERRED_LOD_RESOURCE.read_text(encoding="ascii"))
    except OSError as exc:
        raise FormatError(
            f"Unable to read inferred LOD data: {INFERRED_LOD_RESOURCE}"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormatError("Inferred LOD data is not valid JSON") from exc
    return _parse_inferred_lod_chains(payload)


@lru_cache(maxsize=1)
def _inferred_lod_chain_by_hash() -> dict[int, tuple[int, ...]]:
    return {
        model_hash: chain
        for chain in inferred_lod_chains()
        for model_hash in chain
    }


def inferred_lod_chain(model_hash: int) -> tuple[int, ...] | None:
    return _inferred_lod_chain_by_hash().get(model_hash & 0xFFFFFFFF)


@lru_cache(maxsize=1)
def inferred_lod_lower_hashes() -> frozenset[int]:
    return frozenset(
        model_hash
        for chain in inferred_lod_chains()
        for model_hash in chain[1:]
    )


def is_inferred_lod_lower(model_hash: int) -> bool:
    return model_hash & 0xFFFFFFFF in inferred_lod_lower_hashes()
