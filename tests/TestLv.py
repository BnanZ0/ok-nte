import unittest
from unittest.mock import Mock, patch

import numpy as np
from ok.feature.Feature import Feature
from ok.test.TaskTestCase import TaskTestCase

from src.combat.CombatCheck import CombatCheck
from src.config import config


class TestOcrLv(TaskTestCase):
    task_class = CombatCheck

    config = config

    def test_enemy_lv_text(self):
        # Create a BattleReport object
        self.set_image("tests/images/03.png")
        result = self.task.find_lv()
        self.logger.info(f"enemy_lv_text: {result}")
        self.assertEqual(len(result), 2)

    def test_boss_lv_text(self):
        self.set_image("tests/images/04.png")
        result = self.task.is_boss()
        self.logger.info(f"test test_boss_lv_text: {result}")
        self.assertEqual(result, True)


class TestLvTemplateCache(unittest.TestCase):
    def setUp(self):
        template = np.zeros((40, 80), dtype=np.uint8)
        template[5:30, 5:10] = 255
        template[25:30, 5:22] = 255
        template[5:30, 40:45] = 255
        template[25:30, 45:57] = 255
        self.template = template
        self.feature = Feature(template)

    def _make_task(self):
        task = object.__new__(CombatCheck)
        task.get_feature_by_name = Mock(return_value=self.feature)
        task.log_error = Mock()
        return task

    def test_two_task_instances_share_one_lv_template_initialization(self):
        first_task = self._make_task()
        second_task = self._make_task()
        with patch(
            "src.combat.CombatCheck.gf.isolate_lv_to_white", return_value=self.template
        ) as prep:
            first_features = first_task._get_lv_template_features()
            second_features = second_task._get_lv_template_features()

        self.assertIsNotNone(first_features)
        self.assertIs(first_features, second_features)
        prep.assert_called_once()
        self.assertEqual(self.feature.template_cache, {})

    def test_replaced_feature_rebuilds_contours_at_the_same_resolution(self):
        task = self._make_task()
        replacement = self.template.copy()
        replacement[10:24, 10:16] = 255
        with patch(
            "src.combat.CombatCheck.gf.isolate_lv_to_white", side_effect=lambda image: image
        ):
            before = task._get_lv_template_features()
            task.get_feature_by_name.return_value = Feature(replacement)
            after = task._get_lv_template_features()

        self.assertIsNotNone(after)
        self.assertNotEqual(before.l_fingerprint, after.l_fingerprint)


if __name__ == "__main__":
    unittest.main()
