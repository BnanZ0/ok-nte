"""Project confirmations and lifecycle-safe waiting for kit dialogs."""

import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

import shiboken6
from ok import Logger, og
from ok.ui.qt.kit import Dialog
from ok.ui.qt.kit.theme import set_style_property
from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy, QWidget

from src.events import ConfirmationRequested, communicate
from src.utils import clock

logger = Logger.get_logger(__name__)
_SHOW_DIALOG_FAILED = "show dialog failed"


class FormDialog(Dialog):
    """Keep the panel fitted when inline feedback changes the form layout."""

    def __init__(self, parent: QWidget, title: str, *, width: int):
        super().__init__(parent, title, width=width)
        self.panel.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.LayoutRequest and watched is self.panel and self.isVisible():
            self._fit()
        return super().eventFilter(watched, event)


def _valid(widget) -> bool:
    return widget is not None and shiboken6.isValid(widget)


def _tr(text: str) -> str:
    app = getattr(og, "app", None)
    return app.tr(text) if app is not None and hasattr(app, "tr") else text


def _fill_dialog_footer(dialog: Dialog) -> None:
    """Share the panel footer width between its visible action buttons."""
    panel_layout = dialog.panel.layout()
    for index in range(panel_layout.count()):
        footer = panel_layout.itemAt(index).layout()
        if not isinstance(footer, QHBoxLayout):
            continue
        if footer.indexOf(dialog.ok_button) < 0 or footer.indexOf(dialog.cancel_button) < 0:
            continue

        leading = footer.itemAt(0).spacerItem()
        if leading is not None:
            leading.changeSize(0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            footer.setStretch(0, 0)
        for action_index in range(footer.count()):
            action = footer.itemAt(action_index).widget()
            if action is None:
                continue
            policy = action.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
            action.setSizePolicy(policy)
            footer.setStretch(action_index, 1)
        footer.invalidate()
        return


class _DialogLifetime:
    """Resolve once, including when Qt tears down a modal dialog's owner."""

    def __init__(self, dialog, parent, app, complete):
        self.done = False
        self._complete = complete
        self._connections = []
        self._connect(dialog.accepted, lambda: self.finish(True))
        self._connect(dialog.rejected, lambda: self.finish(False))
        self._connect(dialog.destroyed, lambda *_: self.finish(False))
        self._connect(parent.destroyed, lambda *_: self.finish(False))
        if dialog.parentWidget() is not parent:
            self._connect(dialog.parentWidget().destroyed, lambda *_: self.finish(False))
        self._connect(app.aboutToQuit, lambda: self.finish(False))
        self._connect(app.destroyed, lambda *_: self.finish(False))

    def _connect(self, signal, callback):
        signal.connect(callback)
        self._connections.append((signal, callback))

    def finish(self, accepted: bool):
        if not self.done:
            self.done = True
            self._complete(accepted)

    def disconnect(self):
        for signal, callback in self._connections:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        self._connections.clear()


def exec_dialog(dialog: Dialog, *, owner: QWidget | None = None) -> bool:
    """Open on the UI thread and wait through public signals.

    The caller owns the dialog. Use exec_owned_dialog for a one-shot dialog.
    Pass the original page as ``owner`` when the kit has reparented the dialog
    to its window. Missing application/owner, teardown and errors are unconfirmed.
    """
    if owner is None:
        owner = dialog.parentWidget() if _valid(dialog) else None
    return _wait_for_dialog(dialog, owner)


@contextmanager
def exec_owned_dialog(dialog: Dialog, *, owner: QWidget | None = None):
    """Yield the result before releasing a one-shot dialog and its children."""
    try:
        yield exec_dialog(dialog, owner=owner)
    finally:
        if _valid(dialog):
            dialog.deleteLater()


def _wait_for_dialog(dialog: Dialog, owner: QWidget) -> bool:
    app = QApplication.instance()
    if (
        not _valid(app)
        or app.closingDown()
        or QThread.currentThread() != app.thread()
        or not _valid(dialog)
        or not _valid(dialog.parentWidget())
        or not _valid(owner)
    ):
        return False
    loop = QEventLoop()
    accepted = False

    def complete(value):
        nonlocal accepted
        accepted = value
        loop.quit()

    lifetime = _DialogLifetime(dialog, owner, app, complete)
    try:
        _fill_dialog_footer(dialog)
        dialog.open()
        if not lifetime.done:
            loop.exec()
    except Exception as error:
        logger.error(_SHOW_DIALOG_FAILED, error)
        lifetime.finish(False)
    finally:
        lifetime.disconnect()
        if _valid(dialog):
            dialog.hide()
    return accepted


class _TimedConfirmationDialog(Dialog):
    """Gate every acceptance path until the warning has been visible."""

    def __init__(self, parent, title, content, seconds, *, width=440):
        super().__init__(
            parent, title, content, ok_text=_tr("OK"), cancel_text=_tr("Cancel"), width=width
        )
        self._seconds = max(0.0, float(seconds))
        self._countdown = None
        self._ok_text = self.ok_button.text()
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh_countdown)
        self.accepted.connect(self._timer.stop)
        self.rejected.connect(self._timer.stop)

    def open(self):
        self._countdown = clock.timer(timeout=self._seconds)
        self._refresh_countdown()
        super().open()
        if not self._countdown.expired():
            self._timer.start()

    def validate(self) -> bool:
        return bool(
            self._countdown is not None and self._countdown.expired() and self.ok_button.isEnabled()
        )

    def _refresh_countdown(self):
        ready = self._countdown is not None and self._countdown.expired()
        self.ok_button.setEnabled(ready)
        remaining = math.ceil(self._countdown.remaining()) if self._countdown else 0
        self.ok_button.setText(self._ok_text if ready else f"{self._ok_text} ({remaining})")
        if ready:
            self._timer.stop()


