import unittest

import src.patches.i18n_patch as i18n_patch
from src.patches.i18n_patch import _translate_key
from src.tasks.AnomalyTask import AnomalyTask
from src.tasks.BaseStaminaTask import BaseStaminaTask


class FakeApp:
    def __init__(self, translations):
        self.to_translate = set()
        self.translations = translations

    def original_tr(self, key):
        self.to_translate.add(key)
        return self.translations.get(key, key)


class TestI18nPatch(unittest.TestCase):
    def test_install_i18n_patch_wraps_gui_and_headless_apps(self):
        from ok import App, HeadlessApp

        original_app_tr = App.tr
        original_headless_tr = HeadlessApp.tr
        original_installed = i18n_patch._PATCH_INSTALLED
        try:
            i18n_patch._PATCH_INSTALLED = False
            i18n_patch.install_i18n_patch()

            self.assertIsNot(App.tr, original_app_tr)
            self.assertIsNot(HeadlessApp.tr, original_headless_tr)
        finally:
            App.tr = original_app_tr
            HeadlessApp.tr = original_headless_tr
            i18n_patch._PATCH_INSTALLED = original_installed

    def test_range_description_translates_template_without_collecting_numeric_values(self):
        app = FakeApp(
            {
                AnomalyTask.DESC_ID_RANGE_FMT: "Select which item in the list ({}-{})",
            }
        )

        translated = _translate_key(
            app,
            AnomalyTask.DESC_ID_RANGE_FMT.format(1, 5),
            FakeApp.original_tr,
        )

        self.assertEqual(translated, "Select which item in the list (1-5)")
        self.assertEqual(app.to_translate, {AnomalyTask.DESC_ID_RANGE_FMT})

    def test_cycle_option_translates_task_without_collecting_template_or_dynamic_option(self):
        app = FakeApp({AnomalyTask.TASK_ABILITY: "Ability Upgrade"})
        option = AnomalyTask.CYCLE_CUSTOM_OPTION_FMT.format(task=AnomalyTask.TASK_ABILITY, id=5)

        translated = _translate_key(app, option, FakeApp.original_tr)

        self.assertEqual(translated, "Ability Upgrade: 5")
        self.assertEqual(app.to_translate, {AnomalyTask.TASK_ABILITY})

    def test_stamina_description_reuses_translated_template_for_both_reward_costs(self):
        template = BaseStaminaTask.DESC_STAMINA_TARGET_FMT
        for cost in (40, 60):
            with self.subTest(cost=cost):
                app = FakeApp({template: "Each reward costs {cost} stamina.\nDaily target."})

                translated = _translate_key(app, template.format(cost=cost), FakeApp.original_tr)

                self.assertEqual(translated, f"Each reward costs {cost} stamina.\nDaily target.")
                self.assertEqual(app.to_translate, {template})

    def test_unregistered_text_uses_original_translation_behavior(self):
        app = FakeApp({"普通: 5": "Regular text"})

        self.assertEqual(_translate_key(app, "普通: 5", FakeApp.original_tr), "Regular text")
        self.assertEqual(app.to_translate, {"普通: 5"})
