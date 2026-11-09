from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .asset_names import (
    MAX_CUSTOM_NAME_LENGTH,
    AssetNameError,
    AssetNameRegistry,
    asset_hash_from_path,
    format_asset_hash,
)
from .blender_import import (
    LAYER_MODE_CONSERVATIVE,
    LAYER_MODE_PREVIEW,
    MaterialImportOptions,
    game_transform_matrix,
    import_mdn_model,
    import_motion_archive,
)
from .catalog import (
    MISSION,
    MODEL,
    MOTION,
    STAGE,
    AssetRecord,
    GameCatalog,
    infer_auxiliary_stage_models,
    infer_exterior_stage_models,
)
from .errors import MGS4Error
from .formats.mdn import MdnModel, parse_mdn
from .formats.mtar import parse_mtar
from .formats.texture import TextureResolver


DEFAULT_GAME_ROOT = (
    r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4"
    if Path(r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4").is_dir()
    else ""
)
DEFAULT_CACHE_ROOT = bpy.utils.user_resource(
    "CONFIG",
    path="mgs4_remastered_importer/cache",
    create=True,
)
BUNDLED_ASSET_NAMES = (
    Path(__file__).resolve().parent
    / "resources"
    / "solideye_dictionary.txt"
)
BUNDLED_ASSET_NAMES_VERSION = "solideye-1.2-79b0aad6"
USER_ASSET_NAMES = (
    Path(
        bpy.utils.user_resource(
            "CONFIG",
            path="mgs4_remastered_importer",
            create=True,
        )
    )
    / "asset_names.json"
)

_catalogs: dict[str, GameCatalog] = {}
_resolvers: dict[tuple[str, str], TextureResolver] = {}
_pending_asset_refreshes: dict[int, Callable[[], float | None]] = {}
_asset_name_registry: AssetNameRegistry | None = None


def _game_root_updated(_preferences, context) -> None:
    scene = getattr(context, "scene", None)
    if scene is not None and hasattr(scene, "mgs4_importer"):
        _refresh_asset_results(context, force=True)


def _asset_kind_updated(_settings, context) -> None:
    scene = getattr(context, "scene", None)
    if scene is not None and hasattr(scene, "mgs4_importer"):
        _refresh_asset_results(context)


def _search_text_updated(settings, _context) -> None:
    _select_first_matching_result(settings)


@dataclass(slots=True)
class _StageImportResult:
    models: int
    auxiliary_models: int
    exterior_models: int
    instances: int
    skipped: int
    unresolved_transforms: int
    failures: list[str]
    objects: list[bpy.types.Object]
    armature: bpy.types.Object | None


class MGS4ImporterPreferences(AddonPreferences):
    bl_idname = __package__

    game_root: StringProperty(
        name="MGS4 Install",
        description="METAL GEAR SOLID 4 install folder or its MGS4 subfolder",
        subtype="DIR_PATH",
        default=DEFAULT_GAME_ROOT,
        update=_game_root_updated,
    )
    cache_root: StringProperty(
        name="Texture Cache",
        description="Writable folder for DDS and TGA files generated from PC_TXN_UP",
        subtype="DIR_PATH",
        default=DEFAULT_CACHE_ROOT,
    )
    highlight_missing_textures: BoolProperty(
        name="Highlight Missing Textures",
        description=(
            "Create magenta checker images for unresolved textures instead of "
            "leaving their texture slots empty"
        ),
        default=True,
    )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "game_root")
        layout.prop(self, "cache_root")
        layout.prop(self, "highlight_missing_textures")
        layout.label(text="The importer never writes into the game installation.")


class MGS4AssetResult(PropertyGroup):
    kind: StringProperty()
    virtual_path: StringProperty()
    source_path: StringProperty()
    has_custom_name: BoolProperty(options={"SKIP_SAVE"})
    resolved_name: StringProperty(options={"SKIP_SAVE"})
    searchable_names: StringProperty(options={"SKIP_SAVE"})
    size: IntProperty(min=0)


