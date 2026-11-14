"""Send widget input using the Qt modules shipped with the app."""

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent


def send_key(widget, key, event_type=QEvent.Type.KeyPress):
    QCoreApplication.sendEvent(widget, QKeyEvent(event_type, key, Qt.KeyboardModifier.NoModifier))


def click_at(widget, position):
    local = QPointF(position)
    global_position = QPointF(widget.mapToGlobal(local.toPoint()))
    left = Qt.MouseButton.LeftButton
    for event_type, buttons in (
        (QEvent.Type.MouseButtonPress, left),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(
            event_type, local, global_position, left, buttons, Qt.KeyboardModifier.NoModifier
        )
        QCoreApplication.sendEvent(widget, event)
