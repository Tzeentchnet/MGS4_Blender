from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from mgs4_remastered_importer.asset_names import (
    AssetNameError,
    AssetNameRegistry,
    asset_hash_from_path,
    load_user_names,
)
from mgs4_remastered_importer.catalog import (
    MISSION,
    MODEL,
    MOTION,
    STAGE,
    AssetRecord,
    GameCatalog,
    first_model_occurrences,
    infer_auxiliary_stage_models,
    infer_exterior_stage_models,
    select_default_motion,
)
from mgs4_remastered_importer.errors import FormatError
from mgs4_remastered_importer.formats.common import uint32_to_int32
from mgs4_remastered_importer.formats.mdn import parse_mdn
from mgs4_remastered_importer.formats.mtar import MotionArchive, parse_mtar
from mgs4_remastered_importer.formats.texture import TextureResolver, _write_tga
from mgs4_remastered_importer.formats.vpak import (
    VpakArchive,
    decompress_lz4_block,
)
from mgs4_remastered_importer.inferred_lods import (
    inferred_lod_chain,
    inferred_lod_chains,
    inferred_lod_lower_hashes,
    is_inferred_lod_lower,
)


INSTALL_ROOT = Path(
    os.environ.get(
        "MGS4_ROOT",
        r"E:\SteamLibrary\steamapps\common\METAL GEAR SOLID 4",
    )
)
DATA_ROOT = INSTALL_ROOT / "MGS4" if (INSTALL_ROOT / "MGS4").is_dir() else INSTALL_ROOT
STAGE_PAK = DATA_ROOT / "common" / "stage" / "stage_data_compressed.1.pak"
RIG_PATH = "common/stage/stage01/s01a00l/cache/007ec102.mdn"
MOTION_PATH = "common/stage/stage01/s01a00l/cache/0005bb6e.mtar"
CHARACTER_MOTION_PATH = "common/stage/stage01/s01a00l/cache/00815bae.mtar"
RESTART_STRIP_PATH = "common/stage/stage00/r_sna01/resident/00397327.mdn"
NON_MDN_PATH = "common/stage/slot/00000027/cache/00560e6d.mdn"
BUNDLED_NAMES = (
    Path(__file__).resolve().parents[1]
    / "mgs4_remastered_importer"
    / "resources"
    / "solideye_dictionary.txt"
)


