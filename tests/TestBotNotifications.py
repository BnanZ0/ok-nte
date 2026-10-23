import unittest
from threading import Thread
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from ok import og
from ok.core.events import EventSignal
from ok.core.notifications import alert_info
from ok.ui.qt.MainWindow import MainWindow

from src.events import BotNotificationMessage, communicate
from src.globals import _install_bot_notification_filter
from src.tasks.BaseNTETask import BaseNTETask


class TestBotNotifications(unittest.TestCase):
    def setUp(self):
        self.external_submit = Mock()
        self.manager = SimpleNamespace(submit=self.external_submit, notify_system=Mock())
        self.window = SimpleNamespace(
            notification_manager=self.manager,
            destroyed=EventSignal("destroyed"),
            window=lambda: None,
            navigate_tab=Mock(),
        )
        self.window.show_notification = MethodType(MainWindow.show_notification, self.window)
        communicate.notification.connect(self.window.show_notification)
        _install_bot_notification_filter(self.window)
        self.addCleanup(self.window.destroyed.emit)
        self.popup = self.enterContext(patch("ok.ui.qt.util.app.show_info_bar"))
        self.enterContext(patch.object(og, "app", SimpleNamespace(tr=lambda text: text)))
        self.enterContext(patch("ok.core.events._dispatcher", None))
        self.task = object.__new__(BaseNTETask)
        self.task.logger = Mock()
        self.task.info = {}
        self.task._executor = SimpleNamespace(nullable_frame=lambda: None)

    def test_framework_and_task_notifications_stay_local(self):
        alert_info("framework notification", tray=True)
        self.task.log_info("info", notify=True)
        self.task.log_warning("warning", notify=True)
        self.task.log_error("error", notify=True)

        self.external_submit.assert_not_called()
        self.assertEqual(self.popup.call_count, 4)
        self.assertEqual(self.manager.notify_system.call_count, 4)
        self.manager.notify_system.assert_any_call("", "error", True, True)

    def test_custom_bar_uses_the_ui_thread_without_replacing_framework_notifications(self):
        from ok.ui.qt.events import QtEventDispatcher
        from PySide6.QtCore import QCoreApplication, QEvent, QThread
        from PySide6.QtWidgets import QApplication, QWidget

        from src.ui.foundation.info_bar import install_info_bar_handler

        type(self)._qt_app = QApplication.instance() or QApplication([])
        app = type(self)._qt_app
        parent = QWidget()
        threads = []
        forwarded = []
        dispatcher = QtEventDispatcher()
        with (
            patch("ok.core.events._dispatcher", dispatcher.dispatch),
            communicate.any.subscribed(forwarded.append),
            patch(
                "src.ui.foundation.info_bar.InfoBar.error",
                side_effect=lambda **kwargs: threads.append(QThread.currentThread()),
            ) as error_bar,
            patch("src.ui.foundation.info_bar.InfoBar.success") as success_bar,
        ):
            install_info_bar_handler(parent)
            install_info_bar_handler(parent)
            self.addCleanup(communicate.info_bar_requested.disconnect, parent._info_bar_handler)
            worker = Thread(
                target=communicate.info_bar_requested.emit,
                kwargs={"title": "External Code", "content": "external error", "duration": -1},
            )
            worker.start()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            error_bar.assert_not_called()
            app.processEvents()
            error_bar.assert_called_once()
            self.assertEqual(threads, [app.thread()])
            self.assertEqual(error_bar.call_args.kwargs["duration"], -1)
            self.assertTrue(error_bar.call_args.kwargs["isClosable"])
            self.assertIs(error_bar.call_args.kwargs["parent"], parent)
            events = [event for event in forwarded if event.name == "info_bar_requested"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].kwargs["duration"], -1)
            self.popup.assert_not_called()
            self.task.log_error("ordinary error", notify=True)
            self.popup.assert_called_once_with(None, "ordinary error", "", True)

            communicate.info_bar_requested.emit(
                title="Saved", content="custom duration", success=True, duration=12000
            )
            app.processEvents()
            self.assertEqual(success_bar.call_args.kwargs["duration"], 12000)
            pending = []
            with patch(
                "ok.core.events._dispatcher", side_effect=lambda *args: pending.append(args)
            ):
                communicate.info_bar_requested.emit(title="Queued", content="before destruction")
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            for callback, args, kwargs in pending:
                callback(*args, **kwargs)
            communicate.info_bar_requested.emit(title="Closed", content="after destruction")
            app.processEvents()
            error_bar.assert_called_once()

        self.external_submit.assert_not_called()

    def test_queued_bot_log_sends_original_text_once(self):
        pending = []
        message = "success: ['daily']\nfailed: []\nskipped: []"
        with patch("ok.core.events._dispatcher", side_effect=lambda *args: pending.append(args)):
            self.task.log_bot_info(message)
            alert_info(message, tray=True)
        for callback, args, kwargs in pending:
            callback(*args, **kwargs)

        self.assertEqual(self.task.info["Log"], message)
        self.popup.assert_any_call(None, message, "", False)
        self.manager.notify_system.assert_any_call("", message, False, True)
        self.external_submit.assert_called_once_with("", message, [])

    def test_forwards_changed_sender_parameters_without_constructing_them(self):
        sender = Mock()
        window = SimpleNamespace(
            notification_manager=SimpleNamespace(submit=sender),
            destroyed=EventSignal("destroyed"),
        )

        def future_show(_window, message, *, priority):
            return window.notification_manager.submit(payload=str(message), priority=priority)

        window.show_notification = MethodType(future_show, window)
        _install_bot_notification_filter(window)
        self.addCleanup(window.destroyed.emit)

        result = window.show_notification(
            message=BotNotificationMessage("result"), priority="high"
        )

        sender.assert_called_once_with(payload="result", priority="high")
        self.assertIs(result, sender.return_value)

    def test_nested_notification_and_exception_do_not_leak_permission(self):
        def nested_popup(_window, message, *_):
            if message == "bot result":
                self.window.show_notification("ordinary nested notification", tray=True)

        self.popup.side_effect = nested_popup
        self.window.show_notification(BotNotificationMessage("bot result"), tray=True)
        self.external_submit.assert_called_once_with("", "bot result", None)

        self.window.navigate_tab.side_effect = RuntimeError("notification failed")
        with self.assertRaises(RuntimeError):
            self.window.show_notification(BotNotificationMessage("another result"))
        self.manager.submit("", "ordinary notification after error")

        self.assertEqual(self.external_submit.call_count, 2)

    def test_reinstall_and_window_destruction_do_not_leave_duplicate_handlers(self):
        _install_bot_notification_filter(self.window)
        self.task.log_bot_info("result")
        self.external_submit.assert_called_once_with("", "result", [])
        self.window.destroyed.emit()

        self.task.log_bot_info("after destruction")

        self.assertEqual(self.external_submit.call_count, 1)

