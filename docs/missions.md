# Missions

[Technical reference index](./README.md)

A Mission is a catalog grouping of related Stage Areas. It is not a game file
and has no independent binary parser.

Implementations:

- Grouping: [`catalog.py`](../mgs4_remastered_importer/catalog.py)
- Import operator: [`addon.py`](../mgs4_remastered_importer/addon.py)

## Grouping rule

Stage area names beginning with `s<digits>a` are grouped by the prefix before
the `a`.

For example:

```text
common/stage/stage05/s20a00l
common/stage/stage05/s20a10l
```

become:

```text
common/stage/stage05/s20
```

Grouping remains within one chapter. Stage Areas are sorted by virtual path,
so imports are deterministic.

## Blender hierarchy

Importing the example mission creates:

```text
MGS4_COMMON_STAGE_STAGE05_S20
|- MGS4_COMMON_STAGE_STAGE05_S20A00L
`- MGS4_COMMON_STAGE_STAGE05_S20A10L
```

The mission root stores `mgs4_mission_path`. Every child stores its own
`mgs4_stage_path`.

Each child is assembled through the complete Stage Area pipeline described in
[Stage areas](./stage-areas.md), including GCX/GEOM placement, manifest
resolution, auxiliary inference, and exterior classification.

## Settings and totals

The current stage settings are applied independently to every area:

- Scale.
- Materials and textures.
- Bone limit.
- Model limit.
- GCX/GEOM layout toggle.
- Auxiliary environment toggle.
- Exterior/cinematic toggle.

The model limit is therefore per Stage Area, not one shared mission-wide
budget.

After all children finish, the operator reports aggregate counts for model
sources, instances, auxiliary sources, exterior sources, skipped models,
failures, and unresolved transforms. The mission import fails and removes its
root only when every area produces zero models.

## Current limits

- Missions contain no mission timeline, transition, gameplay, or streaming
  logic.
- Shared model sources are imported separately in each Stage Area rather than
  deduplicated across the mission.
- Areas without common-region MDNs are not exposed as Stage Areas and
  therefore cannot enter a Mission group.
- A failure in one model or placement is reported while other areas continue;
  this can produce a partial mission hierarchy.
