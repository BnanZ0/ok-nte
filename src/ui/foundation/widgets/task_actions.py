"""Shared controls for one-time tasks presented on project pages."""

from ok.ui.qt.kit import Button
from ok.ui.qt.tasks.tasks_page import TaskDetail
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QSizePolicy, QVBoxLayout


def add_task_actions(
    layout: QBoxLayout, *, pause_text: str, stop_text: str, start_text: str, stacked: bool = False
) -> tuple[Button, Button, Button]:
    """Move the upstream task-detail buttons into an installed page layout."""
    detail = TaskDetail()
    pause, stop, start = detail.pause_button, detail.stop_button, detail.run_button
    actions = QVBoxLayout() if stacked else QHBoxLayout()
    actions.setSpacing(8)
    layout.addLayout(actions)
    for button, text in ((pause, pause_text), (stop, stop_text), (start, start_text)):
        # The detail's handlers require its own task. The custom page binds its own handlers.
        button.clicked.disconnect()
        button.setText(text)
        if stacked:
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        actions.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
    detail.deleteLater()
    return pause, stop, start
