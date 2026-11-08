from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from mgs4_remastered_importer.catalog import (
    MISSION,
    MODEL,
    MOTION,
    STAGE,
    GameCatalog,
    infer_auxiliary_stage_models,
    infer_exterior_stage_models,
)
from mgs4_remastered_importer.errors import FormatError
from mgs4_remastered_importer.formats.mdn import parse_mdn
from mgs4_remastered_importer.formats.mtar import parse_mtar
from mgs4_remastered_importer.formats.texture import TextureResolver, _write_tga
from mgs4_remastered_importer.formats.vpak import (
    VpakArchive,
    decompress_lz4_block,
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


class Lz4Tests(unittest.TestCase):
    def test_literal_block(self):
        self.assertEqual(decompress_lz4_block(b"\x50hello", 5), b"hello")

    def test_overlapping_match(self):
        block = b"\x32abc\x03\x00"
        self.assertEqual(decompress_lz4_block(block, 9), b"abcabcabc")


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


if __name__ == "__main__":
    unittest.main()
