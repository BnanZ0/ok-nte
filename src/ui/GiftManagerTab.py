from ok import og, relative_box
from ok.ui.qt.kit import (
    Button,
    Card,
    ComboBox,
    Label,
    SettingRow,
    Switch,
    show_toast,
)
from ok.ui.qt.tasks.tasks_page import start_task, stop_task
from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.events import communicate
from src.gifts.GiftManager import GiftManager
from src.gifts.layout import GIFT_LAYOUT
from src.tasks.daily.GiftTask import GiftTask
from src.ui.foundation.images import cv_to_pixmap
from src.ui.foundation.widgets.cards import BorderCardWidget, SectionCard
from src.ui.foundation.widgets.help_popover import HelpPopover
from src.ui.foundation.widgets.lists import style_flat_collection
from src.ui.foundation.widgets.task_actions import add_task_actions


class GiftPriorityCard(BorderCardWidget):
    slot_clicked = Signal(int)

    def __init__(self, slot: int, image, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.blocked = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumWidth(128)
        self.setFixedHeight(132)
        self.caption = Label(parent=self)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.image = QLabel(self)
        self.image.setFixedSize(108, 72)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.image.setPixmap(
            cv_to_pixmap(image).scaled(
                104,
                72,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.set_priority(None)
        self.setBorderWidth(2)

    def set_priority(self, priority: int | None) -> None:
        if self.blocked:
            self.caption.setText(self.tr("不可赠送"))
            self.setBorderColor(QColor("#d13438"))
            self.setAccessibleName(self.caption.text())
            self.setToolTip(self.caption.text())
            self._position_content()
            return
        if priority is None:
            self.caption.setText(self.tr("不赠送"))
            self.setBorderColor(None)
        else:
            self.caption.setText(self.tr("优先级 {}").format(priority))
            self.setBorderColor(QColor("#00b7c3"))
        self.setAccessibleName(self.caption.text())
        self.setToolTip(self.caption.text())
        self._position_content()

    def set_blocked(self, blocked: bool) -> None:
        self.blocked = blocked
        self.setCursor(
            QCursor(
                Qt.CursorShape.ForbiddenCursor if blocked else Qt.CursorShape.PointingHandCursor
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_content()

    def _position_content(self) -> None:
        """Keep the gift artwork at the actual center; reserve only the bottom for its label."""
        self.image.move(
            max(0, (self.width() - self.image.width()) // 2),
            max(0, (self.height() - self.image.height()) // 2),
        )
        self.caption.adjustSize()
        self.caption.move(
            max(0, (self.width() - self.caption.width()) // 2),
            max(0, self.height() - self.caption.height() - 10),
        )

    def mouseReleaseEvent(self, e) -> None:
        clicked_here = self.rect().contains(e.position().toPoint())
        if not self.blocked and e.button() == Qt.MouseButton.LeftButton and clicked_here:
            self.slot_clicked.emit(self.slot)
        super().mouseReleaseEvent(e)


class GiftManagerTab(CustomTab):
    """A single-screen editor for captured character gifts and task controls."""

    icon = "heart"

    def __init__(self):
        super().__init__()
        self.tr_name = self.tr("羁遇赠礼")
        self.manager = GiftManager()
        self.current_profile_id: str | None = None
        self.gift_cards: list[GiftPriorityCard] = []
        self.task: GiftTask | None = None
        self._capture_pending = False
        self._disposed = False
        self._capture_launch_check = QTimer(self)
        self._capture_launch_check.setInterval(100)
        self._capture_launch_check.timeout.connect(self._check_capture_launch)

        self.vBoxLayout.setContentsMargins(20, 18, 20, 20)

        content = QWidget(self.view)
        root = QHBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        root.addWidget(self._build_profile_panel(), 1)
        root.addWidget(self._build_editor_panel(), 4)
        self.vBoxLayout.addWidget(content, 1)

        self.capture_button.clicked.connect(lambda: self._start_capture(None))
        self.recapture_button.clicked.connect(lambda: self._start_capture(self.current_profile_id))
        self.delete_button.clicked.connect(self._delete_current)
        self.start_button.clicked.connect(self._start_run)
        self.stop_button.clicked.connect(self._stop_run)
        self.pause_button.clicked.connect(self._pause_run)
        self.profile_list.currentItemChanged.connect(self._on_profile_selected)
        self.enabled_switch.toggled.connect(self._save_current_options)
        self.count_combo.currentIndexChanged.connect(self._save_current_options)
        self.name_edit.editingFinished.connect(self._save_current_options)
        communicate.task.connect(self._on_framework_task_changed)

        self._refresh_profiles()
        self._refresh_task_controls()

    @property
    def name(self):  # type: ignore
        return self.tr_name

    def _add_section_help(self, card: SectionCard, content: str) -> HelpPopover:
        title = self.tr("提示")
        button = Button(icon="info", variant="subtle", size="small", parent=card)
        button.setAccessibleName(title)
        button.setToolTip(title)
        popover = HelpPopover(self, title, content)
        button.clicked.connect(lambda: popover.toggle_for(button))
        card.header.setSpacing(6)
        card.header.addWidget(button)
        card.header.addStretch(1)
        return popover

    def _build_profile_panel(self) -> SectionCard:
        panel = SectionCard(self.tr("角色列表"), parent=self.view)
        self._profile_help = self._add_section_help(
            panel, self.tr("在游戏赠礼页点击{}").format(self.tr("新增角色"))
        )
        layout = panel.body
        self.capture_button = Button(
            self.tr("新增角色"), icon="camera", variant="primary", parent=panel
        )
        self.recapture_button = Button(self.tr("更新当前角色"), icon="refresh", parent=panel)
        self.delete_button = Button(self.tr("删除当前角色"), icon="trash", parent=panel)
        for button in (self.capture_button, self.recapture_button, self.delete_button):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(button)

        self.profile_list = QListWidget(panel)
        style_flat_collection(self.profile_list)
        layout.addWidget(self.profile_list, 1)
        self.pause_button, self.stop_button, self.start_button = add_task_actions(
            layout,
            pause_text=self.tr("Pause"),
            stop_text=self.tr("停止"),
            start_text=self.tr("开始赠礼"),
            stacked=True,
        )

        return panel

    def _build_editor_panel(self) -> QWidget:
        editor = QWidget(self.view)
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.name_card = Card(editor)
        name_layout = QHBoxLayout(self.name_card)
        name_layout.setContentsMargins(14, 10, 14, 10)
        name_layout.addWidget(Label(self.tr("角色名称"), "strong", self.name_card))
        self.name_preview = QLabel(self.name_card)
        self.name_preview.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.name_preview.setMinimumHeight(72)
        name_layout.addWidget(self.name_preview, 1)
        layout.addWidget(self.name_card)

        self.settings_card = SectionCard(self.tr("设置"), icon_name="gear", parent=editor)
        settings_body = self.settings_card.body
        settings_body.setContentsMargins(0, 0, 0, 0)
        settings_body.setSpacing(0)
        status_widget = QWidget(self.settings_card)
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.enabled_switch = Switch(parent=status_widget)
        self.enabled_status = Label(self.tr("未启用"), "body", status_widget)
        status_layout.addStretch(1)
        status_layout.addWidget(self.enabled_status)
        status_layout.addWidget(self.enabled_switch)
        settings_body.addWidget(SettingRow(self.tr("赠礼状态"), control=status_widget))

        self.name_edit = QLineEdit(self.settings_card)
        self.name_edit.setPlaceholderText(self.tr("角色显示名称"))
        name_row = SettingRow(self.tr("名称"), control=self.name_edit)
        name_row.set_first(False)
        settings_body.addWidget(name_row)

        self.count_combo = ComboBox(parent=self.settings_card)
        for count in range(1, 4):
            self.count_combo.addItem(str(count), userData=count)
        count_row = SettingRow(self.tr("赠送次数"), control=self.count_combo)
        count_row.set_first(False)
        settings_body.addWidget(count_row)

        layout.addWidget(self.settings_card)

        gifts_card = SectionCard(self.tr("礼物优先级"), parent=editor)
        self._priority_help = self._add_section_help(
            gifts_card, self.tr("点击礼物卡片可设定优先级，再次点击取消")
        )
        self.gift_grid_widget = QWidget(gifts_card)
        self.gift_grid = QGridLayout(self.gift_grid_widget)
        self.gift_grid.setContentsMargins(0, 0, 0, 0)
        self.gift_grid.setSpacing(10)
        self.gift_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        gift_scroll = QScrollArea(gifts_card)
        gift_scroll.setFrameShape(QFrame.Shape.NoFrame)
        gift_scroll.setWidgetResizable(True)
        gift_scroll.setWidget(self.gift_grid_widget)
        gift_scroll.viewport().setAutoFillBackground(False)
        self.gift_grid_widget.setAutoFillBackground(False)
        gifts_card.body.addWidget(gift_scroll, 1)
        self.gift_scroll = gift_scroll
        layout.addWidget(gifts_card, 1)
        return editor

    def _show_error(self, title: str, content: str) -> None:
        show_toast(self.window(), content, title=title, tone="danger", duration=4500)

    def _get_task(self) -> GiftTask | None:
        if self.task is None and self.executor is not None:
            self.task = self.get_task(GiftTask)
        return self.task

    def _refresh_profiles(self, selected_id=None) -> None:
        selected_id = selected_id or self.current_profile_id
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile_id, profile in self.manager.get_profiles().items():
            item = QListWidgetItem(profile["display_name"])
            item.setData(Qt.ItemDataRole.UserRole, profile_id)
            self.profile_list.addItem(item)
            if profile_id == selected_id:
                self.profile_list.setCurrentItem(item)
        if self.profile_list.currentItem() is None and self.profile_list.count():
            self.profile_list.setCurrentRow(0)
        self.profile_list.blockSignals(False)
        self._on_profile_selected(self.profile_list.currentItem(), None)

    def _on_profile_selected(self, current, _previous) -> None:
        self.current_profile_id = (
            current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        )
        self._render_current()

    def _clear_gift_grid(self) -> None:
        self.gift_cards = []
        while self.gift_grid.count():
            card = self.gift_grid.takeAt(0).widget()
            if card is not None:
                card.hide()
                card.deleteLater()

    def _render_current(self, *, refresh_gifts: bool = True) -> None:
        profile = (
            self.manager.get_profile(self.current_profile_id) if self.current_profile_id else None
        )
        has_profile = profile is not None
        self.recapture_button.setEnabled(has_profile and self.capture_button.isEnabled())
        self.delete_button.setEnabled(has_profile)
        self.enabled_switch.setEnabled(has_profile)
        self.count_combo.setEnabled(has_profile)
        self.name_edit.setEnabled(has_profile)
        self.name_card.setVisible(has_profile)
        self.gift_grid_widget.setVisible(has_profile)
        enabled = profile["enabled"] if has_profile else False
        target_count = profile["target_count"] if has_profile else 3
        display_name = profile["display_name"] if has_profile else ""
        with QSignalBlocker(self.enabled_switch), QSignalBlocker(self.count_combo):
            self.enabled_switch.setChecked(enabled)
            self.count_combo.setCurrentIndex(target_count - 1)
        self.enabled_status.setText(self.tr("已启用") if enabled else self.tr("未启用"))
        if self.name_edit.text() != display_name:
            self.name_edit.setText(display_name)
        current = self.profile_list.currentItem()
        if has_profile and current is not None:
            current.setText(display_name)

        if not refresh_gifts:
            return
        self._clear_gift_grid()
        self.name_preview.clear()
        if profile is None:
            return
        frame = self.manager.load_frame(profile)
        if frame is not None:
            height, width = frame.shape[:2]
            name_box = relative_box(
                width, height, *GIFT_LAYOUT.name_ratio, name="gift_character_name"
            )
            name_image = name_box.crop_frame(frame)
            self.name_preview.setPixmap(
                cv_to_pixmap(name_image).scaled(
                    300,
                    72,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            priorities = {slot: index + 1 for index, slot in enumerate(profile["selected_slots"])}
            blocked_slots = set(profile.get("blocked_slots", []))
            first_x, first_y, first_to_x, first_to_y = GIFT_LAYOUT.gift_first_ratio
            expand_ratio = 0.016
            first_y -= expand_ratio
            first_to_y += expand_ratio
            box_width = first_to_x - first_x
            box_height = first_to_y - first_y
            for index in range(GIFT_LAYOUT.gift_rows * GIFT_LAYOUT.gift_columns):
                row, column = divmod(index, GIFT_LAYOUT.gift_columns)
                gift_box = relative_box(
                    width,
                    height,
                    first_x + column * GIFT_LAYOUT.gift_column_step,
                    first_y + row * GIFT_LAYOUT.gift_row_step,
                    first_x + column * GIFT_LAYOUT.gift_column_step + box_width,
                    first_y + row * GIFT_LAYOUT.gift_row_step + box_height,
                    name=f"gift_slot_{index}",
                )
                card = GiftPriorityCard(
                    index,
                    gift_box.crop_frame(frame),
                    self.gift_grid_widget,
                )
                card.set_blocked(index in blocked_slots)
                card.set_priority(priorities.get(index))
                card.slot_clicked.connect(self._toggle_gift_slot)
                self.gift_cards.append(card)
                self.gift_grid.addWidget(card, index // 5, index % 5)

    def _toggle_gift_slot(self, slot: int) -> None:
        if not self.current_profile_id:
            return
        profile = self.manager.get_profile(self.current_profile_id)
        if not profile:
            return
        if slot in profile.get("blocked_slots", []):
            return
        slots = list(profile["selected_slots"])
        if slot in slots:
            slots.remove(slot)
        else:
            slots.append(slot)
        self.manager.update_profile(self.current_profile_id, selected_slots=slots)
        self._render_current()

    def _save_current_options(self) -> None:
        if not self.current_profile_id:
            return
        self.manager.update_profile(
            self.current_profile_id,
            enabled=self.enabled_switch.isChecked(),
            target_count=self.count_combo.currentData(),
            display_name=self.name_edit.text(),
        )
        self._render_current(refresh_gifts=False)

    def _start_capture(self, profile_id: str | None) -> None:
        task = self._get_task()
        if task is None:
            self._show_error(self.tr("捕获失败"), self.tr("赠礼任务未注册"))
            return
        if task.enabled or task.running or self._capture_pending:
            return
        self._capture_pending = True
        task.capture_profile(profile_id)
        self._refresh_task_controls(task)
        self._capture_launch_check.start()
        og.app.start_controller.start(task)

    def _check_capture_launch(self) -> None:
        task = self.task
        if task is None:
            self._capture_launch_check.stop()
            return
        controller = og.app.start_controller
        # The controller removes a successful launch from the pending queue
        # before enabling it. Wait for both steps before checking for failure.
        is_task_pending = getattr(controller, "is_task_pending", None)
        if controller.starting or (is_task_pending is not None and is_task_pending(task)):
            return
        self._capture_launch_check.stop()
        self._finish_unstarted_capture(task)

    def _finish_unstarted_capture(self, task: GiftTask) -> None:
        if self._disposed:
            return
        if (
            self._capture_pending
            and task.mode == GiftTask.MODE_CAPTURE
            and not task.enabled
            and not task.running
        ):
            self._capture_pending = False
            self._capture_launch_check.stop()
            task.mode = None
        self._refresh_task_controls(task)

    def _on_framework_task_changed(self, task) -> None:
        if self._disposed:
            return
        if task is not self._get_task():
            return
        if self._capture_pending and not task.running and task.mode is None:
            self._capture_pending = False
            self._capture_launch_check.stop()
            self._on_capture_done(
                task.capture_frame,
                task.capture_error,
                task.capture_name,
                list(task.capture_blocked_slots),
                task.capture_profile_id,
            )
        self._refresh_task_controls(task)

    def _on_capture_done(
        self,
        frame,
        error: str,
        recognized_name: str,
        blocked_slots: list[int],
        profile_id: str | None,
    ) -> None:
        if self._disposed:
            return
        if error:
            self._show_error(self.tr("捕获失败"), error)
            return
        profile = self.manager.get_profile(profile_id) if profile_id is not None else None
        try:
            if profile_id is not None and profile is not None:
                self.manager.recapture_profile(
                    profile_id,
                    frame,
                    profile["selected_slots"],
                    profile["target_count"],
                    profile["display_name"],
                    blocked_slots=blocked_slots,
                )
                selected_id = profile_id
            else:
                selected_id = self.manager.create_profile(
                    recognized_name or self.tr("未命名角色"),
                    frame,
                    [],
                    target_count=3,
                    blocked_slots=blocked_slots,
                )
            self._refresh_profiles(selected_id)
        except Exception as error:
            self._show_error(self.tr("保存失败"), str(error).strip() or type(error).__name__)

    def _delete_current(self) -> None:
        if not self.current_profile_id:
            return
        self.manager.delete_profile(self.current_profile_id)
        self.current_profile_id = None
        self._refresh_profiles()

    def _start_run(self) -> None:
        task = self._get_task()
        resume = bool(task is not None and task.enabled and task.paused)
        if task is not None and (task.running or task.enabled) and not resume:
            return
        if not resume and not self.manager.get_enabled_profiles():
            self._show_error(self.tr("无法开始"), self.tr("请先捕获、选择礼物并启用至少一个角色。"))
            return
        if task is None:
            self._show_error(self.tr("无法开始"), self.tr("赠礼任务未注册"))
            return
        if not resume:
            task.run_gift_mode()
        start_task(task, self)
        self._refresh_task_controls(task)

    def _pause_run(self) -> None:
        task = self._get_task()
        if (
            task is not None
            and task.enabled
            and task.running
            and not task.paused
            and task.mode != GiftTask.MODE_CAPTURE
        ):
            task.pause()
            self._refresh_task_controls(task)

    def _stop_run(self) -> None:
        task = self._get_task()
        if task is None:
            return
        stop_task(task)
        self._finish_unstarted_capture(task)

    def _refresh_task_controls(self, task: GiftTask | None = None) -> None:
        task = task or self._get_task()
        active = bool(task is not None and (task.running or task.enabled))
        paused = bool(task is not None and task.enabled and task.paused)
        can_pause = bool(
            task is not None
            and task.enabled
            and task.running
            and not paused
            and task.mode != GiftTask.MODE_CAPTURE
        )
        self.start_button.setVisible(not active or paused)
        self.start_button.setEnabled(not self._capture_pending)
        self.start_button.setText(self.tr("继续赠礼") if paused else self.tr("开始赠礼"))
        self.stop_button.setVisible(active)
        self.stop_button.setEnabled(active)
        self.pause_button.setVisible(can_pause)
        can_capture = not active and not self._capture_pending
        self.capture_button.setEnabled(can_capture)
        self.recapture_button.setEnabled(can_capture and self.current_profile_id is not None)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._capture_pending = False
        self._capture_launch_check.stop()
        for popover in (self._profile_help, self._priority_help):
            popover.close_popover()
            popover.deleteLater()
        communicate.task.disconnect(self._on_framework_task_changed)
