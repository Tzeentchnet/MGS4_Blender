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

## Observed shader variants

The installed PC asset corpus contains 48 distinct low-byte material/shader
variants. These counts deduplicate repeated stage copies by model filename
hash and file size: 4,068 parseable model variants contain 46,907 material
table entries. The catalogued `00560E6D.mdn` is not an MDN and is excluded.
**Model variants** is the number of distinct models containing at least one
material with that shader variant.

| Variant | Material entries | Model variants |
| --- | ---: | ---: |
| `0x00` | 22,968 | 1,335 |
| `0x01` | 2,121 | 1,022 |
| `0x02` | 1,540 | 266 |
| `0x03` | 8,083 | 904 |
| `0x04` | 494 | 148 |
| `0x05` | 3,484 | 331 |
| `0x06` | 757 | 92 |
| `0x07` | 8 | 3 |
| `0x0A` | 1,057 | 139 |
| `0x0C` | 801 | 102 |
| `0x0D` | 291 | 26 |
| `0x10` | 539 | 362 |
| `0x13` | 88 | 42 |
| `0x14` | 7 | 7 |
| `0x20` | 3 | 3 |
| `0x21` | 2 | 2 |
| `0x24` | 9 | 6 |
| `0x2A` | 12 | 3 |
| `0x30` | 416 | 216 |
| `0x31` | 33 | 25 |
| `0x32` | 1 | 1 |
| `0x41` | 142 | 59 |
| `0x50` | 1,312 | 922 |
| `0x51` | 12 | 9 |
| `0x52` | 294 | 96 |
| `0x60` | 146 | 91 |
| `0x61` | 251 | 174 |
| `0x62` | 2 | 2 |
| `0x64` | 8 | 4 |
| `0x66` | 1 | 1 |
| `0x67` | 2 | 2 |
| `0xA0` | 550 | 137 |
| `0xA1` | 1 | 1 |
| `0xA2` | 117 | 100 |
| `0xA3` | 451 | 126 |
| `0xA4` | 30 | 21 |
| `0xA5` | 47 | 40 |
| `0xA6` | 64 | 53 |
| `0xA8` | 8 | 8 |
| `0xAB` | 20 | 11 |
| `0xE0` | 341 | 212 |
| `0xE1` | 268 | 198 |
| `0xF0` | 74 | 23 |
| `0xF1` | 25 | 16 |
| `0xF2` | 7 | 4 |
| `0xF3` | 9 | 4 |
| `0xF4` | 5 | 2 |
| `0xF5` | 6 | 3 |

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

The first parameter's RGB components are treated as diffuse color. Its fourth
component is not used as opacity; opaque materials commonly store zero there.
The second parameter is treated as legacy specular color plus shininess.

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
both no-specular and environment flags and two declared textures resolves to
`diffuse, normal`.

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

Exact four-texture `0x00000050` materials whose first parameter is
`(3, 0, 0, 1)` use the observed slot order `secondary_color`, `normal`,
`diffuse`, `specular_gradient`. Slot 2 supplies Base Color while slot 0
remains available as an unconnected sRGB node. Across the model corpus this
signature occurs 15 times, covering five distinct texture sets; all five put
the complete presentation color in slot 2. This includes the handprint and
signature materials in `00B267E5`.

## Principled material conversion

Every MDN material becomes a new Blender material named
`MGS4_MAT_<HASH>`.

### Diffuse

Parameter zero's RGB components become Base Color. Non-finite, effectively
zero, or invalid values fall back to `(0.8, 0.8, 0.8)`. Components are
clamped to Blender's zero-to-one range.

The exact variant-`0x50` signature described above treats parameter zero as
shader-specific control data instead. Its neutral fallback avoids presenting
the `(3, 0, 0, 1)` control value as a red Blender material color.

Principled Alpha remains `1.0` unless an `opacity` texture role, a recognized
diffuse-alpha material, or enabled vertex alpha supplies an opacity link. The
fourth legacy diffuse component is not a reliable opacity value.

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
- `diffuse`, `layer`, `environment`, and `secondary_color` roles use sRGB
  Images. Normal, specular, opacity, incidence, gradient, and unknown roles
  use Non-Color Images.
