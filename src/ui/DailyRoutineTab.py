from ok import og
from ok.ui.qt.Communicate import communicate
from ok.ui.qt.kit import Button, Card, Label, Pill, SettingRow, Table, icon
from ok.ui.qt.kit.theme import rgba, set_style_property, theme
from ok.ui.qt.tasks.config_form import ConfigForm
from ok.ui.qt.tasks.tasks_page import start_pending, start_task, stop_task
from ok.ui.qt.widget.CustomTab import CustomTab
from ok.ui.qt.widget.UpdateConfigWidgetItem import value_to_string
from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSignalBlocker,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.tasks.daily.DailyRoutineTask import DailyRoutineTask, selection_is_complete
from src.ui.foundation.widgets.page_tabs import PageTabStrip
from src.ui.foundation.widgets.separator import Separator
from src.ui.foundation.widgets.task_actions import add_task_actions


def _task_is_active(task):
    return task is not None and (task.enabled or task.running or start_pending(task))


class _DragHandle(QWidget):
    """Only this handle can reorder a row; checking a task never selects it."""

    def __init__(self, row, page):
        super().__init__(row)
        self.row = row
        self.page = page
        self.press_position = None
        self.dragging = False
        self.setFixedSize(24, 30)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAccessibleName(page.tr("Drag to reorder"))
        self.setToolTip(self.accessibleName())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("text_tertiary"))
        for row in range(3):
            for column in range(2):
                painter.drawEllipse(7 + column * 6, 7 + row * 6, 3, 3)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = event.globalPosition()
            self.dragging = False
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.grabMouse()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (
                not self.dragging
                and (event.globalPosition() - self.press_position).manhattanLength() >= 8
            ):
                self.dragging = self.page.start_drag(self.row.task_id, self.press_position)
            if self.dragging:
                self.page.move_drag(self.row.task_id, event.globalPosition())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._end_press(commit=True)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.press_position is not None and event.key() == Qt.Key.Key_Escape:
            self._end_press(commit=False)
            event.accept()
        else:
            super().keyPressEvent(event)

    def event(self, event):
        if (
            event.type() in (QEvent.Type.UngrabMouse, QEvent.Type.Hide)
            and self.press_position is not None
        ):
            self._end_press(commit=False)
        return super().event(event)

    def _end_press(self, *, commit):
        was_dragging = self.dragging
        self.reset_press()
        if was_dragging:
            self.page.finish_drag(commit=commit)

    def reset_press(self):
        self.press_position = None
        self.dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()


