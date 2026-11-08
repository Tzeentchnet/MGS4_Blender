# Stage Areas

[Technical reference index](./README.md)

A Stage Area is a synthetic catalog asset assembled from several game
resources sharing the same chapter and area path. It is not one standalone
file.

Implementations:

- Catalog and inference:
  [`catalog.py`](../mgs4_remastered_importer/catalog.py)
- GCX layout parser:
  [`formats/gcx.py`](../mgs4_remastered_importer/formats/gcx.py)
- GEOM transform parser:
  [`formats/geom.py`](../mgs4_remastered_importer/formats/geom.py)
- Blender operator: [`addon.py`](../mgs4_remastered_importer/addon.py)

## Resource grouping

For a path such as `common/stage/stage05/s20a10l`, the catalog joins:

| Resource | Region | Purpose |
| --- | --- | --- |
| `*.mdn` | `common` | Model sources |
| `*.gcx` | `ww` | Model-set and placement commands |
| `*.geom` | `common` | Named effect/property transforms |
| `data.cnf` | `ww` | Model dependency manifest |

A Stage Area is created only when the common-region path contains at least one
MDN.

## Default import pipeline

With Use GCX/GEOM Layout enabled:

1. Parse the largest/primary GEOM resource when present.
2. Parse every GCX resource and merge their stage model sets and placements.
3. Resolve referenced model hashes, preferring the current stage and then the
   current chapter.
4. Import static stage model sources.
5. Import one source collection for each placed model hash.
6. Create collection instances for every resolved placement.
7. Optionally inspect local and manifest models for auxiliary environment and
   exterior/cinematic candidates.

With layout mode disabled, every MDN under the local stage path is imported
as a cache model. GCX placements and manifest inference are not used.

## GCX parsing

GCX uses little-endian container metadata and a tagged script bytecode.

- The parser reads a terminated definition table.
- Header offsets identify string and script sections.
- Defined scripts and the main script are decoded independently.
- Nested commands retain parent indices.
- Parameters retain their letter, name hash, and literal/strcode/argument
  values.
- Script and definition counts are bounded and every block must advance and
  end at its declared boundary.

The layout extractor recognizes known model-placement command hashes,
including the newer put-object and put-stage-model-set forms.

It resolves:

- Model and `model_glass` hashes.
- Direct positions.
- Packed game-angle directions.
- Named GEOM effects.
- `prop` and `prop_pos` property references.
- Values supplied through parent `foreach` data/argument tables.

A put-stage-model-set command with no transform contributes static stage model
hashes. Commands with transforms produce placements.

## GEOM transforms

GEOM supports both byte orders. Its 32-byte header is followed by 12-byte
chunk records. The importer currently reads chunk type 6 as a linked hierarchy
of effects.

Each effect exposes:

- Name hash.
- Position vector from its packed property slot.
- Three packed signed-angle values converted with `pi / 32768`.
- Optional parent index.

GCX property references can resolve either a direct effect or the children of
an effect, depending on the command form.

Other GEOM chunks are validated but not converted.

## Placement instances

Placed models are imported once as a source collection, then unlinked from the
visible stage hierarchy. Empty Blender objects instance that collection.

The transform conversion applies:

- Game-to-Blender axis conversion.
- Configured position scale.
- Basis conversion for XYZ Euler rotation.

If neither position nor rotation resolves, the instance remains at the model
origin and receives `mgs4_unresolved_transform`.

### Instance custom properties

| Property | Meaning |
| --- | --- |
| `mgs4_model_hash` | Referenced MDN hash |
| `mgs4_command_hash` | GCX placement command |
| `mgs4_effect_hash` | GEOM effect reference, when present |
| `mgs4_property_hash` | GEOM property reference, when present |
| `mgs4_unresolved_transform` | No position or rotation was resolved |

The hidden source collection receives `mgs4_instance_source`.

## `data.cnf` dependencies

The ASCII `data.cnf` manifest is parsed for unique non-comment lines ending in
`.mdn`. Resolution supports:

- Absolute `common/...` virtual paths.
- `cache/...` paths relative to the stage.
- `/../...` cross-stage suffix references.
- Other paths relative to the current stage.

Ambiguous cross-stage suffixes prefer a model stored in the same VPAK as the
configuration. Unresolved references are reported with their source
configuration path.

Manifest models are candidates for auxiliary environment and exterior
classification; the manifest is not a command to import every dependency
directly.

## Auxiliary environment inference

The optional `MGS4_Auxiliary_Environment` collection contains conservative
static candidates authored in world coordinates.

A candidate must:

- Have at most one bone.
- Have a largest axis span from 1,000 through 500,000 game units.
- Be at least 25,000 game units horizontally from the origin.
- Be within 25,000 game units of an already selected or inferred environment
  bounding box.

Inference grows outward from selected models whose largest span is no more
than 500,000 units. The resulting collection receives
`mgs4_auxiliary_environment`; each source receives the
`AUXILIARY_ENVIRONMENT` stage role and `mgs4_inferred_stage_model`.

## Exterior/cinematic inference

The optional `MGS4_Exterior_Environment` collection preserves large rigged
representations that may overlap the detailed playable area.

A candidate must:

- Have at least two bones.
- Have a largest span from 500,000 through 1,500,000 game units.
- Have a second-largest span of at least 75,000 game units.
- Not already be selected as explicit or auxiliary geometry.

Exterior sources receive the `EXTERIOR_LOD` stage role and
`mgs4_exterior_stage_model`. The collection is disabled for both viewport and
render by default. Exterior classification bypasses the normal stage bone
limit.

## Settings

| Setting | Default | Effect |
| --- | ---: | --- |
| Scale | `0.001` | Converts source positions to Blender units |
| Materials and Textures | On | Builds MDN materials and resolves images |
| Stage Bone Limit | `32` | Skips non-exterior MDNs with more bones |
| Stage Model Limit | `0` | Maximum imported model sources; zero is unlimited |
| Use GCX/GEOM Layout | On | Uses explicit stage model sets and placements |
| Auxiliary Cached Environment | On | Enables conservative static inference |
| Exterior / Cinematic Models | On | Preserves large rigged exterior sources |

## Collection metadata

The stage root receives `mgs4_stage_path`. Imported model collections also
receive:

| Property | Meaning |
| --- | --- |
| `mgs4_stage_role` | `GCX_STATIC`, `CACHE_MODEL`, `AUXILIARY_ENVIRONMENT`, or `EXTERIOR_LOD` |
| `mgs4_manifest_stage_model` | Source came from another manifest-resolved stage |
| `mgs4_inferred_stage_model` | Auxiliary inference selected this source |
| `mgs4_exterior_stage_model` | Exterior inference selected this source |

The stage collection records:

- `mgs4_auxiliary_model_count`
- `mgs4_exterior_model_count`
- `mgs4_manifest_model_count`
- `mgs4_manifest_environment_count`

## Current limits

- Collision geometry, navigation/routes, lights, particles, and gameplay logic
  are not converted.
- Only known GCX placement commands and parameter forms are recognized.
- Runtime-generated placements that depend on unresolved systems such as LA2
  remain at model origin.
- GEOM data outside the named-effect chunk is not represented in Blender.
- Auxiliary and exterior selection is heuristic by design.
- Stage import assembles existing assets; it does not reproduce runtime
  streaming, visibility, or level logic.