- Blender Images are keyed by region, texture hash, dimensions, and
  `COLOR`/`DATA` interpretation. If one game texture hash is used in both
  classes, the importer deliberately creates separate Image datablocks so
  their Image-level color-space settings cannot overwrite one another.
- Diffuse color connects to Base Color in Conservative mode. First Layer
  Preview substitutes the first inferred `layer` texture without guessing a
  mask or blend operation.
- Two-texture variant `0x00` materials with bits 17 and 20 set use the diffuse
  image alpha as opacity when its source format carries a full alpha channel
  (`RGBA32`, `DXT3`, or `DXT5`). This covers bullet marks, grates, and similar
  transparent surfaces without treating their DXT1 counterparts as
  transparent. Materials used exclusively by mesh render-family `0x10`
  additionally require render-state bit `0x00001000` on at least one user
  mesh. Matching materials use dithered masks with transparency overlap
  enabled for grass and similar cards. Without that mesh state, diffuse alpha
  is treated as auxiliary packed data and the material remains opaque, as for
  `MGS4_MAT_00F9864C` on `s01a33a_Mesh_045`. Other render families retain
  blended rendering with transparency overlap disabled.
- Single-texture materials with exact flags `0x00100010` use alpha-capable
  diffuse sources as blended opacity by default. This signature covers grime,
  drip, and smear decals such as `MGS4_MAT_001E4C5B`; DXT1 variants remain
  opaque. The importer separately recognizes dense spatial tree geometry
  within render-family `0x00`: a state-qualified material region with at least
  512 triangles and a smallest-to-largest axis-span ratio of at least 40% uses
  dithered opacity instead. Across all 536 occurrences of this material
  signature, that rule selects only the six `s04a_tree_a[0-2]_loda[_t1]`
  variants. It restores the authored branch masks in diffuse atlases
  `00DDEC65` and `008D4334` while preserving blended decal behavior.
- Four-texture materials with exact flags `0x00110000`, used exclusively by
  mesh render-family `0x10`, use graded diffuse alpha as blended opacity when
  the diffuse source supports alpha. This covers reflective vehicle glass
  such as materials `004E2EE5` and `0048747B` in `00C3A92A`; their flat-gray
  RGB is a base tint while alpha defines the window coverage. DXT1 variants
  remain opaque. Environment texture nodes remain visible but conservatively
  unconnected until their reflection mapping is understood.
- Two-texture materials with exact flags `0x000000A5` use a shader-specific
  constant presentation when parameter 2 W lies strictly between zero and
  one. Parameter 2 W becomes blended opacity and parameter 3 W becomes
  Principled roughness. This covers environment-reflective glass such as
  `MGS4_MAT_00F31905` on `0033F269_Mesh_001`, where the white diffuse texture
  is intentionally opaque and the material parameters instead specify
  opacity `0.3999` and roughness `0.09998`. Corpus uses include glasses,
  visors, champagne glass, and vehicle windows. Parameter 2 W boundary values
  of zero and one remain unchanged because those occurrences represent
  distinct fully reflective or opaque presentations. The environment node
  remains available but unconnected until its reflection mapping is known.
- Two- or three-texture materials with flags `0x00120000` or `0x00120002`
  are inferred as terrain-detail decals when all of their mesh users belong
  to render-family `0x15` and their geometry contains a vertex-alpha boundary
  fade. These materials use blended rendering with transparency overlap
  disabled. Vertex alpha is connected automatically even when the general
  **Vertex Color Alpha** option is off. An alpha-capable diffuse texture is
  multiplied by vertex alpha; DXT1 materials use vertex alpha alone.
- Single-texture variant `0x30` materials with an alpha-capable source use the
  diffuse image alpha as a vegetation-card mask. They use Blender's dithered
  rendering with transparency overlap enabled so intersecting cards remain
  visible.
- Three-texture materials with exact flags `0x00100050` are also inferred as
  vegetation masks when they are used exclusively by render-family `0x80`,
  carry mesh render-state bit `0x00001000`, and their material geometry spans
  at least 10% of its largest dimension on every axis. This spatial-card
  signature selects plant clusters such as `0078B3B0` and `0078B3F0` while
  excluding thin planar effects with the same material flags. Alpha-capable
  diffuse sources use dithered masks with transparency overlap enabled.
