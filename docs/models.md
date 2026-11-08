# Models

[Technical reference index](./README.md)

Models are stored as `MDN` files. An MDN can represent static environment
geometry, a rigid prop, a skinned character, or a rigged stage object.

Implementations:

- Parser: [`formats/mdn.py`](../mgs4_remastered_importer/formats/mdn.py)
- Blender conversion:
  [`blender_import.py`](../mgs4_remastered_importer/blender_import.py)

## Discovery

The installed-game catalog finds:

- Loose `common/mesh/**/*.mdn` files.
- MDNs in `common` and `ww` stage VPAKs.

Only common-region stage MDNs are used as direct stage geometry sources.
Models referenced by scripts or manifests are resolved by their filename
hash. Loose files can also be imported through Blender's File > Import menu.

## MDN header

The header is 128 bytes and begins with `MDN `. Both big- and little-endian
files are supported. Endianness is selected by interpreting the declared file
size at offset `0x5C` and comparing it with the available data.

The header records:

- Filename hash.
- Counts for bones, groups, meshes, face sections, vertex definitions,
  materials, textures, and skins.
- Offsets for each table.
- Vertex and face buffer offsets and sizes.
- Declared file size.
- Maximum and minimum bounds.

All counts and table spans are validated before records are read.

## Fixed-size tables

| Table | Stride | Imported data |
| --- | ---: | --- |
| Bone | 80 bytes | Name hash, flags, parent, parent/world positions, bounds |
| Group | 16 bytes | Name hash, flags, parent group |
| Mesh | 80 bytes | Group, flags, face range, vertex definition, skin, count, bounds, position |
| Face section | 16 bytes | Primitive type, index count/offset, material, start, size |
| Vertex definition | 48 bytes | Component count, vertex stride, buffer offset, semantic/type bytes |
| Material | 112 bytes | Flags, name hash, texture slots, half-float parameters |
| Texture | 32 bytes | Texture hash, flags, UV scale and offset |
| Skin | 40 bytes | Palette size and up to 32 global bone IDs |

See [Materials and textures](./materials-and-textures.md) for the material and
texture tables.

## Vertex data

Each vertex-definition component encodes its semantic in the low nibble and
its storage type in the high nibble.

### Imported semantics

| Semantic | Imported result |
| --- | --- |
| Position | Required three-component vertex position |
| Weight | Four normalized non-negative weights |
| Normal | Custom split normal |
| Color | Corner color attribute named `Color` |
| Bone index | Four indices, remapped through the skin palette when present |
| Texture 3DS / Texture 00-05 | Up to six UV layers |
| Tangent | Point-domain vector attribute named `MGS4_Tangent` |

### Supported component encodings

- 32-bit float.
- Signed normalized 16-bit integer.
- 16-bit half float.
- Unsigned normalized byte.
- Signed normalized byte.
- Packed 11:11:10 normal.

Packed normals are sign-extended, normalized to floating point, and
renormalized to unit length.

If a model supplies any values for an optional attribute, missing values are
completed with safe defaults. A missing position is a format error.

## Faces and materials

Face indices are 16-bit values stored in the shared face buffer.

- Sections containing `0xFFFF` restart markers, or whose index count is not a
  multiple of three, are decoded as triangle strips.
- Other sections are decoded as triangle lists.
- Alternating strip winding is corrected.
- Degenerate triangles are discarded.
- Every emitted triangle retains its face-section material index.

The Blender mesh receives only the materials actually referenced by that
mesh. Source material indices are remapped to its local material slots.

## Skeleton and skinning

An armature is created whenever the MDN contains bones.

- Bone names are uppercase hexadecimal hashes.
- Duplicate hashes receive an index suffix.
- Bone heads use the MDN world positions.
- Parent relationships follow the MDN parent indices.
- Bone tails are synthetic display tails; the source does not currently
  provide a complete Blender rest orientation and length.
- Bone flags, source index, and name hash are preserved as custom properties.

Skin palettes map each mesh-local bone byte to a global MDN bone index.
Weights are normalized while parsing and again after invalid influences are
removed. A mesh with valid influences receives vertex groups, an Armature
modifier, and the armature as its parent.

## Coordinate and attribute conversion

Game coordinates are converted as:

```text
Blender X = Game X
Blender Y = -Game Z
Blender Z = Game Y
```

Positions use the configured scale, which defaults to `0.001`. Normals and
tangents use the axis conversion without scale.

UV coordinates preserve U and flip V as `1.0 - v`. Meshes use smooth shading
and retain imported custom normals.

## Blender hierarchy

The model creates one root collection named from the MDN filename.

- One child collection is created for every MDN group.
- Group parent indices reproduce the source collection hierarchy.
- One Blender mesh object is created for every MDN mesh.
- Meshes are linked into their referenced group, or the model root when the
  group index is invalid.
- The armature, when present, is linked directly to the model root.

The MDN mesh position is retained as `mgs4_position`; it is not applied as a
separate object transform.

## Custom properties

| Owner | Property | Meaning |
| --- | --- | --- |
| Model collection | `mgs4_filename_hash` | MDN filename hash |
| Model collection | `mgs4_scale` | Import scale |
| Installed model collection | `mgs4_virtual_path` | Source archive path |
| Loose model collection | `mgs4_source_file` | Source filesystem path |
| Armature | `mgs4_filename_hash` | MDN filename hash |
| Bone | `mgs4_bone_index` | Source bone index |
| Bone | `mgs4_name_hash` | Source bone hash |
| Bone | `mgs4_flags` | Source bone flags |
| Group collection | `mgs4_group_index` | Source group index |
| Group collection | `mgs4_name_hash` | Source group hash |
| Group collection | `mgs4_flags` | Source group flags |
| Mesh object | `mgs4_mesh_index` | Source mesh index |
| Mesh object | `mgs4_flags` | Source mesh flags |
| Mesh object | `mgs4_group_index` | Source group index |
| Mesh object | `mgs4_skin_index` | Source skin palette index |
| Mesh object | `mgs4_position` | Source mesh position vector |

## Current limits

- Only the documented vertex semantics and encodings above are converted.
- Bone rest orientation and source bone shape are not reconstructed beyond
  hierarchy and positions.
- MDN material shaders are approximated with Blender nodes rather than
  executing the game's shaders.
- The importer is read-only and does not export MDN.