class MGS4SceneSettings(PropertyGroup):
    asset_kind: EnumProperty(
        name="Asset Type",
        items=(
            (MODEL, "Models", "Rigged characters, props, and map MDNs"),
            (MOTION, "Animations", "MTAR skeletal motion archives"),
            (STAGE, "Stage Areas", "GCX-defined map areas and placed props"),
            (MISSION, "Missions", "All related stage areas in a mission"),
        ),
        default=MODEL,
        update=_asset_kind_updated,
    )
    search_text: StringProperty(
        name="Search",
        description="Friendly name, path, stage code, or hexadecimal asset hash",
        update=_search_text_updated,
    )
    results: CollectionProperty(
        type=MGS4AssetResult,
        options={"SKIP_SAVE"},
    )
    result_index: IntProperty(default=0)
    catalog_root: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    catalog_kind: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    catalog_names_version: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    catalog_error: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    scale: FloatProperty(
        name="Scale",
        description="MGS4 units to Blender units",
        default=0.001,
        min=0.000001,
        precision=6,
    )
    import_materials: BoolProperty(
        name="Materials and Textures",
        default=True,
    )
    material_layer_mode: EnumProperty(
        name="Layer Presentation",
        description="Choose how layer texture slots are presented in Blender",
        items=(
            (
                LAYER_MODE_CONSERVATIVE,
                "Conservative",
                "Use the diffuse texture for Base Color and keep layers inspectable",
            ),
            (
                LAYER_MODE_PREVIEW,
                "First Layer Preview",
                "Use the first layer texture for Base Color without guessing blending",
            ),
        ),
        default=LAYER_MODE_CONSERVATIVE,
    )
    use_vertex_color: BoolProperty(
        name="Vertex Color RGB",
        description="Multiply Base Color by the model's Color attribute when safe",
        default=True,
    )
    use_vertex_alpha: BoolProperty(
        name="Vertex Color Alpha",
        description="Use vertex alpha for material opacity when available",
        default=False,
    )
    import_animations: BoolProperty(
        name="Import Animations",
        description=(
            "Import the best compatible motion archive from the model's "
            "stage cache"
        ),
        default=True,
    )
    stage_bone_limit: IntProperty(
        name="Stage Bone Limit",
        description=(
            "Import stage MDNs with at most this many bones; 32 includes "
            "rigged map props while excluding most characters"
        ),
        default=32,
        min=0,
        max=1024,
    )
    stage_model_limit: IntProperty(
        name="Stage Model Limit",
        description="Maximum models imported from a stage; 0 imports all matches",
        default=0,
        min=0,
    )
    stage_use_layout: BoolProperty(
        name="Use GCX/GEOM Layout",
        description=(
            "Import the model set and placements referenced by the stage scripts; "
            "disable to import every MDN cached under the stage path"
        ),
        default=True,
    )
    stage_include_auxiliary: BoolProperty(
        name="Auxiliary Cached Environment",
        description=(
            "Also import conservative static cache and data.cnf dependency "
            "models authored in world coordinates next to the GCX-defined "
            "environment"
        ),
        default=True,
    )
    stage_include_exterior: BoolProperty(
        name="Exterior / Cinematic Models",
        description=(
            "Import large rigged exterior models into a disabled collection "
            "so complete vehicle or structure shells remain available without "
            "obscuring the detailed playable stage"
        ),
        default=True,
    )
    animation_limit: IntProperty(
        name="Action Limit",
        description="Maximum actions imported from an MTAR; 0 imports all",
        default=100,
        min=0,
    )


def _preferences(context) -> MGS4ImporterPreferences:
    return context.preferences.addons[__package__].preferences


def _material_options(
    context,
    *,
    layer_mode: str,
    use_vertex_color: bool,
    use_vertex_alpha: bool,
) -> MaterialImportOptions:
    return MaterialImportOptions(
        layer_mode=layer_mode,
        highlight_missing_textures=_preferences(
            context
        ).highlight_missing_textures,
        use_vertex_color=use_vertex_color,
        use_vertex_alpha=use_vertex_alpha,
    )


def _asset_names(*, force: bool = False) -> AssetNameRegistry:
    global _asset_name_registry
    if _asset_name_registry is None or force:
        _asset_name_registry = AssetNameRegistry.load(
            BUNDLED_ASSET_NAMES,
            USER_ASSET_NAMES,
        )
    return _asset_name_registry


def _catalog(context, *, refresh: bool = False) -> GameCatalog:
    preferences = _preferences(context)
    catalog = GameCatalog(preferences.game_root)
    key = str(catalog.data_root.resolve()).lower()
    existing = _catalogs.get(key)
    if existing is None or refresh:
        _catalogs[key] = catalog
        existing = catalog
    return existing


def _normalized_asset_query(query: str) -> str:
    return query.strip().lower().replace("\\", "/")


def _result_matches(item: MGS4AssetResult, query: str) -> bool:
    normalized_query = _normalized_asset_query(query)
    return (
        not normalized_query
        or normalized_query in item.virtual_path.lower()
        or normalized_query in item.searchable_names.lower()
    )


def _select_first_matching_result(settings: MGS4SceneSettings) -> None:
    for index, item in enumerate(settings.results):
        if _result_matches(item, settings.search_text):
            settings.result_index = index
            return
    settings.result_index = 0


def _asset_name_hash(asset: AssetRecord) -> int | None:
    if asset.kind not in {MODEL, MOTION}:
        return None
    return asset_hash_from_path(asset.virtual_path)


def _asset_resolved_name(
    asset: AssetRecord,
    names: AssetNameRegistry,
) -> str:
    name_hash = _asset_name_hash(asset)
    return names.display_name(name_hash) if name_hash is not None else ""


def _asset_display_name(
    asset: AssetRecord,
    names: AssetNameRegistry,
) -> str:
    if asset.kind not in {MODEL, MOTION}:
        return asset.label
    parts = PurePosixPath(asset.virtual_path).parts
    filename = parts[-1]
    resolved_name = _asset_resolved_name(asset, names)
    label = (
        f"{resolved_name}  ({filename})"
        if resolved_name
        else filename
    )
    context_label = ""
    if len(parts) >= 5 and parts[1].lower() == "stage":
        context_label = parts[3]
    elif len(parts) >= 3:
        context_label = "/".join(parts[-3:-1])
    return (
        f"{label}  [{context_label}]"
        if context_label
        else label
    )


def _asset_import_name(asset: AssetRecord, names: AssetNameRegistry) -> str:
    name_hash = _asset_name_hash(asset)
    if name_hash is not None:
        friendly_name = names.preferred_name(name_hash)
        if friendly_name:
            return friendly_name
    return Path(asset.virtual_path).stem.upper()


def _set_asset_result(
    item: MGS4AssetResult,
    asset: AssetRecord,
    names: AssetNameRegistry,
) -> None:
    name_hash = _asset_name_hash(asset)
    item.name = _asset_display_name(asset, names)
    item.kind = asset.kind
    item.virtual_path = asset.virtual_path
    item.source_path = str(asset.source_path) if asset.source_path else ""
    item.has_custom_name = (
        name_hash is not None
        and names.custom_name(name_hash) is not None
    )
    item.resolved_name = _asset_resolved_name(asset, names)
    item.searchable_names = (
        "\n".join(names.searchable_names(name_hash))
        if name_hash is not None
        else ""
    )
    item.size = asset.size


