# MGS4 Remastered Blender Importer

A Blender 5.2 extension for importing assets from the PC release of **METAL
GEAR SOLID 4**. It reads the installed game data directly with Python and does
not run the game executable, Solideye, or any other bundled executable.

## Features

- Reads PC `VPAK` archives and their chunked raw-LZ4 entries.
- Imports big- and little-endian `MDN` models.
- Imports static map geometry, rigid props, and skinned characters.
- Builds armatures with the MDN hierarchy and maps local skin palettes to the
  correct global bones.
- Converts the game's Y-up coordinates and millimeter-scale units to Blender's
  Z-up coordinates and meters.
- Resolves material texture hashes through the PC `PC_TXN_UP` index and
  `TextureData.pak`.
- Generates cached DDS/TGA files for DXT1, DXT3, DXT5, and ARGB32 textures,
  loading compressed DDS data directly through Blender 5.2.
- Uses Blender's bulk mesh/attribute APIs with Blender's bundled NumPy for
  accelerated geometry, UV, color, tangent, and material assignment.
- Builds Principled BSDF materials with diffuse, alpha, packed-normal, and
  conservative dielectric specular/shininess conversion. Opaque shaders do
  not route unused diffuse alpha into viewport dithering, and shader-specific
  layer/environment slots remain available as labeled nodes. Opacity/decal
  shaders use blended transparency with overlap sorting disabled.
- Imports `MTAR`/`MTCM` skeletal clips as Blender 5.2 layered Actions at 60 FPS.
- Reads stage GCX model-set and placement commands plus GEOM effect transforms.
- Resolves cross-stage model dependencies declared by each stage's `data.cnf`
  manifest instead of limiting map assembly to MDNs stored in the local cache.
- Provides a searchable archive browser for models, motion archives, complete
  stage areas, and multi-area mission groups.
- Includes read-only pure-Python DAR and QAR parsers based on the layouts
  documented by Solideye.

## Install

1. Build or use `dist/mgs4_remastered_importer-1.0.0.zip`.
2. In Blender 5.2, open **Edit > Preferences > Extensions**.
3. Use the menu in the upper-right and choose **Install from Disk**.
4. Select the ZIP and enable **MGS4 Remastered Importer**.

The extension defaults to:

```text
E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4
```

Change the path in the add-on preferences or the **MGS4** sidebar if needed.
Decoded textures are written only to the configured Blender cache. The game
installation is read-only.

## Use

### Installed-game assets

1. Open the **MGS4** tab in the 3D View sidebar.
2. Choose **Models**, **Animations**, **Stage Areas**, or **Missions**.
3. The selected asset type is indexed and listed automatically. Enter a stage
   code, virtual path fragment, or hexadecimal asset hash to refine the list.
   The refresh button re-indexes the installed archives when needed.
4. Select a result and click **Import Selected MGS4 Asset**.

For animation results, select the matching imported armature first. MTAR names
and model names are hashes; matching archives normally live in the same stage
cache. Human-readable clip names are not stored in MTAR, so imported Actions
use `ARCHIVE_CLIPINDEX_CLIPHASH`, retain the source fields as custom
properties, and define their own playback frame ranges. The importer skips
tracks whose hashed bone is not present.

For stage areas, **Use GCX/GEOM Layout** imports the static model set and placed
models referenced by the stage scripts. The importer also resolves `data.cnf`
model dependencies, including assets reused from other stage archives.
**Auxiliary Cached Environment** adds conservative static local or
manifest-declared models whose geometry is already authored in world
coordinates next to the explicit stage. These inferred sources are kept in a
named `MGS4_Auxiliary_Environment` collection instead of mixing in local props,
characters, alternate skies, or whole-map exterior LODs. For example, this
restores S20A10L's manifest-only `00674471` detailed ship section from
S04A75L. GEOM-backed transforms and direct script positions are converted into
Blender collection instances. **Exterior / Cinematic Models** preserves large
rigged structure or vehicle shells, including manifest dependencies, in a
disabled `MGS4_Exterior_Environment` collection; enable that collection when
the exterior representation is needed, since it can overlap the detailed
playable model. The default bone limit of `32` includes rigged doors and map
props while excluding most characters; classified exteriors are retained
independently of that limit. Disable layout mode only when intentionally
inspecting every MDN cached under a stage path.

Mission results group every area sharing a mission code within one stage
chapter. For example, `common/stage/stage05/s20` imports both `s20a00l` and
`s20a10l` into named child collections.

### Loose files

Use **File > Import > Metal Gear Solid 4 Model (.mdn)** or **Metal Gear Solid 4
Motion Archive (.mtar)**. Loose MDNs still resolve remastered textures through
the configured game installation.

## Format coverage and limits

- Stage import covers direct and `foreach` GCX model references, direct
  positions/directions, and transforms addressed through GEOM property
  effects. Some runtime-generated placements refer to unresolved properties or
  other systems such as `LA2`; these are imported at their model origin and
  marked with `mgs4_unresolved_transform` instead of being silently omitted.
- Auxiliary environment inference is deliberately conservative: local and
  `data.cnf`-declared candidates must be static, spatially authored away from
  the model origin, and contiguous with explicit stage geometry. Local runtime
  props still require a resolved GCX/GEOM transform before they can be
  assembled automatically.
- Large rigged exterior/cinematic representations are preserved separately and
  disabled in the viewport and render by default because their lower-detail
  surfaces can obscure the detailed stage.
- GEOM collision meshes, navmesh/routes, lights, particles, and gameplay logic
  are not converted to Blender scene data.
- MTAR import covers MTCM bone rotations and root translations. MTEX events and
  motion-point tracks are preserved in the source archive but are not converted
  to Blender markers. Absolute root positions are transformed from game space
  into the target root bone's local basis so the original floor height and
  horizontal root motion are preserved.
- MGS4 has many shader variants. The importer maps the known diffuse, normal,
  specular, opacity, layer, detail-normal, and environment slot ordering and
  keeps unconnected/unknown slots visible as labeled nodes.
- The default scale is `0.001`. Change it before import if a different scene
  unit convention is required.

## Development and validation

Run the standalone parser tests:

```powershell
python -m unittest discover -s tests -v
```

Run the Blender integration smoke test:

```powershell
& 'D:\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup `
  --python 'D:\Github\MGS4_Blender\tests\blender_smoke.py'
```

Build the installable ZIP:

```powershell
python .\build_extension.py
```

The integration tests read real assets from the configured installation but do
not modify it.

## References

Format research and behavior were cross-checked against:

- [Jayveer/Solideye](https://github.com/Jayveer/Solideye)
- [cipherxof/MGS4-MDN](https://github.com/cipherxof/MGS4-MDN)
- [Jayveer/MGS-MDN-Noesis](https://github.com/Jayveer/MGS-MDN-Noesis)
- [cipherxof/Haven](https://github.com/cipherxof/Haven)
- [LittleBitUA/GUNS-OF-THE-PATRIOTS-LOCALIZATION-TOOL](https://github.com/LittleBitUA/GUNS-OF-THE-PATRIOTS-LOCALIZATION-TOOL)

No game assets are included.