- Three-texture variant `0xA3` materials used exclusively by render-family
  `0x05` use alpha-capable diffuse sources as dithered hair or fur masks. The
  variant itself is the discriminator because verified hair can omit mesh
  render-state bit `0x00001000`, as on `00846B09_Mesh_011`. DXT1 sources
  remain opaque.
- Four-texture variant `0x03` materials use the same dithered hair-mask
  presentation only when they are exclusive to render-family `0x05` and at
  least one user mesh carries render-state bit `0x00001000`. This covers the
  low-range chest-hair alpha in `MGS4_MAT_009FDFB9` on
  `00846B09_Mesh_012` without changing the many opaque variant-`0x03`
  materials.
- Five-texture variant `0x60` materials exclusive to render-family `0x05`
  with render-state bit `0x00001000` use alpha-capable diffuse sources as
  blended scripted-overlay opacity. This covers the graded blood textures on
  `00846B09_Mesh_009`, its paired mesh 10, and `00846B09_Mesh_022`.
  Transparency overlap is disabled for these overlays, and DXT1 counterparts
  remain opaque.
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
enables blended rendering. Inferred render-family `0x15` detail decals are the
exception: their verified boundary-fade alpha is connected automatically.
RGB and alpha availability are evaluated independently, so disabling or
rejecting RGB does not discard valid alpha.

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
location.

The cache root also contains `.mgs4-texture-sources-v1.json`, a versioned index
of TXN texture hashes and their `TextureData.pak` blob paths. It avoids
rescanning all TXN descriptors in each fresh Blender process. The index is
rebuilt automatically when the configured game root changes or an index/data
archive's size or modification time changes. The game archives remain
unchanged.

Loaded Blender Images use names shaped like:

```text
MGS4_<REGION>_<HASH>_<WIDTH>x<HEIGHT>_<COLOR|DATA>
```

Missing checker Images use
`MGS4_MISSING_<HASH>_<COLOR|DATA>`.

## Custom properties

Unsigned 32-bit hashes and flags retain their source bit pattern in Blender's
signed 32-bit integer representation. Use `value & 0xFFFFFFFF` when an
unsigned value is required.

| Owner | Property | Meaning |
| --- | --- | --- |
| Material | `mgs4_flags` | Complete MDN material flags |
| Material | `mgs4_name_hash` | Material name hash |
| Material | `mgs4_texture_count` | Declared active texture count |
| Material | `mgs4_layer_mode` | Conservative or first-layer presentation |
| Material | `mgs4_vertex_color_available` | All users have meaningful vertex RGB |
| Material | `mgs4_vertex_alpha_available` | All users have meaningful vertex alpha |
| Material | `mgs4_inferred_detail_decal` | Material matched the detail-decal usage signature |
| Material | `mgs4_vertex_color_enabled` | Vertex RGB is connected |
| Material | `mgs4_vertex_alpha_enabled` | Vertex alpha is connected |
| Material | `mgs4_diffuse_alpha_enabled` | Recognized diffuse alpha is connected to opacity |
| Material | `mgs4_diffuse_alpha_mode` | `BLENDED`, `MASKED`, or `NONE` |
| Material | `mgs4_alpha_render_state` | At least one user mesh carries render-state bit `0x00001000` |
| Material | `mgs4_diffuse_alpha_suppressed` | Family-`0x10` packed diffuse alpha was kept opaque because the render-state bit is absent |
| Material | `mgs4_inferred_tree_mask` | Exact variant-`0x10` dense spatial-tree signature matched |
| Material | `mgs4_inferred_variant_50_vegetation` | Exact variant-`0x50` spatial-card vegetation signature matched |
| Material | `mgs4_inferred_hair_mask` | Variant-`0xA3` or qualified variant-`0x03` character hair-mask signature matched |
| Material | `mgs4_inferred_scripted_overlay` | Qualified variant-`0x60` scripted overlay uses blended diffuse alpha |
| Material | `mgs4_variant_50_color_presentation` | Exact variant-`0x50` slot-2 color presentation is active |
| Material | `mgs4_variant_a5_translucency` | Fractional variant-`0xA5` material-parameter opacity is active |
| Material | `mgs4_variant_a5_opacity` | Constant opacity read from variant-`0xA5` parameter 2 W |
| Material | `mgs4_variant_a5_roughness` | Principled roughness read from variant-`0xA5` parameter 3 W |
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
