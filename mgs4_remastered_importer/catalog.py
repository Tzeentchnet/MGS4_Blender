from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Final, Mapping, Sequence

from .errors import AssetNotFoundError, FormatError
from .formats.common import strcode_from_name
from .formats.gcx import StageLayout, extract_stage_layout, parse_gcx
from .formats.geom import GeomEffects, parse_geom_effects
from .formats.mdn import MdnModel
from .formats.vpak import VpakArchive


MODEL: Final = "MODEL"
MOTION: Final = "MOTION"
STAGE: Final = "STAGE"
MISSION: Final = "MISSION"
_RESOURCE: Final = "RESOURCE"

_STAGE_PATH = re.compile(
    r"^(?P<region>common|ww)/stage/(?P<chapter>stage[^/]+)/"
    r"(?P<stage>[^/]+)/",
    re.IGNORECASE,
)
_MISSION_CODE = re.compile(r"^(?P<mission>s\d+)a", re.IGNORECASE)
_AUXILIARY_MIN_SPAN: Final = 1_000.0
_AUXILIARY_MAX_SPAN: Final = 500_000.0
_AUXILIARY_MIN_ORIGIN_GAP: Final = 25_000.0
_AUXILIARY_MAX_NEIGHBOR_GAP: Final = 25_000.0
_EXTERIOR_MIN_SPAN: Final = 500_000.0
_EXTERIOR_MAX_SPAN: Final = 1_500_000.0
_EXTERIOR_MIN_SECONDARY_SPAN: Final = 75_000.0


@dataclass(frozen=True, slots=True)
class AssetRecord:
    kind: str
    virtual_path: str
    source_path: Path | None
    size: int

    @property
    def is_loose(self) -> bool:
        return self.source_path is None

    @property
    def label(self) -> str:
        if self.kind == STAGE:
            parts = self.virtual_path.strip("/").split("/")
            return f"{parts[-2]} / {parts[-1]}"
        if self.kind == MISSION:
            parts = self.virtual_path.strip("/").split("/")
            return f"{parts[-2]} / {parts[-1]} mission"
        return self.virtual_path


@dataclass(frozen=True, slots=True)
class StageResources:
    models: tuple[AssetRecord, ...]
    scripts: tuple[AssetRecord, ...]
    geometries: tuple[AssetRecord, ...]
    configurations: tuple[AssetRecord, ...]


@dataclass(frozen=True, slots=True)
class StageManifest:
    models: tuple[AssetRecord, ...]
    unresolved_models: tuple[str, ...]


def _model_bounds(
    model: MdnModel,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    vertices = (
        vertex
        for mesh in model.meshes
        for vertex in mesh.vertices
    )
    try:
        first = next(vertices)
    except StopIteration:
        return None
    minimum = list(first)
    maximum = list(first)
    for vertex in vertices:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], vertex[axis])
            maximum[axis] = max(maximum[axis], vertex[axis])
    return tuple(minimum), tuple(maximum)


