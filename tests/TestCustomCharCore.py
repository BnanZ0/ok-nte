import json
import os
import shutil
import unittest
import uuid
import zipfile
from unittest.mock import Mock, patch

import numpy as np

from src.char.core.CharFactory import get_char_by_impl_id
from src.char.core.CharRegistry import char_registry
from src.char.custom.CustomChar import CustomChar
from src.char.custom.CustomCharDb import DB_SCHEMA_VERSION, CustomCharDb
from src.char.custom.CustomCharDbMigrator import CustomCharDbMigrator, MigrationContext
from src.char.custom.CustomCharManager import CustomCharManager

PREDEFINED_CHARACTER_ID = "builtin:zero"


class TestCustomCharCore(unittest.TestCase):
    def setUp(self):
        temp_root = os.path.join(os.getcwd(), "tests", ".tmp")
        os.makedirs(temp_root, exist_ok=True)
        self.temp_dir = os.path.join(temp_root, f"case_{uuid.uuid4().hex}")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.db_path = os.path.join(self.temp_dir, "db.json")
        self.features_dir = os.path.join(self.temp_dir, "features")
        self.external_chars_dir = os.path.join(self.temp_dir, "external_chars")
        os.makedirs(self.features_dir, exist_ok=True)

        self.patchers = [
            patch("src.char.custom.CustomCharManager.CUSTOM_CHARS_DIR", self.temp_dir),
            patch("src.char.custom.CustomCharManager.DB_PATH", self.db_path),
            patch("src.char.custom.CustomCharManager.FEATURES_DIR", self.features_dir),
            patch("src.char.custom.CustomCharManager.EXTERNAL_CHARS_DIR", self.external_chars_dir),
        ]
        for patcher in self.patchers:
            patcher.start()
        CustomCharManager._instance = None
        CustomCharDb.reset_instance()

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        CustomCharManager._instance = None
        CustomCharDb.reset_instance()

    def _write_db(self, data):
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _read_persisted_db(self):
        with open(self.db_path, encoding="utf-8") as file:
            return json.load(file)

    def test_manager_creates_external_characters_directory(self):
        CustomCharManager()

        self.assertTrue(os.path.isdir(self.external_chars_dir))

    def test_delete_external_impl_removes_source_and_empty_child_folder(self):
        manager = CustomCharManager()
        self.addCleanup(char_registry.rescan_external)
        team_dir = os.path.join(self.external_chars_dir, "test_team")
        os.makedirs(team_dir)
        source_template = (
            "from src.char.BaseChar import BaseChar, Element\n\n"
            "class {class_name}(BaseChar):\n"
            "    cn_name = '{cn_name}'\n"
            "    en_name = '{class_name}'\n"
            "    element = Element.PURPLE\n"
        )
        first_source = os.path.join(team_dir, "first.py")
        second_source = os.path.join(team_dir, "second.py")
        with open(first_source, "w", encoding="utf-8") as file:
            file.write(source_template.format(class_name="FirstExternal", cn_name="甲"))
        with open(second_source, "w", encoding="utf-8") as file:
            file.write(source_template.format(class_name="SecondExternal", cn_name="乙"))

        char_registry.rescan_external()
        first_impl_id = "external:test_team/first"
        second_impl_id = "external:test_team/second"
        char_id = manager.create_character("external_user", first_impl_id)

        self.assertEqual(manager.get_character_impl_id_by_id(char_id), first_impl_id)
        self.assertTrue(manager.delete_external_impl(first_impl_id))
        self.assertFalse(os.path.exists(first_source))
        self.assertTrue(os.path.isdir(team_dir))
        self.assertTrue(os.path.isfile(second_source))

        self.assertTrue(manager.delete_external_impl(second_impl_id))
        self.assertFalse(os.path.exists(team_dir))
        self.assertTrue(os.path.isdir(self.external_chars_dir))

    def test_import_removes_stale_external_character_files(self):
        manager = CustomCharManager()
        os.makedirs(self.external_chars_dir, exist_ok=True)
        old_external_file = os.path.join(self.external_chars_dir, "old.py")
        with open(old_external_file, "w", encoding="utf-8") as file:
            file.write("old")

        archive_path = os.path.join(
            os.path.dirname(self.temp_dir), f"import_{uuid.uuid4().hex}.zip"
        )
        try:
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "custom_chars/db.json", '{"combos": {}, "characters": {}, "features": {}}'
                )
                archive.writestr("custom_chars/external_chars/new.py", "new")

            manager.import_custom_data(archive_path)
        finally:
            if os.path.exists(archive_path):
                os.remove(archive_path)

        self.assertFalse(os.path.exists(old_external_file))
        self.assertTrue(os.path.isfile(os.path.join(self.external_chars_dir, "new.py")))

    @staticmethod
    def _character_id_by_name(manager, char_name):
        return next(
            char_id
            for char_id, info in manager.get_all_characters().items()
            if info["char_name"] == char_name
        )

    def test_db_schema_migrates_legacy_combo_name(self):
        legacy = {
            "schema_version": 3,
            "combos": {"combo_old": "skill,wait(0.1)"},
            "characters": {
                "char_legacy": {
                    "combo_name": "combo_old",
                    "feature_ids": [],
                }
            },
            "features": {},
        }
        self._write_db(legacy)

        manager = CustomCharManager()
        persisted = self._read_persisted_db()
        self.assertEqual(persisted["schema_version"], DB_SCHEMA_VERSION)
        combo_id = manager.find_custom_combo_id_by_name("combo_old")
        self.assertTrue(combo_id.startswith("combo_"))
        raw = next(iter(persisted["characters"].values()))
        self.assertEqual(raw["name"], "char_legacy")
        self.assertEqual(raw["impl_id"], combo_id)
        self.assertNotIn("combo_name", raw)
        self.assertNotIn("combo_ref", raw)

        info = manager.get_character_info_by_id(self._character_id_by_name(manager, "char_legacy"))
        self.assertIsNotNone(info)
        self.assertEqual(info["impl_id"], combo_id)
        self.assertEqual(manager.get_impl_name(info["impl_id"]), "combo_old")
        self.assertNotIn("combo_ref", info)

    def test_db_schema_migrates_legacy_builtin_label(self):
        bootstrap = {
            "schema_version": DB_SCHEMA_VERSION,
            "combos": {},
            "characters": {},
            "features": {},
        }
        self._write_db(bootstrap)
        manager = CustomCharManager()
        legacy_builtin_label = (
            f"{manager.get_builtin_prefix()}{manager.get_impl_name(PREDEFINED_CHARACTER_ID)}"
        )

        legacy = {
            "schema_version": 3,
            "combos": {},
            "characters": {
                "char_builtin": {
                    "combo_name": legacy_builtin_label,
                    "feature_ids": [],
                }
            },
            "features": {},
        }
        self._write_db(legacy)
        CustomCharManager._instance = None

        manager = CustomCharManager()
        info = manager.get_character_info_by_id(self._character_id_by_name(manager, "char_builtin"))
        self.assertIsNotNone(info)
        self.assertEqual(info["impl_id"], PREDEFINED_CHARACTER_ID)
        self.assertNotIn("combo_ref", info)

    def test_db_schema_remaps_custom_combo_key_conflicting_with_builtin(self):
        legacy = {
            "schema_version": 3,
            "combos": {"builtin:char_zero": "skill,wait(0.1)"},
            "characters": {
                "char_conflict": {
                    "combo_name": "builtin:char_zero",
                    "feature_ids": [],
                }
            },
            "features": {},
        }
        self._write_db(legacy)

        manager = CustomCharManager()
        remapped_key = manager.find_custom_combo_id_by_name("builtin:char_zero")

        self.assertFalse(manager.is_custom_combo_exist("builtin:char_zero"))
        self.assertTrue(manager.is_custom_combo_exist(remapped_key))
        self.assertEqual(manager.get_combo(remapped_key), "skill,wait(0.1)")

        info = manager.get_character_info_by_id(
            self._character_id_by_name(manager, "char_conflict")
        )
        self.assertIsNotNone(info)
        self.assertEqual(info["impl_id"], remapped_key)
        self.assertNotIn("combo_ref", info)
        self.assertEqual(manager.get_combo(info["impl_id"]), "skill,wait(0.1)")

    def test_validate_combo_syntax_reports_line_and_column(self):
        is_valid, error = CustomChar.validate_combo_syntax("skill,wait(0.5)")
        self.assertTrue(is_valid)
        self.assertIsNone(error)

        is_valid, error = CustomChar.validate_combo_syntax("skill(\nwait(0.5)")
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)
        self.assertIn("line", error)
        self.assertIn("column", error)

    def test_validate_combo_rejects_unsupported_and_unknown(self):
        is_valid, error = CustomChar.validate_combo_syntax("wait(**data)")
        self.assertFalse(is_valid)
        self.assertIn("**kwargs", error or "")

        is_valid, error = CustomChar.validate_combo_syntax("not_a_command")
        self.assertFalse(is_valid)
        self.assertIn("unknown command", error or "")

    def test_validate_combo_supports_compact_if_else_and_return(self):
        is_valid, error = CustomChar.validate_combo_syntax("if ultimate: skill")
        self.assertTrue(is_valid)
        self.assertIsNone(error)

        is_valid, error = CustomChar.validate_combo_syntax(
            "if ultimate: skill, wait(0.1)\nelse: l_click(2)"
        )
        self.assertTrue(is_valid)
        self.assertIsNone(error)

    def test_combo_syntax_guide_uses_single_searchable_flow_block(self):
        guide = CustomChar.get_combo_syntax_guide()
        header = guide.splitlines()[0].lower()

        self.assertTrue(header.startswith("▶"))
        self.assertEqual(guide.count("▶"), 1)
        for keyword in ("if", "else", "return"):
            self.assertIn(keyword, header)

        is_valid, error = CustomChar.validate_combo_syntax("if ultimate: return")
        self.assertTrue(is_valid)
        self.assertIsNone(error)

    def test_validate_combo_rejects_legacy_and_invalid_if_usage(self):
        is_valid, error = CustomChar.validate_combo_syntax("if wait: skill")
        self.assertFalse(is_valid)
        self.assertIn("not enabled as if condition", error or "")

        is_valid, error = CustomChar.validate_combo_syntax("if_(ultimate, skill)")
        self.assertFalse(is_valid)
        self.assertIn("unknown command 'if_'", error or "")

        is_valid, error = CustomChar.validate_combo_syntax("return")
        self.assertFalse(is_valid)
        self.assertIn("only allowed inside if or else", error or "")

    def test_if_runtime_executes_selected_branch_only_when_condition_is_bool(self):
        char = object.__new__(CustomChar)
        char.logger = Mock()
        state = {"then_count": 0, "else_count": 0}

        cond_true = ("ultimate", lambda self: True, [], {}, "ultimate")
        then_cmds = [
            (
                "skill",
                lambda self: state.__setitem__("then_count", state["then_count"] + 1),
                [],
                {},
                "skill",
            ),
            (
                "wait",
                lambda self: state.__setitem__("then_count", state["then_count"] + 1),
                [],
                {},
                "wait(0.1)",
            ),
        ]
        else_cmds = [
            (
                "l_click",
                lambda self: state.__setitem__("else_count", state["else_count"] + 1),
                [],
                {},
                "l_click",
            )
        ]
        result = char._execute_if_statement(cond_true, then_cmds, else_cmds)
        self.assertTrue(result)
        self.assertEqual(state["then_count"], 2)
        self.assertEqual(state["else_count"], 0)

        cond_false = ("ultimate", lambda self: False, [], {}, "ultimate")
        result = char._execute_if_statement(cond_false, then_cmds, else_cmds)
        self.assertFalse(result)
        self.assertEqual(state["then_count"], 2)
        self.assertEqual(state["else_count"], 1)

    def test_if_runtime_treats_non_bool_condition_as_false(self):
        char = object.__new__(CustomChar)
        char.logger = Mock()
        state = {"then_count": 0, "else_count": 0}

        cond_non_bool = ("ultimate", lambda self: "yes", [], {}, "ultimate")
        then_cmds = [
            (
                "skill",
                lambda self: state.__setitem__("then_count", state["then_count"] + 1),
                [],
                {},
                "skill",
            )
        ]
        else_cmds = [
            (
                "l_click",
                lambda self: state.__setitem__("else_count", state["else_count"] + 1),
                [],
                {},
                "l_click",
            )
        ]
        result = char._execute_if_statement(cond_non_bool, then_cmds, else_cmds)

        self.assertFalse(result)
        self.assertEqual(state["then_count"], 0)
        self.assertEqual(state["else_count"], 1)
        char.logger.warning.assert_called_once()
        self.assertIn("non-bool", char.logger.warning.call_args[0][0])

    def test_if_return_stops_remaining_combo_commands(self):
        char = object.__new__(CustomChar)
        char.logger = Mock()
        char.check_combat = Mock()
        char._held_keys = set()
        char._held_mouse_buttons = set()
        state = {"after_return": 0}
        condition = ("ultimate", lambda self: True, [], {}, "ultimate")
        return_command = ("return", CustomChar._return_combo, [], {}, "return")
        after_command = (
            "skill",
            lambda self: state.__setitem__("after_return", state["after_return"] + 1),
            [],
            {},
            "skill",
        )
        char.parsed_combo = [
            (
                "if",
                CustomChar._execute_if_statement,
                [condition, [return_command], []],
                {},
                "if ultimate: return",
            ),
            after_command,
        ]

        char._execute_parsed_combo()

        self.assertEqual(state["after_return"], 0)
        char.check_combat.assert_not_called()

    def test_combo_releases_keydown_without_explicit_keyup(self):
        char = object.__new__(CustomChar)
        char.logger = Mock()
        char.task = Mock()
        char.check_combat = Mock()
        char._held_keys = set()
        char._held_mouse_buttons = set()
        char.parsed_combo = [
            ("keydown", CustomChar.keydown, ["a"], {}, "keydown(a)"),
        ]

        char._execute_parsed_combo()

        char.task.send_key_down.assert_called_once_with("a")
        char.task.send_key_up.assert_called_once_with("a")
        self.assertEqual(char._held_keys, set())

    def test_combo_releases_mousedown_without_explicit_mouseup(self):
        char = object.__new__(CustomChar)
        char.logger = Mock()
        char.task = Mock()
        char.check_combat = Mock()
        char._held_keys = set()
        char._held_mouse_buttons = set()
        char.parsed_combo = [
            ("mousedown", CustomChar.mousedown, ["right"], {}, "mousedown(right)"),
        ]

        char._execute_parsed_combo()

        char.task.mouse_down.assert_called_once_with(key="right")
        char.task.mouse_up.assert_called_once_with(key="right")
        self.assertEqual(char._held_mouse_buttons, set())

    def test_db_schema_migrates_v5_combo_syntax_and_creates_backup(self):
        legacy = {
            "schema_version": 5,
            "combos": {
                "combo_a": {"name": "combo_a", "content": "skill,if_(ultimate, wait(0.1)),arc"},
                "combo_b": {"name": "combo_b", "content": "l_click(2)"},
            },
            "characters": {},
            "features": {},
            "fixed_team": {"enabled": False, "slots": []},
        }
        self._write_db(legacy)

        manager = CustomCharManager()

        self.assertEqual(self._read_persisted_db()["schema_version"], DB_SCHEMA_VERSION)
        self.assertEqual(
            manager.get_combo("combo_a"),
            "skill\nif ultimate: wait(0.1)\narc",
        )
        self.assertEqual(manager.get_combo("combo_b"), "l_click(2)")
        with open(f"{self.db_path}.pre-v{DB_SCHEMA_VERSION}.bak", encoding="utf-8") as file:
            self.assertEqual(json.load(file), legacy)

        CustomCharManager._instance = None
        manager = CustomCharManager()
        self.assertEqual(manager.get_combo("combo_a"), "skill\nif ultimate: wait(0.1)\narc")

    def test_db_schema_keeps_unmigratable_combo_and_reports_diagnostic(self):
        legacy = {
            "schema_version": 5,
            "combos": {"combo_invalid": {"name": "broken", "content": "if_(ultimate)"}},
            "characters": {},
            "features": {},
            "fixed_team": {"enabled": False, "slots": []},
        }
        self._write_db(legacy)

        with patch("src.char.custom.CustomCharManager.logger") as manager_logger:
            manager = CustomCharManager()

        self.assertEqual(manager.get_combo("combo_invalid"), "if_(ultimate)")
        warning_messages = [call.args[0] for call in manager_logger.warning.call_args_list]
        self.assertTrue(any("not converted" in message for message in warning_messages))

    def test_manager_reuses_the_global_database_object(self):
        manager = CustomCharManager()
        combo_id = manager.add_combo("immutable view", "skill")
        database = manager._db

        CustomCharManager._instance = None
        reloaded_manager = CustomCharManager()

        self.assertIs(reloaded_manager._db, database)
        self.assertEqual(reloaded_manager.get_combo(combo_id), "skill")
        self.assertFalse(hasattr(manager, "db"))

    def test_validate_db_removes_missing_feature_assets_and_metadata(self):
        existing_fid = "feat_exists"
        missing_fid = "feat_missing"

        with open(os.path.join(self.features_dir, f"{existing_fid}.png"), "wb") as f:
            f.write(b"ok")

        legacy = {
            "schema_version": DB_SCHEMA_VERSION,
            "combos": {},
            "characters": {
                "char_a": {
                    "impl_id": "",
                    "feature_ids": [existing_fid, missing_fid],
                }
            },
            "features": {
                existing_fid: {"width": 1920, "height": 1080},
                missing_fid: {"width": 1920, "height": 1080},
            },
        }
        self._write_db(legacy)

        manager = CustomCharManager()

        char_info = manager.get_character_info_by_id(self._character_id_by_name(manager, "char_a"))
        self.assertIsNotNone(char_info)
        self.assertEqual(char_info["feature_ids"], [existing_fid])
        persisted = self._read_persisted_db()
        self.assertIn(existing_fid, persisted["features"])
        self.assertNotIn(missing_fid, persisted["features"])

    def test_char_name_is_stripped_and_kept_unique(self):
        manager = CustomCharManager()
        raw_name = "  custom hero  "

        char_id = manager.create_character(raw_name, "")
        duplicate_id = manager.create_character("custom hero", "")
        blank_id = manager.create_character("   ", "")

        names = [c["char_name"] for c in manager.get_all_characters().values()]
        self.assertIn("custom hero", names)
        self.assertNotIn(raw_name, names)
        self.assertEqual(names.count("custom hero"), 1)
        self.assertEqual(duplicate_id, char_id)
        self.assertEqual(blank_id, "")

        id_custom = self._character_id_by_name(manager, "custom hero")

        self.assertEqual(id_custom, char_id)
        self.assertEqual(manager.get_character_info_by_id(id_custom)["char_name"], "custom hero")
        self.assertNotIn("   ", names)

    def test_character_info_by_id_has_expected_shape(self):
        manager = CustomCharManager()
        char_id = manager.create_character("fixed shape", "")

        info = manager.get_character_info_by_id(char_id)
        missing = manager.get_character_info_by_id("missing")

        self.assertEqual(info["char_id"], char_id)
        self.assertEqual(info["char_name"], "fixed shape")
        self.assertEqual(info["impl_id"], "")
        self.assertEqual(info["feature_ids"], [])
        self.assertNotIn("name", info)

        self.assertIsNone(missing)

    def test_char_factory_loads_custom_char_metadata_by_id(self):
        from src.char.core.CharFactory import _build_char_instance

        manager = CustomCharManager()
        combo_id = manager.add_combo("combo_runtime", "skill, wait(0.1)")
        char_id = manager.create_character("runtime hero", combo_id)

        char = _build_char_instance(Mock(), 0, char_id, 1, manager)

        self.assertIsInstance(char, CustomChar)
        self.assertEqual(char.char_name, "runtime hero")
        self.assertEqual(char.impl_id, combo_id)
        self.assertEqual([command[0] for command in char.parsed_combo], ["skill", "wait"])

    def test_fixed_team_migrates_combo_ref_to_combo_id(self):
        legacy = {
            "schema_version": 4,
            "combos": {},
            "characters": {"char_001": {"name": "零", "combo_ref": "builtin:char_zero"}},
            "features": {},
            "fixed_team": {
                "enabled": True,
                "slots": [
                    {"char_name": "零", "combo_ref": "builtin:char_zero"},
                ],
            },
        }
        self._write_db(legacy)

        manager = CustomCharManager()
        fixed_team = manager.get_fixed_team()

        self.assertTrue(fixed_team["enabled"])
        char_id = ""
        for character_id, character_info in manager.get_all_characters().items():
            if character_info["char_name"] == "零":
                char_id = character_id
                break
        self.assertNotEqual(char_id, "")
        self.assertEqual(fixed_team["slots"][0]["char_id"], char_id)
        self.assertEqual(fixed_team["slots"][0]["impl_id"], PREDEFINED_CHARACTER_ID)
        self.assertNotIn("combo_ref", fixed_team["slots"][0])

    def test_migrator_converts_in_memory_without_file_io(self):
        context = MigrationContext(
            is_builtin_impl=lambda _impl_id: False,
            get_builtin_prefix=lambda: "[内置代码] ",
            iter_builtin_impl_items=lambda: [],
            generate_combo_id=lambda existing: f"combo_{len(existing or set())}",
        )
        source = {
            "schema_version": 5,
            "combos": {"combo_a": {"name": "combo_a", "content": "if_(ultimate, skill)"}},
            "characters": {},
            "features": {},
        }

        with patch("builtins.open", side_effect=AssertionError("migration must not open files")):
            result = CustomCharDbMigrator(context, DB_SCHEMA_VERSION).migrate(source)

        self.assertTrue(result.modified)
        self.assertTrue(result.needs_backup)
        self.assertEqual(result.db["combos"]["combo_a"]["content"], "if ultimate: skill")

    def test_migrator_converts_legacy_layout_and_builtin_reference(self):
        context = MigrationContext(
            is_builtin_impl=lambda impl_id: impl_id == PREDEFINED_CHARACTER_ID,
            get_builtin_prefix=lambda: "[内置代码] ",
            iter_builtin_impl_items=lambda: [("Zero", PREDEFINED_CHARACTER_ID)],
            generate_combo_id=lambda existing: f"combo_{len(existing or set())}",
        )
        source = {
            "schema_version": 3,
            "combos": {"custom": "skill"},
            "characters": {"legacy": {"combo_name": "custom", "feature_ids": []}},
            "features": {},
            "fixed_team": {
                "enabled": True,
                "slots": [{"char_name": "legacy", "combo_ref": "builtin:char_zero"}],
            },
        }

        result = CustomCharDbMigrator(context, DB_SCHEMA_VERSION).migrate(source)

        self.assertEqual(result.db["schema_version"], DB_SCHEMA_VERSION)
        self.assertEqual(result.db["combos"]["combo_0"]["content"], "skill")
        self.assertEqual(result.db["characters"]["char_0001"]["impl_id"], "combo_0")
        self.assertEqual(
            result.db["team_presets"][0]["slots"][0],
            {
                "char_id": "char_0001",
                "impl_id": PREDEFINED_CHARACTER_ID,
            },
        )
        self.assertTrue(result.db["team_presets"][0]["is_fixed"])

    def test_validate_db_removes_unreferenced_feature_images(self):
        manager = CustomCharManager()
        char_id = manager.create_character("char_cleanup", "")
        referenced_feature_id = manager.add_feature_to_character(
            char_id, np.zeros((10, 10, 3), dtype=np.uint8), 1920, 1080
        )
        orphan_feature_id = "orphan_feature"
        manager.save_feature_image(orphan_feature_id, np.zeros((10, 10, 3), dtype=np.uint8))
        note_path = os.path.join(self.temp_dir, "features", "note.txt")
        with open(note_path, "w", encoding="utf-8") as file:
            file.write("keep")

        manager.validate_db()

        self.assertTrue(
            os.path.exists(os.path.join(self.temp_dir, "features", f"{referenced_feature_id}.png"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.temp_dir, "features", f"{orphan_feature_id}.png"))
        )
        self.assertTrue(os.path.exists(note_path))

    def test_combo_compile(self):
        """測試 CustomChar 透過 AST 語法樹將字串解析為獨立指令的容錯與精準度"""
        manager = CustomCharManager()
        combo_id = manager.add_combo(
            "combo_ast", "skill, l_click(), l_hold(1.5), walk(w, 2), wait(0.5)"
        )
        char_id = manager.create_character("test_ast_hero", combo_id)

        # 初始化 CustomChar
        char = CustomChar(
            task=Mock(),
            index=0,
            char_id=char_id,
            impl_id=combo_id,
        )
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

    def test_char_factory_builds_direct_implementation_without_a_character_record(self):
        manager = CustomCharManager()
        combo_id = manager.add_combo("combo_direct_factory", "skill")

        char = get_char_by_impl_id(Mock(), index=0, impl_id=combo_id)

        self.assertIsInstance(char, CustomChar)
        self.assertEqual(char.char_id, "")
        self.assertEqual(char.impl_id, combo_id)

    def test_builtin_combo_roundtrip(self):
        manager = CustomCharManager()
        builtin_id = PREDEFINED_CHARACTER_ID
        builtin_name = manager.get_impl_name(builtin_id)
        builtin_display = f"{manager.get_builtin_prefix()}{builtin_name}"

        self.assertTrue(manager.is_builtin_impl(builtin_id))
        self.assertFalse(builtin_name.startswith(manager.get_builtin_prefix()))

        char_id = manager.create_character("char_builtin", builtin_id)
        char_info = manager.get_character_info_by_id(char_id)
        assert char_info is not None
        self.assertEqual(char_info["impl_id"], builtin_id)
        self.assertEqual(manager.get_impl_name(char_info["impl_id"]), builtin_name)

        combo_items = manager.get_all_impl_items(with_source_prefix=True)
        self.assertIn((builtin_display, builtin_id), combo_items)

    def test_char_factory_loads_builtin_metadata_by_id(self):
        from src.char.core.CharFactory import _build_char_instance
        from src.char.Zero import Zero

        manager = CustomCharManager()
        char_id = manager.create_character("builtin_char", PREDEFINED_CHARACTER_ID)
        instance = _build_char_instance(Mock(), 0, char_id, 0.95, manager)
        self.assertIsInstance(instance, Zero)
        self.assertEqual(instance.char_name, "builtin_char")
        self.assertEqual(instance.impl_id, PREDEFINED_CHARACTER_ID)

    def test_builtin_impl_name_uses_metadata_and_ui_prefix_is_display_only(self):
        manager = CustomCharManager()
        with patch.object(CustomCharManager, "_locale_name", return_value="zh_CN"):
            self.assertEqual(manager.get_impl_name(PREDEFINED_CHARACTER_ID), "零")
            self.assertEqual(
                manager.get_impl_name(PREDEFINED_CHARACTER_ID, with_source_prefix=True),
                "[内置代码] 零",
            )


if __name__ == "__main__":
    unittest.main()
