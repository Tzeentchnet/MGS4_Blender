# Animations

[Technical reference index](./README.md)

Skeletal animations are stored in `MTAR` archives containing compressed
`MTCM` clips. An animation import targets an already imported MDN armature.

Implementations:

- Parser: [`formats/mtar.py`](../mgs4_remastered_importer/formats/mtar.py)
- Blender conversion:
  [`blender_import.py`](../mgs4_remastered_importer/blender_import.py)

## Discovery and target selection

The catalog indexes `.mtar` entries in the stage VPAKs. Loose MTAR files can
also be imported through Blender's File > Import menu.

Direct installed-game Model imports enable automatic animation import by
default. For a skinned MDN, candidates are limited to MTARs in the same
virtual directory and must have a non-empty bone table that is a subset of the
model skeleton. If that directory has no compatible archive, the importer
searches the remaining MTARs in the same region and stage chapter. Local-cache
matches always retain priority. Within the selected scope, one archive is
chosen by preferring an exact skeleton, then the largest compatible bone
table, then the greatest source clip count, and finally path order. This
restores chapter-shared animation libraries such as `0084802F.mtar` for
`00AEE46B.mdn`. Stage, mission, and loose MDN imports do not perform this
automatic search.

For manual animation imports, an armature object must be selected. Tracks
match Blender bones by the uppercase hexadecimal form of their source bone
hash. Matching model and motion archives commonly reside in the same stage
cache, but this is a convention rather than an enforced relationship.

## MTAR header

Both byte orders are supported:

| Magic bytes | Endianness |
| --- | --- |
| `ratM` | Big-endian |
| `Mtar` | Little-endian |

The 32-byte header records:

- Maximum joint count.
- Maximum effect-position count.
- Bone count.
- Motion count.
- Archive flags.
- MTCM and MTEX section offsets.
- Bone-hash table offset.
- Motion data-table offset.

The bone table contains one `u32` hash per bone. Each 16-byte motion-table
record contains MTCM offset/size and MTEX offset/size.

## MTCM clip data

The importer reads the 64-byte MTCM header, including:

- Clip name hash and flags.
- Base tick and frame count.
- Compressed archive offset and size.
- Joint count.
- Per-joint precision mask words.
- Low- and high-precision quaternion widths.
- Joint-index, root, movement, turn, and fix offsets.

Joint indices reference the MTAR-level bone table.

### Translation keys

Only the first joint's translation stream is currently decoded.

- X, Y, and Z are stored as 16-bit half floats.
- Parsed values are multiplied by `128.0`.
- An unsigned eight-bit frame delta follows each vector.
- Frame deltas are accumulated to produce keyframe positions.

### Rotation keys

Rotations use a variable-width bitstream.

1. Read an unsigned eight-bit frame delta.
2. Decode an angle in the zero-to-pi range.
3. Decode two axis components and derive the third.
4. Apply three sign bits.
5. Normalize the axis.
6. Convert the axis/angle pair to a quaternion.

The per-joint precision mask selects the low or high quaternion bit width.
Malformed widths, offsets, and bitstream spans are rejected.

## Blender Action conversion

Each usable clip becomes a Blender 5.2 layered Action named:

```text
<ARCHIVE_STEM>_<SOURCE_INDEX:04>_<CLIP_HASH:08X>
```

The Action receives:

- A fake user so it is retained when not active.
- An explicit frame range.
- One object slot for the target armature.
- One layer named `MGS4 Motion`.
- One keyframe strip and channel bag.
- Linear location and quaternion curves grouped by bone.

Quaternion signs are made continuous between adjacent keys to avoid
interpolation flips.

Root translations are converted from game coordinates and then transformed
into the target bone's local basis relative to its rest head. This preserves
the original floor height and horizontal root motion.

After import:

- The first generated Action becomes active.
- The scene frame rate is set to 60 FPS.
- The scene frame range follows the first Action.
- Bones animated by any imported clip are selected.

An Action with no matching curves is removed and counted as skipped.

## Action custom properties

Unsigned 32-bit hashes and flags retain their source bit pattern in Blender's
signed 32-bit integer representation. Use `value & 0xFFFFFFFF` when an
unsigned value is required.

| Property | Meaning |
| --- | --- |
| `mgs4_archive_name` | Source MTAR stem |
| `mgs4_clip_index` | Original motion-table index |
| `mgs4_name_hash` | Clip name hash |
| `mgs4_flags` | MTCM clip flags |
| `mgs4_base_tick` | Source base tick |
| `mgs4_frame_start` | Earliest decoded key |
| `mgs4_frame_count` | Declared clip frame count |
| `mgs4_fps` | Import rate, currently 60 |

## Filtering and limits

The parser API can select a set of clip hashes and can stop after a maximum
number of decoded clips. The Blender UI exposes an Action Limit, defaulting to
100; zero imports every clip. The same limit applies to the single archive
chosen during automatic Model import.

Tracks whose bone hashes are absent from the armature are skipped and
reported. The remaining tracks still produce a valid Action.

## Current limits

- MTEX event data is indexed but not converted to markers or events.
- Motion-point, movement, turn, and fix tracks are not converted.
- Only root-joint translations and joint rotations are currently decoded.
- `base_tick` is preserved as metadata but does not offset Blender frames.
- Human-readable clip and bone names are not available unless a separate hash
  dictionary is supplied.
- The importer does not retarget between different skeletons.
- Actions are imported but not exported back to MTAR.
