# Materials and Textures

[Technical reference index](./README.md)

Materials and texture references are embedded in MDN models. Texture pixels
are resolved independently through the PC `PC_TXN_UP` indexes and
`TextureData.pak`.

Implementations:

- MDN records: [`formats/mdn.py`](../mgs4_remastered_importer/formats/mdn.py)
- Texture resolver:
  [`formats/texture.py`](../mgs4_remastered_importer/formats/texture.py)
- Blender nodes:
  [`blender_import.py`](../mgs4_remastered_importer/blender_import.py)

## MDN material record

Each 112-byte material record contains:

| Field | Representation |
| --- | --- |
| Flags | `u32` |
| Name hash | `u32` |
| Texture count | `u32`, capped to eight |
| Parameter count | `u32`, capped to eight |
| Texture indices | Eight `u32` indices into the MDN texture table |
| Parameters | Eight vectors of four half floats |

The first parameter is treated as diffuse color and alpha. The second is
treated as legacy specular color plus shininess.

Each 32-byte MDN texture record contains a texture hash, flags, U/V scale,
U/V offset, and two currently unused words.

## Material flags used by the importer

| Bits | Current interpretation |
| --- | --- |
| `0-7` | Material/shader variant used to infer texture slot roles |
| `16` | Use `UV1` instead of `UV0` |
| `17` | Disable specular response |
| `20` | Environment texture is present |

The low-byte mapping is a texture-role heuristic based on known MGS4 material
variants. It is not a static link to one compiled game shader.

### Base role sequence

Most variants begin with:

1. `diffuse`
2. `normal`, unless the variant disables normals
3. `specular`, unless flags or the variant disable specular
4. Variant-specific roles
5. `environment` when bit 20 is set and the variant permits it

Variant `0xA6` begins with `opacity` instead of `diffuse`. Variant `0x00` with
both no-specular and environment flags uses `diffuse, unknown`.

Normal is omitted for variants `0x30`, `0x31`, `0xA3`, `0xA5`, `0xA8`,
`0xE0`, and `0xE1`.

Specular is omitted when bit 17 is set or for variants `0x05`, `0xA5`,
`0xA8`, `0xAB`, `0xE0`, `0xE1`, and `0xF3`.

### Variant-specific roles

| Variant | Additional roles |
| --- | --- |
| `0x02` | `detail_normal` |
| `0x03` | `layer` |
| `0x05` | `layer` |
| `0x06` | `detail_normal`, `layer` |
| `0x0A` | `layer`, `detail_normal` |
| `0x0C` | `layer` |
| `0x10` | `environment` |
| `0x13` | `layer`, `unknown`, `unknown` |
| `0x14` | `environment`, `layer` |
| `0x21` | `detail_normal` |
| `0x24` | `detail_normal`, `layer` |
| `0x2A` | `incidence`, `layer`, `detail_normal` |
| `0x30` | `detail_normal` |
| `0x31` | `detail_normal` |
| `0x41` | `incidence`, `unknown` |
| `0x50` | `specular_gradient` |
| `0x51` | `specular_gradient` |
| `0x52` | `specular_gradient`, `environment` |
| `0x60` | `specular_gradient`, `incidence` |
| `0x61` | `specular_gradient`, `incidence` |
| `0xA0` | `unknown`, `unknown`, `unknown` |
| `0xA2` | `environment` |
| `0xA3` | `layer` |
| `0xA4` | `detail_normal`, `layer` |
| `0xA5` | `environment` |
| `0xAB` | `environment`, `specular_gradient` |
| `0xF0` | `specular_gradient`, `layer`, `layer_normal`, `incidence`, `unknown` |
| `0xF1` | `specular_gradient`, `incidence`, `unknown` |
| `0xF5` | `incidence`, `layer`, `detail_normal`, `detail_normal` |

The role list is padded with `unknown` or truncated to the material's declared
texture count. Unknown and advanced roles remain visible as labeled nodes
instead of being discarded.

## Principled material conversion

Every MDN material becomes a new Blender material named
`MGS4_MAT_<HASH>`.

### Diffuse

Parameter zero becomes Base Color. Non-finite, effectively zero, or invalid
values fall back to `(0.8, 0.8, 0.8, 1.0)`. Components are clamped to
Blender's zero-to-one range.

The alpha value is assigned to the Principled input, but the material remains
opaque unless an `opacity` texture role or the opt-in vertex-alpha setting
enables blended rendering.

### Specular and roughness

The RGB luminance of parameter one is converted conservatively:

```text
specular_level = min(0.25, luminance * 0.12)
roughness = clamp(1.6 * (2 / (shininess + 2)) ^ 0.25, 0.45, 1.0)
```

Zero shininess produces roughness `1.0`. A no-specular material forces
Specular IOR Level to zero and roughness to one. Materials are always
non-metallic.

### Texture nodes

- Every resolved texture receives an Image Texture, UV Map, and Mapping node.
- MDN scale and offset values drive the Mapping node.
- `diffuse`, `layer`, and `environment` roles use sRGB Images. Normal,
  specular, opacity, incidence, gradient, and unknown roles use Non-Color
  Images.
- Blender Images are keyed by region, texture hash, dimensions, and
  `COLOR`/`DATA` interpretation. If one game texture hash is used in both
  classes, the importer deliberately creates separate Image datablocks so
  their Image-level color-space settings cannot overwrite one another.
- Diffuse color connects to Base Color in Conservative mode. First Layer
  Preview substitutes the first inferred `layer` texture without guessing a
  mask or blend operation.
- Opacity color connects to Alpha and enables blended rendering.
- Specular color is multiplied by the converted specular level before
  connecting to Specular IOR Level.