def _refresh_asset_results(
    context: bpy.types.Context,
    *,
    scene: bpy.types.Scene | None = None,
    force: bool = False,
) -> tuple[int, str]:
    settings = (scene or context.scene).mgs4_importer
    preferences = _preferences(context)
    if (
        not force
        and settings.catalog_root == preferences.game_root
        and settings.catalog_kind == settings.asset_kind
        and settings.catalog_names_version == BUNDLED_ASSET_NAMES_VERSION
    ):
        return len(settings.results), settings.catalog_error

    settings.results.clear()
    settings.catalog_root = preferences.game_root
    settings.catalog_kind = settings.asset_kind
    settings.catalog_names_version = ""
    settings.catalog_error = ""
    try:
        names = _asset_names(force=force)
        results = _catalog(context, refresh=force).search(
            settings.asset_kind,
            limit=0,
        )
    except (OSError, MGS4Error) as exc:
        settings.catalog_error = str(exc)
        settings.result_index = 0
        return 0, settings.catalog_error

    for asset in results:
        item = settings.results.add()
        _set_asset_result(item, asset, names)
    settings.catalog_names_version = BUNDLED_ASSET_NAMES_VERSION
    _select_first_matching_result(settings)
    return len(results), ""


def _schedule_asset_refresh(scene: bpy.types.Scene) -> None:
    scene_pointer = scene.as_pointer()
    if scene_pointer in _pending_asset_refreshes:
        return

    scene_name = scene.name_full

    def refresh() -> float | None:
        try:
            current_scene = bpy.data.scenes.get(scene_name)
            if (
                current_scene is not None
                and current_scene.as_pointer() == scene_pointer
                and hasattr(bpy.types.Scene, "mgs4_importer")
            ):
                _refresh_asset_results(bpy.context, scene=current_scene)
        finally:
            _pending_asset_refreshes.pop(scene_pointer, None)
        return None

    _pending_asset_refreshes[scene_pointer] = refresh
    bpy.app.timers.register(refresh, first_interval=0.0)


def _cancel_pending_asset_refreshes() -> None:
    for refresh in tuple(_pending_asset_refreshes.values()):
        if bpy.app.timers.is_registered(refresh):
            bpy.app.timers.unregister(refresh)
    _pending_asset_refreshes.clear()


def _resolver(context, catalog: GameCatalog) -> TextureResolver:
    preferences = _preferences(context)
    cache_root = Path(preferences.cache_root).expanduser()
    if not preferences.cache_root:
        raise MGS4Error("Choose a writable texture cache folder")
    key = (
        str(catalog.data_root.resolve()).lower(),
        str(cache_root.resolve()).lower(),
    )
    resolver = _resolvers.get(key)
    if resolver is None:
        resolver = TextureResolver(catalog.data_root, cache_root)
        _resolvers[key] = resolver
    return resolver


def _selected_armature(context) -> bpy.types.Object | None:
    active = context.view_layer.objects.active
    if active is not None and active.type == "ARMATURE":
        return active
    return next(
        (obj for obj in context.selected_objects if obj.type == "ARMATURE"),
        None,
    )


def _model_is_skinned(model: MdnModel) -> bool:
    return any(
        mesh.weights and mesh.bone_indices
        for mesh in model.meshes
    )


def _activate_import(
    context: bpy.types.Context,
    objects: tuple[bpy.types.Object, ...],
    armature: bpy.types.Object | None,
) -> None:
    for selected in context.selected_objects:
        selected.select_set(False)
    for obj in objects:
        obj.select_set(True)
    if armature is not None:
        context.view_layer.objects.active = armature
    elif objects:
        context.view_layer.objects.active = objects[0]


def _report_model_warnings(operator: Operator, warnings: tuple[str, ...]) -> None:
    if not warnings:
        return
    preview = "; ".join(warnings[:3])
    if len(warnings) > 3:
        preview += f"; and {len(warnings) - 3} more"
    operator.report({"WARNING"}, preview)


def _asset_from_result(item: MGS4AssetResult) -> AssetRecord:
    return AssetRecord(
        kind=item.kind,
        virtual_path=item.virtual_path,
        source_path=Path(item.source_path) if item.source_path else None,
        size=item.size,
    )


class MGS4_OT_search_assets(Operator):
    bl_idname = "mgs4.search_assets"
    bl_label = "Refresh MGS4 Assets"
    bl_description = "Re-index assets from the installed game"

    def execute(self, context):
        count, error = _refresh_asset_results(context, force=True)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Indexed {count} assets")
        return {"FINISHED"}


class MGS4_UL_asset_results(UIList):
    def filter_items(self, _context, data, property_name):
        query = _normalized_asset_query(data.search_text)
        if not query:
            return [], []
        flags = [
            self.bitflag_filter_item
            if _result_matches(item, query)
            else 0
            for item in getattr(data, property_name)
        ]
        return flags, []

    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index=0,
        _flt_flag=0,
    ):
        icon = {
            MODEL: "MESH_DATA",
            MOTION: "ACTION",
            STAGE: "OUTLINER_COLLECTION",
            MISSION: "SCENE_DATA",
        }.get(item.kind, "FILE")
        layout.label(text=item.name, icon=icon)


