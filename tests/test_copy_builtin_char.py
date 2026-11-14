import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from ok.ui.qt.kit import Dialog
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from src.char.core.CharRegistry import CharRegistry
from src.char.custom.CustomCharDb import CustomCharDb
from src.char.custom.CustomCharManager import CustomCharManager
from src.ui.CharHubTab import CharHubTab
from src.ui.CharManagerTab import CharManagerTab


class CopyBuiltinCharTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.external_chars_dir = root / "external_chars"
        self.registry = CharRegistry(external_dir=self.external_chars_dir)
        app = SimpleNamespace(
            debug=False,
            locale=SimpleNamespace(name=lambda: "zh_CN"),
            tr=lambda text: text,
        )
        self.patchers = [
            patch("src.char.custom.CustomCharManager.CUSTOM_CHARS_DIR", str(root)),
            patch("src.char.custom.CustomCharManager.FEATURES_DIR", str(root / "features")),
            patch("src.char.custom.CustomCharManager.DB_PATH", str(root / "db.json")),
            patch(
                "src.char.custom.CustomCharManager.EXTERNAL_CHARS_DIR",
                str(self.external_chars_dir),
            ),
            patch("src.char.core.CharRegistry.char_registry", self.registry),
            patch("src.ui.CharManagerTab.og.app", app),
            patch("src.ui.CharManagerTab.char_registry", self.registry),
        ]
        for patcher in self.patchers:
            patcher.start()

        CustomCharManager._instance = None
        CustomCharDb.reset_instance()
        self.manager = CustomCharManager()
        self.test_directory = "copy_tests"
        self.test_filename = "test_copy_builtin_shinku"
        self.test_target_file = (
            self.external_chars_dir / self.test_directory / f"{self.test_filename}.py"
        )

    def tearDown(self):
        CustomCharManager._instance = None
        CustomCharDb.reset_instance()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_get_builtin_impl_source(self):
        # Shinku builtin character
        source = self.manager.get_builtin_impl_source("builtin:shinku")
        self.assertTrue(bool(source))
        self.assertIn("class Shinku(BaseChar):", source)
        self.assertIn('cn_name = "真红"', source)

    def test_copy_builtin_to_external_and_delete(self):
        # 1. Copy builtin:shinku to external
        success, new_impl_id, err = self.manager.copy_builtin_to_external(
            "builtin:shinku",
            self.test_directory,
            self.test_filename,
        )
        self.assertTrue(success, f"Copy failed with error: {err}")
        self.assertEqual(new_impl_id, f"external:{self.test_directory}/{self.test_filename}")
        self.assertTrue(self.test_target_file.exists())

        # 2. Check the copied source content
        copied_source = self.manager.get_external_impl_source(new_impl_id)
        self.assertIn('cn_name = "真红"', copied_source)

        # 3. Check registered entry in CharRegistry
        entry = self.registry.get(new_impl_id)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "external")
        self.assertEqual(entry.cn_name, "真红")

        # 4. Clean up via delete_external_impl
        deleted = self.manager.delete_external_impl(new_impl_id)
        self.assertTrue(deleted)
        self.assertFalse(self.test_target_file.exists())
        self.assertIsNone(self.registry.get(new_impl_id))

    def test_copy_builtin_uses_directory_and_filename_extension(self):
        success, new_impl_id, err = self.manager.copy_builtin_to_external(
            "builtin:shinku",
            "测试目录",
            "shinku_copy.py",
        )

        self.assertTrue(success, f"Copy failed with error: {err}")
        self.assertEqual(new_impl_id, "external:测试目录/shinku_copy")
        self.assertTrue((self.external_chars_dir / "测试目录" / "shinku_copy.py").exists())
        self.assertEqual(self.registry.get(new_impl_id).cn_name, "真红")

    def test_copy_builtin_rejects_hidden_or_uncreatable_directory(self):
        success, _, err = self.manager.copy_builtin_to_external(
            "builtin:shinku",
            "_hidden",
            self.test_filename,
        )
        self.assertFalse(success)
        self.assertIn("underscore", err)

        with (
            patch("src.char.custom.CustomCharManager.logger.error"),
            patch("pathlib.Path.mkdir", side_effect=OSError("denied")),
        ):
            success, _, err = self.manager.copy_builtin_to_external(
                "builtin:shinku",
                self.test_directory,
                self.test_filename,
            )
        self.assertFalse(success)
        self.assertIn("denied", err)

    def test_update_external_source_restores_previous_code_when_reload_fails(self):
        success, new_impl_id, err = self.manager.copy_builtin_to_external(
            "builtin:shinku",
            self.test_directory,
            self.test_filename,
        )
        self.assertTrue(success, f"Copy failed with error: {err}")
        previous_source = self.manager.get_external_impl_source(new_impl_id)

        success, err = self.manager.update_external_impl_source(
            new_impl_id,
            "class InvalidExternalCharacter:\n    pass\n",
        )

        self.assertFalse(success)
        self.assertTrue(err)
        self.assertEqual(self.manager.get_external_impl_source(new_impl_id), previous_source)
        self.assertIsNotNone(self.registry.get(new_impl_id))

    def test_main_editor_previews_builtin_and_edits_external_source(self):
        success, new_impl_id, err = self.manager.copy_builtin_to_external(
            "builtin:shinku",
            self.test_directory,
            self.test_filename,
        )
        self.assertTrue(success, f"Copy failed with error: {err}")

        tab = CharManagerTab()
        self.addCleanup(tab.deleteLater)

        tab.on_combo_changed("[内置代码] 真红", "builtin:shinku")
        self.assertTrue(tab.combo_text.isReadOnly())
        self.assertFalse(tab.ask_ai_btn.isEnabled())
        self.assertIn("class Shinku(BaseChar):", tab.combo_text.toPlainText())

        tab.on_combo_changed("[外置代码] copy_tests - 真红", new_impl_id)
        self.assertFalse(tab.combo_text.isReadOnly())
        self.assertTrue(tab.ask_ai_btn.isEnabled())
        self.assertIn('cn_name = "真红"', tab.combo_text.toPlainText())

        tab._set_combo_selection_by_id(new_impl_id)
        with patch("src.ui.CharManagerTab.show_info_bar"):
            tab.on_ask_ai()
        prompt = self.qt_app.clipboard().text()
        self.assertIn("```python", prompt)
        self.assertIn("class Shinku(BaseChar):", prompt)
        self.assertIn("请修改上面完整的 Shinku 角色自动化代码。", prompt)

        tab.combo_text.setPlainText(f"{tab.combo_text.toPlainText()}\n# unsaved")
        with patch("src.ui.CharManagerTab.show_bar") as warning:
            tab.on_test_combo()
        warning.assert_called_once()

    def test_closing_data_manager_releases_its_dialog_each_time(self):
        tab = CharManagerTab(manager=self.manager)
        self.addCleanup(tab.deleteLater)
        self.addCleanup(tab.dispose)
        tab.show()

        def reject_open_dialog():
            for dialog in tab.findChildren(Dialog):
                if dialog.isVisible():
                    dialog.reject()

        for _ in range(2):
            QTimer.singleShot(0, reject_open_dialog)
            tab.show_data_manager()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertEqual(tab.findChildren(Dialog), [])

    def test_character_panel_clears_selection_and_keeps_feature_card_on_unbind(self):
        combo_id = self.manager.add_combo("Example combo", "wait(1)")
        char_id = self.manager.create_character("Example character", combo_id)
        image = np.zeros((40, 32, 3), dtype=np.uint8)
        self.manager.add_feature_to_character(char_id, image, width=32, height=40)
        tab = CharManagerTab(manager=self.manager)
        self.addCleanup(tab.deleteLater)
        self.addCleanup(tab.dispose)
        items = tab.char_list_widget.list_widget
        items.setCurrentRow(0)
        self.assertEqual(tab.combo_text.toPlainText(), "wait(1)")
        card = tab.feature_grid.itemAt(0).widget()

        with patch("src.ui.CharManagerTab.show_info_bar"):
            tab.on_unbind_combo()
        self.assertEqual(tab.combo_select.currentText(), "")
        self.assertIs(tab.feature_grid.itemAt(0).widget(), card)

        self.manager.update_character(char_id, impl_id=combo_id)
        tab.refresh_list()
        self.assertEqual(tab.current_char_id, char_id)
        self.assertEqual(tab.combo_text.toPlainText(), "wait(1)")
        items.setCurrentItem(None)
        self.assertIsNone(tab.current_char_id)
        self.assertEqual(tab.char_title.text(), tab.tr_choose_char)
        self.assertEqual(tab.combo_text.toPlainText(), tab.tr_unbound_text)
        self.assertEqual(tab.feature_grid.count(), 0)
        self.assertFalse(tab.delete_char_btn.isEnabled())

    def test_long_imported_names_keep_team_controls_inside_the_viewport(self):
        self.manager.create_team_preset("Team")
        combo_id = self.manager.add_combo("Long imported combo " * 6, "skill")
        char_id = self.manager.create_character("Long imported character " * 6, combo_id)
        hub = CharHubTab(manager=self.manager)
        self.addCleanup(hub.deleteLater)
        self.addCleanup(hub.dispose)
        hub.resize(992, 800)
        hub.show()
        hub.pivot.set_current("TeamManagerTab")
        team = hub.team_manager_tab
        team.command_stack_layout.setCurrentIndex(1)
        team.preset_rows[0].set_data(char_id, combo_id)
        self.qt_app.processEvents()

        self.assertEqual(team.preset_rows[0].get_data(), (char_id, combo_id))
        self.assertEqual(team.view.width(), team.viewport().width())
        self.assertLess(team.scan_btn.geometry().right(), team.viewport().width())
        self.assertLess(team.slots[3].geometry().right(), team.viewport().width())


if __name__ == "__main__":
    unittest.main()
