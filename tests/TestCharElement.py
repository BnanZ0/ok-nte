import tempfile
import unittest
from pathlib import Path

import cv2
from ok.test.TaskTestCase import TaskTestCase

from src.char.BaseChar import Element
from src.config import config
from src.tasks.trigger.AutoCombatTask import AutoCombatTask

TEAM = [0, 1, 2, 3]


class TestAutoCombatTask(TaskTestCase):
    task_class = AutoCombatTask

    config = config

    def test_element_matching_across_resolutions(self):
        frame = cv2.imread("tests/images/elements_1.png")
        expected = [Element.WHITE, Element.GREEN, Element.GREEN, Element.WHITE]
        with tempfile.TemporaryDirectory() as temporary:
            for width, height in ((2560, 1440), (1920, 1080), (3840, 2160)):
                with self.subTest(height=height):
                    path = Path(temporary) / f"elements_{height}.png"
                    interpolation = cv2.INTER_AREA if height < frame.shape[0] else cv2.INTER_LINEAR
                    resized = cv2.resize(frame, (width, height), interpolation=interpolation)
                    success, encoded = cv2.imencode(".png", resized)
                    self.assertTrue(success)
                    path.write_bytes(encoded.tobytes())
                    self.set_image(str(path))
                    chars_elements = self.task.load_chars_element(TEAM)
                    self.assertEqual([chars_elements[i] for i in TEAM], expected)


if __name__ == "__main__":
    unittest.main()
