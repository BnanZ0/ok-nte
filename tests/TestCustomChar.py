import unittest
from unittest.mock import MagicMock, patch
import json

import numpy as np
from ok.test.TaskTestCase import TaskTestCase

from src.char.custom.CustomChar import CustomChar
from src.char.custom.BuiltinComboRegistry import BuiltinComboRegistry
from src.char.custom.CustomCharManager import CustomCharManager
from src.config import config
from src.tasks.trigger.AutoCombatTask import AutoCombatTask
from src.ui.CharManagerTab import CharManagerTab
from src.ui.TeamManagerTab import TeamManagerTab

PREDEFINED_CHARACTER_REF = "builtin:char_zero"

class TestCustomChar(TaskTestCase):
    task_class = AutoCombatTask
    config = config

    def test_scan_team(self):
        from src.tasks.trigger.AutoCombatTask import scanner_signals

        self.set_image('tests/images/02.png')

        # 建立 Mock 物件來捕捉信號參數
        mock_handler = MagicMock()
        # 連結至實際發射出的 scan_done 信號
        scanner_signals.scan_done.connect(mock_handler)
        
        try:
            # 執行真正的掃描 (會運用到 OCR 和 CV)
            self.task.scan_team()
            
            # 確認信號被成功發送了一次
            mock_handler.assert_called_once()
            
            # 獲取信號被發送時的第一個 Argument (即 results)
            results = mock_handler.call_args[0][0]
            
            # 驗證傳出的報告結構
            self.assertIsInstance(results, list)
            # 因為 02.png 有隊伍，只要解析沒出錯通常 results 的長度會大於 0
            if len(results) > 0:
                self.assertIn("index", results[0])
                self.assertIn("mat", results[0])
                self.assertIn("width", results[0])
                self.assertIn("match", results[0])
            self.assertEqual(len(results), 4)
        finally:
            # 測試完畢切斷連結以避免影響其他測試
            scanner_signals.scan_done.disconnect(mock_handler)


    def setUp(self):
        super().setUp()
        import tempfile
        import os
        from unittest.mock import patch

        self.set_image('tests/images/03.png')

        # 建立隔離的沙盒資料夾
        self.temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(self.temp_dir, "db.json")
        features_dir = os.path.join(self.temp_dir, "features")
        os.makedirs(features_dir, exist_ok=True)
        
        # 封裝所有的路徑修改 Patch 以免感染到專案環境
        self.patchers = [
            patch('src.char.custom.CustomCharManager.CUSTOM_CHARS_DIR', self.temp_dir),
            patch('src.char.custom.CustomCharManager.DB_PATH', db_path),
            patch('src.char.custom.CustomCharManager.FEATURES_DIR', features_dir),
        ]
        for p in self.patchers:
            p.start()

        # 放個空的 DB 外殼給他
        import json
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump({"combos": {}, "characters": {}, "features": {}}, f)
            
        # 破壞單例快取，強迫 CustomCharManager 以沙盒的 Path 初始化
        CustomCharManager._instance = None
        self.manager = CustomCharManager()

    def tearDown(self):
        super().tearDown()
        import shutil
        
        # 停止所有路徑攔截
        for p in self.patchers:
            p.stop()
            
        # 刪除沙盒環境中的圖片與 DB
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        
        # 拔除單例快取，這確保開發中或測試結束後，原本環境要讀 CustomCharManager 都能載入正式的 custom_chars
        CustomCharManager._instance = None

    def test_manager_crud(self):
        """測試 CustomCharManager 基本存取功能與特徵匹配"""
        # 新增 Combo 控制串
        self.manager.add_combo("combo_test", "skill, jump")
        self.assertEqual(self.manager.get_combo("combo_test"), "skill, jump")
        
        # 新增與連結 Character
        self.manager.add_character("char1", "combo_test")
        self.assertIn("char1", self.manager.get_all_characters())
        char_info = self.manager.get_character_info("char1")
        assert char_info is not None
        self.assertEqual(char_info["combo_ref"], "combo_test")
        
        # 刪除 Combo 檢查
        self.manager.delete_combo("combo_test")
        self.assertEqual(self.manager.get_combo("combo_test"), "")

        # 模擬截圖特徵值的加入
        np.random.seed(42)
        fake_mat = np.random.randint(0, 256, (10, 10, 3), dtype=np.uint8)
        fid = self.manager.add_feature_to_character("char1", fake_mat, self.task.width, self.task.height)
        char_info_features = self.manager.get_character_info("char1")
        assert char_info_features is not None
        self.assertIn(fid, char_info_features["feature_ids"])

        # 測試特徵匹配邏輯 match_feature
        # 目前特徵庫內有一張假圖，如果餵入一模一樣的黑圖，應該回報 True
        is_match, match_char, similarity = self.manager.match_feature(self.task, fake_mat, threshold=0.99)
        self.assertTrue(is_match, f"match_char: {match_char}, similarity: {similarity}")
        self.assertEqual(match_char, "char1")

    def test_combo_compile(self):
        """測試 CustomChar 透過 AST 語法樹將字串解析為獨立指令的容錯與精準度"""
        self.manager.add_combo("combo_ast", "skill, l_click(), l_hold(1.5), walk(w, 2), wait(0.5)")
        self.manager.add_character("test_ast_hero", "combo_ast")
        
        # 初始化 CustomChar
        char = CustomChar(task=self.task, index=0, char_name="test_ast_hero")
        self.assertTrue(len(char.parsed_combo) > 0)
        
        # 1. 無括號無參數的指令: skill
        self.assertEqual(char.parsed_combo[0][0], "skill")
        self.assertEqual(char.parsed_combo[0][2], [])
        
        # 2. 帶有浮點數參數的指令: l_hold(1.5)
        self.assertEqual(char.parsed_combo[2][0], "l_hold")
        self.assertEqual(char.parsed_combo[2][2], [1.5])
        
        # 3. 帶有裸寫字串(不用引號)與數值的混合參數: walk(w, 2)
        self.assertEqual(char.parsed_combo[3][0], "walk")
        self.assertEqual(char.parsed_combo[3][2], ["w", 2])

    def test_char_manager_tab_ui(self):
        """測試 CharManagerTab 角色管理 UI 行為與資料聯動"""
        tab = CharManagerTab()
        # 置換其內部的 manager 以使用我們乾淨的測試實體
        tab.manager = self.manager
        
        # 準備假資料
        self.manager.add_combo("combo_ui", "skill, wait(1)")
        self.manager.add_character("char_ui_1", "combo_ui")
        self.manager.add_character("char_ui_2", "")
        
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
        char_ui_info = self.manager.get_character_info("char_ui_1")
        assert char_ui_info is not None
        self.assertEqual(char_ui_info["combo_ref"], "")
        # 解綁後，介面會刷新，combo_text 應顯示未綁定的提示文字
        self.assertEqual(tab.combo_text.toPlainText(), tab.tr_unbound_text)

    def test_team_manager_tab_ui(self):
        """測試 TeamManagerTab 掃描結束後的 UI 狀態變更邏輯與 SlotCard 確認"""
        tab = TeamManagerTab(manager=self.manager)
        
        # 準備假資料
        self.manager.add_combo("combo_scanner", "skill")
        self.manager.add_character("scan_char_1", "combo_scanner")
        
        # 模擬 on_scan_done 發送了掃描成功結果
        fake_mat = np.zeros((10, 10, 3), dtype=np.uint8)
        mock_results = [
            {"index": 0, "mat": fake_mat, "width": 1920, "height": 1080, "match": "scan_char_1"},
            # index 1 掃描到但未匹配角色字串
            {"index": 1, "mat": fake_mat, "width": 1920, "height": 1080, "match": None}
        ]
        
        tab.on_scan_done(mock_results)
        
        # 分析 Slot UI 變化
        # 槽位 0: 應該顯示匹配成功
        self.assertIn("scan_char_1", tab.slots[0].status.text())
        self.assertTrue(tab.slots[0].btn_act.isHidden())
        
        # 槽位 1: 應該顯示未匹配，並出現可關聯的按鈕
        self.assertEqual(tab.slots[1].status.text(), tab.slots[1].tr_unrecognized)
        self.assertFalse(tab.slots[1].btn_act.isHidden())

        # 槽位 2: 未收到掃描結果，應被清空並寫著無畫面
        self.assertEqual(tab.slots[2].status.text(), tab.tr_no_feature)

    def test_builtin_combo_roundtrip(self):
        builtin_ref = PREDEFINED_CHARACTER_REF
        builtin_label = self.manager.to_combo_label(builtin_ref)

        self.assertTrue(self.manager.is_builtin_combo(builtin_ref))
        self.assertEqual(self.manager.to_combo_ref(builtin_label), builtin_ref)

        self.manager.add_character("char_builtin", builtin_label)
        char_info = self.manager.get_character_info("char_builtin")
        assert char_info is not None
        self.assertEqual(char_info["combo_ref"], builtin_ref)

        combos = self.manager.get_all_combos()
        self.assertIn(builtin_label, combos)
        self.assertNotIn(builtin_ref, combos)

        combo_items = self.manager.get_all_combo_items()
        self.assertIn((builtin_label, builtin_ref), combo_items)

    def test_migrate_legacy_builtin_combo_name(self):
        import src.char.custom.CustomCharManager as manager_module

        legacy_label = self.manager.to_combo_label(PREDEFINED_CHARACTER_REF)
        with open(manager_module.DB_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "schema_version": 3,
                    "combos": {},
                    "characters": {
                        "legacy_char": {
                            "combo_name": legacy_label,
                            "feature_ids": []
                        }
                    },
                    "features": {}
                },
                f,
                ensure_ascii=False,
                indent=2
            )

        CustomCharManager._instance = None
        migrated_manager = CustomCharManager()
        migrated_info = migrated_manager.get_character_info("legacy_char")
        assert migrated_info is not None
        self.assertEqual(migrated_info["combo_ref"], PREDEFINED_CHARACTER_REF)

    def test_char_factory_uses_builtin_ref_without_ui_import(self):
        from src.char.CharFactory import _build_char_instance
        from src.char.Zero import Zero

        self.manager.add_character("builtin_char", PREDEFINED_CHARACTER_REF)
        instance = _build_char_instance(self.task, 0, "builtin_char", 0.95, self.manager)
        self.assertIsInstance(instance, Zero)

    def test_builtin_label_disambiguates_duplicate_cn_name(self):
        fake_entries = {
            "char_a": {"cn_name": "重名"},
            "char_b": {"cn_name": "重名"},
            "char_c": {"cn_name": "唯一名"},
        }

        with (
            patch.object(BuiltinComboRegistry, "_get_builtin_entries", return_value=fake_entries),
            patch.object(BuiltinComboRegistry, "_legacy_prefix", return_value="[内置代码] "),
            patch.object(BuiltinComboRegistry, "_locale_name", return_value="zh_CN"),
        ):
            label_a = BuiltinComboRegistry.to_label("builtin:char_a")
            label_b = BuiltinComboRegistry.to_label("builtin:char_b")
            label_c = BuiltinComboRegistry.to_label("builtin:char_c")

            self.assertEqual(label_a, "[内置代码] 重名 (char_a)")
            self.assertEqual(label_b, "[内置代码] 重名 (char_b)")
            self.assertEqual(label_c, "[内置代码] 唯一名")
            self.assertEqual(BuiltinComboRegistry.to_ref(label_a), "builtin:char_a")
            self.assertEqual(BuiltinComboRegistry.to_ref(label_b), "builtin:char_b")

if __name__ == '__main__':
    unittest.main()