def _selected_asset_result(context) -> MGS4AssetResult | None:
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "mgs4_importer"):
        return None
    settings = scene.mgs4_importer
    if not 0 <= settings.result_index < len(settings.results):
        return None
    return settings.results[settings.result_index]


def _selected_asset_hash(context) -> int | None:
    item = _selected_asset_result(context)
    return asset_hash_from_path(item.virtual_path) if item is not None else None


def _refresh_asset_name_labels(names: AssetNameRegistry) -> None:
    for scene in bpy.data.scenes:
        if not hasattr(scene, "mgs4_importer"):
            continue
        settings = scene.mgs4_importer
        for item in settings.results:
            _set_asset_result(item, _asset_from_result(item), names)
        _select_first_matching_result(settings)


class MGS4_OT_set_asset_name(Operator):
    bl_idname = "mgs4.set_asset_name"
    bl_label = "Set Asset Name"
    bl_description = "Assign a custom friendly name to the selected asset hash"

    name: StringProperty(
        name="Friendly Name",
        maxlen=MAX_CUSTOM_NAME_LENGTH,
    )
    name_hash: StringProperty(options={"HIDDEN"})
    virtual_path: StringProperty(options={"HIDDEN"})
    suggestions: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _selected_asset_hash(context) is not None

    def invoke(self, context, _event):
        item = _selected_asset_result(context)
        name_hash = _selected_asset_hash(context)
        if item is None or name_hash is None:
            self.report({"ERROR"}, "Select a hashed model or animation asset")
            return {"CANCELLED"}
        try:
            names = _asset_names(force=True)
        except AssetNameError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.name_hash = format_asset_hash(name_hash)
        self.virtual_path = item.virtual_path
        self.suggestions = " | ".join(names.bundled_candidates(name_hash))
        self.name = (
            names.custom_name(name_hash)
            or names.preferred_name(name_hash)
        )
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        layout = self.layout
        layout.label(text=f"Hash: {self.name_hash}")
        layout.label(text=self.virtual_path, icon="FILE")
        if self.suggestions:
            layout.label(text=f"Bundled: {self.suggestions}", icon="INFO")
        layout.prop(self, "name")

    def execute(self, _context):
        try:
            names = _asset_names(force=True)
            names.set_custom_name(int(self.name_hash, 16), self.name)
            _refresh_asset_name_labels(names)
        except (AssetNameError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved name for {self.name_hash}")
        return {"FINISHED"}


class MGS4_OT_remove_asset_name(Operator):
    bl_idname = "mgs4.remove_asset_name"
    bl_label = "Remove Custom Asset Name"
    bl_description = "Remove the custom name and restore the bundled or hashed name"

    @classmethod
    def poll(cls, context):
        item = _selected_asset_result(context)
        return item is not None and item.has_custom_name

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        name_hash = _selected_asset_hash(context)
        if name_hash is None:
            self.report({"ERROR"}, "Select a hashed model or animation asset")
            return {"CANCELLED"}
        try:
            names = _asset_names(force=True)
            removed = names.remove_custom_name(name_hash)
            if removed:
                _refresh_asset_name_labels(names)
        except AssetNameError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not removed:
            self.report(
                {"INFO"},
                f"{format_asset_hash(name_hash)} has no custom name",
            )
            return {"CANCELLED"}
        self.report({"INFO"}, f"Removed name for {format_asset_hash(name_hash)}")
        return {"FINISHED"}


class MGS4_OT_import_asset_names(Operator, ImportHelper):
    bl_idname = "mgs4.import_asset_names"
    bl_label = "Import Asset Names"
    bl_description = "Merge custom asset names from a JSON dictionary"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    overwrite_existing: BoolProperty(
        name="Overwrite Existing Names",
        description="Replace custom names that already exist for imported hashes",
        default=False,
    )

    def execute(self, _context):
        try:
            names = _asset_names(force=True)
            result = names.import_custom_names(
                Path(self.filepath),
                overwrite_existing=self.overwrite_existing,
            )
            _refresh_asset_name_labels(names)
        except AssetNameError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Imported {result.added} new and {result.updated} updated names; "
            f"skipped {result.skipped}",
        )
        return {"FINISHED"}


class MGS4_OT_export_asset_names(Operator, ExportHelper):
    bl_idname = "mgs4.export_asset_names"
    bl_label = "Export Asset Names"
    bl_description = "Export custom asset names to a shareable JSON dictionary"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = "mgs4_asset_names.json"
        return ExportHelper.invoke(self, context, event)

    def execute(self, _context):
        try:
            names = _asset_names(force=True)
            count = names.export_custom_names(Path(self.filepath))
        except AssetNameError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {count} custom asset names")
        return {"FINISHED"}


