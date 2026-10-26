import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from ok.test.TaskTestCase import TaskTestCase
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.char.custom.CustomCharDb import CustomCharDb
from src.char.custom.CustomCharManager import CustomCharManager
from src.config import config
from src.tasks.DebugCharTask import DebugCharTask, TeamScanResult
from src.ui.CharManagerTab import CharManagerTab
from src.ui.TeamManagerTab import TeamManagerTab


class TestCustomChar(TaskTestCase):
    task_class = DebugCharTask
    config = config

    @staticmethod
    def _character_id_by_name(manager, char_name):
        return next(
            char_id
            for char_id, info in manager.get_all_characters().items()
            if info["char_name"] == char_name
        )

    @classmethod
    def setUpClass(cls):
        # This suite instantiates QWidget-based management tabs. Keep its Qt
        # application lifecycle explicit instead of relying on the task-test
        # runtime to create a GUI application as a side effect.
        cls._qt_app = QApplication.instance() or QApplication([])
        super().setUpClass()

    def test_scan_team(self):
        self.set_image("tests/images/02.png")
        self.task.scan_team()
        self.task._scan_team()

        self.assertEqual(self.task.result_error, "")
        self.assertEqual(len(self.task.scan_results), 4)
        self.assertIsInstance(self.task.scan_results[0], TeamScanResult)
        self.assertGreater(self.task.scan_results[0].width, 0)
        self.assertGreater(self.task.scan_results[0].height, 0)

    def test_character_tool_modes_keep_ui_input_on_the_task(self):
        self.task.scan_results = (object(),)
        self.task.result_error = "stale"

        self.task.scan_team()

        self.assertEqual(self.task.mode, DebugCharTask.MODE_SCAN_TEAM)
        self.assertEqual(self.task.scan_results, ())
        self.assertEqual(self.task.result_error, "")

        self.task.test_combo("custom:test", "combo:test", "skill")

        self.assertEqual(self.task.mode, DebugCharTask.MODE_TEST_COMBO)
        self.assertEqual(self.task._combo_character_id, "custom:test")
        self.assertEqual(self.task._combo_implementation_id, "combo:test")
        self.assertEqual(self.task._combo_text, "skill")

    def setUp(self):
        super().setUp()

        # 建立隔離的沙盒資料夾
        self.temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(self.temp_dir, "db.json")
        features_dir = os.path.join(self.temp_dir, "features")
        external_chars_dir = os.path.join(self.temp_dir, "external_chars")
        os.makedirs(features_dir, exist_ok=True)

        # 封裝所有的路徑修改 Patch 以免感染到專案環境
        self.patchers = [
            patch("src.char.custom.CustomCharManager.CUSTOM_CHARS_DIR", self.temp_dir),
            patch("src.char.custom.CustomCharManager.DB_PATH", db_path),
            patch("src.char.custom.CustomCharManager.FEATURES_DIR", features_dir),
            patch("src.char.custom.CustomCharManager.EXTERNAL_CHARS_DIR", external_chars_dir),
        ]
        for p in self.patchers:
            p.start()

        # 放個空的 DB 外殼給他
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump({"combos": {}, "characters": {}, "features": {}}, f)

        # 破壞單例快取，強迫 CustomCharManager 以沙盒的 Path 初始化
        CustomCharManager._instance = None
        CustomCharDb.reset_instance()
        self.manager = CustomCharManager()

    def tearDown(self):
        super().tearDown()

        # 停止所有路徑攔截
        for p in self.patchers:
            p.stop()

        # 刪除沙盒環境中的圖片與 DB
        shutil.rmtree(self.temp_dir, ignore_errors=True)

        # 拔除單例快取，這確保開發中或測試結束後
        # 原本環境要讀 CustomCharManager 都能載入正式的 custom_chars
        CustomCharManager._instance = None
        CustomCharDb.reset_instance()

    def test_manager_crud(self):
        """測試 CustomCharManager 基本存取功能與特徵匹配"""
        self.set_image("tests/images/03.png")

        # 新增 Combo 控制串
        combo_id = self.manager.add_combo("combo_test", "skill, jump")
        self.assertTrue(combo_id.startswith("combo_"))
        self.assertEqual(self.manager.get_combo(combo_id), "skill, jump")

        # 新增與連結 Character
        char_id = self.manager.create_character("char1", combo_id)
        self.assertIn(char_id, self.manager.get_all_characters())
        char_info = self.manager.get_character_info_by_id(char_id)
        assert char_info is not None
        self.assertEqual(char_info["impl_id"], combo_id)
        self.assertEqual(self.manager.get_impl_name(char_info["impl_id"]), "combo_test")

        # 刪除 Combo 檢查
        self.manager.delete_combo(combo_id)
        self.assertEqual(self.manager.get_combo(combo_id), "")

        # 模擬截圖特徵值的加入
        rng = np.random.default_rng(seed=42)
        fake_mat = rng.integers(0, 256, (10, 10, 3), dtype=np.uint8)
        fid = self.manager.add_feature_to_character(
            char_id, fake_mat, self.task.width, self.task.height
        )
        char_info_features = self.manager.get_character_info_by_id(char_id)
        assert char_info_features is not None
        self.assertIn(fid, char_info_features["feature_ids"])

        # 測試特徵匹配邏輯 match_feature
        # 目前特徵庫內有一張假圖，如果餵入一模一樣的黑圖，應該回報 True
        is_match, match_char, similarity = self.manager.match_feature(
            self.task, fake_mat, threshold=0.99
        )
        self.assertTrue(is_match, f"match_char: {match_char}, similarity: {similarity}")
        self.assertEqual(match_char, char_id)

    def test_char_manager_tab_ui(self):
        """測試 CharManagerTab 角色管理 UI 行為與資料聯動"""
        tab = CharManagerTab()
        # 置換其內部的 manager 以使用我們乾淨的測試實體
        tab.manager = self.manager

        # 準備假資料
        combo_id = self.manager.add_combo("combo_ui", "skill, wait(1)")
        char_ui_id = self.manager.create_character("char_ui_1", combo_id)
        self.manager.create_character("char_ui_2", "")

        # 測試列表刷新
        tab.refresh_list()
        self.assertEqual(tab.char_list_widget.count(), 2)

        # 模擬 UI 點擊選擇 "char_ui_1"
        item = tab.char_list_widget.item(0)
        if item.text() != "char_ui_1":
            item = tab.char_list_widget.item(1)
        tab.on_char_selected(item)

        # 預期：右側標題變為 char_ui_1，且 combo 等級顯示為 combo_ui
        self.assertEqual(tab.char_title.text(), "char_ui_1")
        self.assertEqual(tab.combo_select.currentText(), "combo_ui")
        self.assertEqual(tab.combo_text.toPlainText(), "skill, wait(1)")

        # 測試介面的「解綁」功能 (on_unbind_combo)
        tab.on_unbind_combo()
        char_ui_info = self.manager.get_character_info_by_id(char_ui_id)
        assert char_ui_info is not None
        self.assertEqual(char_ui_info["impl_id"], "")
        # 解綁後，介面會刷新，combo_text 應顯示未綁定的提示文字
        self.assertEqual(tab.combo_text.toPlainText(), tab.tr_unbound_text)

    def test_char_manager_tab_combo_selection_updates_content_with_stale_index(self):
        """切换出招表时，即使文本信号先于索引更新，也应显示新出招表内容。"""
        tab = CharManagerTab()
        tab.manager = self.manager

        combo_a_id = self.manager.add_combo("combo_a", "skill")
        self.manager.add_combo("combo_b", "ultimate")
        self.manager.create_character("char_ui_1", combo_a_id)

        tab.refresh_list()
        tab.combo_select.setCurrentIndex(tab.combo_select.findData(combo_a_id))

        tab.on_combo_changed("combo_b")

        self.assertEqual(tab.combo_text.toPlainText(), "ultimate")

    def test_team_manager_tab_ui(self):
        """測試 TeamManagerTab 掃描結束後的 UI 狀態變更邏輯與 SlotCard 確認"""
        tab = TeamManagerTab(manager=self.manager)

        # 準備假資料
        combo_id = self.manager.add_combo("combo_scanner", "skill")
        scan_char_id = self.manager.create_character("scan_char_1", combo_id)

        # 模擬 on_scan_done 發送了掃描成功結果
        fake_mat = np.zeros((10, 10, 3), dtype=np.uint8)
        mock_results = (
            TeamScanResult(0, fake_mat, 1920, 1080, scan_char_id, 1.0),
            # index 1 掃描到但未匹配角色字串
            TeamScanResult(1, fake_mat, 1920, 1080, "", None),
        )

        tab.on_scan_done(mock_results)

        # 槽位 0: 應該顯示匹配成功
        self.assertIn("scan_char_1", tab.slots[0].status.text())
        self.assertFalse(tab.slots[0].btn_act.isHidden())
        self.assertFalse(tab.slots[0].btn_relink.isHidden())

        # 槽位 1: 應該顯示未匹配，並出現可關聯的按鈕
        self.assertEqual(tab.slots[1].status.text(), tab.slots[1].tr_unrecognized)
        self.assertFalse(tab.slots[1].btn_act.isHidden())
        self.assertTrue(tab.slots[1].btn_relink.isHidden())

        # 槽位 2: 未收到掃描結果，應被清空並寫著無畫面
        self.assertEqual(tab.slots[2].status.text(), tab.tr_no_feature)
        self.assertTrue(tab.slots[2].btn_relink.isHidden())

    def test_team_manager_tab_relink_when_mismatched(self):
        tab = TeamManagerTab(manager=self.manager)
        combo_id = self.manager.add_combo("combo_test", "skill")
        wrong_char_id = self.manager.create_character("wrong_char", combo_id)

        fake_mat = np.zeros((10, 10, 3), dtype=np.uint8)
        slot = tab.slots[0]
        slot.update_result(fake_mat, 1920, 1080, wrong_char_id, 0.64)

        # 匹配成功但可能是误判, 应该显示加入特征按钮与不是该角色链接
        self.assertFalse(slot.btn_act.isHidden())
        self.assertFalse(slot.btn_relink.isHidden())

        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.get_data.return_value = ("correct_char", "", "", "")

        with patch("src.ui.TeamManagerTab.NewCharDialog", return_value=dialog):
            slot.on_relink()

        self.assertNotEqual(slot.current_match_char_id, wrong_char_id)
        linked_info = self.manager.get_character_info_by_id(slot.current_match_char_id)
        assert linked_info is not None
        self.assertEqual(linked_info["char_name"], "correct_char")
        self.assertEqual(slot.current_confidence, 1.0)
        self.assertFalse(slot.btn_act.isEnabled())
        self.assertTrue(slot.btn_relink.isHidden())

    def test_team_manager_tab_disables_add_feature_after_first_link(self):
        tab = TeamManagerTab(manager=self.manager)
        fake_mat = np.zeros((10, 10, 3), dtype=np.uint8)
        slot = tab.slots[0]
        slot.update_result(fake_mat, 1920, 1080, None)

        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.get_data.return_value = ("linked_char", "", "", "")

        with patch("src.ui.TeamManagerTab.NewCharDialog", return_value=dialog):
            slot.on_action()

        self.assertNotEqual(slot.current_match_char_id, "")
        linked_info = self.manager.get_character_info_by_id(slot.current_match_char_id)
        assert linked_info is not None
        self.assertEqual(linked_info["char_name"], "linked_char")
        self.assertEqual(slot.current_confidence, 1.0)
        self.assertIn(slot.tr_confidence.format(1.0), slot.status.text())
        self.assertFalse(slot.btn_act.isEnabled())

    def test_team_manager_command_bar_adds_character(self):
        tab = TeamManagerTab(manager=self.manager)
        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.get_data.return_value = ("command_bar_char", "", "", "")

        with patch("src.ui.TeamManagerTab.AddCharacterDialog", return_value=dialog):
            tab.on_add_character()

        char_id = self._character_id_by_name(self.manager, "command_bar_char")
        self.assertTrue(char_id)
        self.assertTrue(tab.fixed_action.isCheckable())

    def test_team_manager_presets_apply_and_fixed_use(self):
        tab = TeamManagerTab(manager=self.manager)
        combo_a = self.manager.add_combo("combo_preset_a", "skill")
        combo_b = self.manager.add_combo("combo_preset_b", "ultimate")
        char_id = self.manager.create_character("preset_char", combo_a)
        tab.reload_preset_options()

        with patch("src.ui.TeamManagerTab.NameInputDialog.exec", return_value=0):
            tab.on_create_preset()
        preset_id = tab.current_preset_id
        self.assertIsNotNone(preset_id)
        tab.preset_rows[0].set_data(char_id, combo_b)
        tab.on_preset_slot_changed(0)

        preset = next(
            preset for preset in self.manager.get_team_presets() if preset["id"] == preset_id
        )
        self.assertEqual(preset["slots"][0], {"char_id": char_id, "impl_id": combo_b})
        tab.on_apply_preset()
        self.assertEqual(self.manager.get_character_info_by_id(char_id)["impl_id"], combo_b)

        tab.on_toggle_fixed_preset()
        self.assertTrue(self.manager.get_fixed_team()["enabled"])
        self.assertEqual(self.manager.get_fixed_team()["slots"][0]["char_id"], char_id)
        tab.on_toggle_fixed_preset()
        self.assertFalse(self.manager.get_fixed_team()["enabled"])

    def test_team_manager_preset_search_keeps_the_selected_preset_visible(self):
        tab = TeamManagerTab(manager=self.manager)
        first = self.manager.create_team_preset("alpha")
        second = self.manager.create_team_preset("beta")
        tab.reload_presets(first["id"])

        tab.preset_list.search_edit.setText("beta")

        self.assertEqual(tab.current_preset_id, second["id"])
        self.assertEqual(tab.preset_list.currentItem().data(Qt.ItemDataRole.UserRole), second["id"])
        self.assertFalse(tab.preset_list.currentItem().isHidden())

    def test_team_manager_can_delete_preset_with_its_external_code(self):
        tab = TeamManagerTab(manager=self.manager)
        preset = self.manager.create_team_preset("external team")
        preset["slots"][0]["impl_id"] = "external:team/hero"
        preset["slots"][1]["impl_id"] = "external:team/hero"
        tab.current_preset_id = preset["id"]
        dialog = MagicMock()
        dialog.exec.return_value = True

        with (
            patch.object(tab, "_current_preset", return_value=preset),
            patch("src.ui.TeamManagerTab.MessageBox", return_value=dialog),
            patch.object(
                self.manager, "delete_external_impl_and_references", return_value=True
            ) as delete_external,
        ):
            tab.on_delete_preset()

        delete_external.assert_called_once_with("external:team/hero")
        self.assertFalse(
            any(item["id"] == preset["id"] for item in self.manager.get_team_presets())
        )

    def test_team_manager_can_keep_external_code_when_deleting_preset(self):
        tab = TeamManagerTab(manager=self.manager)
        preset = self.manager.create_team_preset("external team")
        preset["slots"][0]["impl_id"] = "external:team/hero"
        tab.current_preset_id = preset["id"]
        dialog = MagicMock()
        dialog.exec.return_value = False

        with (
            patch.object(tab, "_current_preset", return_value=preset),
            patch("src.ui.TeamManagerTab.MessageBox", return_value=dialog),
            patch.object(self.manager, "delete_external_impl_and_references") as delete_external,
        ):
            tab.on_delete_preset()

        delete_external.assert_not_called()
        self.assertFalse(
            any(item["id"] == preset["id"] for item in self.manager.get_team_presets())
        )

    def test_team_manager_preset_slot_keeps_implementation_when_character_is_cleared(self):
        tab = TeamManagerTab(manager=self.manager)
        combo_id = self.manager.add_combo("combo_auto_select", "skill")
        char_id = self.manager.create_character("auto_select_char", combo_id)
        tab.reload_preset_options()
        with patch("src.ui.TeamManagerTab.NameInputDialog.exec", return_value=0):
            tab.on_create_preset()
        row = tab.preset_rows[0]

        row.char_combo.setCurrentIndex(row.char_combo.findData(char_id))

        self.assertEqual(row.get_data(), (char_id, combo_id))

        row.char_combo.setCurrentIndex(0)

        self.assertEqual(row.get_data(), ("", combo_id))
        self.assertEqual(row.combo_list.currentIndex(), row.combo_list.findData(combo_id))

        other_combo_id = self.manager.add_combo("combo_other", "ultimate")
        other_char_id = self.manager.create_character("other_char", other_combo_id)
        tab.reload_preset_options()
        row.char_combo.setCurrentIndex(row.char_combo.findData(other_char_id))

        self.assertEqual(row.get_data(), (other_char_id, combo_id))

    def test_team_manager_preset_slot_allows_an_implementation_without_a_character(self):
        tab = TeamManagerTab(manager=self.manager)
        combo_id = self.manager.add_combo("combo_direct", "skill")
        tab.reload_preset_options()
        with patch("src.ui.TeamManagerTab.NameInputDialog.exec", return_value=0):
            tab.on_create_preset()

        row = tab.preset_rows[0]
        row.combo_list.setCurrentIndex(row.combo_list.findData(combo_id))

        self.assertEqual(row.get_data(), ("", combo_id))
        preset = next(
            preset
            for preset in self.manager.get_team_presets()
            if preset["id"] == tab.current_preset_id
        )
        self.assertEqual(preset["slots"][0], {"char_id": "", "impl_id": combo_id})

        default_combo_id = self.manager.add_combo("combo_default", "ultimate")
        char_id = self.manager.create_character("char_with_default", default_combo_id)
        tab.reload_preset_options()
        row.char_combo.setCurrentIndex(row.char_combo.findData(char_id))

        self.assertEqual(row.get_data(), (char_id, combo_id))
        preset = next(
            preset
            for preset in self.manager.get_team_presets()
            if preset["id"] == tab.current_preset_id
        )
        self.assertEqual(preset["slots"][0], {"char_id": char_id, "impl_id": combo_id})

    def test_team_manager_fills_only_empty_preset_slots(self):
        tab = TeamManagerTab(manager=self.manager)
        combo_id = self.manager.add_combo("combo_scan_fill", "skill")
        first_id = self.manager.create_character("first", combo_id)
        second_id = self.manager.create_character("second", combo_id)
        tab.reload_preset_options()
        with patch("src.ui.TeamManagerTab.NameInputDialog.exec", return_value=0):
            tab.on_create_preset()
        tab.preset_rows[0].set_data(first_id, combo_id)
        tab.on_preset_slot_changed(0)
        tab.last_scan_results = [
            TeamScanResult(0, None, 0, 0, second_id, 1.0),
            TeamScanResult(1, None, 0, 0, second_id, 1.0),
        ]

        tab.on_fill_from_scan()

        preset = next(
            preset
            for preset in self.manager.get_team_presets()
            if preset["id"] == tab.current_preset_id
        )
        self.assertEqual(preset["slots"][0]["char_id"], first_id)
        self.assertEqual(preset["slots"][1]["char_id"], second_id)
        self.assertEqual(preset["slots"][1]["impl_id"], combo_id)

    @patch("requests.post")
    def test_google_translate_text_uses_post_request(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = [[["Translated text", "Source text", None, None]]]
        mock_post.return_value = mock_response

        res = CharManagerTab._google_translate_text("Source text", "en-US")
        self.assertEqual(res, "Translated text")
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertIn("data", kwargs)
        self.assertEqual(kwargs["data"]["q"], "Source text")
        self.assertEqual(kwargs["data"]["tl"], "en-US")

    def test_char_manager_and_hub_navigate_to_workshop(self):
        from src.ui.CharHubTab import CharHubTab

        hub = CharHubTab(manager=self.manager)
        with patch.object(hub.team_manager_tab, "on_open_workshop") as mock_open_workshop:
            hub.char_manager_tab.workshop_btn.click()
            self.assertEqual(hub.stacked_widget.currentWidget(), hub.team_manager_tab)
            self.assertEqual(hub.team_manager_tab.command_stack_layout.currentIndex(), 1)
            mock_open_workshop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
