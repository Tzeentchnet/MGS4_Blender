# Archive Containers

[Technical reference index](./README.md)

The importer reads assets directly from the installed game. Most PC assets
are virtual files stored in `VPAK` archives; the extension also includes
read-only readers for legacy `DAR` and `QAR` containers.

## VPAK

Implementation:
[`formats/vpak.py`](../mgs4_remastered_importer/formats/vpak.py)

### Header

The 16-byte little-endian header is:

| Field | Type | Meaning |
| --- | --- | --- |
| Magic | `char[4]` | Must be `VPAK` |
| Major version | `u16` | Stored on the archive object |
| Minor version | `u16` | Stored as both the minor version and archive flags |
| Entry count | `u32` | Number of variable-length TOC records |
| TOC size | `u32` | Size of the table at the end of the file |

The table of contents starts at `file_size - toc_size`. Entry data must end
before that offset.

### TOC record

Each record contains:

| Field | Type | Notes |
| --- | --- | --- |
| Name length | `u32` | Includes any stored trailing null |
| Record flags | `u16` | Preserved but not interpreted |
| Name | `byte[name_length]` | UTF-8 with Latin-1 fallback |
| Reserved | `u64` | Must be zero |
| Uncompressed size | `u64` | Required output size |
| Compressed size | `u64` | Zero means the entry is stored raw |
| Data offset | `u64` | Absolute archive offset |
| Chunk size | `u32` | Expected uncompressed size per chunk |
| Chunk count | `u32` | Number of relative chunk offsets |
| Chunk offsets | `u64[chunk_count]` | Relative to the entry data |

Names are normalized to forward slashes before being placed in the record
index.

### Compression

Compressed entries use raw LZ4 blocks without an LZ4 frame header.

- A record with no effective chunk table is decompressed as one block.
- A chunked record is decompressed block by block.
- Every chunk except possibly the last must produce `chunk_size` bytes.
- The final combined output must exactly match `uncompressed_size`.

The decompressor implements literal-length and match-length extensions,
overlapping match copies, and strict source/output bounds checks.

### Archives used by imports

| Data | Typical archive |
| --- | --- |
| Models, motions, stage resources | `common/stage/stage_data_compressed.*.pak` and `ww/stage/stage_data_compressed.*.pak` |
| Texture descriptors | `common|ww/textures/PC_TXN_UP/txn_up.*.pak` |
| Texture payloads | `common|ww/textures/PC_TXN_UP/paks/TextureData.pak` |

The catalog indexes TOCs lazily and fetches an entry only when its content is
needed.

## DAR

Implementation:
[`formats/legacy_archive.py`](../mgs4_remastered_importer/formats/legacy_archive.py)

The read-only DAR reader handles the big-endian layout documented by
Solideye:

1. A big-endian `u32` entry count.
2. A null-terminated entry name.
3. Alignment to four bytes.
4. A big-endian `u32` payload size.
5. Payload data aligned to 16 bytes.

Names use UTF-8 with a Latin-1 fallback and are normalized to forward
slashes. The current catalog does not use DAR as a primary source for its
search results.

## QAR

The read-only QAR reader obtains the table offset from the final big-endian
`u32` in the file. The table contains:

1. A big-endian `u16` entry count and `u16` padding.
2. One `(file_info, size)` pair of big-endian `u32` values per entry.
3. One null-terminated name per entry.

Payloads begin at offset zero and are laid out in table order with `0x80`
alignment. `file_info` is preserved on each entry but is not interpreted.

## Validation and limitations

- Archive readers are extraction-only; they do not repack or modify game
  files.
- Entry counts, names, chunk offsets, payload spans, and final sizes are
  validated.
- VPAK record flags, DAR trailing bytes, and QAR `file_info` are retained or
  ignored as appropriate; no semantic interpretation is currently applied.
- Legacy archive support is not wired into the main asset catalog.