class MGS4_OT_import_selected(Operator):
    bl_idname = "mgs4.import_selected"
    bl_label = "Import Selected MGS4 Asset"
    bl_description = "Import the selected virtual model, motion archive, or stage"

    def _import_model(
        self,
        context,
        catalog: GameCatalog,
        asset: AssetRecord,
        parent_collection: bpy.types.Collection | None = None,
        model=None,
    ):
        settings = context.scene.mgs4_importer
        model = model or parse_mdn(catalog.read(asset))
        resolver = (
            _resolver(context, catalog) if settings.import_materials else None
        )
        preferred_region = (
            "ww" if asset.virtual_path.lower().startswith("ww/") else "common"
        )
        result = import_mdn_model(
            context,
            model,
            name=_asset_import_name(asset, _asset_names()),
            scale=settings.scale,
            resolver=resolver,
            preferred_region=preferred_region,
            parent_collection=parent_collection,
            material_options=_material_options(
                context,
                layer_mode=settings.material_layer_mode,
                use_vertex_color=settings.use_vertex_color,
                use_vertex_alpha=settings.use_vertex_alpha,
            ),
        )
        result.collection["mgs4_virtual_path"] = asset.virtual_path
        _report_model_warnings(self, result.warnings)
        return result

    def _import_motion(
        self,
        context,
        catalog: GameCatalog,
        asset: AssetRecord,
        *,
        armature: bpy.types.Object | None = None,
        report: bool = True,
    ):
        settings = context.scene.mgs4_importer
        if armature is None:
            armature = _selected_armature(context)
        if armature is None:
            raise MGS4Error("Select the target MGS4 armature first")
        limit = settings.animation_limit or None
        archive = parse_mtar(catalog.read(asset), max_clips=limit)
        result = import_motion_archive(
            context,
            armature,
            archive,
            scale=settings.scale,
            max_actions=settings.animation_limit,
            archive_name=asset.virtual_path,
        )
        if not result.actions:
            raise MGS4Error("No motion tracks matched the selected armature")
        if result.missing_bones:
            self.report(
                {"WARNING"},
                f"{len(result.missing_bones)} motion bones were not in the armature",
            )
        if report:
            self.report(
                {"INFO"},
                f"Imported {len(result.actions)} actions"
                + (
                    f"; skipped {result.skipped_clips}"
                    if result.skipped_clips
                    else ""
                ),
            )
        return result

    def _import_stage_contents(
        self,
        context,
        catalog: GameCatalog,
        asset: AssetRecord,
        stage_collection: bpy.types.Collection,
    ) -> _StageImportResult:
        settings = context.scene.mgs4_importer
        layout = (
            catalog.stage_layout(asset.virtual_path)
            if settings.stage_use_layout
            else None
        )
        placements = layout.placements if layout is not None else ()
        failures = []
        parsed_models = {}
        failed_records = set()

        def load_model(record):
            if record in parsed_models:
                return parsed_models[record]
            if record in failed_records:
                return None
            try:
                model = parse_mdn(catalog.read(record))
            except MGS4Error as exc:
                failures.append(f"{record.virtual_path}: {exc}")
                failed_records.add(record)
                return None
            parsed_models[record] = model
            return model

        if layout is None:
            records = list(catalog.stage_models(asset.virtual_path))
        else:
            records = []
            for name_hash in layout.stage_model_hashes:
                record = catalog.model_for_hash(name_hash, asset.virtual_path)
                if record is None:
                    failures.append(
                        f"{asset.virtual_path}: stage model {name_hash:08X} "
                        "was not found"
                    )
                elif record not in records:
                    records.append(record)

        placed_hashes = tuple(
            dict.fromkeys(placement.model_hash for placement in placements)
        )
        placed_records = {}
        for name_hash in placed_hashes:
            record = catalog.model_for_hash(name_hash, asset.virtual_path)
            if record is None:
                failures.append(
                    f"{asset.virtual_path}: placed model {name_hash:08X} "
                    "was not found"
                )
            else:
                placed_records[name_hash] = record

        auxiliary_records = []
        exterior_records = []
        manifest_records = ()
        external_manifest_records = set()
        if layout is not None and (
            settings.stage_include_auxiliary
            or settings.stage_include_exterior
        ):
            manifest = catalog.stage_manifest(asset.virtual_path)
            manifest_records = manifest.models
            failures.extend(
                f"Unresolved data.cnf model reference: {reference}"
                for reference in manifest.unresolved_models
            )
            local_stage_records = catalog.stage_models(asset.virtual_path)
            external_manifest_records = (
                set(manifest_records) - set(local_stage_records)
            )
            candidate_records = tuple(
                dict.fromkeys(
                    (
                        *records,
                        *placed_records.values(),
                        *local_stage_records,
                        *manifest_records,
                    )
                )
            )
            for record in candidate_records:
                load_model(record)
            selected_records = (*records, *placed_records.values())
            if settings.stage_include_auxiliary:
                auxiliary_records = list(
                    infer_auxiliary_stage_models(
                        parsed_models,
                        selected_records,
                    )
                )
            if settings.stage_include_exterior:
                exterior_records = list(
                    infer_exterior_stage_models(
                        parsed_models,
                        (*selected_records, *auxiliary_records),
                    )
                )

        auxiliary_collection = None
        exterior_collection = None
        record_entries = [
            (
                record,
                stage_collection,
                "GCX_STATIC" if layout is not None else "CACHE_MODEL",
            )
            for record in records
        ]
        if auxiliary_records:
            auxiliary_collection = bpy.data.collections.new(
                "MGS4_Auxiliary_Environment"
            )
            auxiliary_collection["mgs4_auxiliary_environment"] = True
            stage_collection.children.link(auxiliary_collection)
            record_entries.extend(
                (record, auxiliary_collection, "AUXILIARY_ENVIRONMENT")
                for record in auxiliary_records
            )
        if exterior_records:
            exterior_collection = bpy.data.collections.new(
                "MGS4_Exterior_Environment"
            )
            exterior_collection["mgs4_exterior_environment"] = True
            stage_collection.children.link(exterior_collection)
            record_entries.extend(
                (record, exterior_collection, "EXTERIOR_LOD")
                for record in exterior_records
            )

        imported = 0
        auxiliary_imported = 0
        exterior_imported = 0
        instances = 0
        skipped = 0
        unresolved_transforms = 0
        imported_objects = []
        armature = None
        window_manager = context.window_manager
        progress_total = len(record_entries) + len(placed_hashes)
        window_manager.progress_begin(0, max(1, progress_total))
        try:
            for record_index, (
                record,
                parent_collection,
                stage_role,
            ) in enumerate(record_entries):
                window_manager.progress_update(record_index)
                model = load_model(record)
                if model is None:
                    continue
                if (
                    stage_role != "EXTERIOR_LOD"
                    and model.header.bone_count > settings.stage_bone_limit
                ):
                    skipped += 1
                    continue
                if (
                    settings.stage_model_limit > 0
                    and imported >= settings.stage_model_limit
                ):
                    skipped += len(record_entries) - record_index
                    break
                try:
                    result = self._import_model(
                        context,
                        catalog,
                        record,
                        parent_collection=parent_collection,
                        model=model,
                    )
                except MGS4Error as exc:
                    failures.append(f"{record.virtual_path}: {exc}")
                    continue
                result.collection["mgs4_stage_role"] = stage_role
                if record in external_manifest_records:
                    result.collection["mgs4_manifest_stage_model"] = True
                if stage_role == "AUXILIARY_ENVIRONMENT":
                    result.collection["mgs4_inferred_stage_model"] = True
                    auxiliary_imported += 1
                elif stage_role == "EXTERIOR_LOD":
                    result.collection["mgs4_exterior_stage_model"] = True
                    exterior_imported += 1
                imported += 1
                if stage_role != "EXTERIOR_LOD":
                    imported_objects.extend(result.objects)
                if (
                    stage_role != "EXTERIOR_LOD"
                    and result.armature is not None
                ):
                    armature = result.armature

            for placed_index, name_hash in enumerate(placed_hashes):
                window_manager.progress_update(
                    len(record_entries) + placed_index
                )
                if (
                    settings.stage_model_limit > 0
                    and imported >= settings.stage_model_limit
                ):
                    skipped += len(placed_hashes) - placed_index
                    break
                record = placed_records.get(name_hash)
                if record is None:
                    continue
                model = load_model(record)
                if model is None:
                    continue
                if model.header.bone_count > settings.stage_bone_limit:
                    skipped += 1
                    continue
                try:
                    result = self._import_model(
                        context,
                        catalog,
                        record,
                        parent_collection=stage_collection,
                        model=model,
                    )
                except MGS4Error as exc:
                    failures.append(f"{record.virtual_path}: {exc}")
                    continue
                stage_collection.children.unlink(result.collection)
                result.collection["mgs4_instance_source"] = True
                imported += 1

                model_placements = (
                    placement
                    for placement in placements
                    if placement.model_hash == name_hash
                )
                for instance_index, placement in enumerate(model_placements):
                    instance = bpy.data.objects.new(
                        f"{result.collection.name}_Instance_{instance_index:04d}",
                        None,
                    )
                    instance.instance_type = "COLLECTION"
                    instance.instance_collection = result.collection
                    instance.matrix_world = game_transform_matrix(
                        placement.position,
                        placement.rotation,
                        settings.scale,
                    )
                    instance["mgs4_model_hash"] = name_hash
                    instance["mgs4_command_hash"] = placement.command_hash
                    if placement.effect_hash is not None:
                        instance["mgs4_effect_hash"] = placement.effect_hash
                    if placement.property_hash is not None:
                        instance["mgs4_property_hash"] = placement.property_hash
                    if (
                        placement.position is None
                        and placement.rotation is None
                    ):
                        unresolved_transforms += 1
                        instance["mgs4_unresolved_transform"] = True
                    stage_collection.objects.link(instance)
                    imported_objects.append(instance)
                    instances += 1
        finally:
            window_manager.progress_end()

        if (
            auxiliary_collection is not None
            and not auxiliary_collection.children
            and not auxiliary_collection.objects
        ):
            bpy.data.collections.remove(auxiliary_collection)
        if (
            exterior_collection is not None
            and not exterior_collection.children
            and not exterior_collection.objects
        ):
            bpy.data.collections.remove(exterior_collection)
        elif exterior_collection is not None:
            exterior_collection.hide_render = True
            exterior_collection.hide_viewport = True
        stage_collection["mgs4_auxiliary_model_count"] = auxiliary_imported
        stage_collection["mgs4_exterior_model_count"] = exterior_imported
        stage_collection["mgs4_manifest_model_count"] = len(manifest_records)
        stage_collection["mgs4_manifest_environment_count"] = sum(
            record in external_manifest_records
            for record in (*auxiliary_records, *exterior_records)
        )
        return _StageImportResult(
            models=imported,
            auxiliary_models=auxiliary_imported,
            exterior_models=exterior_imported,
            instances=instances,
            skipped=skipped,
            unresolved_transforms=unresolved_transforms,
            failures=failures,
            objects=imported_objects,
            armature=armature,
        )

    def _import_stage_group(
        self,
        context,
        catalog: GameCatalog,
        asset: AssetRecord,
    ) -> None:
        group_name = asset.virtual_path.replace("/", "_").upper()
        root = bpy.data.collections.new(f"MGS4_{group_name}")
        (context.collection or context.scene.collection).children.link(root)

        if asset.kind == MISSION:
            root["mgs4_mission_path"] = asset.virtual_path
            stages = catalog.mission_stages(asset.virtual_path)
        else:
            root["mgs4_stage_path"] = asset.virtual_path
            stages = (asset,)

        totals = _StageImportResult(0, 0, 0, 0, 0, 0, [], [], None)
        for stage in stages:
            if asset.kind == MISSION:
                stage_name = stage.virtual_path.replace("/", "_").upper()
                stage_collection = bpy.data.collections.new(
                    f"MGS4_{stage_name}"
                )
                stage_collection["mgs4_stage_path"] = stage.virtual_path
                root.children.link(stage_collection)
            else:
                stage_collection = root
            result = self._import_stage_contents(
                context,
                catalog,
                stage,
                stage_collection,
            )
            totals.models += result.models
            totals.auxiliary_models += result.auxiliary_models
            totals.exterior_models += result.exterior_models
            totals.instances += result.instances
            totals.skipped += result.skipped
            totals.unresolved_transforms += result.unresolved_transforms
            totals.failures.extend(result.failures)
            totals.objects.extend(result.objects)
            totals.armature = result.armature or totals.armature

        if totals.models == 0:
            bpy.data.collections.remove(root)
            detail = (
                totals.failures[0]
                if totals.failures
                else "No models matched the stage layout and bone limit"
            )
            raise MGS4Error(f"Stage import produced no models: {detail}")
        _activate_import(context, tuple(totals.objects), totals.armature)
        message = (
            f"Imported {totals.models} model sources and "
            f"{totals.instances} placed instances; skipped {totals.skipped}"
        )
        if totals.auxiliary_models:
            message += (
                f"; {totals.auxiliary_models} auxiliary environment sources"
            )
        if totals.exterior_models:
            message += (
                f"; {totals.exterior_models} disabled exterior sources"
            )
        warnings = len(totals.failures) + totals.unresolved_transforms
        if totals.unresolved_transforms:
            message += (
                f"; {totals.unresolved_transforms} placements used "
                "their model origin"
            )
        if totals.failures:
            message += f"; {len(totals.failures)} failed (see console)"
            for failure in totals.failures:
                print(f"MGS4 stage import: {failure}")
        self.report({"WARNING"} if warnings else {"INFO"}, message)

    def execute(self, context):
        settings = context.scene.mgs4_importer
        if not settings.results:
            self.report({"ERROR"}, "Search for an asset first")
            return {"CANCELLED"}
        if not 0 <= settings.result_index < len(settings.results):
            self.report({"ERROR"}, "Choose a search result")
            return {"CANCELLED"}

        item = settings.results[settings.result_index]
        if not _result_matches(item, settings.search_text):
            self.report({"ERROR"}, "Choose a matching search result")
            return {"CANCELLED"}
        asset = _asset_from_result(item)
        try:
            catalog = _catalog(context)
            if asset.kind == MODEL:
                model = parse_mdn(catalog.read(asset))
                result = self._import_model(
                    context,
                    catalog,
                    asset,
                    model=model,
                )
                _activate_import(context, result.objects, result.armature)
                report_level = {"INFO"}
                message = (
                    f"Imported {len(result.objects)} objects from "
                    f"{_asset_import_name(asset, _asset_names())}"
                )
                if (
                    settings.import_animations
                    and result.armature is not None
                    and _model_is_skinned(model)
                ):
                    try:
                        motion_asset = catalog.default_motion_for_model(
                            asset,
                            tuple(bone.name_hash for bone in model.bones),
                        )
                        if motion_asset is None:
                            message += "; no compatible animation archive found"
                        else:
                            motion_result = self._import_motion(
                                context,
                                catalog,
                                motion_asset,
                                armature=result.armature,
                                report=False,
                            )
                            message += (
                                f"; imported {len(motion_result.actions)} actions "
                                f"from {Path(motion_asset.virtual_path).name}"
                            )
                    except MGS4Error as exc:
                        report_level = {"WARNING"}
                        message += (
                            "; automatic animation import failed: "
                            f"{exc}"
                        )
                self.report(
                    report_level,
                    message,
                )
            elif asset.kind == MOTION:
                self._import_motion(context, catalog, asset)
            elif asset.kind in {STAGE, MISSION}:
                self._import_stage_group(context, catalog, asset)
            else:
                raise MGS4Error(f"Unsupported catalog asset type: {asset.kind}")
        except MGS4Error as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MGS4_OT_import_mdn(Operator, ImportHelper):
    bl_idname = "import_scene.mgs4_mdn"
    bl_label = "Import MGS4 Model"
    bl_options = {"UNDO", "PRESET"}

    filename_ext = ".mdn"
    filter_glob: StringProperty(default="*.mdn", options={"HIDDEN"})
    scale: FloatProperty(name="Scale", default=0.001, min=0.000001, precision=6)
    import_materials: BoolProperty(name="Materials and Textures", default=True)
    material_layer_mode: EnumProperty(
        name="Layer Presentation",
        items=(
            (
                LAYER_MODE_CONSERVATIVE,
                "Conservative",
                "Use diffuse for Base Color and keep layer textures inspectable",
            ),
            (
                LAYER_MODE_PREVIEW,
                "First Layer Preview",
                "Use the first layer texture for Base Color",
            ),
        ),
        default=LAYER_MODE_CONSERVATIVE,
    )
    use_vertex_color: BoolProperty(
        name="Vertex Color RGB",
        default=True,
    )
    use_vertex_alpha: BoolProperty(
        name="Vertex Color Alpha",
        default=False,
    )

    def execute(self, context):
        try:
            model = parse_mdn(Path(self.filepath).read_bytes())
            resolver = None
            if self.import_materials:
                catalog = _catalog(context)
                resolver = _resolver(context, catalog)
            asset = AssetRecord(MODEL, self.filepath, None, 0)
            result = import_mdn_model(
                context,
                model,
                name=_asset_import_name(asset, _asset_names()),
                scale=self.scale,
                resolver=resolver,
                material_options=_material_options(
                    context,
                    layer_mode=self.material_layer_mode,
                    use_vertex_color=self.use_vertex_color,
                    use_vertex_alpha=self.use_vertex_alpha,
                ),
            )
            result.collection["mgs4_source_file"] = self.filepath
            _activate_import(context, result.objects, result.armature)
            _report_model_warnings(self, result.warnings)
        except (OSError, MGS4Error) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MGS4_OT_import_mtar(Operator, ImportHelper):
    bl_idname = "import_scene.mgs4_mtar"
    bl_label = "Import MGS4 Motion Archive"
    bl_options = {"UNDO", "PRESET"}

    filename_ext = ".mtar"
    filter_glob: StringProperty(default="*.mtar", options={"HIDDEN"})
    scale: FloatProperty(name="Scale", default=0.001, min=0.000001, precision=6)
    action_limit: IntProperty(
        name="Action Limit",
        default=100,
        min=0,
        description="Maximum actions to import; 0 imports all",
    )

    def execute(self, context):
        armature = _selected_armature(context)
        if armature is None:
            self.report({"ERROR"}, "Select the target MGS4 armature first")
            return {"CANCELLED"}
        try:
            archive = parse_mtar(
                Path(self.filepath).read_bytes(),
                max_clips=self.action_limit or None,
            )
            result = import_motion_archive(
                context,
                armature,
                archive,
                scale=self.scale,
                max_actions=self.action_limit,
                archive_name=self.filepath,
            )
        except (OSError, MGS4Error, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not result.actions:
            self.report({"ERROR"}, "No motion tracks matched the selected armature")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {len(result.actions)} actions")
        return {"FINISHED"}


class MGS4_PT_importer(Panel):
    bl_label = "MGS4 Remastered"
    bl_idname = "MGS4_PT_importer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MGS4"

    def draw(self, context):
        layout = self.layout
        preferences = _preferences(context)
        settings = context.scene.mgs4_importer
        if (
            settings.catalog_root != preferences.game_root
            or settings.catalog_kind != settings.asset_kind
            or settings.catalog_names_version
            != BUNDLED_ASSET_NAMES_VERSION
        ):
            _schedule_asset_refresh(context.scene)

        paths = layout.box()
        paths.label(text="Game Data", icon="FILE_FOLDER")
        paths.prop(preferences, "game_root", text="")
        paths.prop(preferences, "cache_root", text="Cache")

        search = layout.box()
        search.prop(settings, "asset_kind")
        row = search.row(align=True)
        row.prop(settings, "search_text", text="")
        row.operator("mgs4.search_assets", text="", icon="FILE_REFRESH")
        if settings.catalog_error:
            search.label(text=settings.catalog_error, icon="ERROR")
        search.template_list(
            "MGS4_UL_asset_results",
            "",
            settings,
            "results",
            settings,
            "result_index",
            rows=7,
        )
        names = search.row(align=True)
        names.operator("mgs4.set_asset_name", text="Set Name", icon="GREASEPENCIL")
        names.operator("mgs4.remove_asset_name", text="Remove", icon="X")
        transfer = search.row(align=True)
        transfer.operator(
            "mgs4.import_asset_names",
            text="Import Names",
            icon="IMPORT",
        )
        transfer.operator(
            "mgs4.export_asset_names",
            text="Export Names",
            icon="EXPORT",
        )

        options = layout.box()
        options.prop(settings, "scale")
        options.prop(settings, "import_materials")
        if settings.import_materials:
            options.prop(settings, "material_layer_mode")
            options.prop(settings, "use_vertex_color")
            options.prop(settings, "use_vertex_alpha")
        if settings.asset_kind == MODEL:
            options.prop(settings, "import_animations")
            if settings.import_animations:
                options.prop(settings, "animation_limit")
        elif settings.asset_kind in {STAGE, MISSION}:
            options.prop(settings, "stage_bone_limit")
            options.prop(settings, "stage_model_limit")
            options.prop(settings, "stage_use_layout")
            if settings.stage_use_layout:
                options.prop(settings, "stage_include_auxiliary")
                options.prop(settings, "stage_include_exterior")
        elif settings.asset_kind == MOTION:
            options.prop(settings, "animation_limit")

        layout.operator("mgs4.import_selected", icon="IMPORT")
        layout.separator()
        row = layout.row(align=True)
        row.operator("import_scene.mgs4_mdn", text="Loose MDN")
        row.operator("import_scene.mgs4_mtar", text="Loose MTAR")


def _menu_import(self, _context):
    self.layout.operator(
        MGS4_OT_import_mdn.bl_idname,
        text="Metal Gear Solid 4 Model (.mdn)",
    )
    self.layout.operator(
        MGS4_OT_import_mtar.bl_idname,
        text="Metal Gear Solid 4 Motion Archive (.mtar)",
    )


CLASSES = (
    MGS4ImporterPreferences,
    MGS4AssetResult,
    MGS4SceneSettings,
    MGS4_OT_search_assets,
    MGS4_UL_asset_results,
    MGS4_OT_set_asset_name,
    MGS4_OT_remove_asset_name,
    MGS4_OT_import_asset_names,
    MGS4_OT_export_asset_names,
    MGS4_OT_import_selected,
    MGS4_OT_import_mdn,
    MGS4_OT_import_mtar,
    MGS4_PT_importer,
)


def register():
    _asset_names(force=True)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mgs4_importer = PointerProperty(type=MGS4SceneSettings)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    global _asset_name_registry
    _cancel_pending_asset_refreshes()
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    del bpy.types.Scene.mgs4_importer
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _catalogs.clear()
    _resolvers.clear()
    _asset_name_registry = None