class AssetNameTests(unittest.TestCase):
    def test_hash_is_parsed_from_asset_filename(self):
        self.assertEqual(
            asset_hash_from_path("common/mesh/pc/00c3a92a.mdn"),
            0x00C3A92A,
        )
        self.assertEqual(asset_hash_from_path(r"cache\000062b5.mdn"), 0x000062B5)
        self.assertIsNone(asset_hash_from_path("cache/model.mdn"))
        self.assertIsNone(asset_hash_from_path("cache/123456789.mdn"))

    def test_bundled_names_preserve_collisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = AssetNameRegistry.load(
                BUNDLED_NAMES,
                Path(temporary) / "asset_names.json",
            )
        self.assertEqual(
            registry.bundled_candidates(0x000062B5),
            ("s01a_bpot_a0_sk",),
        )
        self.assertEqual(
            registry.bundled_candidates(0x0031397B),
            ("nwp_def", "p90_def"),
        )
        self.assertEqual(registry.display_name(0x00C3A92A), "")

    def test_custom_name_overrides_and_persists(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            wordlist = directory / "bundled.txt"
            wordlist.write_text("known_name\n", encoding="utf-8")
            user_path = directory / "asset_names.json"
            registry = AssetNameRegistry.load(wordlist, user_path)
            name_hash = next(iter(registry.bundled_names))

            registry.set_custom_name(name_hash, "Friendly Name")
            self.assertEqual(registry.display_name(name_hash), "Friendly Name")
            self.assertEqual(
                registry.searchable_names(name_hash),
                ("Friendly Name", "known_name"),
            )
            reloaded = AssetNameRegistry.load(wordlist, user_path)
            self.assertEqual(reloaded.custom_name(name_hash), "Friendly Name")

            self.assertTrue(reloaded.remove_custom_name(name_hash))
            self.assertEqual(reloaded.display_name(name_hash), "known_name")
            self.assertFalse(reloaded.remove_custom_name(name_hash))

    def test_import_merges_without_overwriting_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            wordlist = directory / "bundled.txt"
            wordlist.write_text("known_name\n", encoding="utf-8")
            user_path = directory / "asset_names.json"
            registry = AssetNameRegistry.load(wordlist, user_path)
            registry.set_custom_name(1, "Existing")

            imported_path = directory / "imported.json"
            imported_path.write_text(
                '{"schema_version": 1, "names": {'
                '"00000001": "Replacement", "00000002": "Added"}}',
                encoding="utf-8",
            )
            result = registry.import_custom_names(
                imported_path,
                overwrite_existing=False,
            )
            self.assertEqual((result.added, result.updated, result.skipped), (1, 0, 1))
            self.assertEqual(registry.custom_name(1), "Existing")
            self.assertEqual(registry.custom_name(2), "Added")

            result = registry.import_custom_names(
                imported_path,
                overwrite_existing=True,
            )
            self.assertEqual((result.added, result.updated, result.skipped), (0, 1, 1))
            self.assertEqual(registry.custom_name(1), "Replacement")

    def test_invalid_user_dictionary_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            path.write_text(
                '{"schema_version": 1, "names": {"not-a-hash": "Name"}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AssetNameError, "Invalid asset hash"):
                load_user_names(path)


class InferredLodTests(unittest.TestCase):
    def test_validated_chains_are_loaded_highest_to_lowest(self):
        chains = inferred_lod_chains()
        self.assertEqual(len(chains), 105)
        self.assertEqual(sum(len(chain) for chain in chains), 307)
        self.assertEqual(sum(len(chain) == 3 for chain in chains), 97)
        self.assertEqual(sum(len(chain) == 2 for chain in chains), 8)
        self.assertIn(
            (0x0051D172, 0x0051A3F7, 0x0051B7F4),
            chains,
        )

    def test_every_member_resolves_to_its_complete_chain(self):
        chain = (0x0051D172, 0x0051A3F7, 0x0051B7F4)
        self.assertEqual(inferred_lod_chain(chain[0]), chain)
        self.assertEqual(inferred_lod_chain(chain[1]), chain)
        self.assertEqual(inferred_lod_chain(chain[2]), chain)
        self.assertIsNone(inferred_lod_chain(0x00FFFFFF))

    def test_only_lower_levels_are_hidden(self):
        lower_hashes = inferred_lod_lower_hashes()
        self.assertEqual(len(lower_hashes), 202)
        self.assertNotIn(0x0051D172, lower_hashes)
        self.assertTrue(is_inferred_lod_lower(0x0051A3F7))
        self.assertTrue(is_inferred_lod_lower(0x0051B7F4))
        self.assertFalse(is_inferred_lod_lower(0x0051D172))


class CommonFormatTests(unittest.TestCase):
    def test_uint32_to_int32_preserves_the_source_bit_pattern(self):
        self.assertEqual(uint32_to_int32(0), 0)
        self.assertEqual(uint32_to_int32(0x7FFFFFFF), 0x7FFFFFFF)
        self.assertEqual(uint32_to_int32(0x80000000), -0x80000000)
        self.assertEqual(uint32_to_int32(0xFFFFFFFF), -1)
        self.assertEqual(uint32_to_int32(0x80000000) & 0xFFFFFFFF, 0x80000000)
        with self.assertRaises(ValueError):
            uint32_to_int32(-1)
        with self.assertRaises(ValueError):
            uint32_to_int32(0x100000000)


class Lz4Tests(unittest.TestCase):
    def test_literal_block(self):
        self.assertEqual(decompress_lz4_block(b"\x50hello", 5), b"hello")

    def test_literal_length_extension(self):
        literal = b"abcdefghijklmnopqrst"
        self.assertEqual(
            decompress_lz4_block(b"\xf0\x05" + literal, len(literal)),
            literal,
        )

    def test_non_overlapping_match(self):
        block = b"\x80abcdWXYZ\x08\x00"
        self.assertEqual(
            decompress_lz4_block(block, 12),
            b"abcdWXYZabcd",
        )

    def test_overlapping_match(self):
        block = b"\x32abc\x03\x00"
        self.assertEqual(decompress_lz4_block(block, 9), b"abcabcabc")

    def test_match_length_extension(self):
        block = b"\x4fabcd\x04\x00\x01"
        self.assertEqual(
            decompress_lz4_block(block, 24),
            b"abcd" * 6,
        )

    def test_decodes_without_declared_size(self):
        block = b"\x32abc\x03\x00"
        self.assertEqual(decompress_lz4_block(block), b"abcabcabc")

    def test_rejects_truncated_literal(self):
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"\x20a")

    def test_rejects_truncated_match_offset(self):
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"\x00\x01")

    def test_rejects_invalid_match_offsets(self):
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"\x00\x00\x00")
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"\x00\x01\x00")

    def test_enforces_declared_size(self):
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"\x50hello", 4)
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"\x50hello", 6)
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"\x10a\x01\x00", 4)
        with self.assertRaises(FormatError):
            decompress_lz4_block(b"", -1)


