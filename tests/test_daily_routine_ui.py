"""Exercise the daily page's config boundary and task controls."""

import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok import og
from ok.ui.qt.Communicate import communicate
from ok.ui.qt.kit import Table
from ok.util.config import Config
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from src.tasks.AnomalyTask import AnomalyTask
from src.tasks.daily.DailyRoutineTask import DailyRoutineTask
from src.ui.DailyRoutineTab import DailyRoutineTab
from tests.support.qt_events import send_key


class _MemoryConfig(Config):
    def __init__(self, default, values=None):
        self.default = deepcopy(default)
        self.validator = None
        self.writes = 0
        dict.__init__(self, deepcopy(default if values is None else values))

    def save_file(self):
        self.writes += 1


class TestDailyRoutineUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.runtime_patch = patch.object(og, "app", None)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        self.routine = object.__new__(DailyRoutineTask)
        self.routine.name = "Daily routine"
        self.routine._enabled = False
        self.routine._paused = False
        self.routine.running = False
        self.routine.first_run_alert = None
        self.routine.info = {}
        self.routine.single_task_request = None
        self.routine.task_status = {}
        self.routine.default_config = {
            DailyRoutineTask.CONF_ITEMS: self.routine.default_items(),
            "Notify": True,
        }
        self.routine.config_description = {}
        self.routine.config_type = {DailyRoutineTask.CONF_ITEMS: {"hidden": True}}
        self.routine.config = _MemoryConfig(self.routine.default_config)
        self.children = {
            task_id: SimpleNamespace(
                name=task_id,
                enabled=False,
                running=False,
                info={},
                config=_MemoryConfig({"Option": True, "Count": 2}),
                default_config={"Option": True, "Count": 2},
                config_description={},
                config_type={},
            )
            for task_id in self.routine.entries_by_id()
        }
        self.routine.task_for_id = lambda task_id: self.children.get(task_id)
        # Populate valid stored configs before rendering, then count UI writes only.
        stored = {
            task_id: self.routine.daily_task_schema(task_id, child).default_config
            for task_id, child in self.children.items()
        }
        self.routine.routine_task_configs = _MemoryConfig(stored)
        self.routine.daily_task_card_context = Mock(
            side_effect=AssertionError("UI must not swap child config")
        )
        self.page = DailyRoutineTab()
        self.addCleanup(self._dispose_page)
        self.page.executor = SimpleNamespace(get_task_by_class=lambda cls: self.routine)
        self.page.resize(1080, 720)

    def _dispose_page(self):
        self.page.dispose()
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    @staticmethod
    def _mouse(widget, event_type, local, global_position, button, buttons):
        event = QMouseEvent(
            event_type,
            QPointF(local),
            QPointF(global_position),
            button,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(widget, event)

    def _begin_drag(self, task_id, destination):
        handle = self.page.rows[task_id].drag_handle
        press = handle.rect().center()
        self._mouse(
            handle,
            QEvent.Type.MouseButtonPress,
            press,
            handle.mapToGlobal(press),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
        )
        self._mouse(
            handle,
            QEvent.Type.MouseMove,
            press,
            destination,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
        )
        return handle, press

    def _release_drag(self, handle, press, destination):
        self._mouse(
            handle,
            QEvent.Type.MouseButtonRelease,
            press,
            destination,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )

    def test_edit_and_reset_daily_settings_never_changes_normal_task_config(self):
        self.page.select_task("daily_claim")
        child = self.children["daily_claim"]
        normal = child.config
        form = self.page.task_form
        form.config_widget_by_key["Option"].switch_button.click()
        self.assertFalse(self.routine.routine_task_configs["daily_claim"]["Option"])
        self.assertTrue(normal["Option"])
        self.assertIs(child.config, normal)
        self.page.reset_button.click()
        self.assertTrue(self.routine.routine_task_configs["daily_claim"]["Option"])
        self.assertEqual(dict(normal), {"Option": True, "Count": 2})
        self.assertEqual(normal.writes, 0)
        self.routine.daily_task_card_context.assert_not_called()

    def test_enable_checkbox_and_select_all_preserve_exclusivity_and_selection(self):
        self.page.select_task("daily_claim")
        self.page.rows["daily_anomaly_hunter"].enabled_check.click()
        self.assertTrue(self.page.rows["daily_anomaly_hunter"].enabled_check.isChecked())
        self.assertFalse(self.page.rows["daily_anomaly"].enabled_check.isChecked())
        self.assertEqual(self.page.selected_task_id, "daily_claim")
        self.page.select_all_check.click()
        self.assertTrue(self.page.select_all_check.isChecked())
        self.assertTrue(self.page.rows["daily_claim"].enabled_check.isChecked())
        self.page.select_all_check.click()
        self.assertFalse(any(item["enabled"] for item in self.routine.normalize_items()))
        self.assertFalse(self.page.start_button.isEnabled())

    def test_handle_reorder_preserves_selected_id_and_unavailable_entry_position(self):
        self.children.pop("coffee")
        self.page.refresh_tasks()
        unavailable_position = [item["id"] for item in self.routine.normalize_items()].index(
            "coffee"
        )
        self.page.select_task("daily_claim")
        self.page.show()
        self.app.processEvents()
        sibling = self.page.rows["daily_anomaly"]
        start_y = sibling.y()
        destination = self.page.list_contents.mapToGlobal(self.page.list_contents.rect().topLeft())
        handle, press = self._begin_drag("daily_claim", destination)
        self.assertEqual(sibling.y(), start_y)
        self.page._swaps.setCurrentTime(self.page._swaps.duration() // 2)
        middle_y = sibling.y()
        self.page._swaps.setCurrentTime(self.page._swaps.duration())
        self.assertLess(start_y, middle_y)
        self.assertLess(middle_y, sibling.y())
        self._release_drag(handle, press, destination)
        order = [item["id"] for item in self.routine.normalize_items()]
        self.assertEqual(order[0], "daily_claim")
        self.assertEqual(order.index("coffee"), unavailable_position)
        self.page.refresh_tasks()
        self.assertEqual(next(iter(self.page.rows)), "daily_claim")
        self.assertEqual(self.page.selected_task_id, "daily_claim")

    def test_drag_cancel_restores_order_after_scrolling_and_on_refresh(self):
        self.page.resize(1080, 380)
        self.page.show()
        self.app.processEvents()
        persisted = self.routine.normalize_items()
        visible_order = list(self.page.rows)
        viewport = self.page.list_scroll.viewport()
        destination = viewport.mapToGlobal(viewport.rect().bottomLeft() + QPoint(12, 20))
        scroll_bar = self.page.list_scroll.verticalScrollBar()
        for cancel in ("escape", "refresh"):
            with self.subTest(cancel=cancel):
                scroll_bar.setValue(0)
                handle, _ = self._begin_drag("daily_anomaly", destination)
                self.assertGreater(scroll_bar.value(), 0)
                self.assertNotEqual(
                    [row.task_id for row in self.page._ordered_rows()], visible_order
                )
                if cancel == "escape":
                    send_key(handle, Qt.Key.Key_Escape)
                else:
                    self.page.refresh_tasks()
                self.assertIsNone(self.page._drag_proxy)
                self.assertIsNone(self.page.rows["daily_anomaly"].graphicsEffect())
                self.assertEqual([row.task_id for row in self.page._ordered_rows()], visible_order)
                self.assertEqual(self.routine.normalize_items(), persisted)

    def test_reorder_rejected_after_availability_change_restores_saved_order(self):
        self.page.show()
        self.app.processEvents()
        persisted = self.routine.normalize_items()
        destination = self.page.list_contents.mapToGlobal(self.page.list_contents.rect().topLeft())
        handle, press = self._begin_drag("daily_claim", destination)
        self.assertEqual(self.page._ordered_rows()[0].task_id, "daily_claim")

        self.children.pop("coffee")
        self._release_drag(handle, press, destination)

        visible_order = [item["id"] for item in persisted if item["id"] != "coffee"]
        self.assertEqual([row.task_id for row in self.page._ordered_rows()], visible_order)
        self.assertEqual(self.routine.normalize_items(), persisted)

    def test_refresh_selection_and_show_do_not_write_hydrated_configs(self):
        self.page.select_task("daily_claim")
        self.routine.config.writes = 0
        self.routine.routine_task_configs.writes = 0
        before = deepcopy(dict(self.routine.routine_task_configs))
        self.page.refresh_tasks()
        self.page.select_task("daily_claim")
        self.page._refresh_state()
        self.page.show()
        self.app.processEvents()
        self.assertEqual(self.routine.config.writes, 0)
        self.assertEqual(self.routine.routine_task_configs.writes, 0)
        self.assertEqual(dict(self.routine.routine_task_configs), before)
        self.assertTrue(all(child.config.writes == 0 for child in self.children.values()))

    def test_long_setting_description_fits_after_narrowing_and_widening(self):
        self.page.select_task("daily_anomaly")
        self.page.show()
        editor = self.page.task_form.config_widget_by_key[AnomalyTask.CONF_CYCLEB_TASK_MODE]
        description = editor.description_label
        heights = []
        for width in (1200, 800, 1200):
            self.page.resize(width, 480)
            self.app.processEvents()
            self.app.processEvents()
            measured = description.heightForWidth(description.width())
            self.assertGreaterEqual(description.height(), measured)
            heights.append(measured)
        self.assertGreater(heights[1], heights[0])
        self.assertEqual(heights[2], heights[0])

    def test_parent_controller_actions_and_dispose_unsubscribe(self):
        start = Mock()
        self.routine.pause = Mock()
        self.routine.disable = Mock(side_effect=lambda: setattr(self.routine, "_enabled", False))
        self.routine.unpause = Mock(side_effect=lambda: setattr(self.routine, "_paused", False))
        with patch.object(
            og,
            "app",
            SimpleNamespace(start_controller=SimpleNamespace(start=start), tr=lambda text: text),
        ):
            self.page.start_button.click()
            start.assert_called_once_with(self.routine)
            self.routine._enabled = True
            self.routine.running = True
            self.page._refresh_state()
            self.page.pause_button.click()
            self.routine.pause.assert_called_once_with()
            self.routine._paused = True
            self.page._refresh_state()
            self.page.start_button.click()
            self.routine.unpause.assert_called_once_with()
            self.page.stop_button.click()
            self.routine.disable.assert_called_once_with()
        self.page.dispose()
        self.page.dispose()
        self.assertFalse(self.page.timer.isActive())
        self.assertNotIn(self.page._on_task_event, communicate.task._subscribers)
        self.assertNotIn(self.page.refresh_tasks, communicate.task_list_updated._subscribers)

    def test_info_follows_selected_task_and_row_status_follows_routine(self):
        self.page.select_task("daily_claim")
        claim = self.children["daily_claim"]
        normal = claim.config
        claim.info = {"Progress": "preparing"}
        self.children["coffee"].info = {"Supplies": "available"}
        self.children["coffee"].enabled = True
        self.routine._enabled = True
        self.routine.running = True
        self.routine.task_status = {"daily_claim": "running", "furniture": "pending"}
        self.routine.info = {"Result": "working"}
        communicate.task.emit(self.routine)
        self.assertEqual(self.page.rows["daily_claim"].status_label.text(), "Running")
        self.assertEqual(self.page.rows["daily_claim"].status_label.property("tone"), "success")
        self.assertTrue(self.page.rows["coffee"].status_label.isHidden())
        self.assertTrue(self.page.routine_row.property("selected"))
        self.page.select_task("furniture")
        self.assertIs(self.page.pages.currentWidget(), self.page.task_scroll)
        self.assertTrue(self.page.tabs.item("info").isHidden())
        self.page.select_task("daily_claim")
        self.assertIs(self.page.pages.currentWidget(), self.page.info_table)
        self.page.tabs.item("settings").click()
        self.page._refresh_state()
        self.assertEqual(self.page.tabs.current_key(), "settings")
        self.page.tabs.item("info").click()
        self.assertIsInstance(self.page.info_table, Table)
        self.assertEqual(self.page.info_table.rowCount(), 1)
        self.assertEqual(self.page.info_table.item(0, 0).text(), "Progress")
        self.assertEqual(self.page.info_table.item(0, 1).text(), "preparing")
        claim.info["Progress"] = "claiming"
        self.page._refresh_state()
        self.assertEqual(self.page.info_table.item(0, 1).text(), "claiming")
        self.page.select_task("coffee")
        self.assertIs(self.page.pages.currentWidget(), self.page.info_table)
        self.assertEqual(self.page.info_table.item(0, 0).text(), "Supplies")
        self.page.select_task("daily_claim")
        self.routine.routine_task_configs["daily_claim"] = {"Option": False, "Count": 3}
        self.routine._enabled = False
        self.routine.running = False
        self.routine.task_status = {"daily_claim": "success"}
        self.routine.info = {"Result": "completed"}
        claim.info["Progress"] = "claimed"
        communicate.task.emit(self.routine)
        self.assertEqual(self.page.info_table.item(0, 1).text(), "claimed")
        self.assertEqual(self.page.rows["daily_claim"].status_label.text(), "Succeeded")
        self.assertFalse(self.page.task_form.config["Option"])
        self.assertIs(claim.config, normal)
        self.page.select_task(None)
        self.assertEqual(self.page.info_table.item(0, 1).text(), "completed")
        self.page.close_info_button.click()
        self.page._refresh_state()
        self.assertTrue(self.page.tabs.item("info").isHidden())
        self.assertIs(self.page.pages.currentWidget(), self.page.routine_scroll)
        self.assertEqual(self.routine.info["Result"], "completed")
        self.routine.info.clear()
        self.routine._enabled = True
        communicate.task.emit(self.routine)
        self.assertIs(self.page.pages.currentWidget(), self.page.info_table)
        self.assertEqual(self.page.info_table.rowCount(), 0)
        self.page.select_task("daily_claim")
        self.assertEqual(self.page.info_table.item(0, 1).text(), "claimed")
        self.assertEqual(self.page.rows["daily_claim"].status_label.text(), "Pending")

    def test_batch_queue_shows_only_checked_items_and_clears_on_cancel(self):
        def start(task):
            pending.append(task)
            controller.starting = True
            communicate.task.emit(task)

        def cancel(task):
            pending.remove(task)
            communicate.task.emit(task)

        pending = []
        controller = SimpleNamespace(
            start=start, is_task_pending=pending.__contains__, cancel_task=cancel, starting=False
        )
        app = SimpleNamespace(start_controller=controller, tr=lambda text: text)
        self.routine.disable = Mock()
        self.routine.unpause = Mock()
        self.routine.task_status = {"coffee": "success"}
        with patch.object(og, "app", app):
            self.page.start_button.click()

            self.assertEqual(self.page.rows["daily_claim"].status_label.text(), "Pending")
            self.assertEqual(self.page.rows["daily_anomaly"].status_label.text(), "Pending")
            self.assertTrue(self.page.rows["coffee"].status_label.isHidden())

            self.page.stop_button.click()
            controller.starting = False
            self.page._tick()

            self.assertTrue(all(row.status_label.isHidden() for row in self.page.rows.values()))
            self.assertFalse(self.page.start_button.isHidden())

    def test_detail_runs_selected_unchecked_item_and_locks_while_active(self):
        row = self.page.rows["coffee"]
        child = self.children["coffee"]
        child.info = {"Previous": "stale"}
        run_button = self.page.run_button
        self.assertTrue(run_button.isHidden())
        self.assertEqual(run_button.variant, "secondary")
        self.assertFalse(row.enabled_check.isChecked())
        pending = []

        def start_item(task):
            controller.starting = True
            pending.append(task)
            communicate.task.emit(task)

        controller = SimpleNamespace(
            start=Mock(side_effect=start_item), is_task_pending=pending.__contains__, starting=False
        )
        app = SimpleNamespace(start_controller=controller, tr=lambda text: text)
        with patch.object(og, "app", app):
            self.routine._enabled = True
            communicate.task.emit(self.routine)
            self.page.select_task("coffee")
            self.assertFalse(run_button.isHidden())
            self.assertFalse(run_button.isEnabled())
            run_button.click()
            controller.start.assert_not_called()

            self.routine._enabled = False
            self.routine.task_status = {"daily_claim": "success", "furniture": "skipped"}
            communicate.task.emit(self.routine)
            previous_result = self.page.rows["daily_claim"].status_label
            previous_skip = self.page.rows["furniture"].status_label
            self.assertEqual(previous_result.text(), "Succeeded")
            self.assertEqual(previous_skip.text(), "Skipped")
            self.page.tabs.item("settings").click()
            self.assertEqual(self.page.tabs.current_key(), "settings")
            run_button.click()
            controller.start.assert_called_once_with(self.routine)
            request = self.routine.single_task_request
            self.assertEqual(request.task_id, "coffee")
            self.assertEqual(row.status_label.text(), "Pending")
            self.assertTrue(previous_result.isHidden())
            self.assertTrue(previous_skip.isHidden())
            self.assertFalse(row.enabled_check.isChecked())
            self.assertFalse(run_button.isEnabled())
            self.assertFalse(self.page.stop_button.isHidden())
            self.assertEqual(self.page.selected_task_id, "coffee")
            self.assertIs(self.page.pages.currentWidget(), self.page.info_table)
            self.assertEqual(self.page.info_table.rowCount(), 0)

            pending.clear()
            self.page._refresh_state()
            self.assertTrue(self.page.start_button.isHidden())
            self.assertFalse(run_button.isEnabled())
            self.page.start_button.click()
            controller.start.assert_called_once_with(self.routine)
            self.assertIs(self.routine.single_task_request, request)

            self.routine.single_task_request = None
            self.routine._enabled = True
            self.routine.running = True
            controller.starting = False
            communicate.task.emit(self.routine)
            self.assertEqual(self.page.selected_task_id, "coffee")
            self.assertTrue(row.property("selected"))
            self.assertFalse(run_button.isHidden())
            self.assertFalse(run_button.isEnabled())
            self.assertIs(self.page.pages.currentWidget(), self.page.info_table)
            self.assertEqual(self.page.info_table.rowCount(), 0)

            self.routine.task_status["coffee"] = "running"
            child.info.clear()
            request.started = True
            child.info["Progress"] = "collecting"
            self.page._refresh_state()
            self.assertEqual(self.page.info_table.item(0, 1).text(), "collecting")
            self.assertEqual(row.status_label.text(), "Running")
            self.assertTrue(previous_result.isHidden())
            self.assertTrue(previous_skip.isHidden())

            self.routine._enabled = False
            self.routine.running = False
            self.routine.task_status["coffee"] = "success"
            communicate.task.emit(self.routine)
            self.assertEqual(self.page.selected_task_id, "coffee")
            self.assertTrue(run_button.isEnabled())
            self.assertEqual(row.status_label.text(), "Succeeded")
            self.assertTrue(previous_result.isHidden())
            self.assertTrue(previous_skip.isHidden())

            self.routine._enabled = True
            communicate.task.emit(self.routine)
            self.assertIsNone(self.page.selected_task_id)
            self.assertTrue(self.page.routine_row.property("selected"))
            self.assertEqual(previous_result.text(), "Pending")
            self.assertTrue(row.status_label.isHidden())

    def test_detail_run_is_disabled_while_selected_child_is_active_elsewhere(self):
        child = self.children["coffee"]
        pending = []
        controller = SimpleNamespace(start=Mock(), is_task_pending=pending.__contains__)
        app = SimpleNamespace(start_controller=controller, tr=lambda text: text)
        with patch.object(og, "app", app):
            self.page.select_task("coffee")
            self.assertTrue(self.page.run_button.isEnabled())
            for enabled, running, launching in (
                (True, False, False),
                (False, True, False),
                (False, False, True),
            ):
                with self.subTest(enabled=enabled, running=running, launching=launching):
                    child.enabled, child.running = enabled, running
                    pending[:] = [child] if launching else []
                    self.page._refresh_state()
                    self.assertFalse(self.page.run_button.isEnabled())
                    self.page.run_button.click()
                    controller.start.assert_not_called()

            pending.clear()
            self.page._refresh_state()
            self.assertTrue(self.page.run_button.isEnabled())

    def test_stop_cancels_single_item_even_after_controller_drops_pending_entry(self):
        controller = SimpleNamespace(start=Mock(), starting=True)
        app = SimpleNamespace(start_controller=controller, tr=lambda text: text)
        self.routine.disable = Mock()
        self.routine.unpause = Mock()
        with patch.object(og, "app", app):
            self.page.select_task("coffee")
            self.page.run_button.click()
            request = self.routine.single_task_request
            self.page.stop_button.click()
            self.assertTrue(request.cancelled)
            self.assertIs(self.routine.single_task_request, request)

            controller.starting = False
            self.page._refresh_state()
            self.assertIsNone(self.routine.single_task_request)
            self.assertTrue(self.page.run_button.isEnabled())
            self.assertTrue(self.page.rows["coffee"].status_label.isHidden())

    def test_routine_reset_restores_order_and_selection_without_resetting_daily_config(self):
        self.assertTrue(self.page.routine_row.property("selected"))
        self.assertIs(self.page.pages.currentWidget(), self.page.routine_scroll)
        self.page.select_task("daily_claim")
        self.assertIs(self.page.pages.currentWidget(), self.page.task_scroll)
        self.page.task_form.config_widget_by_key["Option"].switch_button.click()
        self.page._set_all_selected(False)
        row = self.page.routine_row
        center = row.rect().center()
        self._mouse(
            row,
            QEvent.Type.MouseButtonRelease,
            center,
            row.mapToGlobal(center),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
        )
        self.assertIsNone(self.page.selected_task_id)
        self.assertIs(self.page.pages.currentWidget(), self.page.routine_scroll)
        self.assertIs(self.page.routine_form.config, self.routine.config)
        self.page.reset_button.click()
        self.assertEqual(self.routine.normalize_items(), self.routine.default_items())
        self.assertFalse(self.routine.routine_task_configs["daily_claim"]["Option"])

    def test_late_executor_injection_and_empty_availability(self):
        self.page.executor = None
        self.assertFalse(self.page.rows)
        self.assertTrue(self.page.routine_row.isHidden())
        self.assertFalse(self.page.start_button.isEnabled())
        self.children.clear()
        self.page.executor = SimpleNamespace(get_task_by_class=lambda cls: self.routine)
        self.assertFalse(self.page.rows)
        self.assertFalse(self.page.routine_row.isHidden())
        self.assertIsNone(self.page.selected_task_id)
        self.assertFalse(self.page.select_all_check.isEnabled())
        self.assertFalse(self.page.start_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
