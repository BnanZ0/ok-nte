from typing import Literal

from ok import og
from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon,
    Flyout,
    ImageLabel,
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TransparentToolButton,
)

from src.char.custom.CustomCharManager import CustomCharManager
from src.tasks.trigger.AutoCombatTask import AutoCombatTask, scanner_signals
from src.ui.common import (
    COMBO,
    TEAM_MANAGEMENT,
    SearchableComboBox,
    char_manager_signals,
    cv_to_pixmap,
)


def tr_fmt(text_id, **kwargs):
    t = og.app.tr(text_id)
    for k, v in kwargs.items():
        t = t.replace(f"{{{k}}}", str(v))
    return t


class NewCharDialog(MessageBoxBase):
    def __init__(self, mat, manager: CustomCharManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.tr_title = og.app.tr("记录新特征")
        self.tr_name_ph = og.app.tr("输入或选择关联的角色名称")
        self.tr_list_ph = tr_fmt("输入或选择绑定的{combo} (可选)", combo=COMBO)

        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(
            SubtitleLabel(self.tr_title, self), alignment=Qt.AlignmentFlag.AlignCenter
        )

        img_label = ImageLabel()
        img_label.setImage(
            cv_to_pixmap(mat).scaled(
                80,
                80,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.viewLayout.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.existing_chars = list(self.manager.get_all_characters().keys())
        self.char_combo = SearchableComboBox()
        self.char_combo.setPlaceholderText(self.tr_name_ph)
        self.char_combo.addItems([""] + self.existing_chars)
        self.char_combo.currentTextChanged.connect(self._on_char_select)
        self.viewLayout.addWidget(self.char_combo)

        self.combo_list = SearchableComboBox()
        self.combo_list.setPlaceholderText(self.tr_list_ph)
        self.combo_list.addItem("", userData="")
        for label, combo_ref in self.manager.get_all_combo_items():
            self.combo_list.addItem(label, userData=combo_ref)
        self.viewLayout.addWidget(self.combo_list)

        self.widget.setMinimumWidth(320)

    def _on_char_select(self, text):
        if not text:
            return
        char_info = self.manager.get_character_info(text)
        combo_value = char_info.get("combo_ref", "") if isinstance(char_info, dict) else ""
        if combo_value:
            combo_ref = self.manager.to_combo_ref(combo_value)
            idx = self.combo_list.findData(combo_ref)
            if idx >= 0:
                self.combo_list.setCurrentIndex(idx)
            else:
                self.combo_list.setCurrentText(self.manager.to_combo_label(combo_ref))
        elif isinstance(char_info, dict):
            self.combo_list.setCurrentIndex(0)

    def get_data(self):
        char_name = self.char_combo.currentText().strip()
        combo_label = self.combo_list.currentText().strip()
        combo_ref = self.manager.to_combo_ref(combo_label)
        idx = self.combo_list.currentIndex()
        if idx >= 0 and combo_label == self.combo_list.itemText(idx):
            data = self.combo_list.itemData(idx)
            if isinstance(data, str):
                combo_ref = data
        return char_name, combo_ref


class SlotCard(CardWidget):
    def __init__(self, index, manager: CustomCharManager, parent=None):
        super().__init__(parent)
        self.index = index
        self.manager = manager
        self.tr_match_success = og.app.tr("匹配成功: {}")
        self.tr_unrecognized = og.app.tr("未能识别该特征")
        self.tr_no_image = og.app.tr("无画面")
        self.tr_slot_title = og.app.tr("{} 号位")
        self.tr_scan_prompt = og.app.tr("点击上方按钮扫描...")
        self.tr_action_btn = og.app.tr("未识别，关联新特征")

        self.shadow_effect = QGraphicsDropShadowEffect(self)
        self.shadow_effect.setBlurRadius(30)
        self.shadow_effect.setOffset(2, 2)
        self.shadow_effect.setColor(QColor(0, 0, 0, 40))
        self.setGraphicsEffect(self.shadow_effect)

        self.vbox = QVBoxLayout(self)
        self.title = SubtitleLabel(self.tr_slot_title.format(index + 1))
        self.image = ImageLabel()
        self.image.setFixedSize(120, 120)
        self.status = BodyLabel(self.tr_scan_prompt)
        self.btn_act = PrimaryPushButton(self.tr_action_btn, self)
        self.btn_act.hide()

        self.vbox.addWidget(self.title, alignment=Qt.AlignmentFlag.AlignCenter)
        self.vbox.addWidget(self.image, alignment=Qt.AlignmentFlag.AlignCenter)
        self.vbox.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignCenter)
        self.vbox.addWidget(self.btn_act, alignment=Qt.AlignmentFlag.AlignCenter)

        self.btn_act.clicked.connect(self.on_action)
        self.current_mat = None
        self.current_w = 0
        self.current_h = 0

    def update_result(self, mat, w, h, match_name):
        self.current_mat = mat
        self.current_w = w
        self.current_h = h
        if mat is not None and getattr(mat, "size", 0) > 0:
            pixmap = cv_to_pixmap(mat)
            self.image.setImage(
                pixmap.scaled(
                    120,
                    120,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            empty_pixmap = QPixmap(120, 120)
            empty_pixmap.fill(Qt.GlobalColor.transparent)
            self.image.setImage(empty_pixmap)

        if match_name:
            self.status.setText(self.tr_match_success.format(match_name))
            self.btn_act.hide()
        elif mat is not None:
            self.status.setText(self.tr_unrecognized)
            self.btn_act.show()
        else:
            self.status.setText(self.tr_no_image)
            self.btn_act.hide()

    def on_action(self):
        dialog = NewCharDialog(self.current_mat, self.manager, self.window())
        if dialog.exec():
            char_name, combo_ref = dialog.get_data()
            if char_name and self.current_mat is not None:
                self.manager.add_feature_to_character(
                    char_name,
                    self.current_mat,
                    width=self.current_w,
                    height=self.current_h,
                )
                self.manager.add_character(char_name, combo_ref)
                if (
                    combo_ref
                    and not self.manager.is_builtin_combo(combo_ref)
                    and not self.manager.is_custom_combo_exist(combo_ref)
                ):
                    self.manager.add_combo(combo_ref, "")
                self.update_result(self.current_mat, self.current_w, self.current_h, char_name)
                char_manager_signals.refresh_tab.emit()


class FixedTeamSlotCard(CardWidget):
    def __init__(self, index, manager: CustomCharManager, parent=None):
        super().__init__(parent)
        self.index = index
        self.manager = manager
        self.tr_slot_title = og.app.tr("{} 号位")
        self.tr_char_ph = og.app.tr("角色")
        self.tr_combo_ph = COMBO

        self.shadow_effect = QGraphicsDropShadowEffect(self)
        self.shadow_effect.setBlurRadius(30)
        self.shadow_effect.setOffset(2, 2)
        self.shadow_effect.setColor(QColor(0, 0, 0, 40))
        self.setGraphicsEffect(self.shadow_effect)

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(14, 14, 14, 14)
        self.vbox.setSpacing(10)

        self.title = SubtitleLabel(self.tr_slot_title.format(index + 1))
        self.char_combo = SearchableComboBox()
        self.char_combo.setPlaceholderText(self.tr_char_ph)
        self.combo_list = SearchableComboBox()
        self.combo_list.setPlaceholderText(self.tr_combo_ph)

        self.vbox.addWidget(self.title, alignment=Qt.AlignmentFlag.AlignCenter)
        self.vbox.addWidget(self.char_combo)
        self.vbox.addWidget(self.combo_list)

        self.char_combo.currentTextChanged.connect(self._on_char_select)
        self.reload_options()

    def _resolve_combo_ref(self, text: str | None = None) -> str:
        if text is None:
            text = self.combo_list.currentText()
        text = str(text or "").strip()
        idx = self.combo_list.currentIndex()
        if idx >= 0 and text == self.combo_list.itemText(idx):
            data = self.combo_list.itemData(idx)
            if isinstance(data, str):
                return data
        return self.manager.to_combo_ref(text)

    def _set_combo_by_ref(self, combo_ref: str):
        combo_ref = self.manager.to_combo_ref(combo_ref)
        combo_label = self.manager.to_combo_label(combo_ref)
        idx = self.combo_list.findData(combo_ref)
        if idx >= 0:
            self.combo_list.setCurrentIndex(idx)
        else:
            self.combo_list.setCurrentText(combo_label)

    def reload_options(self):
        current_char, current_combo_ref = self.get_data()

        self.char_combo.blockSignals(True)
        self.char_combo.clear()
        self.char_combo.addItem("")
        for name in self.manager.get_all_characters().keys():
            self.char_combo.addItem(name)
        self.char_combo.setCurrentText(current_char)
        self.char_combo.blockSignals(False)

        self.combo_list.blockSignals(True)
        self.combo_list.clear()
        self.combo_list.addItem("", userData="")
        for label, combo_ref in self.manager.get_all_combo_items():
            self.combo_list.addItem(label, userData=combo_ref)
        self._set_combo_by_ref(current_combo_ref)
        self.combo_list.blockSignals(False)

    def _on_char_select(self, text):
        if not text:
            return
        char_info = self.manager.get_character_info(text)
        combo_value = char_info.get("combo_ref", "") if isinstance(char_info, dict) else ""
        if combo_value:
            self._set_combo_by_ref(combo_value)
        elif isinstance(char_info, dict):
            self.combo_list.setCurrentIndex(0)

    def set_data(self, char_name: str, combo_ref: str):
        self.char_combo.blockSignals(True)
        self.char_combo.setCurrentText(char_name)
        self.char_combo.blockSignals(False)

        self.combo_list.blockSignals(True)
        if combo_ref:
            self._set_combo_by_ref(combo_ref)
        else:
            self.combo_list.setCurrentIndex(0)
        self.combo_list.blockSignals(False)

    def get_data(self):
        char_name = self.char_combo.currentText().strip()
        combo_ref = self._resolve_combo_ref()
        if not char_name:
            combo_ref = ""
        return char_name, combo_ref


class TeamManagerTab(CustomTab):
    def __init__(self, manager: CustomCharManager = None, owner=None):
        super().__init__()
        self.owner = owner
        self._executor = None
        self.tr_scan_btn = og.app.tr("扫描队伍")
        self.tr_scanning = og.app.tr("扫描中...")
        # self.tr_analyzing = og.app.tr("正在分析...")
        self.tr_no_feature = og.app.tr("未获取到特征")
        self.tr_name_tab = TEAM_MANAGEMENT
        self.tr_scan_desc = og.app.tr("不扫描也可自动战斗，将使用通用脚本")
        self.tr_fixed_team_title = og.app.tr("固定队伍")
        self.tr_fixed_team_enabled = og.app.tr("已启用 {}/4")
        self.tr_fixed_team_saved = og.app.tr("已保存 {}/4")
        self.tr_fixed_team_empty = og.app.tr("未启用")
        self.tr_fill_from_scan = og.app.tr("填入扫描")
        self.tr_save_fixed_team = og.app.tr("启用")
        self.tr_update_fixed_team = og.app.tr("更新")
        self.tr_disable_fixed_team = og.app.tr("停用")
        self.tr_clear_fixed_team = og.app.tr("清空")
        self.tr_fill_failed_title = og.app.tr("没有可用扫描结果")
        self.tr_fill_failed_desc = og.app.tr("先扫描或手动填写")
        self.tr_fill_partial_title = og.app.tr("已填入扫描结果")
        self.tr_fill_partial_desc = og.app.tr("已填入 {}")
        self.tr_save_success_title = tr_fmt(
            "{fixed_team}已保存", fixed_team=self.tr_fixed_team_title
        )
        self.tr_save_success_desc = tr_fmt(
            "已启用{fixed_team}", fixed_team=self.tr_fixed_team_title
        )
        self.tr_disable_success_title = tr_fmt(
            "{fixed_team}已停用", fixed_team=self.tr_fixed_team_title
        )
        self.tr_disable_success_desc = og.app.tr("已恢复自动识别")
        self.tr_clear_success_title = tr_fmt(
            "{fixed_team}已清空", fixed_team=self.tr_fixed_team_title
        )
        self.tr_clear_success_desc = og.app.tr("已清空槽位")
        self.tr_fixed_team_desc = og.app.tr(
            "将优先使用固定角色进行战斗，未启用或槽位为空时自动识别"
        )
        self.tr_scan_tips = tr_fmt(
            '增加 <b style="color: #0078d7;">角色特征</b> 后将自动判断当前角色。<br>'
            '如果不想管理 <b style="color: #0078d7;">角色特征</b>，可以直接启用 '
            '<b style="color: #0078d7;">{fixed_team}</b> 功能。',
            fixed_team=self.tr_fixed_team_title,
        )
        self.tr_fixed_team_tips = tr_fmt(
            '<b style="color: #0078d7;">角色</b> 和 '
            '<b style="color: #0078d7;">{combo}</b> '
            '支持输入并创建，也支持选择已有项。',
            combo=COMBO,
        )

        self.manager = manager or CustomCharManager()
        self.icon = FluentIcon.CAMERA
        self.last_scan_results = []
        self.logger.info("Init TeamManagerTab")

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(20, 20, 20, 20)
        self.vbox.setSpacing(20)

        self.scan_card = CardWidget(self)
        self.scan_layout = QVBoxLayout(self.scan_card)
        self.scan_layout.setContentsMargins(16, 16, 16, 16)
        self.scan_layout.setSpacing(12)

        self.scan_header = QHBoxLayout()
        self.scan_title = SubtitleLabel(self.tr_scan_btn)
        self.scan_header.addWidget(self.scan_title)

        self.scan_info_btn = TransparentToolButton(FluentIcon.INFO, self)
        self.scan_info_btn.clicked.connect(self.show_scan_flyout)
        self.scan_header.addWidget(self.scan_info_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self.scan_header.addStretch(1)

        self.scan_btn = PrimaryPushButton(FluentIcon.SYNC, self.tr_scan_btn)
        self.scan_btn.clicked.connect(self.on_scan_clicked)
        self.scan_header.addWidget(self.scan_btn)
        self.scan_layout.addLayout(self.scan_header)

        self.cards_layout = QHBoxLayout()
        self.slots: list[SlotCard] = []
        for i in range(4):
            card = SlotCard(i, self.manager, self)
            self.slots.append(card)
            self.cards_layout.addWidget(card)
        self.scan_layout.addLayout(self.cards_layout)

        self.scan_desc = BodyLabel(self.tr_scan_desc)
        self.scan_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_layout.addWidget(self.scan_desc)

        self.vbox.addWidget(self.scan_card)

        self.fixed_team_card = CardWidget(self)
        self.fixed_team_layout = QVBoxLayout(self.fixed_team_card)
        self.fixed_team_layout.setContentsMargins(16, 16, 16, 16)
        self.fixed_team_layout.setSpacing(12)

        self.fixed_team_header = QHBoxLayout()
        self.fixed_team_header_text = QVBoxLayout()
        self.fixed_team_title_row = QHBoxLayout()
        self.fixed_team_title = SubtitleLabel(self.tr_fixed_team_title)
        self.fixed_team_title_row.addWidget(self.fixed_team_title)

        self.fixed_team_info_btn = TransparentToolButton(FluentIcon.INFO, self)
        self.fixed_team_info_btn.clicked.connect(self.show_fixed_team_flyout)
        self.fixed_team_title_row.addWidget(
            self.fixed_team_info_btn, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self.fixed_team_title_row.addStretch(1)

        self.fixed_team_status = BodyLabel(self.tr_fixed_team_empty)
        self.fixed_team_status.setWordWrap(True)
        self.fixed_team_header_text.addLayout(self.fixed_team_title_row)
        self.fixed_team_header_text.addWidget(self.fixed_team_status)
        self.fixed_team_header.addLayout(self.fixed_team_header_text, 1)

        self.fill_fixed_team_btn = PushButton(self.tr_fill_from_scan, self)
        self.fill_fixed_team_btn.clicked.connect(self.on_fill_from_scan)
        self.fixed_team_header.addWidget(self.fill_fixed_team_btn)

        self.save_fixed_team_btn = PrimaryPushButton(FluentIcon.SAVE, self.tr_save_fixed_team, self)
        self.save_fixed_team_btn.clicked.connect(self.on_save_fixed_team)
        self.fixed_team_header.addWidget(self.save_fixed_team_btn)

        self.disable_fixed_team_btn = PushButton(self.tr_disable_fixed_team, self)
        self.disable_fixed_team_btn.clicked.connect(self.on_disable_fixed_team)
        self.fixed_team_header.addWidget(self.disable_fixed_team_btn)

        self.clear_fixed_team_btn = PushButton(FluentIcon.DELETE, self.tr_clear_fixed_team, self)
        self.clear_fixed_team_btn.clicked.connect(self.on_clear_fixed_team)
        self.fixed_team_header.addWidget(self.clear_fixed_team_btn)

        self.fixed_team_layout.addLayout(self.fixed_team_header)

        self.fixed_team_slots_layout = QHBoxLayout()
        self.fixed_team_slots: list[FixedTeamSlotCard] = []
        for i in range(4):
            card = FixedTeamSlotCard(i, self.manager, self)
            self.fixed_team_slots.append(card)
            self.fixed_team_slots_layout.addWidget(card)
        self.fixed_team_layout.addLayout(self.fixed_team_slots_layout)

        self.fixed_team_desc = BodyLabel(self.tr_fixed_team_desc)
        self.fixed_team_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fixed_team_layout.addWidget(self.fixed_team_desc)

        self.vbox.addWidget(self.fixed_team_card)

        self.vbox.addStretch(1)

        scanner_signals.scan_done.connect(self.on_scan_done)
        char_manager_signals.refresh_tab.connect(self.reload_fixed_team_options)
        self.refresh_fixed_team_state()

    @property
    def name(self) -> Literal["CustomTab"]:
        return self.tr_name_tab  # type: ignore
    
    @property
    def executor(self):
        return self.owner.executor if self.owner else self._executor
    
    @executor.setter
    def executor(self, value):
        self._executor = value

    def _show_bar(self, title: str, content: str, success=True):
        fn = InfoBar.success if success else InfoBar.error
        fn(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500 if success else 3500,
            parent=self.window(),
        )

    def _collect_fixed_team_slots(self, persist=False):
        slots = []
        filled_count = 0
        for card in self.fixed_team_slots:
            char_name, combo_ref = card.get_data()
            if char_name:
                filled_count += 1
                if persist:
                    if (
                        combo_ref
                        and not self.manager.is_builtin_combo(combo_ref)
                        and not self.manager.is_custom_combo_exist(combo_ref)
                    ):
                        self.manager.add_combo(combo_ref, "")
                    self.manager.add_character(char_name, combo_ref)
            else:
                combo_ref = ""
            slots.append(
                {
                    "char_name": char_name,
                    "combo_ref": combo_ref,
                }
            )
        return slots, filled_count

    def reload_fixed_team_options(self):
        for card in self.fixed_team_slots:
            card.reload_options()

    def refresh_fixed_team_state(self):
        fixed_team = self.manager.get_fixed_team()
        slots = fixed_team.get("slots", [])
        for i, card in enumerate(self.fixed_team_slots):
            slot = slots[i] if i < len(slots) else {}
            card.set_data(slot.get("char_name", ""), slot.get("combo_ref", ""))

        filled_count = sum(1 for slot in slots if slot.get("char_name"))
        if fixed_team.get("enabled") and filled_count:
            self.fixed_team_status.setText(self.tr_fixed_team_enabled.format(filled_count))
            self.save_fixed_team_btn.setText(self.tr_update_fixed_team)
            self.disable_fixed_team_btn.setEnabled(True)
        elif filled_count:
            self.fixed_team_status.setText(self.tr_fixed_team_saved.format(filled_count))
            self.save_fixed_team_btn.setText(self.tr_save_fixed_team)
            self.disable_fixed_team_btn.setEnabled(False)
        else:
            self.fixed_team_status.setText(self.tr_fixed_team_empty)
            self.save_fixed_team_btn.setText(self.tr_save_fixed_team)
            self.disable_fixed_team_btn.setEnabled(False)

    def on_scan_clicked(self):
        og.app.start_controller.handler.post(self.scan_team)

    def scan_team(self):
        og.app.start_controller.do_start()
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText(self.tr_scanning)
        for card in self.slots:
            # card.status.setText(self.tr_analyzing)
            card.btn_act.hide()
        self.get_task(AutoCombatTask).scan_team()

    def on_fill_from_scan(self):
        if not self.last_scan_results:
            self._show_bar(self.tr_fill_failed_title, self.tr_fill_failed_desc, success=False)
            return

        filled_count = 0
        for result in self.last_scan_results:
            idx = result.get("index")
            match_name = result.get("match")
            if not (0 <= idx < 4) or not match_name:
                continue
            char_info = self.manager.get_character_info(match_name) or {}
            combo_ref = char_info.get("combo_ref", "")
            self.fixed_team_slots[idx].set_data(match_name, combo_ref)
            filled_count += 1

        if filled_count == 0:
            self._show_bar(self.tr_fill_failed_title, self.tr_fill_failed_desc, success=False)
            return

        self._show_bar(self.tr_fill_partial_title, self.tr_fill_partial_desc.format(filled_count))

    def on_save_fixed_team(self):
        slots, filled_count = self._collect_fixed_team_slots(persist=True)
        if filled_count == 0:
            self.manager.clear_fixed_team()
            self.refresh_fixed_team_state()
            char_manager_signals.refresh_tab.emit()
            self._show_bar(self.tr_clear_success_title, self.tr_clear_success_desc)
        else:
            self.manager.set_fixed_team(True, slots)
            self.refresh_fixed_team_state()
            char_manager_signals.refresh_tab.emit()
            self._show_bar(self.tr_save_success_title, self.tr_save_success_desc)

    def on_disable_fixed_team(self):
        fixed_team = self.manager.get_fixed_team()
        self.manager.set_fixed_team(False, fixed_team.get("slots", []))
        self.refresh_fixed_team_state()
        self._show_bar(self.tr_disable_success_title, self.tr_disable_success_desc)

    def on_clear_fixed_team(self):
        self.manager.clear_fixed_team()
        self.refresh_fixed_team_state()
        char_manager_signals.refresh_tab.emit()
        self._show_bar(self.tr_clear_success_title, self.tr_clear_success_desc)

    def on_scan_done(self, results, error_msg=""):
        self.last_scan_results = results or []
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText(self.tr_scan_btn)

        if error_msg:
            self._show_bar("", error_msg, success=False)
            return

        if not results:
            for card in self.slots:
                card.update_result(None, 0, 0, "")
                card.status.setText(self.tr_no_feature)
            return

        updated_indices = set()
        for res in results:
            idx = res.get("index")
            mat = res.get("mat")
            w = res.get("width", 0)
            h = res.get("height", 0)
            match_name = res.get("match")
            if 0 <= idx < 4:
                self.slots[idx].update_result(mat, w, h, match_name)
                updated_indices.add(idx)

        for i in range(4):
            if i not in updated_indices:
                self.slots[i].update_result(None, 0, 0, "")
                self.slots[i].status.setText(self.tr_no_feature)

    def show_scan_flyout(self):
        Flyout.create(
            icon=InfoBarIcon.INFORMATION,
            title="Tips",
            content=self.tr_scan_tips,
            target=self.scan_info_btn,
            parent=self,
            isClosable=False,
        )

    def show_fixed_team_flyout(self):
        Flyout.create(
            icon=InfoBarIcon.INFORMATION,
            title="Tips",
            content=self.tr_fixed_team_tips,
            target=self.fixed_team_info_btn,
            parent=self,
            isClosable=False,
        )