def _create_dialog(
    title: str,
    content: str,
    *,
    parent: QWidget,
    copyable: bool = True,
    rich_text: bool = True,
    open_external_links: bool = True,
    hide_cancel: bool = True,
    close_delay_seconds: float = 0,
    width: int = 440,
    message_role: str = "secondary",
) -> Dialog:
    if close_delay_seconds > 0:
        dialog = _TimedConfirmationDialog(parent, title, content, close_delay_seconds, width=width)
    else:
        dialog = Dialog(
            parent, title, content, ok_text=_tr("OK"), cancel_text=_tr("Cancel"), width=width
        )
    dialog.set_cancel_visible(not hide_cancel)
    label = dialog.message_label
    set_style_property(label, "role", message_role)
    label.setTextFormat(Qt.TextFormat.RichText if rich_text else Qt.TextFormat.PlainText)
    flags = Qt.TextInteractionFlag.NoTextInteraction
    if copyable:
        flags |= Qt.TextInteractionFlag.TextSelectableByMouse
        flags |= Qt.TextInteractionFlag.TextSelectableByKeyboard
    if open_external_links:
        flags |= Qt.TextInteractionFlag.LinksAccessibleByMouse
        flags |= Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    label.setTextInteractionFlags(flags)
    label.setOpenExternalLinks(open_external_links)
    return dialog


@dataclass
class _DialogRequest:
    options: dict
    event: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False


class _DialogDispatcher(QObject):
    show_requested = Signal(object)

    def __init__(self, app):
        super().__init__()
        self._lock = threading.Lock()
        self._pending = {}
        self._dialogs = {}
        self._stopping = False
        self.moveToThread(app.thread())
        self.show_requested.connect(self._show_dialog)
        app.aboutToQuit.connect(self._shutdown)
        app.destroyed.connect(self._shutdown)

    def submit(self, request):
        with self._lock:
            if self._stopping:
                request.event.set()
                return
            self._pending[id(request)] = request
        try:
            self.show_requested.emit(request)
        except RuntimeError:
            with self._lock:
                self._pending.pop(id(request), None)
                request.event.set()

    def _finish(self, request, accepted=False):
        with self._lock:
            if self._pending.pop(id(request), None) is None:
                return
            request.accepted = accepted
            request.event.set()
        active = self._dialogs.pop(id(request), None)
        if active is not None:
            dialog, lifetime = active
            lifetime.disconnect()
            if _valid(dialog):
                dialog.hide()
                dialog.deleteLater()

    @Slot()
    def _shutdown(self, *_):
        with self._lock:
            self._stopping = True
            pending = list(self._pending.values())
        for request in pending:
            self._finish(request)

    @Slot(object)
    def _show_dialog(self, request):
        if request.event.is_set():
            return
        dialog = None
        try:
            app = QApplication.instance()
            parent = request.options["parent"]
            if not _valid(app) or app.closingDown() or not _valid(parent):
                self._finish(request)
                return
            dialog = _create_dialog(**request.options)
            lifetime = _DialogLifetime(
                dialog, parent, app, lambda accepted: self._finish(request, accepted)
            )
            self._dialogs[id(request)] = (dialog, lifetime)
            _fill_dialog_footer(dialog)
            dialog.open()
        except Exception as error:
            logger.error(_SHOW_DIALOG_FAILED, error)
            self._finish(request)
            if _valid(dialog):
                dialog.deleteLater()


