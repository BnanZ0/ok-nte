import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from ok.task.exceptions import FinishedException, TaskDisabledException

from src.char.BaseChar import BaseChar
from src.char.core.CharRegistry import CharRegistry
from src.combat.BaseCombatTask import BaseCombatTask, NotInCombatException, SleepCheckSkip
from src.combat.planner import CombatPlanner
from src.tasks.DSDFarmTask import DSDFarmTask
from src.utils.clock import TimePoint


class TestCombatSession(unittest.TestCase):
    def _task(self):
        task = BaseCombatTask.__new__(BaseCombatTask)
        task.sleep_check_skip = SleepCheckSkip()
        task.combat_session = None
        task.current_char = object()
        task.start_char = object()
        task.switch_calls = 0

        def switch_to_start():
            task.switch_calls += 1
            task.current_char = task.start_char

        task.switch_to_combat_start_char = switch_to_start
        task.get_current_char = lambda raise_exception=False: task.current_char
        task.click = lambda *_args, **_kwargs: None
        return task

    def test_begin_combat_session_switches_once_and_records_start_char(self):
        task = self._task()

        session = task.begin_combat_session()
        duplicate = task.begin_combat_session()

        self.assertIs(session, duplicate)
        self.assertEqual(task.switch_calls, 1)
        self.assertIs(session.start_char, task.start_char)
        self.assertIsInstance(session.combat_start, TimePoint)
        self.assertFalse(task.sleep_check_skip.all)

    def test_first_engage_is_recorded_once_for_the_active_session(self):
        task = self._task()
        task.begin_combat_session()
        first = object()
        later = object()

        task.record_first_engage(first)
        task.record_first_engage(later)

        self.assertTrue(task.is_first_engage(first))
        self.assertFalse(task.is_first_engage(later))

    def test_first_engage_can_only_be_consumed_once(self):
        task = self._task()
        task.begin_combat_session()
        first = object()
        later = object()
        task.record_first_engage(first)

        self.assertTrue(task.consume_first_engage(first))
        self.assertFalse(task.consume_first_engage(first))
        self.assertFalse(task.consume_first_engage(later))
        self.assertTrue(task.is_first_engage(first))

    def test_base_char_delegates_first_engage_to_task_session(self):
        task = self._task()
        task.begin_combat_session()
        char = BaseChar.__new__(BaseChar)
        char.task = task
        task.record_first_engage(char)

        self.assertTrue(char.is_first_engage())
        self.assertTrue(char.consume_first_engage())
        self.assertFalse(char.consume_first_engage())

    def test_base_char_perform_records_first_engage_before_actions(self):
        task = Mock()
        char = BaseChar(task, index=0)
        char.planner_handles_arc = True
        char.switch_next_char = Mock()

        char.perform()

        task.record_first_engage.assert_called_once_with(char)
        task.combat_planner.perform_current_char.assert_called_once_with(char)

    def test_dsd_switching_config_can_be_set_before_combat_starts(self):
        task = DSDFarmTask.__new__(DSDFarmTask)
        task.sleep_check_skip = SleepCheckSkip()
        task.config = {task.CONF_DONT_SWITCH: True}
        task.combat_session = None
        task.current_char = object()
        task.switch_to_combat_start_char = lambda: None
        task.get_current_char = lambda raise_exception=False: task.current_char
        task.click = lambda *_args, **_kwargs: None

        task.combat_session.switch_enabled = not task.config.get(task.CONF_DONT_SWITCH, False)
        session = task.begin_combat_session()
        self.assertFalse(session.switch_enabled)
        task.config[task.CONF_DONT_SWITCH] = False
        self.assertFalse(task.begin_combat_session().switch_enabled)

    def test_ultimate_usage_is_a_combat_session_policy(self):
        task = self._task()

        task.combat_session.use_ultimate = False

        self.assertFalse(task.begin_combat_session().use_ultimate)

    def _external_failure(self, error, char=None):
        root = Path.cwd() / "external_diagnostic_test"
        namespace = {"error": error, "char": char}
        exec(
            compile("def fail(self=char):\n    raise error\n", str(root / "hero.py"), "exec"),
            namespace,
        )
        self.enterContext(
            patch("src.combat.BaseCombatTask.char_registry", CharRegistry(external_dir=root))
        )
        return namespace["fail"]

    def _cleanup_task(self):
        task = self._task()
        task.info = {}
        task.log_error = Mock()
        task.tr = lambda message: message
        task.custom_bar = self.enterContext(
            patch("src.combat.BaseCombatTask.communicate.info_bar_requested.emit")
        )
        task.reset_to_false = Mock()
        task.get_current_char = Mock(return_value=None)
        task._clear_dead_chars = Mock()
        self.enterContext(patch("src.combat.BaseCombatTask.SoundCombatContext"))
        return task

    def test_external_runtime_failure_is_reported_during_cleanup_and_still_propagates(self):
        task = self._cleanup_task()
        fail = self._external_failure(AttributeError("removed_api"))
        with self.assertRaises(AttributeError):
            try:
                fail()
            finally:
                task.combat_end()
        task.log_error.assert_called_once()
        message = task.log_error.call_args.args[0]
        self.assertIn("hero.py:2", message)
        self.assertIn("removed_api", message)
        self.assertNotIn("notify", task.log_error.call_args.kwargs)
        task.custom_bar.assert_called_once_with(
            title="外置代码", content=message, success=False, duration=-1
        )
        task._clear_dead_chars.assert_called_once()
        self.assertIsNone(task._combat_session)

    def test_external_initialization_failure_is_reported_before_detection_catches_it(self):
        task = self._cleanup_task()
        task._load_chars = self._external_failure(TypeError("unexpected keyword"))
        with self.assertRaises(TypeError):
            task.load_chars()
        task.log_error.assert_called_once()
        self.assertIn("unexpected keyword", task.log_error.call_args.args[0])
        self.assertNotIn("notify", task.log_error.call_args.kwargs)
        task.custom_bar.assert_called_once()

    def test_external_notice_identifies_the_failing_teammate_and_ui_combo_name(self):
        task = self._cleanup_task()
        source = BaseChar.__new__(BaseChar)
        source.task = task
        source.char_name = "我的黑鸟"
        source.impl_id = "external:community_team/blackbird"
        task.get_current_char = Mock(return_value=BaseChar.__new__(BaseChar))
        fail = self._external_failure(TypeError("unexpected keyword"), char=source)
        with (
            patch("src.combat.BaseCombatTask.CustomCharManager") as manager,
            patch(
                "src.char.core.CharRegistry.CharRegistry.tr", side_effect=lambda text: text
            ) as tr,
        ):
            combo_name = "[外置代码] community_team - 黑鸟"
            manager.return_value.get_impl_name.return_value = combo_name
            try:
                fail()
            except TypeError as error:
                task._report_external_error(error)
            manager.return_value.get_impl_name.assert_called_once_with(
                source.impl_id, with_source_prefix=True
            )
        tr.assert_called_once()
        self.assertIn(
            "角色: {character}\n出招表: {combo}\n出错位置: {location}", tr.call_args.args[0]
        )
        message = task.custom_bar.call_args.kwargs["content"]
        self.assertIn(source.char_name, message)
        self.assertIn(combo_name, message)
        self.assertIn("hero.py:2", message)

    def test_hook_binding_error_reports_registered_combo_without_an_external_traceback(self):
        task = self._cleanup_task()
        root = Path(self.enterContext(TemporaryDirectory()))
        folder = root / "test1"
        folder.mkdir()
        (folder / "_shared.py").write_text(
            "class RotationPuppet:\n    def combat_policies(self, context):\n        return None\n",
            encoding="utf-8",
        )
        (folder / "blackbird.py").write_text(
            "from src.char.BaseChar import BaseChar\n"
            "from ._shared import RotationPuppet\n"
            "class BlackbirdPuppet(RotationPuppet, BaseChar):\n"
            "    cn_name = 'Blackbird Puppet'\n",
            encoding="utf-8",
        )
        registry = CharRegistry(external_dir=root)
        entry = registry.get("external:test1/blackbird")
        self.assertIsNotNone(entry)
        source = entry.char_cls(task, index=1)
        source.char_name = "我的黑鸟"
        current = BaseChar(task, index=0)
        task.get_current_char = Mock(return_value=current)
        task.clock = BaseChar.clock
        planner = CombatPlanner(task)

        def load_chars():
            planner.reset([current, source])

        task._load_chars = load_chars
        with (
            patch("src.combat.BaseCombatTask.char_registry", registry),
            patch("src.combat.BaseCombatTask.CustomCharManager") as manager,
        ):
            combo_name = "[外置代码] test1 - 黑鸟固定轴"
            manager.return_value.get_impl_name.return_value = combo_name
            for impl_id in (entry.impl_id, "builtin:blackbird", "external:unregistered"):
                with self.subTest(impl_id=impl_id):
                    source.impl_id = impl_id
                    task.custom_bar.reset_mock()
                    with self.assertRaisesRegex(TypeError, "required positional argument"):
                        task.load_chars()
                    if impl_id != entry.impl_id:
                        task.custom_bar.assert_not_called()
                        continue
                    task.custom_bar.assert_called_once()
                    message = task.custom_bar.call_args.kwargs["content"]
                    self.assertIn(source.char_name, message)
                    self.assertIn(combo_name, message)
                    self.assertIn("test1/blackbird.py", message)
                    self.assertIn("RotationPuppet.combat_policies", message)
                    self.assertNotIn(str(root), message)

    def test_cached_load_failures_use_custom_notifications_without_repeating(self):
        task = self._cleanup_task()
        task._load_chars = Mock(return_value=True)
        with patch("src.combat.BaseCombatTask.char_registry") as registry:
            registry.get_external_errors.return_value = ["hero.py:1: missing API"]
            self.assertTrue(task.load_chars())
            self.assertTrue(task.load_chars())
            task.log_error.assert_called_once_with("hero.py:1: missing API")
            task.custom_bar.assert_called_once()
            registry.get_external_errors.return_value = []
            task.load_chars()
            registry.get_external_errors.return_value = ["hero.py:1: missing API"]
            task.load_chars()
        self.assertEqual(task.log_error.call_count, 2)
        self.assertEqual(task.custom_bar.call_count, 2)

    def test_normal_combat_control_exceptions_do_not_report_external_failures(self):
        for error_type in (NotInCombatException, TaskDisabledException, FinishedException):
            with self.subTest(error_type=error_type):
                task = self._cleanup_task()
                fail = self._external_failure(error_type("stop"))
                with self.assertRaises(error_type):
                    try:
                        fail()
                    finally:
                        task.combat_end()
                task.log_error.assert_not_called()
                task.custom_bar.assert_not_called()

    def test_builtin_error_does_not_get_an_external_code_notice(self):
        task = self._cleanup_task()
        with self.assertRaises(ValueError):
            try:
                raise ValueError("builtin failure")
            finally:
                task.combat_end()
        task.log_error.assert_not_called()
        task.custom_bar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
