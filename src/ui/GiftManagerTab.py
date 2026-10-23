from ok import og, relative_box
from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    GroupHeaderCardWidget,
    HeaderCardWidget,
    ImageLabel,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    SmoothScrollArea,
    SwitchButton,
    TitleLabel,
)

from src.events import communicate
from src.gifts.GiftManager import GiftManager
from src.gifts.layout import GIFT_LAYOUT
from src.tasks.daily.GiftTask import GiftTask
from src.ui.foundation.images import cv_to_pixmap
from src.ui.foundation.widgets.cards import BorderCardWidget


class GiftPriorityCard(BorderCardWidget):
    slot_clicked = Signal(int)

    def __init__(self, slot: int, image, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.blocked = False
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumWidth(128)
        self.setFixedHeight(132)
        self.caption = BodyLabel(self)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.image = ImageLabel(self)
        self.image.setFixedSize(108, 72)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.image.setImage(
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
            self.caption.setText(og.app.tr("不可赠送"))
            self.setBorderColor(QColor("#d13438"))
            self._position_content()
            return
        if priority is None:
            self.caption.setText(og.app.tr("不赠送"))
            self.setBorderColor(None)
        else:
            self.caption.setText(og.app.tr("优先级 {}").format(priority))
            self.setBorderColor(QColor("#00b7c3"))
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

    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.HEART
        self.tr_name = self.tr("羁遇赠礼")
        self.manager = GiftManager()
        self.current_profile_id: str | None = None
        self._loading_profile = False
        self.gift_cards: list[GiftPriorityCard] = []
        self.task: GiftTask | None = None
        self._capture_pending = False

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        root.addWidget(self._build_profile_panel(), 1)
        root.addWidget(self._build_editor_panel(), 4)

        self.capture_button.clicked.connect(lambda: self._start_capture(None))
        self.recapture_button.clicked.connect(lambda: self._start_capture(self.current_profile_id))
        self.delete_button.clicked.connect(self._delete_current)
        self.start_button.clicked.connect(self._start_run)
        self.stop_button.clicked.connect(self._stop_run)
        self.profile_list.currentItemChanged.connect(self._on_profile_selected)
        self.enabled_switch.checkedChanged.connect(self._update_enabled_status)
        self.enabled_switch.checkedChanged.connect(self._save_current_options)
        self.count_combo.currentIndexChanged.connect(self._save_current_options)
        self.name_edit.editingFinished.connect(self._save_current_options)
        communicate.task.connect(self._on_framework_task_changed)

        self._refresh_profiles()
        self._refresh_task_controls()

    @property
    def name(self):  # type: ignore
        return self.tr_name

    def _update_enabled_status(self, enabled: bool) -> None:
        self.enabled_status.setText(self.tr("已启用") if enabled else self.tr("未启用"))

    def _build_profile_panel(self) -> SimpleCardWidget:
        panel = SimpleCardWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.capture_button = PrimaryPushButton(FluentIcon.CAMERA, self.tr("新增角色"), panel)
        self.recapture_button = PushButton(FluentIcon.SYNC, self.tr("更新当前角色"), panel)
        self.delete_button = PushButton(FluentIcon.DELETE, self.tr("删除当前角色"), panel)
        for button in (self.capture_button, self.recapture_button, self.delete_button):
            layout.addWidget(button)

        self.profile_list = ListWidget(panel)
        layout.addWidget(self.profile_list, 1)

        self.start_button = PrimaryPushButton(FluentIcon.PLAY, self.tr("开始赠礼"), panel)
        self.stop_button = PushButton(FluentIcon.CLOSE, self.tr("停止"), panel)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        return panel

    def _build_editor_panel(self) -> QWidget:
        editor = QWidget(self)
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(TitleLabel(self.tr_name, editor))

        hint = BodyLabel(
            self.tr("在游戏赠礼页点击{}。点击礼物卡片可设定优先级，再次点击取消。").format(
                self.tr("新增角色")
            ),
            editor,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.name_card = SimpleCardWidget(editor)
        name_layout = QHBoxLayout(self.name_card)
        name_layout.setContentsMargins(14, 10, 14, 10)
        self.name_preview = ImageLabel(self.name_card)
        name_layout.addWidget(BodyLabel(self.tr("角色名称"), self.name_card))
        name_layout.addWidget(self.name_preview, 1)
        layout.addWidget(self.name_card)

        self.settings_card = GroupHeaderCardWidget(self.tr("设置"), editor)

        status_widget = QWidget(self.settings_card)
        status_layout = QHBoxLayout(status_widget)
        self.enabled_switch = SwitchButton(status_widget)
        self.enabled_switch.setOnText("")
        self.enabled_switch.setOffText("")
        self.enabled_status = BodyLabel(self.tr("未启用"), status_widget)
        status_layout.addWidget(self.enabled_switch)
        status_layout.addWidget(self.enabled_status)

        self.name_edit = LineEdit(self.settings_card)
        self.name_edit.setMinimumWidth(360)
        self.name_edit.setPlaceholderText(self.tr("角色显示名称"))

        self.count_combo = ComboBox(self.settings_card)
        for count in range(1, 4):
            self.count_combo.addItem(str(count), userData=count)

        self.settings_card.addGroup(
            FluentIcon.HEART,
            self.tr("赠礼状态"),
            "",
            status_widget,
        ).contentLabel.hide()
        self.settings_card.addGroup(
            FluentIcon.EDIT,
            self.tr("名称"),
            "",
            self.name_edit,
        ).contentLabel.hide()
        self.settings_card.addGroup(
            FluentIcon.TAG,
            self.tr("赠送次数"),
            "",
            self.count_combo,
        ).contentLabel.hide()

        layout.addWidget(self.settings_card)

        gifts_card = HeaderCardWidget(editor)
        gifts_card.setTitle(self.tr("礼物优先级"))
        gifts_card.viewLayout.setContentsMargins(14, 14, 14, 14)
        self.gift_grid_widget = QWidget(gifts_card.view)
        self.gift_grid = QGridLayout(self.gift_grid_widget)
        self.gift_grid.setContentsMargins(0, 0, 0, 0)
        self.gift_grid.setSpacing(10)
        self.gift_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        gift_scroll = SmoothScrollArea(gifts_card.view)
        gift_scroll.setWidgetResizable(True)
        gift_scroll.setViewportMargins(0, 0, 12, 0)
        gift_scroll.setWidget(self.gift_grid_widget)
        gift_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.gift_grid_widget.setStyleSheet("background: transparent;")
        gifts_card.viewLayout.addWidget(gift_scroll, 1)
        layout.addWidget(gifts_card, 1)
        return editor

    def _show_error(self, title: str, content: str) -> None:
        InfoBar.error(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4500,
            parent=self.window(),
        )

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
        self.profile_list.blockSignals(False)
        if self.profile_list.currentItem() is None and self.profile_list.count():
            self.profile_list.setCurrentRow(0)
        current_item = self.profile_list.currentItem()
        self.current_profile_id = (
            current_item.data(Qt.ItemDataRole.UserRole) if current_item is not None else None
        )
        self._render_current()

    def _on_profile_selected(self, current, _previous) -> None:
        self.current_profile_id = (
            current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        )
        self._render_current()

    def _clear_gift_grid(self) -> None:
        self.gift_cards = []
        while self.gift_grid.count():
            item = self.gift_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_current(self) -> None:
        self._loading_profile = True
        self._clear_gift_grid()
        profile = (
            self.manager.get_profile(self.current_profile_id) if self.current_profile_id else None
        )
        has_profile = profile is not None
        self.recapture_button.setEnabled(has_profile)
        self.delete_button.setEnabled(has_profile)
        self.enabled_switch.setEnabled(has_profile)
        self.count_combo.setEnabled(has_profile)
        self.name_edit.setEnabled(has_profile)
        self.name_card.setVisible(has_profile)
        self.gift_grid_widget.setVisible(has_profile)
        if not profile:
            self.enabled_switch.setChecked(False)
            self._update_enabled_status(False)
            self.count_combo.setCurrentIndex(2)
            self.name_edit.clear()
            self.name_preview.clear()
            self._loading_profile = False
            return

        self.enabled_switch.setChecked(profile["enabled"])
        self._update_enabled_status(profile["enabled"])
        self.count_combo.setCurrentIndex(profile["target_count"] - 1)
        self.name_edit.setText(profile["display_name"])
        frame = self.manager.load_frame(profile)
        if frame is not None:
            height, width = frame.shape[:2]
            name_box = relative_box(
                width, height, *GIFT_LAYOUT.name_ratio, name="gift_character_name"
            )
            name_image = name_box.crop_frame(frame)
            self.name_preview.setImage(
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
        self._loading_profile = False

    def _toggle_gift_slot(self, slot: int) -> None:
        if self._loading_profile or not self.current_profile_id:
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
        if self._loading_profile or not self.current_profile_id:
            return
        self.manager.update_profile(
            self.current_profile_id,
            enabled=self.enabled_switch.isChecked(),
            target_count=self.count_combo.currentData(),
            display_name=self.name_edit.text(),
        )
        self._refresh_profiles(self.current_profile_id)

    def _start_capture(self, profile_id: str | None) -> None:
        task = self._get_task()
        if task is None:
            self._show_error(self.tr("捕获失败"), self.tr("赠礼任务未注册"))
            return
        self.capture_button.setEnabled(False)
        self.recapture_button.setEnabled(False)
        self._capture_pending = True
        task.capture_profile(profile_id)
        og.app.start_controller.start(task)

    def _on_framework_task_changed(self, task) -> None:
        if task is not self._get_task():
            return
        if task.running or task.mode is not None:
            self._refresh_task_controls(task)
            return
        if self._capture_pending:
            self._capture_pending = False
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
        self.capture_button.setEnabled(True)
        self.recapture_button.setEnabled(self.current_profile_id is not None)
        if error:
            self._show_error(self.tr("捕获失败"), error)
            return
        profile = self.manager.get_profile(profile_id)
        try:
            if profile:
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
        if not self.manager.get_enabled_profiles():
            self._show_error(self.tr("无法开始"), self.tr("请先捕获、选择礼物并启用至少一个角色。"))
            return
        task = self._get_task()
        if task is None:
            self._show_error(self.tr("无法开始"), self.tr("赠礼任务未注册"))
            return
        task.run_gift_mode()
        og.app.start_controller.start(task)
        self._refresh_task_controls(task)

    def _stop_run(self) -> None:
        task = self._get_task()
        if task is None:
            return
        if task.executor.current_task is task:
            task.executor.stop_current_task()
        else:
            task.disable()
            task.unpause()
        self._refresh_task_controls(task)

    def _refresh_task_controls(self, task: GiftTask | None = None) -> None:
        task = task or self._get_task()
        if task is None:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            return
        active = task.running or task.enabled
        self.stop_button.setEnabled(active)
        if task.paused:
            self.start_button.setText(self.tr("继续赠礼"))
            self.start_button.setEnabled(True)
        else:
            self.start_button.setText(self.tr("开始赠礼"))
            self.start_button.setEnabled(not active)