class TextureConversionTests(unittest.TestCase):
    def test_argb32_is_batched_to_tga_bgra(self):
        payload = bytes(
            (
                0x10,
                0x20,
                0x30,
                0x40,
                0x50,
                0x60,
                0x70,
                0x80,
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "texture.tga"
            _write_tga(path, payload, 2, 1)
            self.assertEqual(
                path.read_bytes()[18:],
                bytes(
                    (
                        0x40,
                        0x30,
                        0x20,
                        0x10,
                        0x80,
                        0x70,
                        0x60,
                        0x50,
                    )
                ),
            )


class MotionSelectionTests(unittest.TestCase):
    @staticmethod
    def _candidate(
        virtual_path: str,
        bone_hashes: tuple[int, ...],
        clip_count: int,
    ) -> tuple[AssetRecord, MotionArchive]:
        return (
            AssetRecord(MOTION, virtual_path, None, 0),
            MotionArchive(
                max_joint_count=0,
                max_effect_position_count=0,
                flags=0,
                bone_hashes=bone_hashes,
                clips=(),
                source_motion_count=clip_count,
            ),
        )

    def test_exact_skeleton_has_priority(self):
        selected = select_default_motion(
            (1, 2, 3),
            (
                self._candidate("cache/partial.mtar", (1, 2), 1_000),
                self._candidate("cache/exact.mtar", (1, 2, 3), 1),
            ),
        )
        self.assertEqual(selected.virtual_path, "cache/exact.mtar")

    def test_coverage_clip_count_and_path_break_ties(self):
        selected = select_default_motion(
            (1, 2, 3, 4),
            (
                self._candidate("cache/small.mtar", (1,), 1_000),
                self._candidate("cache/short.mtar", (1, 2), 5),
                self._candidate("cache/z.mtar", (1, 2), 10),
                self._candidate("cache/a.mtar", (1, 2), 10),
            ),
        )
        self.assertEqual(selected.virtual_path, "cache/a.mtar")

    def test_incompatible_and_empty_archives_are_ignored(self):
        selected = select_default_motion(
            (1, 2, 3),
            (
                self._candidate("cache/incompatible.mtar", (1, 4), 10),
                self._candidate("cache/empty-bones.mtar", (), 10),
                self._candidate("cache/empty-clips.mtar", (1,), 0),
            ),
        )
        self.assertIsNone(selected)


class CatalogFilterTests(unittest.TestCase):
    def test_first_model_occurrences_preserve_catalog_order(self):
        first = AssetRecord(
            MODEL,
            "common/stage/stage00/s00a10l/cache/00e1a168.mdn",
            None,
            3_440,
        )
        duplicate = AssetRecord(
            MODEL,
            "common/stage/stage01/s01a00l/cache/00e1a168.mdn",
            None,
            3_440,
        )
        other = AssetRecord(
            MODEL,
            "common/stage/stage01/s01a00l/cache/00463172.mdn",
            None,
            21_280,
        )

        self.assertEqual(
            first_model_occurrences((first, duplicate, other)),
            (first, other),
        )


@unittest.skipUnless(STAGE_PAK.is_file(), "MGS4 installation is not available")
class InstalledAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = VpakArchive(STAGE_PAK)

    def test_catalog_discovers_asset_types(self):
        catalog = GameCatalog(INSTALL_ROOT)
        self.assertGreater(len(catalog.search(MODEL, limit=0)), 6_000)
        self.assertGreater(len(catalog.search(MOTION, limit=0)), 200)
        self.assertGreater(len(catalog.search(STAGE, limit=0)), 60)
        self.assertGreater(len(catalog.search(MISSION, limit=0)), 5)

    def test_catalog_falls_back_to_compatible_chapter_motion(self):
        catalog = GameCatalog(INSTALL_ROOT)
        model_asset = next(
            asset
            for asset in catalog.search(MODEL, "00aee46b.mdn", limit=0)
            if asset.virtual_path
            == "common/stage/stage03/s03a00l/cache/00aee46b.mdn"
        )
        model = parse_mdn(catalog.read(model_asset))
        motion_asset = catalog.default_motion_for_model(
            model_asset,
            tuple(bone.name_hash for bone in model.bones),
        )
        self.assertIsNotNone(motion_asset)
        self.assertEqual(
            motion_asset.virtual_path,
            "common/stage/stage03/s03a35l/cache/0084802f.mtar",
        )

    def test_catalog_falls_back_to_reused_model_motion(self):
        catalog = GameCatalog(INSTALL_ROOT)
        for virtual_path in (
            "common/stage/stage02/s02a30l/cache/00463172.mdn",
            "common/stage/stage03/s03a15l/cache/00463172.mdn",
            "ww/stage/slot/0000301e/cache/00463172.mdn",
        ):
            model_asset = next(
                asset
                for asset in catalog.search(MODEL, "00463172.mdn", limit=0)
                if asset.virtual_path == virtual_path
            )
            model = parse_mdn(catalog.read(model_asset))
            motion_asset = catalog.default_motion_for_model(
                model_asset,
                tuple(bone.name_hash for bone in model.bones),
            )
            self.assertIsNotNone(motion_asset)
            self.assertEqual(
                motion_asset.virtual_path,
                "common/stage/stage01/s01a10l/cache/00489370.mtar",
            )

    def test_catalog_resolves_inferred_lod_chain_from_any_member(self):
        catalog = GameCatalog(INSTALL_ROOT)
        selected = catalog.model_for_hash(
            0x0051A3F7,
            "common/stage/stage02/s02a40l",
        )
        self.assertIsNotNone(selected)
        chain = catalog.inferred_lod_models(selected)
        self.assertEqual(
            tuple(Path(asset.virtual_path).stem for asset in chain),
            ("0051d172", "0051a3f7", "0051b7f4"),
        )
        self.assertTrue(
            all(
                asset.virtual_path.startswith(
                    "common/stage/stage02/s02a40l/"
                )
                for asset in chain
            )
        )
        self.assertEqual(catalog.inferred_lod_models(chain[-1]), chain)

    def test_stage_layout_resolves_environment_and_auxiliary_models(self):
        catalog = GameCatalog(INSTALL_ROOT)
        stage = catalog.search(STAGE, "stage05/s20a10l", limit=1)[0]
        resources = catalog.stage_resources(stage.virtual_path)
        self.assertEqual(len(resources.models), 22)
        self.assertEqual(len(resources.scripts), 1)
        self.assertEqual(len(resources.geometries), 1)
        self.assertEqual(len(resources.configurations), 1)

        manifest = catalog.stage_manifest(stage.virtual_path)
        self.assertEqual(len(manifest.models), 104)
        self.assertEqual(manifest.unresolved_models, ())
        self.assertIn(
            "common/stage/stage04/s04a75l/cache/00674471.mdn",
            {asset.virtual_path for asset in manifest.models},
        )
        self.assertIn(
            "common/stage/stage04/s04a75l/cache/00174953.mdn",
            {asset.virtual_path for asset in manifest.models},
        )

        layout = catalog.stage_layout(stage.virtual_path)
        self.assertIsNotNone(layout)
        self.assertEqual(
            set(layout.stage_model_hashes),
            {
                0x00FB6416,
                0x00931C63,
                0x002EA1C9,
                0x00C3447C,
                0x00B53074,
                0x00B53874,
                0x00B54074,
            },
        )
        self.assertEqual(layout.placements, ())
        for name_hash in layout.stage_model_hashes:
            asset = catalog.model_for_hash(name_hash, stage.virtual_path)
            self.assertIsNotNone(asset)
            self.assertTrue(
                asset.virtual_path.startswith(
                    "common/stage/stage05/s20a10l/"
                )
            )
        models = {
            asset: parse_mdn(catalog.read(asset))
            for asset in manifest.models
        }
        selected = tuple(
            catalog.model_for_hash(name_hash, stage.virtual_path)
            for name_hash in layout.stage_model_hashes
        )
        auxiliary = infer_auxiliary_stage_models(models, selected)
        self.assertEqual(
            {Path(asset.virtual_path).stem for asset in auxiliary},
            {"00374816", "00674471", "00f7458e"},
        )
        self.assertEqual(
            {
                Path(asset.virtual_path).stem:
                sum(len(mesh.triangles) for mesh in models[asset].meshes)
                for asset in auxiliary
            },
            {
                "00374816": 266,
                "00674471": 8_894,
                "00f7458e": 873,
            },
        )
        exterior = infer_exterior_stage_models(
            models,
            (*selected, *auxiliary),
        )
        self.assertEqual(
            tuple(Path(asset.virtual_path).stem for asset in exterior),
            ("00174953", "00374492"),
        )
        self.assertEqual(
            {
                Path(asset.virtual_path).stem: (
                    models[asset].header.bone_count,
                    len(models[asset].meshes),
                )
                for asset in exterior
            },
            {
                "00174953": (10, 1),
                "00374492": (59, 16),
            },
        )

    def test_mission_groups_stage_areas_and_direct_placements(self):
        catalog = GameCatalog(INSTALL_ROOT)
        mission = catalog.search(MISSION, "stage05/s20", limit=1)[0]
        stages = catalog.mission_stages(mission.virtual_path)
        self.assertEqual(
            tuple(stage.virtual_path for stage in stages),
            (
                "common/stage/stage05/s20a00l",
                "common/stage/stage05/s20a10l",
            ),
        )
        layout = catalog.stage_layout(stages[0].virtual_path)
        self.assertEqual(
            layout.stage_model_hashes,
            (0x000ACB5F, 0x004DA2BB),
        )
        self.assertEqual(len(layout.placements), 4)
        self.assertEqual(
            {placement.model_hash for placement in layout.placements},
            {0x00FF5F4B},
        )
        self.assertTrue(
            all(placement.position is not None for placement in layout.placements)
        )

    def test_rigged_model_and_matching_motion(self):
        model = parse_mdn(self.archive.fetch(RIG_PATH))
        motion = parse_mtar(self.archive.fetch(MOTION_PATH))
        self.assertEqual(len(model.bones), 70)
        self.assertEqual(motion.source_motion_count, 3)
        self.assertEqual(len(motion.clips), 3)
        self.assertEqual(set(motion.bone_hashes), {bone.name_hash for bone in model.bones})
        self.assertTrue(any(mesh.weights for mesh in model.meshes))
        self.assertTrue(any(mesh.bone_indices for mesh in model.meshes))
        self.assertTrue(all(clip.tracks for clip in motion.clips))

    def test_restart_separated_triangle_strip(self):
        model = parse_mdn(self.archive.fetch(RESTART_STRIP_PATH))
        self.assertEqual(len(model.meshes), 1)
        self.assertEqual(len(model.meshes[0].triangles), 84)

    def test_motion_quaternion_uses_blender_convention(self):
        motion = parse_mtar(
            self.archive.fetch(CHARACTER_MOTION_PATH),
            max_clips=1,
        )
        self.assertEqual(motion.clips[0].source_index, 0)
        key = motion.clips[0].tracks[0].rotations[0]
        self.assertEqual(key.frame, 1)
        expected = (
            0.9999839678277213,
            -0.005375227720716683,
            -0.001780708699733073,
            0.0000095396085132908,
        )
        for actual, wanted in zip(key.value, expected):
            self.assertAlmostEqual(actual, wanted, places=7)

    def test_non_mdn_payload_is_rejected(self):
        payload = self.archive.fetch(NON_MDN_PATH)
        self.assertEqual(payload[:4], b"INDX")
        with self.assertRaisesRegex(FormatError, "Not an MDN model"):
            parse_mdn(payload)

    def test_texture_resolution_writes_blender_input(self):
        model = parse_mdn(self.archive.fetch(RIG_PATH))
        texture_hash = model.textures[0].name_hash
        with tempfile.TemporaryDirectory() as temporary:
            resolver = TextureResolver(DATA_ROOT, temporary)
            path, source = resolver.materialize(texture_hash)
            self.assertTrue(path.is_file())
            self.assertGreater(source.width, 0)
            self.assertGreater(source.height, 0)
            self.assertIn(path.suffix.lower(), {".dds", ".tga"})
            if path.suffix.lower() == ".dds":
                self.assertEqual(path.read_bytes()[:4], b"DDS ")

            cached_resolver = TextureResolver(DATA_ROOT, temporary)
            with mock.patch.object(
                cached_resolver,
                "_scan_sources",
                side_effect=AssertionError("source archives were rescanned"),
            ):
                self.assertEqual(
                    cached_resolver.resolve(texture_hash),
                    source,
                )


if __name__ == "__main__":
    unittest.main()