- A packed normal reconstructs red from image alpha, preserves green, forces
  blue to one, and passes through a Normal Map node at strength `0.5`.
- Only the primary `normal` role is connected automatically. Detail, layer,
  incidence, environment, gradient, and unknown nodes remain available for
  inspection or manual node work.
- An outer frame identifies the complete material flags and shader variant.
  Nested slot frames identify role, slot, hash, and missing state.

### Missing textures

With **Highlight Missing Textures** enabled in the add-on preferences, an
unresolved texture gets an 8-by-8 magenta/dark checker Image. The expected
Image Texture, UV Map, and Mapping nodes remain in place, the warning is still
reported, and the Image and nodes carry missing-texture metadata. Color and
data roles receive appropriately configured checker datablocks.

Disable the preference to report the warning and omit the unresolved texture
node. The material's `mgs4_missing_texture_count` continues to record how many
declared slots could not be resolved.

### Vertex colors

When **Vertex Color RGB** is enabled, the `Color` corner attribute multiplies
the selected diffuse or layer-preview source before Base Color. The importer
only enables this connection when every mesh using that material has a valid
color layer with meaningful RGB values. Legacy character meshes that store
uniform zero in RGB but use alpha for separate data keep their diffuse texture
connected directly instead of being multiplied to black.

**Vertex Color Alpha** is independent and disabled by default. When enabled,
it drives Alpha, multiplying an existing opacity texture when present, and
enables blended rendering. RGB and alpha availability are evaluated
independently, so disabling or rejecting RGB does not discard valid alpha.

## Texture lookup

The resolver scans both regions:

```text
<game>/common/textures/PC_TXN_UP
<game>/ww/textures/PC_TXN_UP
```

Each tree uses `txn_up.1.pak` and `txn_up.2.pak` as descriptor indexes and
`paks/TextureData.pak` for pixel payloads.

When the same hash exists in both regions, the model's preferred region wins;
otherwise the first available source is used.

## TXN descriptor layout

TXN descriptors are big-endian.

- The 32-byte header identifies image and texture table counts and offsets.
- Each image record is 16 bytes and stores packed dimensions, format/mip
  flags, and payload start/end offsets.
- Each texture record is 48 bytes and maps a texture hash to an image-record
  reference.
- Every image has a 256-byte path slot after the tables.
- The payload size at slot offset `0xFC` is little-endian and must match the
  image record's declared range.

Blob paths are normalized before lookup in `TextureData.pak`.

## Supported pixel formats

| Format | ID | Cache output |
| --- | ---: | --- |
| 32-bit color | `0x03` | Uncompressed 32-bit TGA |
| DXT1 | `0x09` | DDS with generated header |
| DXT3 | `0x0A` | DDS with generated header |
| DXT5 | `0x0B` | DDS with generated header |

DDS mip count is inferred by summing expected mip sizes until the payload is
consumed. Compressed payload bytes are not recompressed.

## Cache behavior

Materialized files use:

```text
<cache>/<region>/<HASH>_<WIDTH>x<HEIGHT>_<FORMAT>.dds|tga
```

Existing non-empty files are reused. New files are written to a
process-specific temporary path and atomically replaced into the final
location. The game archives remain unchanged.

Loaded Blender Images use names shaped like:

```text
MGS4_<REGION>_<HASH>_<WIDTH>x<HEIGHT>_<COLOR|DATA>
```

Missing checker Images use
`MGS4_MISSING_<HASH>_<COLOR|DATA>`.

## Custom properties

| Owner | Property | Meaning |
| --- | --- | --- |
| Material | `mgs4_flags` | Complete MDN material flags |
| Material | `mgs4_name_hash` | Material name hash |
| Material | `mgs4_texture_count` | Declared active texture count |
| Material | `mgs4_layer_mode` | Conservative or first-layer presentation |
| Material | `mgs4_vertex_color_available` | All users have meaningful vertex RGB |
| Material | `mgs4_vertex_alpha_available` | All users have meaningful vertex alpha |
| Material | `mgs4_vertex_color_enabled` | Vertex RGB is connected |
| Material | `mgs4_vertex_alpha_enabled` | Vertex alpha is connected |
| Material | `mgs4_missing_texture_count` | Number of unresolved texture slots |
| Image | `mgs4_texture_hash` | Source texture hash |
| Image | `mgs4_interpretation` | `COLOR` or `DATA` |
| Image | `mgs4_missing_texture` | Generated missing-texture checker |
| Image Texture node | `mgs4_texture_hash` | Referenced texture hash |
| Image Texture node | `mgs4_texture_flags` | MDN texture flags |
| Image Texture node | `mgs4_role` | Inferred slot role |
| Image Texture node | `mgs4_interpretation` | `COLOR` or `DATA` |
| Image Texture node | `mgs4_missing_texture` | Texture could not be resolved |

## Game shader relationship

The PC game contains stage-oriented Shader Model 5 VFP bundles, but the
current importer does not parse or execute those programs. MDN material flags
alone do not select one unique vertex/pixel program: the game also considers
render pass, mesh layout, skinning, lighting, and runtime state.

The imported node graph is therefore a conservative Blender approximation,
not recovered HLSL and not a byte-for-byte recreation of the game renderer.

## Current limits

- Unsupported texture formats raise an explicit error.
- Texture-role mappings are incomplete for unknown material variants.
- Layer blending, detail composition, environment sampling, incidence, and
  gradient composition are not reconstructed automatically. First Layer
  Preview is a direct Base Color substitution, not inferred blending.
- Cubemap or other runtime sampling behavior is not inferred from the image
  payload alone.
- Materials and converted textures are imported, not exported back to MDN or
  TXN.
