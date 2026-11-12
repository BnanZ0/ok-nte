import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch
from weakref import ref

import cv2
import numpy as np

from src.utils.game_templates import GameTemplateSet


def image_and_mask(image, mask, _height_ratio):
    return image, mask


class TestGameTemplates(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(temporary)
        self.name = "巡礼素材"
        self.path = self.directory / f"{self.name}.PNG"

    def write_png(self, image):
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        self.path.write_bytes(encoded.tobytes())

    def test_concurrent_first_lookup_shares_one_decode_and_preparation(self):
        image = np.zeros((12, 16, 4), np.uint8)
        image[2:10, 3:13] = 255
        image[3, 5, 3] = 100
        self.write_png(image)
        group = GameTemplateSet(self.directory, reference_scale=1)
        start = Barrier(4)
        prepare = Mock(side_effect=image_and_mask)

        def lookup(_):
            start.wait(timeout=5)
            return group.get_template(self.name, 1440, prepare=prepare)

        with (
            patch("src.utils.game_templates.cv2.imdecode", wraps=cv2.imdecode) as decode,
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            results = list(pool.map(lookup, range(4)))

        decode.assert_called_once()
        prepare.assert_called_once()
        first = results[0]
        for result in results:
            self.assertIs(result, first)
        loaded, mask = first
        np.testing.assert_array_equal(loaded, image[2:10, 3:13])
        self.assertEqual(mask[1, 2], 0)
        self.assertEqual(mask[1, 3], 255)
        self.assertFalse(loaded.flags.writeable)
        self.assertFalse(mask.flags.writeable)

    def test_name_lookup_preserves_filename_for_display_and_ignores_case(self):
        self.path = self.directory / "WHITE.png"
        self.write_png(np.zeros((8, 8, 3), np.uint8))
        group = GameTemplateSet(self.directory, reference_scale=1)
        self.assertEqual(group.list_names(), ("WHITE",))
        first = group.get_template("White", 1440, prepare=image_and_mask)
        self.assertIsNotNone(first)
        self.assertIs(group.get_template("WHITE", 1440, prepare=image_and_mask), first)

    def test_height_change_releases_old_size_and_resizes_the_original(self):
        image = np.random.default_rng(2).integers(0, 256, (24, 32, 3), np.uint8)
        self.write_png(image)
        group = GameTemplateSet(self.directory, reference_scale=0.5, reference_height=1440)
        with patch("src.utils.game_templates.cv2.imdecode", wraps=cv2.imdecode) as decode:
            first, _ = group.get_template(self.name, 1440, prepare=image_and_mask)
            old_image = ref(first)
            del first
            resized, _ = group.get_template(self.name, 2160, prepare=image_and_mask)
            self.assertIsNone(old_image())
            expected = cv2.resize(image, (24, 18), interpolation=cv2.INTER_AREA)
            np.testing.assert_array_equal(resized, expected)
            decode.assert_called_once()

    def test_failed_reads_and_decodes_are_retried(self):
        self.path.write_bytes(b"invalid PNG")
        group = GameTemplateSet(self.directory, reference_scale=1)
        self.assertEqual(group.list_names(), (self.name,))
        self.assertIsNone(group.get_template(self.name, 1440, prepare=image_and_mask))

        self.path.unlink()
        self.assertIsNone(group.get_template(self.name, 1440, prepare=image_and_mask))

        image = np.full((8, 8, 3), 100, np.uint8)
        self.write_png(image)
        loaded, mask = group.get_template(self.name, 1440, prepare=image_and_mask)
        np.testing.assert_array_equal(loaded, image)
        self.assertIsNone(mask)