_dialog_dispatcher = None
_dialog_dispatcher_lock = threading.Lock()


def _get_dialog_dispatcher(app):
    global _dialog_dispatcher
    with _dialog_dispatcher_lock:
        if not _valid(_dialog_dispatcher):
            if QThread.currentThread() != app.thread():
                return None
            _dialog_dispatcher = _DialogDispatcher(app)
        return _dialog_dispatcher


def show_dialog_and_wait(
    title: str,
    content: str,
    *,
    parent: QWidget | None = None,
    copyable: bool = True,
    rich_text: bool = True,
    open_external_links: bool = True,
    hide_cancel: bool = True,
    close_delay_seconds: float = 0,
    width: int = 440,
    message_role: str = "secondary",
) -> bool:
    app = QApplication.instance()
    if not _valid(app) or app.closingDown() or not _valid(parent):
        return False
    options = {
        "title": title,
        "content": content,
        "parent": parent,
        "copyable": copyable,
        "rich_text": rich_text,
        "open_external_links": open_external_links,
        "hide_cancel": hide_cancel,
        "close_delay_seconds": close_delay_seconds,
        "width": width,
        "message_role": message_role,
    }
    if QThread.currentThread() == app.thread():
        try:
            with exec_owned_dialog(_create_dialog(**options), owner=parent) as accepted:
                return accepted
        except Exception as error:
            logger.error(_SHOW_DIALOG_FAILED, error)
            return False
    request = _DialogRequest(options)
    dispatcher = _get_dialog_dispatcher(app)
    if dispatcher is None:
        return False
    dispatcher.submit(request)
    request.event.wait()
    return request.accepted


def install_confirmation_handler(parent: QWidget):
    """Bind project confirmation events to dialogs owned by the main window."""
    installed = getattr(parent, "_confirmation_handler", None)
    if installed is not None:
        return installed
    app = QApplication.instance()
    if _valid(app):
        _get_dialog_dispatcher(app)
        previous_disconnect = getattr(app, "_confirmation_disconnect", None)
        if previous_disconnect is not None:
            previous_disconnect()
    active = True
    signal = communicate.confirmation_requested

    def handle_confirmation(request: ConfirmationRequested) -> None:
        if not active:
            request.resolve(False)
            return
        try:
            accepted = show_dialog_and_wait(
                request.title,
                request.content,
                parent=parent,
                copyable=request.copyable,
                rich_text=request.rich_text,
                hide_cancel=request.hide_cancel,
                close_delay_seconds=request.close_delay_seconds or 0,
            )
        except Exception:
            accepted = False
        request.resolve(accepted if active else False)

    def disconnect(*_):
        nonlocal active
        if not active:
            return
        active = False
        signal.disconnect(handle_confirmation)
        if _valid(parent):
            parent._confirmation_handler = None
        if _valid(app) and getattr(app, "_confirmation_disconnect", None) is disconnect:
            app._confirmation_disconnect = None

    signal.connect(handle_confirmation)
    parent.destroyed.connect(disconnect)
    parent._confirmation_handler = handle_confirmation
    if _valid(app):
        app._confirmation_disconnect = disconnect
    return handle_confirmation
