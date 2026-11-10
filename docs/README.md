# Technical Asset Reference

This directory documents the asset types exposed by the MGS4 Blender
importer. The descriptions follow the current parser and Blender conversion
code; they are implementation references, not claims that every field in the
game formats is understood.

## Import types

| Import type | Primary source data | Blender result | Reference |
| --- | --- | --- | --- |
| Models | `MDN` | Collections, meshes, armatures, groups, and custom properties | [Models](./models.md) |
| Materials and textures | MDN material/texture tables, `TXN`, `TextureData.pak` | Principled materials, image nodes, DDS/TGA cache files | [Materials and textures](./materials-and-textures.md) |
| Animations | `MTAR` containing `MTCM` clips | Layered Blender Actions on an existing armature | [Animations](./animations.md) |
| Stage areas | MDN, `GCX`, `GEOM`, and `data.cnf` | A stage collection containing model sources and collection instances | [Stage areas](./stage-areas.md) |
| Missions | Catalog grouping of related stage areas | A mission collection with one child collection per stage area | [Missions](./missions.md) |

The selectable catalog types are Models, Animations, Stage Areas, and
Missions. Materials and textures are imported as part of a model, but have a
separate reference because their lookup and conversion pipeline is
independent of MDN geometry.

## Supporting formats

[Archive containers](./archive-containers.md) describes the PC `VPAK`
container used by the catalog and texture resolver, plus the read-only legacy
`DAR` and `QAR` readers.

## Shared conventions

- Game identifiers are generally 24-bit or 32-bit hashes. Blender names
  display them as eight uppercase hexadecimal digits. Numeric custom
  properties preserve unsigned 32-bit source values in Blender's signed
  32-bit representation; use `value & 0xFFFFFFFF` to recover the unsigned
  value.
- Game vectors are converted from Y-up to Blender Z-up as
  `(x, y, z) -> (x, -z, y)`.
- The default model and translation scale is `0.001`, converting the
  millimeter-scale source data to meters.
- Installed-game imports are read-only. Converted images and the derived TXN
  source index are written only to the configured texture cache.
- Parsers validate table counts, offsets, sizes, references, and decompressed
  lengths before exposing data to Blender.
- Unsupported or unresolved data is reported or retained as metadata rather
  than silently represented as a successful conversion.

## Implementation entry points

- Catalog and grouping: [`catalog.py`](../mgs4_remastered_importer/catalog.py)
- Blender conversion: [`blender_import.py`](../mgs4_remastered_importer/blender_import.py)
- Operators and import settings: [`addon.py`](../mgs4_remastered_importer/addon.py)
- Binary format parsers: [`formats/`](../mgs4_remastered_importer/formats)
