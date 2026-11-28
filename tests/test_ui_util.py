import threading
import unittest
from unittest.mock import patch

import shiboken6
from ok.core.events import EventSignal
from ok.ui.qt.kit import Dialog, theme
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionFrame,
    QVBoxLayout,
    QWidget,
)

from src.events import ConfirmationRequested
from src.ui.foundation import dialogs
from src.ui.foundation.name_input_dialog import NameInputDialog
from src.ui.foundation.widgets.help_popover import HelpPopover
from src.ui.foundation.widgets.search import SearchableComboBox, SearchableListWidget
from tests.support.qt_events import send_key


class _Countdown:
    seconds = 2

    def expired(self):
        return self.seconds <= 0

    def remaining(self):
        return max(0, self.seconds)


class HelpPopoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_help_popover_stays_anchored_inside_resized_window(self):
        window = QWidget()
        self.addCleanup(shiboken6.delete, window)
        window.resize(1200, 800)
        anchor = QWidget(window)
        anchor.setGeometry(1000, 400, 30, 22)
        window.show()
        popover = HelpPopover(window, "Help", "<b>Configuration</b><br>Choose a team preset.")
        self.addCleanup(popover.close_popover)
        popover.toggle_for(anchor)
        self.assertGreater(popover.y(), anchor.geometry().bottom())
        self.assertLess(popover.width(), window.width() // 3)

        window.resize(600, 450)
        anchor.move(540, 420)
        self.app.processEvents()

        self.assertTrue(popover.isVisible())
        self.assertTrue(window.rect().adjusted(8, 8, -8, -8).contains(popover.geometry()))
        self.assertLess(popover.geometry().bottom(), anchor.y())


class DialogBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parent = QWidget()
        self.parent.resize(800, 600)
        self.parent.show()

    def test_name_form_uses_project_gettext_translation(self):
        from src.ui.foundation import i18n

        with patch.object(i18n.og, "app") as app:
            app.tr.side_effect = lambda text: f"localized:{text}"
            dialog = NameInputDialog(title="Rename", parent=self.parent)
            self.assertEqual(dialog.title_label.text(), "localized:Rename")
            self.assertEqual(dialog.ok_button.text(), "localized:OK")
            shiboken6.delete(dialog)

    def tearDown(self):
        if shiboken6.isValid(self.parent):
            shiboken6.delete(self.parent)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_blocking_wait_reports_accept_and_reject(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                dialog = Dialog(self.parent, "Confirm")
                QTimer.singleShot(0, dialog.accept if accepted else dialog.reject)
                self.assertEqual(dialogs.exec_dialog(dialog), accepted)
                self.assertFalse(dialog.isVisible())

    def test_footer_actions_fill_available_width(self):
        for cancel_visible, ok_visible in ((True, True), (False, True), (True, False)):
            with self.subTest(cancel_visible=cancel_visible, ok_visible=ok_visible):
                dialog = Dialog(self.parent, "Confirm")
                dialog.set_cancel_visible(cancel_visible)
                dialog.ok_button.setVisible(ok_visible)
                geometry = {}

                def capture():
                    self.app.processEvents()
                    panel_layout = dialog.panel.layout()
                    footer = panel_layout.itemAt(panel_layout.count() - 1).layout()
                    geometry["row"] = footer.geometry()
                    geometry["buttons"] = [
                        button.geometry()
                        for button in (dialog.cancel_button, dialog.ok_button)
                        if not button.isHidden()
                    ]
                    geometry["spacing"] = footer.spacing()
                    dialog.reject()

                QTimer.singleShot(0, capture)
                self.assertFalse(dialogs.exec_dialog(dialog))
                row = geometry["row"]
                buttons = geometry["buttons"]
                self.assertEqual(buttons[0].x(), row.x())
                self.assertEqual(buttons[-1].x() + buttons[-1].width(), row.x() + row.width())
                if len(buttons) == 2:
                    gap = buttons[1].x() - buttons[0].x() - buttons[0].width()
                    self.assertEqual(gap, geometry["spacing"])
                    self.assertLessEqual(abs(buttons[0].width() - buttons[1].width()), 1)
                else:
                    self.assertEqual(buttons[0].width(), row.width())

    def test_blocking_wait_ends_when_parent_is_destroyed(self):
        dialog = Dialog(self.parent, "Confirm")
        QTimer.singleShot(0, self.parent.deleteLater)
        self.assertFalse(dialogs.exec_dialog(dialog))
        self.assertFalse(shiboken6.isValid(dialog))

    def test_blocking_wait_ends_when_application_quits(self):
        dialog = Dialog(self.parent, "Confirm")
        QTimer.singleShot(0, self.app.aboutToQuit.emit)
        self.assertFalse(dialogs.exec_dialog(dialog))
        self.assertFalse(dialog.isVisible())

    def test_open_failure_is_unconfirmed(self):
        dialog = Dialog(self.parent, "Confirm")
        with (
            patch.object(dialog, "open", side_effect=RuntimeError("opening failed")),
            patch.object(dialogs.logger, "error"),
        ):
            self.assertFalse(dialogs.exec_dialog(dialog))

    def test_request_owner_destruction_ends_wait_even_while_window_survives(self):
        owner = QWidget(self.parent)
        original_create = dialogs._create_dialog

        def create(*args, **kwargs):
            dialog = original_create(*args, **kwargs)
            QTimer.singleShot(0, owner.deleteLater)
            return dialog

        with patch.object(dialogs, "_create_dialog", side_effect=create):
            self.assertFalse(dialogs.show_dialog_and_wait("Confirm", "Message", parent=owner))
        self.assertTrue(shiboken6.isValid(self.parent))

    def test_explicit_exec_owner_destruction_ends_wait_while_window_survives(self):
        owner = QWidget(self.parent)
        dialog = Dialog(owner, "Confirm")
        self.assertIs(dialog.parentWidget(), self.parent)
        QTimer.singleShot(0, owner.deleteLater)
        self.assertFalse(dialogs.exec_dialog(dialog, owner=owner))
        self.assertTrue(shiboken6.isValid(self.parent))
        self.assertTrue(shiboken6.isValid(dialog))
        self.assertFalse(dialog.isVisible())

    def test_queued_confirmation_is_canceled_on_destroy_without_reemission(self):
        signal = EventSignal("confirmation_requested")
        old_request = ConfirmationRequested("Old confirmation", "Message")
        new_request = ConfirmationRequested("New confirmation", "Message")

        def schedule(callback, args, kwargs):
            QTimer.singleShot(0, lambda: callback(*args, **kwargs))

        with (
            patch.object(dialogs.communicate, "confirmation_requested", signal),
            patch("ok.core.events._dispatcher", schedule),
            patch.object(dialogs, "show_dialog_and_wait", return_value=True) as show,
            patch.object(old_request, "resolve", wraps=old_request.resolve) as old_resolve,
            patch.object(new_request, "resolve", wraps=new_request.resolve) as new_resolve,
        ):
            stale_handler = dialogs.install_confirmation_handler(self.parent)
            self.assertIs(dialogs.install_confirmation_handler(self.parent), stale_handler)
            signal.emit(old_request)
            shiboken6.delete(self.parent)
            self.parent = QWidget()
            handler = dialogs.install_confirmation_handler(self.parent)
            self.assertIs(dialogs.install_confirmation_handler(self.parent), handler)
            self.app.processEvents()
            old_resolve.assert_called_once_with(False)
            self.assertFalse(old_request._response.result(timeout=0))
            show.assert_not_called()
            signal.emit(new_request)
            self.app.processEvents()
            new_resolve.assert_called_once_with(True)
            self.assertTrue(new_request._response.result(timeout=0))
            show.assert_called_once()
            self.assertIs(show.call_args.kwargs["parent"], self.parent)
            shiboken6.delete(self.parent)

    def test_install_retires_previous_handler_even_while_old_window_lives(self):
        signal = EventSignal("confirmation_requested")
        replacement = QWidget()
        old_request = ConfirmationRequested("Old confirmation", "Message")
        new_request = ConfirmationRequested("New confirmation", "Message")
        try:
            with (
                patch.object(dialogs.communicate, "confirmation_requested", signal),
                patch.object(dialogs, "show_dialog_and_wait", return_value=True) as show,
                patch.object(new_request, "resolve", wraps=new_request.resolve) as resolve,
            ):
                old_handler = dialogs.install_confirmation_handler(self.parent)
                new_handler = dialogs.install_confirmation_handler(replacement)
                self.assertIs(dialogs.install_confirmation_handler(replacement), new_handler)
                old_handler(old_request)
                self.assertFalse(old_request._response.result(timeout=0))
                signal.emit(new_request)
                resolve.assert_called_once_with(True)
                show.assert_called_once()
                self.assertIs(show.call_args.kwargs["parent"], replacement)
        finally:
            shiboken6.delete(replacement)

    def test_missing_application_or_parent_is_unconfirmed(self):
        self.assertFalse(dialogs.show_dialog_and_wait("Confirm", "Message"))
        with patch.object(dialogs.QApplication, "instance", return_value=None):
            self.assertFalse(dialogs.show_dialog_and_wait("Confirm", "Message", parent=self.parent))

    def test_hidden_cancel_escape_and_direct_accept_obey_clock_countdown(self):
        countdown = _Countdown()
        dialog = dialogs._create_dialog(
            "Confirm", "Warning", parent=self.parent, close_delay_seconds=2
        )
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(True))
        with patch.object(dialogs.clock, "timer", return_value=countdown):
            dialog.open()
            self.assertFalse(dialog.ok_button.isEnabled())
            dialog.accept()
            send_key(dialog, Qt.Key.Key_Escape)
            send_key(dialog, Qt.Key.Key_Return)
            self.assertTrue(dialog.isVisible())
            self.assertEqual(accepted, [])
            countdown.seconds = 0
            dialog._refresh_countdown()
            self.assertTrue(dialog.ok_button.isEnabled())
            dialog.ok_button.setEnabled(False)
            dialog.accept()
            self.assertEqual(accepted, [])
            dialog.ok_button.setEnabled(True)
            send_key(dialog, Qt.Key.Key_Escape)
        self.assertEqual(accepted, [True])

    def test_visible_cancel_can_reject_during_countdown(self):
        dialog = dialogs._create_dialog(
            "Confirm", "Warning", parent=self.parent, hide_cancel=False, close_delay_seconds=2
        )
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))
        dialog.open()
        self.assertFalse(dialog.ok_button.isEnabled())
        dialog.cancel_button.click()
        self.assertEqual(rejected, [True])

    def test_name_validation_also_gates_hidden_cancel_escape(self):
        dialog = NameInputDialog(
            "",
            parent=self.parent,
            name_validator=lambda value: value != "reserved",
            error_message="Unavailable",
        )
        dialog.set_cancel_visible(False)
        dialog.open()
        dialog.name_edit.setText(" reserved ")
        self.assertFalse(dialog.ok_button.isEnabled())
        send_key(dialog, Qt.Key.Key_Escape)
        self.assertTrue(dialog.isVisible())
        dialog.name_edit.setText(" valid ")
        self.assertTrue(dialog.ok_button.isEnabled())
        dialog.accept()
        self.assertEqual(dialog.get_name(), "valid")
        self.assertFalse(dialog.isVisible())

    def test_name_validation_message_fits_below_the_field(self):
        dialog = NameInputDialog(
            "available",
            parent=self.parent,
            name_validator=lambda value: value != "reserved",
            error_message="That name is already in use.",
        )
        dialog.open()
        self.app.processEvents()
        initial_height = dialog.panel.height()

        dialog.name_edit.setText("reserved")
        self.app.processEvents()
        self.assertTrue(dialog.name_error_label.isVisible())
        self.assertGreater(dialog.name_error_label.y(), dialog.name_edit.geometry().bottom())
        self.assertGreater(dialog.ok_button.y(), dialog.name_error_label.geometry().bottom())

        dialog.name_edit.setText("available")
        self.app.processEvents()
        self.assertFalse(dialog.name_error_label.isVisible())
        self.assertEqual(dialog.panel.height(), initial_height)

    def _run_worker_confirmation(self, owner, create_dialog):
        result = []
        dispatcher = dialogs._DialogDispatcher(self.app)
        loop = QEventLoop()
        poll = QTimer(loop)
        poll.setInterval(5)
        poll.timeout.connect(lambda: loop.quit() if result else None)
        watchdog = QTimer(loop)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        worker = threading.Thread(
            target=lambda: result.append(
                dialogs.show_dialog_and_wait("Confirm", "Message", parent=owner)
            ),
            daemon=True,
        )
        try:
            with (
                patch.object(dialogs, "_dialog_dispatcher", dispatcher),
                patch.object(dialogs, "_create_dialog", side_effect=create_dialog),
            ):
                poll.start()
                watchdog.start(2000)
                worker.start()
                loop.exec()
                worker.join(timeout=0.1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(dispatcher._pending, {})
            self.assertEqual(dispatcher._dialogs, {})
            return result
        finally:
            poll.stop()
            watchdog.stop()
            dispatcher._shutdown()
            if worker.is_alive():
                worker.join(timeout=0.1)
            shiboken6.delete(dispatcher)

    def test_worker_confirmation_is_built_on_ui_thread(self):
        created_threads = []
        original_create = dialogs._create_dialog

        def create(*args, **kwargs):
            created_threads.append(QThread.currentThread())
            dialog = original_create(*args, **kwargs)
            QTimer.singleShot(0, dialog.accept)
            return dialog

        self.assertEqual(self._run_worker_confirmation(self.parent, create), [True])
        self.assertEqual(created_threads, [self.app.thread()])

    def test_worker_wait_ends_on_owner_deletion_quit_and_creation_failure(self):
        original_create = dialogs._create_dialog
        for outcome in ("owner_destroyed", "quit", "error"):
            with self.subTest(outcome=outcome), patch.object(dialogs.logger, "error"):
                owner = QWidget(self.parent)

                def create(*args, **kwargs):
                    if outcome == "error":
                        raise RuntimeError("creation failed")
                    dialog = original_create(*args, **kwargs)
                    callback = (
                        owner.deleteLater
                        if outcome == "owner_destroyed"
                        else self.app.aboutToQuit.emit
                    )
                    QTimer.singleShot(0, callback)
                    return dialog

                self.assertEqual(self._run_worker_confirmation(owner, create), [False])

    def test_accept_then_reject_and_shutdown_resolve_exactly_once(self):
        dispatcher = dialogs._DialogDispatcher(self.app)
        request = dialogs._DialogRequest(
            {"title": "Confirm", "content": "Message", "parent": self.parent}
        )
        try:
            with patch.object(request.event, "set", wraps=request.event.set) as finish:
                dispatcher.submit(request)
                dialog = dispatcher._dialogs[id(request)][0]
                dialog.accept()
                dialog.reject()
                dispatcher._shutdown()
            self.assertTrue(request.accepted)
            self.assertTrue(request.event.is_set())
            finish.assert_called_once_with()
        finally:
            shiboken6.delete(dispatcher)

    def test_shutdown_resolves_request_before_queued_dialog_is_created(self):
        dispatcher = dialogs._DialogDispatcher(self.app)
        request = dialogs._DialogRequest(
            {"title": "Confirm", "content": "Message", "parent": self.parent}
        )
        worker = threading.Thread(target=lambda: dispatcher.submit(request), daemon=True)
        try:
            with patch.object(dialogs, "_create_dialog") as create:
                worker.start()
                worker.join(timeout=0.1)
                self.assertFalse(worker.is_alive())
                self.assertFalse(request.event.is_set())
                dispatcher._shutdown()
                self.app.processEvents()
                self.assertTrue(request.event.is_set())
                self.assertFalse(request.accepted)
                create.assert_not_called()
        finally:
            shiboken6.delete(dispatcher)

    def test_worker_without_ui_initialized_dispatcher_is_unconfirmed(self):
        result = []
        worker = threading.Thread(
            target=lambda: result.append(
                dialogs.show_dialog_and_wait("Confirm", "Message", parent=self.parent)
            ),
            daemon=True,
        )
        with patch.object(dialogs, "_dialog_dispatcher", None):
            worker.start()
            worker.join(timeout=0.1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [False])
            self.assertIsNone(dialogs._dialog_dispatcher)


class SearchBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_combo_completion_tracks_bulk_add_rename_remove_and_clear(self):
        combo = SearchableComboBox()
        try:
            combo.addItem("Alpha", {"id": 1})
            combo.addItems(["Beta", "Gamma"])
            self.assertEqual(combo.itemData(0), {"id": 1})
            completer = combo.completer()
            completer.setCompletionPrefix("AM")
            self.assertEqual(completer.completionCount(), 1)
            self.assertEqual(completer.currentCompletion(), "Gamma")
            combo.setItemText(2, "Delta")
            completer.setCompletionPrefix("AM")
            self.assertEqual(completer.completionCount(), 0)
            combo.removeItem(1)
            completer.setCompletionPrefix("ta")
            self.assertEqual(completer.completionCount(), 1)
            combo.clear()
            self.assertEqual(completer.completionCount(), 0)
        finally:
            shiboken6.delete(combo)

    def test_combo_line_edit_fits_themed_edit_field_without_native_inset(self):
        container = QWidget()
        self.addCleanup(shiboken6.delete, container)
        theme.install(container)
        self.addCleanup(theme.uninstall, container)

        layout = QVBoxLayout(container)
        combo = SearchableComboBox(container)
        combo.addItem("Alpha")
        layout.addWidget(combo)

        container.resize(320, 80)
        container.show()
        self.app.processEvents()

        line_edit = combo.lineEdit()
        self.assertIsNotNone(line_edit)

        combo_option = QStyleOptionComboBox()
        combo.initStyleOption(combo_option)
        edit_field = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            combo_option,
            QStyle.SubControl.SC_ComboBoxEditField,
            combo,
        )
        self.assertTrue(edit_field.contains(line_edit.geometry()))

        edit_option = QStyleOptionFrame()
        line_edit.initStyleOption(edit_option)
        content_rect = line_edit.style().subElementRect(
            QStyle.SubElement.SE_LineEditContents,
            edit_option,
            line_edit,
        )
        self.assertEqual(content_rect, line_edit.rect())

    def test_list_filter_preserves_items_and_can_be_reapplied_after_rebuild(self):
        search = SearchableListWidget()
        try:
            search.list_widget.addItems(["Alpha", "Beta"])
            search.search_edit.setText(" ALP ")
            self.assertFalse(search.list_widget.item(0).isHidden())
            self.assertTrue(search.list_widget.item(1).isHidden())
            search.list_widget.clear()
            search.list_widget.addItems(["Gamma", "Alphabet"])
            search.reapply_filter()
            self.assertTrue(search.list_widget.item(0).isHidden())
            self.assertFalse(search.list_widget.item(1).isHidden())
            search.search_edit.clear()
            self.assertEqual(search.list_widget.count(), 2)
            self.assertFalse(search.list_widget.item(0).isHidden())
        finally:
            shiboken6.delete(search)


if __name__ == "__main__":
    unittest.main()