def _box_gap(
    left: tuple[tuple[float, float, float], tuple[float, float, float]],
    right: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> float:
    squared = 0.0
    for axis in range(3):
        gap = max(
            left[0][axis] - right[1][axis],
            right[0][axis] - left[1][axis],
            0.0,
        )
        squared += gap * gap
    return math.sqrt(squared)


def _horizontal_origin_gap(
    bounds: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> float:
    gaps = []
    for axis in (0, 2):
        minimum = bounds[0][axis]
        maximum = bounds[1][axis]
        if minimum > 0.0:
            gaps.append(minimum)
        elif maximum < 0.0:
            gaps.append(-maximum)
        else:
            gaps.append(0.0)
    return math.hypot(*gaps)


def infer_auxiliary_stage_models(
    models: Mapping[AssetRecord, MdnModel],
    selected_records: Sequence[AssetRecord],
) -> tuple[AssetRecord, ...]:
    selected = set(selected_records)
    frontier = []
    candidates = []
    for record, model in models.items():
        bounds = _model_bounds(model)
        if bounds is None:
            continue
        spans = tuple(
            bounds[1][axis] - bounds[0][axis]
            for axis in range(3)
        )
        largest_span = max(spans)
        if record in selected:
            if largest_span <= _AUXILIARY_MAX_SPAN:
                frontier.append(bounds)
            continue
        if model.header.bone_count > 1:
            continue
        if not _AUXILIARY_MIN_SPAN <= largest_span <= _AUXILIARY_MAX_SPAN:
            continue
        if _horizontal_origin_gap(bounds) < _AUXILIARY_MIN_ORIGIN_GAP:
            continue
        candidates.append((record, bounds))

    inferred = []
    pending = sorted(candidates, key=lambda item: item[0].virtual_path)
    while frontier and pending:
        remaining = []
        added = False
        for record, bounds in pending:
            if any(
                _box_gap(bounds, existing)
                <= _AUXILIARY_MAX_NEIGHBOR_GAP
                for existing in frontier
            ):
                inferred.append(record)
                frontier.append(bounds)
                added = True
            else:
                remaining.append((record, bounds))
        if not added:
            break
        pending = remaining
    return tuple(inferred)


def infer_exterior_stage_models(
    models: Mapping[AssetRecord, MdnModel],
    selected_records: Sequence[AssetRecord],
) -> tuple[AssetRecord, ...]:
    selected = set(selected_records)
    inferred = []
    for record, model in models.items():
        if record in selected or model.header.bone_count < 2:
            continue
        bounds = _model_bounds(model)
        if bounds is None:
            continue
        spans = sorted(
            bounds[1][axis] - bounds[0][axis]
            for axis in range(3)
        )
        if not _EXTERIOR_MIN_SPAN <= spans[2] <= _EXTERIOR_MAX_SPAN:
            continue
        if spans[1] < _EXTERIOR_MIN_SECONDARY_SPAN:
            continue
        inferred.append(record)
    return tuple(sorted(inferred, key=lambda item: item.virtual_path))


def resolve_data_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    nested = candidate / "MGS4"
    if (nested / "common").is_dir():
        return nested
    if (candidate / "common").is_dir():
        return candidate
    raise AssetNotFoundError(
        f"{candidate} is not the MGS4 install folder or its MGS4 data folder"
    )


class GameCatalog:
    def __init__(self, game_root: str | Path):
        self.data_root = resolve_data_root(game_root)
        self._archives: dict[Path, VpakArchive] = {}
        self._assets: tuple[AssetRecord, ...] | None = None
        self._stage_assets: dict[str, tuple[AssetRecord, ...]] = {}
        self._stage_resources: dict[str, StageResources] = {}
        self._stage_manifests: dict[str, StageManifest] = {}
        self._mission_stages: dict[str, tuple[AssetRecord, ...]] = {}
        self._models_by_hash: dict[int, tuple[AssetRecord, ...]] = {}
        self._models_by_path: dict[str, AssetRecord] = {}
        self._models_by_stage_suffix: dict[str, tuple[AssetRecord, ...]] = {}

    def _archive(self, path: Path) -> VpakArchive:
        archive = self._archives.get(path)
        if archive is None:
            archive = VpakArchive(path)
            self._archives[path] = archive
        return archive

    def _stage_paks(self) -> tuple[Path, ...]:
        paths = []
        for region in ("common", "ww"):
            stage_folder = self.data_root / region / "stage"
            if not stage_folder.is_dir():
                continue
            paths.extend(sorted(stage_folder.glob("stage_data_compressed.*.pak")))
        return tuple(paths)

    def _build(self) -> tuple[AssetRecord, ...]:
        assets = []
        stage_parts: dict[
            tuple[str, str],
            dict[str, list[AssetRecord]],
        ] = {}
        model_hashes: dict[int, list[AssetRecord]] = {}

        loose_root = self.data_root / "common" / "mesh"
        if loose_root.is_dir():
            for path in sorted(loose_root.rglob("*.mdn")):
                relative = path.relative_to(self.data_root).as_posix()
                asset = AssetRecord(
                    MODEL, relative, None, path.stat().st_size
                )
                assets.append(asset)
                model_hashes.setdefault(
                    strcode_from_name(asset.virtual_path), []
                ).append(asset)

        for pak_path in self._stage_paks():
            archive = self._archive(pak_path)
            for record in archive.records.values():
                lower_name = record.name.lower()
                kind = None
                if lower_name.endswith(".mdn"):
                    kind = MODEL
                elif lower_name.endswith(".mtar"):
                    kind = MOTION
                asset = None
                if kind is not None:
                    asset = AssetRecord(
                        kind=kind,
                        virtual_path=record.name,
                        source_path=pak_path,
                        size=record.uncompressed_size,
                    )
                    assets.append(asset)
                    if kind == MODEL:
                        model_hashes.setdefault(
                            strcode_from_name(asset.virtual_path), []
                        ).append(asset)

                match = _STAGE_PATH.match(record.name)
                if match is None:
                    continue
                key = (
                    match.group("chapter").lower(),
                    match.group("stage").lower(),
                )
                parts = stage_parts.setdefault(
                    key,
                    {
                        "models": [],
                        "scripts": [],
                        "geometries": [],
                        "configurations": [],
                    },
                )
                region = match.group("region").lower()
                if kind == MODEL and region == "common" and asset is not None:
                    parts["models"].append(asset)
                elif lower_name.endswith(".gcx") and region == "ww":
                    parts["scripts"].append(
                        AssetRecord(
                            _RESOURCE,
                            record.name,
                            pak_path,
                            record.uncompressed_size,
                        )
                    )
                elif lower_name.endswith(".geom") and region == "common":
                    parts["geometries"].append(
                        AssetRecord(
                            _RESOURCE,
                            record.name,
                            pak_path,
                            record.uncompressed_size,
                        )
                    )
                elif lower_name.endswith("/data.cnf") and region == "ww":
                    parts["configurations"].append(
                        AssetRecord(
                            _RESOURCE,
                            record.name,
                            pak_path,
                            record.uncompressed_size,
                        )
                    )

        stages = []
        for (chapter, stage), parts in stage_parts.items():
            models = tuple(
                sorted(parts["models"], key=lambda item: item.virtual_path)
            )
            if not models:
                continue
            prefix = f"common/stage/{chapter}/{stage}"
            stage_asset = AssetRecord(STAGE, prefix, None, len(models))
            assets.append(stage_asset)
            stages.append(stage_asset)
            self._stage_assets[prefix] = models
            self._stage_resources[prefix] = StageResources(
                models=models,
                scripts=tuple(
                    sorted(
                        parts["scripts"],
                        key=lambda item: (-item.size, item.virtual_path),
                    )
                ),
                geometries=tuple(
                    sorted(
                        parts["geometries"],
                        key=lambda item: (-item.size, item.virtual_path),
                    )
                ),
                configurations=tuple(
                    sorted(
                        parts["configurations"],
                        key=lambda item: (-item.size, item.virtual_path),
                    )
                ),
            )

        mission_groups: dict[str, list[AssetRecord]] = {}
        for stage_asset in stages:
            parts = stage_asset.virtual_path.split("/")
            mission_match = _MISSION_CODE.match(parts[-1])
            if mission_match is None:
                continue
            prefix = (
                f"common/stage/{parts[-2]}/"
                f"{mission_match.group('mission').lower()}"
            )
            mission_groups.setdefault(prefix, []).append(stage_asset)
        for prefix, mission_stages in mission_groups.items():
            ordered = tuple(
                sorted(mission_stages, key=lambda item: item.virtual_path)
            )
            assets.append(AssetRecord(MISSION, prefix, None, len(ordered)))
            self._mission_stages[prefix] = ordered

        self._models_by_hash = {
            key: tuple(sorted(value, key=lambda item: item.virtual_path))
            for key, value in model_hashes.items()
        }
        model_assets = tuple(
            asset for asset in assets if asset.kind == MODEL
        )
        self._models_by_path = {
            asset.virtual_path.lower(): asset for asset in model_assets
        }
        suffixes: dict[str, list[AssetRecord]] = {}
        for asset in model_assets:
            parts = asset.virtual_path.lower().split("/")
            if (
                len(parts) >= 5
                and parts[0] in {"common", "ww"}
                and parts[1] == "stage"
            ):
                suffixes.setdefault("/".join(parts[3:]), []).append(asset)
        self._models_by_stage_suffix = {
            suffix: tuple(
                sorted(records, key=lambda item: item.virtual_path)
            )
            for suffix, records in suffixes.items()
        }
        return tuple(sorted(assets, key=lambda item: (item.kind, item.virtual_path)))

    @property
    def assets(self) -> tuple[AssetRecord, ...]:
        if self._assets is None:
            self._assets = self._build()
        return self._assets

    def search(
        self,
        kind: str,
        query: str = "",
        *,
        limit: int = 200,
    ) -> tuple[AssetRecord, ...]:
        normalized_query = query.strip().lower().replace("\\", "/")
        matches = (
            asset
            for asset in self.assets
            if asset.kind == kind
            and (
                not normalized_query
                or normalized_query in asset.virtual_path.lower()
            )
        )
        if limit <= 0:
            return tuple(matches)
        results = []
        for asset in matches:
            results.append(asset)
            if len(results) >= limit:
                break
        return tuple(results)

    def stage_models(self, prefix: str) -> tuple[AssetRecord, ...]:
        _ = self.assets
        try:
            return self._stage_assets[prefix.rstrip("/")]
        except KeyError as exc:
            raise AssetNotFoundError(f"Unknown MGS4 stage: {prefix}") from exc

    def stage_resources(self, prefix: str) -> StageResources:
        _ = self.assets
        try:
            return self._stage_resources[prefix.rstrip("/").lower()]
        except KeyError as exc:
            raise AssetNotFoundError(f"Unknown MGS4 stage: {prefix}") from exc

    def stage_manifest(self, prefix: str) -> StageManifest:
        key = prefix.rstrip("/").lower()
        cached = self._stage_manifests.get(key)
        if cached is not None:
            return cached

        resources = self.stage_resources(key)
        models = []
        unresolved = []
        for configuration in resources.configurations:
            try:
                text = self.read(configuration).decode("ascii")
            except UnicodeDecodeError as exc:
                raise FormatError(
                    f"{configuration.virtual_path} is not an ASCII data.cnf"
                ) from exc
            references = tuple(
                dict.fromkeys(
                    line.strip().replace("\\", "/")
                    for line in text.splitlines()
                    if line.strip()
                    and not line.lstrip().startswith(".")
                    and line.strip().lower().endswith(".mdn")
                )
            )
            for reference in references:
                normalized = reference.lower()
                record = None
                if normalized.startswith("common/"):
                    record = self._models_by_path.get(normalized)
                elif normalized.startswith("cache/"):
                    record = self._models_by_path.get(f"{key}/{normalized}")
                elif normalized.startswith("/../"):
                    candidates = self._models_by_stage_suffix.get(
                        normalized[4:],
                        (),
                    )
                    if len(candidates) > 1 and configuration.source_path:
                        archive_name = configuration.source_path.name.lower()
                        same_archive = tuple(
                            candidate
                            for candidate in candidates
                            if candidate.source_path is not None
                            and candidate.source_path.name.lower() == archive_name
                        )
                        if len(same_archive) == 1:
                            candidates = same_archive
                    if len(candidates) == 1:
                        record = candidates[0]
                else:
                    record = self._models_by_path.get(
                        f"{key}/{normalized.lstrip('/')}"
                    )

                if record is None:
                    unresolved.append(
                        f"{configuration.virtual_path}: {reference}"
                    )
                elif record not in models:
                    models.append(record)

        manifest = StageManifest(tuple(models), tuple(unresolved))
        self._stage_manifests[key] = manifest
        return manifest

    def mission_stages(self, prefix: str) -> tuple[AssetRecord, ...]:
        _ = self.assets
        try:
            return self._mission_stages[prefix.rstrip("/").lower()]
        except KeyError as exc:
            raise AssetNotFoundError(f"Unknown MGS4 mission: {prefix}") from exc

    def model_for_hash(
        self,
        name_hash: int,
        preferred_stage: str = "",
    ) -> AssetRecord | None:
        _ = self.assets
        candidates = self._models_by_hash.get(name_hash & 0xFFFFFFFF, ())
        if not candidates:
            return None
        preferred = preferred_stage.rstrip("/").lower()
        if preferred:
            exact = next(
                (
                    item
                    for item in candidates
                    if item.virtual_path.lower().startswith(f"{preferred}/")
                ),
                None,
            )
            if exact is not None:
                return exact
            chapter = "/".join(preferred.split("/")[:3])
            related = next(
                (
                    item
                    for item in candidates
                    if item.virtual_path.lower().startswith(f"{chapter}/")
                ),
                None,
            )
            if related is not None:
                return related
        loose = next(
            (
                item
                for item in candidates
                if item.virtual_path.lower().startswith("common/mesh/")
            ),
            None,
        )
        return loose or candidates[0]

    def stage_layout(self, prefix: str) -> StageLayout | None:
        resources = self.stage_resources(prefix)
        if not resources.scripts:
            return None
        effects = (
            parse_geom_effects(self.read(resources.geometries[0]))
            if resources.geometries
            else GeomEffects(())
        )
        stage_hashes = []
        placements = []
        for script in resources.scripts:
            layout = extract_stage_layout(parse_gcx(self.read(script)), effects)
            stage_hashes.extend(layout.stage_model_hashes)
            placements.extend(layout.placements)
        return StageLayout(
            tuple(dict.fromkeys(stage_hashes)),
            tuple(dict.fromkeys(placements)),
        )

    def read(self, asset: AssetRecord) -> bytes:
        if asset.kind in {STAGE, MISSION}:
            raise AssetNotFoundError(f"A {asset.kind.lower()} is an asset group")
        if asset.source_path is None:
            path = self.data_root / Path(asset.virtual_path)
            try:
                return path.read_bytes()
            except OSError as exc:
                raise AssetNotFoundError(f"Cannot read {path}") from exc
        return self._archive(asset.source_path).fetch(asset.virtual_path)
