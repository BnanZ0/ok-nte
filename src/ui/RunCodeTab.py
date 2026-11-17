"""Developer page for executing Python on the task executor."""

import os
import threading

from ok.core.code_runner import CodeSession, wait_result
from ok.core.events import communicate
from ok.ui.qt.kit.buttons import Button
from ok.ui.qt.kit.cards import Card
from ok.ui.qt.kit.feedback import show_toast
from ok.ui.qt.kit.inputs import ComboBox
from ok.ui.qt.kit.labels import Label
from ok.ui.qt.tasks.PythonHighlighter import PythonHighlighter
from ok.ui.qt.widget.CustomTab import CustomTab
from ok.util.config import Config
from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from src.ui.foundation.widgets.code_editor import CodeEditor


class RunCodeTab(CustomTab):
    """Run Python code with a persistent namespace and optional screenshot replay."""

    icon = "code"
    capture_prepared = Signal(object)
    output_ready = Signal(str)
    run_finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("RunCodeTab")
        self._section_move_window = None
        self._section_moved = False
        self.config = Config("run_code", {"code": "", "target_task": "", "screenshots": []})
        self.session = None
        self.selected_images = [
            path
            for path in self.config.get("screenshots", [])
            if isinstance(path, str) and os.path.isfile(path)
        ]
        self.current_job = None
        self._preparing = False
        self._stopping = False
        self._has_output = False

        self.title_label = Label("运行 Python 代码", "display", self)
        self.run_button = Button("运行", "play", "primary", parent=self)
        self.run_button.clicked.connect(self._on_run_clicked)

        title = QVBoxLayout()
        title.addWidget(self.title_label)
        header = QHBoxLayout()
        header.addLayout(title, 1)
        header.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignTop)

        workspace = Card(self.view)
        workspace.setObjectName("runCodeWorkspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        code_panel = QWidget(workspace)
        code_layout = QVBoxLayout(code_panel)
        code_layout.setContentsMargins(0, 0, 0, 14)
        code_layout.setSpacing(0)

        code_header = QHBoxLayout()
        code_header.setContentsMargins(20, 8, 20, 8)
        code_header.setSpacing(8)
        code_header.addWidget(Label("{ }", "mono", code_panel))
        code_header.addWidget(Label("Python", "strong", code_panel))
        code_header.addStretch(1)

        self.task_selector = ComboBox(parent=code_panel)
        self.task_selector.setMinimumWidth(180)
        self.task_selector.setMaximumWidth(260)
        self.task_selector.setAccessibleName("Task")
        self.task_selector.setToolTip("选择执行任务")
        self.task_selector.addItem("无任务", None)
        self.task_selector.currentIndexChanged.connect(self._save_selected_task)
        code_header.addWidget(self.task_selector)

        self.select_screenshots_button = Button("选择截图", "image", "secondary", parent=code_panel)
        self.select_screenshots_button.setAccessibleName("选择截图")
        self.select_screenshots_button.setToolTip("选择截图作为任务画面回放")
        self.select_screenshots_button.clicked.connect(self.select_screenshots)
        self.clear_screenshots_button = Button("清除", "x", "secondary", parent=code_panel)
        self.clear_screenshots_button.setToolTip("清除截图选择并恢复实时截图")
        self.clear_screenshots_button.clicked.connect(self.clear_screenshots)
        self.clear_screenshots_button.hide()
        code_header.addWidget(self.select_screenshots_button)
        code_header.addWidget(self.clear_screenshots_button)
        code_layout.addLayout(code_header)
        code_layout.addWidget(self._separator(code_panel))

        editor_container = QWidget(code_panel)
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(20, 10, 20, 0)
        self.editor = CodeEditor(editor_container)
        self.editor.setObjectName("codeEditor")
        self.editor.setPlaceholderText("# print(og.config.get('gui_title'))")
        self.editor.setPlainText(self.config.get("code", ""))
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * 4)
        self.highlighter = PythonHighlighter(self.editor.document())
        editor_layout.addWidget(self.editor, 1)
        code_layout.addWidget(editor_container, 1)

        output_panel = QWidget(workspace)
        output_layout = QVBoxLayout(output_panel)
        output_layout.setContentsMargins(0, 0, 0, 16)
        output_layout.setSpacing(0)

        output_header = QHBoxLayout()
        output_header.setContentsMargins(20, 8, 20, 8)
        output_header.setSpacing(8)
        output_header.addWidget(Label("输出", "strong", output_panel))
        self.output_status = Label("尚未执行", "secondary", output_panel)
        output_header.addWidget(self.output_status)
        output_header.addStretch(1)

        self.reset_session_button = Button("重置会话", "reset", "secondary", parent=output_panel)
        self.reset_session_button.setToolTip("清除运行过程中创建的变量")
        self.reset_session_button.clicked.connect(self.reset_session)
        self.copy_button = Button("复制", "file", "secondary", parent=output_panel)
        self.copy_button.setToolTip("复制输出")
        self.copy_button.clicked.connect(self.copy_output)
        self.clear_output_button = Button("清空", "trash", "secondary", parent=output_panel)
        self.clear_output_button.setToolTip("清空输出")
        self.clear_output_button.clicked.connect(self.clear_output)
        self.copy_button.setEnabled(False)
        self.clear_output_button.setEnabled(False)
        output_header.addWidget(self.reset_session_button)
        output_header.addWidget(self.copy_button)
        output_header.addWidget(self.clear_output_button)
        output_layout.addLayout(output_header)
        output_layout.addWidget(self._separator(output_panel))

        output_container = QWidget(output_panel)
        output_content_layout = QVBoxLayout(output_container)
        output_content_layout.setContentsMargins(20, 10, 20, 0)
        self.output = QPlainTextEdit(output_container)
        self.output.setObjectName("logView")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        self.output.setPlaceholderText("运行结果和日志将显示在这里。")
        self.output.setStyleSheet(
            "QPlainTextEdit#logView { background: transparent; border: 0; "
            "border-radius: 0; padding: 6px 4px; }"
        )
        output_content_layout.addWidget(self.output, 1)
        output_layout.addWidget(output_container, 1)

        workspace_layout.addWidget(code_panel, 3)
        workspace_layout.addWidget(self._separator(workspace))
        workspace_layout.addWidget(output_panel, 2)

        self.vBoxLayout.setContentsMargins(28, 18, 28, 20)
        self.vBoxLayout.setSpacing(14)
        self.vBoxLayout.addLayout(header)
        self.vBoxLayout.addWidget(workspace, 1)

        self.capture_prepared.connect(self._on_capture_prepared)
        self.output_ready.connect(self._append_output)
        self.run_finished.connect(self._on_run_finished)
        self._update_screenshot_selection()
        refresh_tasks = self._refresh_task_selector
        communicate.task_list_updated.connect(refresh_tasks)
        self.destroyed.connect(lambda: communicate.task_list_updated.disconnect(refresh_tasks))
        QTimer.singleShot(0, self, lambda: refresh_tasks(restore_saved=True))

    @staticmethod
    def _separator(parent):
        separator = QFrame(parent)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setFixedHeight(1)
        return separator

    @property
    def name(self):  # type: ignore
        return "运行 Python 代码"

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.ParentChange and not getattr(self, "_section_moved", False):
            window = self.window()
            if getattr(window, "navigation", None) is not None:
                self._section_move_window = window
                window.installEventFilter(self)
        return result

    def eventFilter(self, watched, event):
        section_window = getattr(self, "_section_move_window", None)
        if (
            section_window is not None
            and watched is section_window
            and event.type() == QEvent.Type.Show
        ):
            watched.removeEventFilter(self)
            self._section_move_window = None
            self._move_to_develop(watched)
            return False
        return super().eventFilter(watched, event)

    def _move_to_develop(self, window):
        self._section_moved = True
        navigation = getattr(window, "navigation", None)
        key = self.objectName()
        if navigation is None or not navigation.has_item(key):
            return
        was_current = navigation.current_key() == key
        window.remove_page(self)
        window.add_page(self, self.icon, window.app.tr(self.name), "develop")
        if was_current:
            window.switch_to(self)

    def select_screenshots(self):
        from ok.ui.qt.shell.diagnostics import screenshot_folder

        folder = next(
            (os.path.dirname(path) for path in self.selected_images if os.path.isfile(path)),
            None,
        )
        if not folder:
            folder = screenshot_folder()
        if not folder or not os.path.isdir(folder):
            folder = os.getcwd()
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择截图",
            folder,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if paths:
            self.selected_images = paths
            self.config["screenshots"] = list(paths)
            self._update_screenshot_selection()

    def clear_screenshots(self):
        self.selected_images = []
        self.config["screenshots"] = []
        self._update_screenshot_selection()

    def _update_screenshot_selection(self):
        if self.selected_images:
            self.select_screenshots_button.setText(f"{len(self.selected_images)} 张截图")
            self.select_screenshots_button.setToolTip("\n".join(self.selected_images))
            self.clear_screenshots_button.show()
        else:
            self.select_screenshots_button.setText("选择截图")
            self.select_screenshots_button.setToolTip("选择截图作为任务画面回放")
            self.clear_screenshots_button.hide()

    def _refresh_task_selector(self, *, restore_saved=False):
        if self.executor is None:
            return

        selected = self.task_selector.currentData()
        selected_class = type(selected)
        saved_name = self.config.get("target_task", "") if restore_saved else ""
        with QSignalBlocker(self.task_selector):
            self.task_selector.clear()
            self.task_selector.addItem("无任务", None)
            for task in self.executor.get_all_tasks():
                task_class = type(task)
                self.task_selector.addItem(task_class.__name__, task)
                if selected is None:
                    matches = task_class.__name__ == saved_name
                else:
                    # Reloads recreate the class as well as the instance.
                    matches = (task_class.__module__, task_class.__qualname__) == (
                        selected_class.__module__,
                        selected_class.__qualname__,
                    )
                if matches:
                    self.task_selector.setCurrentIndex(self.task_selector.count() - 1)

    def _save_selected_task(self):
        task = self.task_selector.currentData()
        self.config["target_task"] = task.__class__.__name__ if task is not None else ""

    def _task_for_run(self):
        return self.task_selector.currentData()

    def _on_run_clicked(self):
        if self.current_job is not None:
            self.stop_run()
        elif not self._preparing:
            self.run_code()

    def run_code(self):
        if self.current_job is not None or self._preparing:
            return
        if self.executor is None:
            show_toast(self, "任务执行器不可用", tone="danger")
            return

        self._stopping = False
        code = self.editor.toPlainText()
        self.config["code"] = code
        task = self._task_for_run()
        self.config["target_task"] = task.__class__.__name__ if task is not None else ""
        if self.selected_images:
            self._preparing = True
            self.output_status.setText("准备截图")
            self._set_busy(True)
            threading.Thread(
                target=self._prepare_capture,
                args=(code, task, list(self.selected_images)),
                name="RunCodeScreenshotSetup",
                daemon=True,
            ).start()
            return
        self._submit_code(code, task)

    def _prepare_capture(self, code, task, paths):
        try:
            from ok.device.capture_methods.image import ImageCaptureMethod
            from ok.device.interaction_methods.do_nothing import DoNothingInteraction

            capture = ImageCaptureMethod(self.executor.exit_event, paths)
            interaction = DoNothingInteraction(capture)
            result = (code, task, capture, interaction, None)
        except Exception as error:
            result = (code, task, None, None, error)
        self.capture_prepared.emit(result)

    def _on_capture_prepared(self, result):
        self._preparing = False
        code, task, capture, interaction, error = result
        if error is not None:
            self._set_busy(False)
            self.output_status.setText("截图读取失败")
            show_toast(self, str(error), tone="danger")
            return
        self._submit_code(code, task, capture, interaction)

    def _submit_code(self, code, task, capture=None, interaction=None):
        if task is not None and not any(
            current is task for current in self.executor.get_all_tasks()
        ):
            self._set_busy(False)
            self.output_status.setText("无法启动")
            show_toast(self, "所选任务已变更。请重新运行。", tone="danger")
            return
        if self.session is None or self.session.executor is not self.executor:
            self.session = CodeSession(self.executor, owner="RunCodeTab")
        if task is None:
            self.session.namespace.pop("task", None)
            self.session.namespace.pop("self", None)
        else:
            self.session.namespace.update({"self": task})
        try:
            job = self.session.submit(
                code,
                task=task,
                capture=capture,
                interaction=interaction,
                on_output=self.output_ready.emit,
            )
        except Exception as error:
            self._set_busy(False)
            self.output_status.setText("无法启动")
            show_toast(self, str(error), tone="danger")
            return
        self.current_job = job
        self.output_status.setText("运行中")
        self._set_busy(True)
        job.future.add_done_callback(lambda _: self.run_finished.emit(job))

    def stop_run(self):
        job = self.current_job
        if job is not None:
            self._stopping = True
            self._set_busy(True)
            job.cancel()

    def copy_output(self):
        QGuiApplication.clipboard().setText(self.output.toPlainText())

    def reset_session(self):
        if self.current_job is not None or self._preparing:
            return
        if self.executor is None:
            show_toast(self, "任务执行器不可用", tone="danger")
            return

        if self.session is None or self.session.executor is not self.executor:
            self.session = CodeSession(self.executor, owner="RunCodeTab")
        else:
            self.session.reset()
        self.output_status.setText("会话已重置")
        show_toast(self, "运行变量已重置")

    def clear_output(self):
        self.output.clear()
        self._has_output = False
        self.output_status.setText("尚未执行")
        self._update_output_buttons()

    def _set_busy(self, busy):
        self.task_selector.setEnabled(not busy)
        self.select_screenshots_button.setEnabled(not busy)
        self.clear_screenshots_button.setEnabled(not busy and bool(self.selected_images))
        self.reset_session_button.setEnabled(not busy)
        self.copy_button.setEnabled(self._has_output and not busy)
        self.clear_output_button.setEnabled(self._has_output and not busy)

        if self._stopping:
            self.run_button.setText("正在停止")
            self.run_button.set_icon("stop")
            self.run_button.set_variant("danger")
            self.run_button.setEnabled(False)
        elif self._preparing:
            self.run_button.setText("准备截图")
            self.run_button.set_icon("image")
            self.run_button.set_variant("secondary")
            self.run_button.setEnabled(False)
        elif self.current_job is not None:
            self.run_button.setText("停止")
            self.run_button.set_icon("stop")
            self.run_button.set_variant("danger")
            self.run_button.setEnabled(True)
        else:
            self.run_button.setText("运行")
            self.run_button.set_icon("play")
            self.run_button.set_variant("primary")
            self.run_button.setEnabled(True)

    def _append_output(self, text):
        if not text:
            return
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
        self._has_output = True
        self._update_output_buttons()

    def _update_output_buttons(self):
        can_edit = self.current_job is None and not self._preparing
        self.copy_button.setEnabled(self._has_output and can_edit)
        self.clear_output_button.setEnabled(self._has_output and can_edit)

    def _on_run_finished(self, job):
        if self.current_job is not job:
            return
        self.current_job = None
        self._stopping = False
        self._set_busy(False)
        try:
            result = wait_result(job)
        except Exception as error:
            self.output_status.setText("运行失败")
            self._append_output(f"\n{type(error).__name__}: {error}\n")
            return

        if result.error:
            self.output_status.setText("运行失败")
            self._append_output(f"\n{result.error}\n")
        elif result.cancelled:
            self.output_status.setText("已取消")
            self._append_output("\n运行已取消。\n")
        else:
            self.output_status.setText("已完成")
            if result.value is not None:
                self._append_output(f"\n>>> {result.value}\n")
            self._append_output(f"\n运行完成，用时 {result.duration:.2f} 秒。\n")