class _RoutineRow(QFrame):
    picked = Signal(object)
    enabled_changed = Signal(str, bool)

    def __init__(self, task_id, name, page, parent):
        super().__init__(parent)
        self.task_id = task_id
        self.setObjectName("dailyRoutineRow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(50)
        self.name_label = Label(name, "strong", self)
        self.name_label.setWordWrap(True)
        self.name_label.setMinimumWidth(1)
        name_policy = self.name_label.sizePolicy()
        name_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.name_label.setSizePolicy(name_policy)
        self.name_label.setToolTip(name)
        self.status_label = Pill("", "neutral", self)
        self.status_label.hide()
        for label in (self.name_label, self.status_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 12, 6)
        layout.setSpacing(8)
        if task_id is None:
            self.calendar = Label(parent=self)
            self.calendar.setFixedWidth(24)
            self.calendar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.calendar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(self.calendar)
            layout.addSpacing(24)
        else:
            self.drag_handle = _DragHandle(self, page)
            self.enabled_check = QCheckBox(self)
            self.enabled_check.setAccessibleName(page.tr("Include in this daily routine"))
            self.enabled_check.toggled.connect(lambda on: self.enabled_changed.emit(task_id, on))
            layout.addWidget(self.drag_handle)
            layout.addWidget(self.enabled_check)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        theme.changed.connect(self._refresh_style)
        self._refresh_style()

    def _refresh_style(self):
        if self.task_id is None:
            self.calendar.setPixmap(icon("calendar").pixmap(20, 20))
        accent = theme.accent()
        self.setStyleSheet(
            "QFrame#dailyRoutineRow { border: 1px solid transparent; border-radius: 10px; }"
            f"QFrame#dailyRoutineRow:hover {{ background: {theme.tokens()['control_hover']}; }}"
            'QFrame#dailyRoutineRow[selected="true"] {'
            f"background: {rgba(accent, 0.12)}; border-color: {rgba(accent, 0.24)}; }}"
            f"QFrame#dailyRoutineRow:focus {{ border-color: {accent.name()}; }}"
        )

    def set_enabled(self, enabled):
        with QSignalBlocker(self.enabled_check):
            self.enabled_check.setChecked(enabled)
        self.name_label.setEnabled(enabled)

    def set_selected(self, selected):
        set_style_property(self, "selected", selected)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.picked.emit(self.task_id)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.picked.emit(self.task_id)
            event.accept()
        else:
            super().keyPressEvent(event)


class DailyRoutineTab(CustomTab):
    """Daily selection, settings and run controls."""

    ACTION_BAR_HEIGHT = 72
    icon = "calendar"

    def __init__(self):
        super().__init__()
        self.setObjectName("DailyRoutineTab")
        self.tr_name = self.tr("日常任务")
        self.rows = {}
        self.selected_task_id = None
        self.task_form = None
        self.routine_form = None
        self._daily_config = None
        self._drag_row = None
        self._drag_proxy = None
        self._drag_offset = None
        self._swap_targets = {}
        self._swaps = QParallelAnimationGroup(self)
        self._swaps.finished.connect(self._swap_targets.clear)
        self._was_active = False
        self._single_run = None
        self._info_closed = False
        self._disposed = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.vBoxLayout.setContentsMargins(20, 18, 20, 0)
        self.vBoxLayout.setSpacing(8)
        content = QHBoxLayout()
        content.setSpacing(16)
        list_pane = Card(self.view)
        list_pane.setMinimumWidth(220)
        list_pane.setMaximumWidth(304)
        list_layout = QVBoxLayout(list_pane)
        list_layout.setContentsMargins(8, 8, 8, 8)
        list_layout.setSpacing(2)
        self.routine_row = _RoutineRow(None, self.tr_name, self, list_pane)
        self.routine_row.picked.connect(self.select_task)
        list_layout.addWidget(self.routine_row)
        self.routine_separator = Separator(list_pane, spacing=8)
        list_layout.addWidget(self.routine_separator)
        self.list_scroll = self._scroll_area()
        self.list_contents = QWidget()
        self.list_contents.setObjectName("scrollContents")
        self.list_layout = QVBoxLayout(self.list_contents)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(2)
        self.list_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_contents)
        list_layout.addWidget(self.list_scroll, 1)
        self.empty_label = Label(self.tr("No tasks available."), "secondary")
        self.empty_label.setWordWrap(True)
        list_layout.addWidget(self.empty_label)
        content.addWidget(list_pane, 1)

        detail = Card(self.view)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(24, 22, 24, 20)
        detail_layout.setSpacing(14)
        self.detail_title = Label("", "title")
        self.detail_title.setWordWrap(True)
        header_text = QVBoxLayout()
        header_text.setSpacing(6)
        header_text.addWidget(self.detail_title)
        self.detail_description = Label("", "secondary", word_wrap=True)
        header_text.addWidget(self.detail_description)
        self.run_button = Button(
            QCoreApplication.translate("TasksPage", "Run task"), icon="play", variant="secondary"
        )
        self.run_button.clicked.connect(self._start_item)
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addLayout(header_text, 1)
        header.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignVCenter)
        detail_layout.addLayout(header)

        self.tabs = PageTabStrip()
        self.tabs.add_tab(
            "settings", QCoreApplication.translate("TasksPage", "Settings"), icon="gear"
        )
        self.tabs.add_tab("info", QCoreApplication.translate("TaskTab", "Info"), icon="file")
        self.tabs.current_changed.connect(self._show_detail)
        self.reset_button = Button(
            QCoreApplication.translate("ConfigCard", "Reset Config"),
            icon="reset",
            variant="subtle",
            size="small",
        )
        self.reset_button.clicked.connect(self.reset_config)
        self.close_info_button = Button(icon="x", variant="subtle", size="small")
        self.close_info_button.setAccessibleName(self.tr("Close"))
        self.close_info_button.setToolTip(self.tr("Close"))
        self.close_info_button.clicked.connect(self._close_info)
        for button in (self.reset_button, self.close_info_button):
            self.tabs.layout().addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        detail_layout.addWidget(self.tabs)
        self.pages = QStackedWidget()
        self.task_scroll = self._scroll_area()
        self.routine_scroll = self._scroll_area()
        self.pages.addWidget(self.task_scroll)
        self.pages.addWidget(self.routine_scroll)
        self.info_table = Table(0, 2)
        self.info_table.setHorizontalHeaderLabels(
            [QCoreApplication.translate("TaskTab", text) for text in ("Info", "Value")]
        )
        table_header = self.info_table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setStretchLastSection(True)
        table_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.info_table.verticalHeader().hide()
        self.info_table.verticalHeader().setDefaultSectionSize(40)
        self.info_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.info_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pages.addWidget(self.info_table)
        detail_layout.addWidget(self.pages, 1)
        content.addWidget(detail, 2)
        self.vBoxLayout.addLayout(content, 1)

        self.action_bar = QWidget(self.view)
        self.action_bar.setFixedHeight(self.ACTION_BAR_HEIGHT)
        action_layout = QHBoxLayout(self.action_bar)
        action_layout.setContentsMargins(0, 12, 0, 12)
        self.select_all_check = QCheckBox(self.tr("全选"))
        self.select_all_check.toggled.connect(self._set_all_selected)
        action_layout.addWidget(self.select_all_check)
        action_layout.addStretch(1)
        self.status_label = Label("", "secondary")
        action_layout.addWidget(self.status_label)
        self.pause_button, self.stop_button, self.start_button = add_task_actions(
            action_layout,
            pause_text=self.tr("Pause"),
            stop_text=self.tr("Stop"),
            start_text=self.tr("Start"),
        )
        self.pause_button.clicked.connect(self._pause)
        self.stop_button.clicked.connect(self._stop)
        self.start_button.clicked.connect(self._start)
        self.vBoxLayout.addWidget(self.action_bar)

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        communicate.task.connect(self._on_task_event)
        communicate.task_list_updated.connect(self.refresh_tasks)
        self._ready = True
        self.refresh_tasks()
        self.timer.start()

    @staticmethod
    def _scroll_area():
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    @property
    def name(self):  # type: ignore
        return self.tr_name

    @property
    def executor(self):
        return getattr(self, "_executor", None)

    @executor.setter
    def executor(self, value):
        # CustomTab sets this before our widgets exist; the host injects it later.
        self._executor = value
        if getattr(self, "_ready", False):
            self.refresh_tasks()
        else:
            self.task = None

    def refresh_tasks(self):
        if self._disposed:
            return
        self._cancel_drag()
        task = self.get_task(DailyRoutineTask) if self.executor is not None else None
        if task is not self.task:
            self.task = task
            self._was_active = False
            self._single_run = None
            self._info_closed = False
            with QSignalBlocker(self.tabs):
                self.tabs.set_current("settings")
        for row in self.rows.values():
            self.list_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows.clear()
        routine = self.task
        self.routine_row.setVisible(routine is not None)
        if routine is not None:
            for item in routine.normalize_items():
                child = routine.task_for_id(item["id"])
                if child is None:
                    continue
                row = _RoutineRow(item["id"], self.tr(child.name), self, self.list_contents)
                row.set_enabled(item["enabled"])
                row.picked.connect(self.select_task)
                row.enabled_changed.connect(self._set_enabled)
                self.rows[item["id"]] = row
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self.empty_label.setVisible(not self.rows)
        self.routine_separator.setVisible(bool(self.rows))
        self.select_all_check.setEnabled(bool(self.rows))
        if self.selected_task_id is not None and self.selected_task_id not in self.rows:
            self.selected_task_id = None
            with QSignalBlocker(self.tabs):
                self.tabs.set_current("settings")
        self._bind_routine_form()
        self._update_selection()
        self._refresh_state()

    def _replace_form(self, scroll, form):
        previous = scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        if form is not None:
            if isinstance(form, ConfigForm):
                for editor in form.config_widgets:
                    editor.installEventFilter(self)
            scroll.setWidget(form)

    def eventFilter(self, watched, event):
        if isinstance(watched, SettingRow) and (
            event.type() in (QEvent.Type.Show, QEvent.Type.FontChange, QEvent.Type.StyleChange)
            or (
                event.type() == QEvent.Type.Resize
                and event.size().width() != event.oldSize().width()
            )
        ):
            # At fractional DPI a scrolling form can compress wrapped rows below
            # their layout's height-for-width. Measure after Qt settles the width.
            QTimer.singleShot(0, watched, lambda: self._fit_config_row(watched))
        return super().eventFilter(watched, event)

    def _fit_config_row(self, row):
        if self._disposed or row.isHidden():
            return
        required = row.layout().heightForWidth(row.width())
        if required > 0 and required != row.minimumHeight():
            row.setMinimumHeight(required)

    def _bind_routine_form(self):
        routine = self.task
        self.routine_form = None
        if routine is None:
            self._replace_form(
                self.routine_scroll, Label(self.tr("No tasks available."), "secondary")
            )
        else:
            self.routine_form = ConfigForm(
                routine,
                routine.config,
                routine.default_config,
                routine.config_description,
                routine.config_type,
            )
            self._replace_form(self.routine_scroll, self.routine_form)

    def select_task(self, task_id):
        self.selected_task_id = task_id if task_id in self.rows else None
        self._update_selection()
        task = self.task
        selected_id = self.selected_task_id
        show_info = (
            task is not None
            and task.enabled
            and task.running
            and (
                not self._info_closed
                if selected_id is None
                else selected_id == task.current_task_key
            )
        )
        if show_info:
            with QSignalBlocker(self.tabs):
                self.tabs.set_current("info")
        self._show_detail()

    def _update_selection(self):
        self.routine_row.set_selected(self.task is not None and self.selected_task_id is None)
        for key, row in self.rows.items():
            row.set_selected(key == self.selected_task_id)
        self._bind_task_form()

    def _bind_task_form(self):
        routine = self.task
        task_id = self.selected_task_id
        self.task_form = None
        self._daily_config = None
        if routine is None or task_id is None:
            self._replace_form(self.task_scroll, None)
            return
        child = routine.task_for_id(task_id)
        if child is None:
            self._replace_form(self.task_scroll, None)
            return
        schema = routine.daily_task_schema(task_id, child)
        config = routine.daily_task_config(task_id, child, schema)
        self._daily_config = config
        self.task_form = ConfigForm(
            child,
            config,
            schema.default_config,
            schema.config_description,
            schema.config_type,
        )
        self._replace_form(self.task_scroll, self.task_form)

    def _show_detail(self):
        task_id = self.selected_task_id
        routine = self.task
        routine_selected = task_id is None or routine is None
        if task_id is None or routine is None:
            self.detail_title.setText(self.tr_name)
            selected = routine
        else:
            self.detail_title.setText(self.rows[task_id].name_label.text())
            selected = routine.task_for_id(task_id)
        info = dict(selected.info) if selected is not None else {}
        if routine_selected:
            has_info = (
                selected is not None and (selected.enabled or bool(info)) and not self._info_closed
            )
        else:
            single_run = self._single_run
            is_single_target = single_run is not None and single_run.task_id == task_id
            if is_single_target and not single_run.started:
                info = {}
            has_info = bool(info) or is_single_target or task_id == routine.current_task_key
        description = getattr(selected, "description", "")
        self.detail_description.setText(self.tr(description) if description else "")
        self.detail_description.setVisible(bool(description))
        self.run_button.setVisible(not routine_selected and selected is not None)
        self.run_button.setEnabled(not routine_selected and self._can_start_item(selected))
        self._show_detail_page(routine_selected, info, has_info)

    def _show_detail_page(self, routine_selected, info, has_info):
        self.tabs.item("info").setVisible(has_info)
        if not has_info and self.tabs.current_key() == "info":
            with QSignalBlocker(self.tabs):
                self.tabs.set_current("settings")
        showing_info = self.tabs.current_key() == "info"
        if showing_info:
            self.pages.setCurrentWidget(self.info_table)
            self._fill_info(info)
        else:
            self.pages.setCurrentWidget(
                self.routine_scroll if routine_selected else self.task_scroll
            )
        form = self.routine_form if routine_selected else self.task_form
        self.reset_button.setVisible(not showing_info and form is not None and not form.is_empty)
        self.close_info_button.setVisible(routine_selected and showing_info)

    def reset_config(self):
        if self.task is None or self.tabs.current_key() != "settings":
            return
        if self.selected_task_id is None:
            # The group reset keeps its original semantics, including item order/selection.
            self.task.config.reset_to_default()
            self.refresh_tasks()
        elif self._daily_config is not None:
            self._daily_config.reset_to_default()
            self._bind_task_form()
            self._show_detail()

    def _visible_items(self):
        return [
            {"id": task_id, "enabled": row.enabled_check.isChecked()}
            for task_id, row in self.rows.items()
        ]

    def _sync_routine_items(self, items):
        enabled_by_id = {item["id"]: item["enabled"] for item in items}
        for task_id, row in self.rows.items():
            row.set_enabled(enabled_by_id.get(task_id, False))
        self._refresh_state()

    def _set_enabled(self, task_id, enabled):
        if self.task is not None:
            self._sync_routine_items(self.task.set_item_enabled(task_id, enabled))

    def _set_all_selected(self, selected):
        if self.task is not None:
            self._sync_routine_items(self.task.set_all_available_items_selected(selected))

    def _ordered_rows(self):
        return [self.list_layout.itemAt(i).widget() for i in range(self.list_layout.count() - 1)]

    def start_drag(self, task_id, press_position):
        row = self.rows.get(task_id)
        if row is None or self._drag_row is not None or self._disposed:
            return False
        self._settle_swaps()
        self._drag_row = row
        self._drag_offset = row.mapFromGlobal(press_position.toPoint()).y()
        proxy = QLabel(self.list_scroll.viewport())
        proxy.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        proxy.setPixmap(row.grab())
        proxy.resize(row.size())
        effect = QGraphicsOpacityEffect(proxy)
        effect.setOpacity(0.7)
        proxy.setGraphicsEffect(effect)
        self._drag_proxy = proxy
        effect = QGraphicsOpacityEffect(row)
        effect.setOpacity(0)
        row.setGraphicsEffect(effect)
        return True

    def move_drag(self, task_id, global_position):
        row = self._drag_row
        if row is None or row.task_id != task_id:
            return
        point = global_position.toPoint()
        local_y = self.list_contents.mapFromGlobal(point).y()
        self.list_scroll.ensureVisible(0, local_y, 0, 28)
        viewport = self.list_scroll.viewport()
        proxy = self._drag_proxy
        top = viewport.mapFromGlobal(point).y() - self._drag_offset
        proxy.move(row.x(), max(-proxy.height(), min(top, viewport.height())))
        proxy.raise_()
        proxy.show()

        center = self.list_contents.mapFromGlobal(point).y() - self._drag_offset + row.height() // 2
        ordered = self._ordered_rows()
        others = [candidate for candidate in ordered if candidate is not row]
        index = next(
            (
                i
                for i, candidate in enumerate(others)
                if center
                < self._swap_targets.get(candidate, candidate.pos()).y() + candidate.height() // 2
            ),
            len(others),
        )
        if ordered.index(row) != index:
            self._animate_swap(row, index, ordered)

    def _animate_swap(self, row, index, ordered):
        previous_positions = {widget: widget.pos() for widget in ordered}
        self._swaps.stop()
        self._swaps.clear()
        self._swap_targets.clear()
        self.list_layout.removeWidget(row)
        self.list_layout.insertWidget(index, row)
        self.list_layout.activate()
        for sibling in ordered:
            start = previous_positions[sibling]
            target = sibling.pos()
            if sibling is row or start == target:
                continue
            self._swap_targets[sibling] = target
            animation = QPropertyAnimation(sibling, b"pos")
            animation.setDuration(200)
            animation.setEasingCurve(QEasingCurve.Type.OutQuad)
            animation.setStartValue(start)
            animation.setEndValue(target)
            self._swaps.addAnimation(animation)
            sibling.move(start)
        if self._swap_targets:
            self._swaps.start()

    def _settle_swaps(self):
        self._swaps.stop()
        for row, target in self._swap_targets.items():
            row.move(target)
        self._swap_targets.clear()
        self._swaps.clear()

    def finish_drag(self, *, commit=True):
        row = self._drag_row
        if row is None:
            return
        self._drag_row = None
        self._drag_offset = None
        self._drag_proxy.hide()
        self._drag_proxy.deleteLater()
        self._drag_proxy = None
        row.drag_handle.reset_press()
        row.setGraphicsEffect(None)
        ordered_ids = [widget.task_id for widget in self._ordered_rows()]
        if ordered_ids == list(self.rows):
            return
        if commit and self.task is not None:
            items = self.task.set_available_item_order(ordered_ids)
            saved_order = [item["id"] for item in items if item["id"] in self.rows]
            if saved_order == ordered_ids:
                self.rows = {task_id: self.rows[task_id] for task_id in saved_order}
            else:
                self.refresh_tasks()
        else:
            self._settle_swaps()
            for index, widget in enumerate(self.rows.values()):
                self.list_layout.removeWidget(widget)
                self.list_layout.insertWidget(index, widget)
            self.list_layout.activate()

    def _cancel_drag(self):
        self.finish_drag(commit=False)
        self._settle_swaps()

    def _has_pending_run(self):
        return self._single_run is not None or (
            self.task is not None and self.task.has_pending_items
        )

    def _routine_is_active(self):
        # The controller drops pending entries just before it enables the tasks.
        if self._has_pending_run():
            controller = getattr(getattr(og, "app", None), "start_controller", None)
            if getattr(controller, "starting", False) is True:
                return True
        return _task_is_active(self.task)

    def _can_start_item(self, child):
        return child is not None and not self._routine_is_active() and not _task_is_active(child)

    def _start_item(self):
        task_id = self.selected_task_id
        if task_id is None or self.task is None:
            return
        child = self.task.task_for_id(task_id)
        if not self._can_start_item(child):
            return
        self._start(task_id=task_id)
        if self._single_run is not None and self.selected_task_id == task_id:
            self.tabs.set_current("info")

    def _start(self, *, task_id=None):
        task = self.task
        if task is None:
            return
        if not self._routine_is_active():
            self._clear_single_run()
            if task_id is not None:
                self._single_run = task.request_single_task(task_id)
        elif task_id is not None or not (task.enabled and task.paused):
            return
        try:
            start_task(task, self)
        finally:
            self._refresh_state()

    def _clear_single_run(self):
        if self._single_run is not None and self.task.single_task_request is self._single_run:
            self.task.single_task_request = None
        self._single_run = None

    def _stop(self):
        if self.task is not None:
            if self._single_run is not None:
                self._single_run.cancelled = True
            stop_task(self.task)
            self._refresh_state()

    def _pause(self):
        if self.task is not None:
            self.task.pause()
            self._refresh_state()

    def _refresh_state(self):
        task = self.task
        active = self._routine_is_active()
        enabled = task is not None and task.enabled
        if not active:
            self._clear_single_run()
            if task is not None:
                task.clear_unfinished_statuses()
        elif (
            task is not None
            and self._single_run is None
            and not self._was_active
            and not task.running
            and not task.has_pending_items
        ):
            task.queue_batch_status()
        if enabled and not self._was_active:
            self._info_closed = False
            if self._single_run is None:
                self.selected_task_id = None
                self._update_selection()
                with QSignalBlocker(self.tabs):
                    self.tabs.set_current("info")
        elif not enabled and self._was_active:
            self._bind_task_form()
            if self.routine_form is not None:
                self.routine_form.update_config()
        self._was_active = enabled
        self._refresh_controls(task, active)
        self._refresh_row_statuses(task)
        self._show_detail()

    def _refresh_controls(self, task, active):
        running = active and task.enabled and task.running
        paused = active and task.enabled and task.paused
        items = self._visible_items()
        complete = bool(
            task is not None
            and selection_is_complete(
                items,
                {item["id"]: task.entries_by_id()[item["id"]] for item in items},
            )
        )
        with QSignalBlocker(self.select_all_check):
            self.select_all_check.setChecked(complete)
        self.select_all_check.setText(self.tr("取消全选") if complete else self.tr("全选"))
        self.start_button.setVisible(not active or paused)
        self.start_button.setText(self.tr("Resume") if paused else self.tr("Start"))
        self.start_button.setEnabled(
            task is not None and (paused or any(item["enabled"] for item in items))
        )
        self.stop_button.setVisible(active)
        self.pause_button.setVisible(running and not paused)
        if paused:
            state = self.tr("Paused")
        elif running:
            state = self.tr("Running")
        elif active:
            state = self.tr("Queued")
        else:
            state = ""
        self.status_label.setText(state)

    def _refresh_row_statuses(self, task):
        statuses = task.task_status if task is not None else {}
        labels = {
            "pending": (self.tr("Pending"), "neutral"),
            "running": (self.tr("Running"), "success"),
            "success": (self.tr("Succeeded"), "success"),
            "failed": (self.tr("Failed"), "danger"),
            "skipped": (self.tr("Skipped"), "neutral"),
        }
        for task_id, row in self.rows.items():
            text, tone = labels.get(statuses.get(task_id), ("", "neutral"))
            row.status_label.setText(text)
            row.status_label.set_tone(tone)
            row.status_label.setVisible(bool(text))

    def _fill_info(self, info):
        self.info_table.setRowCount(len(info))
        for row, (key, value) in enumerate(info.items()):
            for column, text in enumerate((self.tr(key), self.tr(value_to_string(value)))):
                item = self.info_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.info_table.setItem(row, column, item)
                item.setText(text)
                item.setToolTip(text)

    def _close_info(self):
        self._info_closed = True
        self.tabs.set_current("settings")

    def _on_task_event(self, task=None):
        if not self._disposed and (task is None or task is self.task):
            self._refresh_state()

    def _tick(self):
        if not self._disposed and (self.isVisible() or self._has_pending_run()):
            self._refresh_state()

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_ready", False) and not self._disposed:
            self.refresh_tasks()

    def hideEvent(self, event):
        self._cancel_drag()
        super().hideEvent(event)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._cancel_drag()
        self.timer.stop()
        communicate.task.disconnect(self._on_task_event)
        communicate.task_list_updated.disconnect(self.refresh_tasks)
