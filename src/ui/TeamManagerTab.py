from dataclasses import replace
from pathlib import Path
from typing import Literal

from ok import og
from ok.ui.qt.kit import Button, Card, Dialog, Label, theme
from ok.ui.qt.kit import icon as kit_icon
from ok.ui.qt.widget.CustomTab import CustomTab
from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from src.char.custom.CustomCharManager import CustomCharManager
from src.char.workshop.archive import default_archive_name
from src.char.workshop.models import PackageSlot, TeamPackage
from src.char.workshop.repository import IndexSource, WorkshopRepository
from src.char.workshop.service import (
    WorkshopInstallError,
    WorkshopInstallErrorCode,
    WorkshopPackageService,
)
from src.events import communicate
from src.tasks.DebugCharTask import DebugCharTask, TeamScanResult
from src.ui.features.characters.safety_dialog import confirm_external_code_import
from src.ui.features.characters.workshop_dialog import (
    BackgroundCall,
    PackageImportDialog,
    PackageMetadataDialog,
    WorkshopDialog,
    workshop_error_text,
)
from src.ui.foundation.dialogs import exec_owned_dialog
from src.ui.foundation.images import cv_to_pixmap
from src.ui.foundation.info_bar import show_bar
from src.ui.foundation.name_input_dialog import NameInputDialog
from src.ui.foundation.text import highlight_bracketed_text
from src.ui.foundation.widgets.button_styles import LinkButton
from src.ui.foundation.widgets.cards import BorderCardWidget, SectionCard
from src.ui.foundation.widgets.help_popover import HelpPopover
from src.ui.foundation.widgets.lists import COLLECTION_STATUS_ROLE
from src.ui.foundation.widgets.search import (
    SearchableComboBox,
    SearchableListWidget,
)
from src.ui.foundation.widgets.themed_toolbar import CommandBarCard, ThemedToolBar
from src.ui.icons import resource_icon

WORKSHOP_REPOSITORY = {
    "github": {
        "index_url": "https://raw.githubusercontent.com/BnanZ0/ok-nte-char-code/main/teams.json",
        "archive_base_url": "https://raw.githubusercontent.com/BnanZ0/ok-nte-char-code/main",
    },
    "cnb": {
        "index_url": "https://cnb.cool/BnanZ0/ok-nte-char-code/-/git/raw/main/teams.json",
        "archive_base_url": "https://cnb.cool/BnanZ0/ok-nte-char-code/-/git/raw/main",
    },
    "upload_url": "https://github.com/BnanZ0/ok-nte-char-code",
}


class NewCharDialog(Dialog):
    """Select or create a character while associating a scanned feature."""

    def __init__(self, mat, manager: CustomCharManager, parent=None):
        super().__init__(parent, "", width=360)
        self.manager = manager
        self.title_label.setText(og.app.tr("关联特征"))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tr_name_ph = og.app.tr("输入或选择关联的角色名称")
        self.tr_list_ph = og.app.tr("输入或选择绑定的{combo} (可选)").format(
            combo=og.app.tr("出招表")
        )

        self.body.setSpacing(10)

        img_label = QLabel()
        img_label.setPixmap(
            cv_to_pixmap(mat).scaled(
                80,
                80,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.body.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.tip_label = Label(og.app.tr("※ 列表可直接输入并创建"), "caption")
        self.body.addWidget(self.tip_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.char_combo = SearchableComboBox()
        self.char_combo.lineEdit().setPlaceholderText(self.tr_name_ph)
        self.char_combo.addItem("", userData="")
        for char_id, char_data in self.manager.get_all_characters().items():
            self.char_combo.addItem(char_data["char_name"], userData=char_id)
        self.char_combo.currentTextChanged.connect(self._on_char_select)
        self.ok_button.setEnabled(False)
        self.body.addWidget(self.char_combo)

        self.combo_list = SearchableComboBox()
        self.combo_list.lineEdit().setPlaceholderText(self.tr_list_ph)
        self.combo_list.addItem("", userData="")
        for combo_name, combo_id in self.manager.get_all_impl_items(with_source_prefix=True):
            self.combo_list.addItem(combo_name, userData=combo_id)
        self.body.addWidget(self.combo_list)

    def _on_char_select(self, text):
        self.ok_button.setEnabled(bool(text.strip()))
        if not text.strip():
            return
        idx = self.char_combo.findText(text)
        char_id = self.char_combo.itemData(idx) if idx >= 0 else ""
        char_info = self.manager.get_character_info_by_id(char_id)
        combo_id = char_info["impl_id"] if char_info else ""
        if combo_id:
            idx = self.combo_list.findData(combo_id)
            if idx >= 0:
                self.combo_list.setCurrentIndex(idx)
            else:
                self.combo_list.setCurrentText(
                    self.manager.get_impl_name(combo_id, with_source_prefix=True)
                )
        elif char_info:
            self.combo_list.setCurrentIndex(0)

    def validate(self) -> bool:
        has_char_name = bool(self.char_combo.currentText().strip())
        return self.ok_button.isEnabled() and has_char_name

    def get_data(self):
        char_name = self.char_combo.currentText().strip()
        idx_char = self.char_combo.findText(char_name)
        char_id = self.char_combo.itemData(idx_char) if idx_char >= 0 else ""

        combo_name = self.combo_list.currentText().strip()
        combo_id = ""
        idx = self.combo_list.currentIndex()
        if idx >= 0 and combo_name == self.combo_list.itemText(idx):
            data = self.combo_list.itemData(idx)
            if isinstance(data, str):
                combo_id = data
        if not char_name.strip():
            combo_id = ""
            combo_name = ""
            char_id = ""
        return char_name, char_id, combo_id, combo_name


class AddCharacterDialog(NameInputDialog):
    """Create a new character without exposing the existing-character picker."""

    def __init__(self, manager: CustomCharManager, parent=None):
        duplicate_error = og.app.tr("角色名称无效或已存在")
        super().__init__(
            title="新增角色",
            placeholder="角色名称",
            title_alignment=Qt.AlignmentFlag.AlignCenter,
            name_validator=lambda name: all(
                char_data["char_name"] != name
                for char_data in manager.get_all_characters().values()
            ),
            error_message=duplicate_error,
            parent=parent,
        )
        self.manager = manager
        self.tr_name_duplicate = duplicate_error
        self.char_name_edit = self.name_edit

        self.combo_list = SearchableComboBox(self)
        self.combo_list.lineEdit().setPlaceholderText(og.app.tr("输入或选择出招表"))
        self.combo_list.addItem("", userData="")
        for combo_name, combo_id in self.manager.get_all_impl_items(with_source_prefix=True):
            self.combo_list.addItem(combo_name, userData=combo_id)
        self.body.addWidget(self.combo_list)

    def get_data(self):
        char_name = self.get_name()
        combo_name = self.combo_list.currentText().strip()
        combo_id = ""
        index = self.combo_list.currentIndex()
        if index >= 0 and combo_name == self.combo_list.itemText(index):
            data = self.combo_list.itemData(index)
            if isinstance(data, str):
                combo_id = data
        return char_name, "", combo_id, combo_name


def save_character_from_dialog(
    manager: CustomCharManager, dialog: NewCharDialog | AddCharacterDialog
) -> str:
    """Persist the selection made in a character dialog and return its character ID."""
    char_name, char_id, combo_id, combo_name = dialog.get_data()
    if not (char_id or char_name):
        return ""
    if combo_name and not combo_id:
        combo_id = manager.add_combo(combo_name, "")
    if not char_id:
        return manager.create_character(char_name, combo_id)
    manager.update_character(char_id, impl_id=combo_id)
    return char_id


class SlotCard(BorderCardWidget):
    def __init__(self, index, manager: CustomCharManager, parent=None):
        super().__init__(parent)
        self.setBorderWidth(1)
        self.index = index
        self.manager = manager
        self.tr_match_success = og.app.tr("匹配成功: {}")
        self.tr_unrecognized = og.app.tr("未能识别该特征")
        self.tr_no_image = og.app.tr("无画面")
        self.tr_slot_title = og.app.tr("{} 号位")
        self.tr_scan_prompt = og.app.tr("等待扫描...")
        self.tr_action_btn = og.app.tr("关联特征")
        self.tr_add_match_feature_btn = og.app.tr("加入特征")
        self.tr_feature_added_btn = og.app.tr("特征已加入")
        self.tr_not_this_char = og.app.tr("不是该角色?")
        self.tr_confidence = og.app.tr("置信度: {:.2f}")
        self.setFixedHeight(146)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 10, 14, 10)
        root_layout.setSpacing(6)

        # Header with slot badge
        header_widget = QWidget(self)
        header_widget.setFixedHeight(24)
        header_row = QHBoxLayout(header_widget)
        header_row.setContentsMargins(0, 0, 0, 0)
        self.slot_badge = Label(
            self.tr_slot_title.format(index + 1), "strong", parent=header_widget
        )
        header_row.addWidget(self.slot_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)
        self.btn_relink = LinkButton(self.tr_not_this_char, size="small", parent=header_widget)
        self.btn_relink.hide()
        header_row.addWidget(self.btn_relink, alignment=Qt.AlignmentFlag.AlignVCenter)
        root_layout.addWidget(header_widget)

        self.stack = QStackedLayout()

        # Empty state
        self.empty_widget = QWidget(self)
        self.empty_layout = QVBoxLayout(self.empty_widget)
        self.empty_layout.setContentsMargins(0, 0, 0, 12)
        self.empty_layout.setSpacing(6)
        self.empty_icon = QLabel(self.empty_widget)
        self.empty_icon.setFixedSize(36, 36)
        self._update_empty_icon()
        theme.changed.connect(self._update_empty_icon)
        self.empty_status = Label(self.tr_scan_prompt, "caption", parent=self.empty_widget)
        self.empty_layout.addStretch(1)
        self.empty_layout.addWidget(self.empty_icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.empty_layout.addWidget(self.empty_status, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.empty_layout.addStretch(1)

        # Result state
        self.result_widget = QWidget(self)
        self.result_layout = QHBoxLayout(self.result_widget)
        self.result_layout.setContentsMargins(0, 0, 0, 6)
        self.result_layout.setSpacing(12)

        self.image = QLabel(self.result_widget)
        self.image.setFixedSize(88, 88)

        self.info_widget = QWidget(self.result_widget)
        self.info_layout = QVBoxLayout(self.info_widget)
        self.info_layout.setContentsMargins(0, 0, 0, 0)
        self.info_layout.setSpacing(4)

        self.status = Label("", "body", parent=self.info_widget)
        self.status.setWordWrap(True)
        self.btn_act = Button(self.tr_action_btn, variant="secondary", parent=self.info_widget)
        self.btn_act.setFixedHeight(28)
        self.btn_act.hide()

        self.info_layout.addStretch(1)
        self.info_layout.addWidget(self.status)
        self.info_layout.addWidget(self.btn_act, alignment=Qt.AlignmentFlag.AlignLeft)
        self.info_layout.addStretch(1)

        self.result_layout.addStretch(1)
        self.result_layout.addWidget(self.image, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.result_layout.addWidget(self.info_widget)
        self.result_layout.addStretch(1)

        self.stack.addWidget(self.empty_widget)
        self.stack.addWidget(self.result_widget)
        self.stack.setCurrentWidget(self.empty_widget)
        root_layout.addLayout(self.stack, 1)

        self.btn_act.clicked.connect(self.on_action)
        self.btn_relink.clicked.connect(self.on_relink)
        self.current_mat = None
        self.current_w = 0
        self.current_h = 0
        self.current_match_char_id = ""
        self.current_confidence = None

    def _update_empty_icon(self) -> None:
        self.empty_icon.setPixmap(kit_icon("users", "text_tertiary").pixmap(36, 36))

    def _status_text(self, text, confidence=None):
        if confidence is None:
            return text
        return f"{text}\n{self.tr_confidence.format(confidence)}"

    def show_empty(self, text=None):
        text = text or self.tr_scan_prompt
        self.status.setText(text)
        self.empty_status.setText(text)
        self.stack.setCurrentWidget(self.empty_widget)
        self.btn_relink.hide()

    def show_result(self):
        self.stack.setCurrentWidget(self.result_widget)

    def update_result(self, mat, w, h, match_char_id, confidence=None):
        self.current_mat = mat
        self.current_w = w
        self.current_h = h
        self.current_match_char_id = match_char_id or ""
        self.current_confidence = confidence
        if mat is not None and getattr(mat, "size", 0) > 0:
            self.show_result()
            pixmap = cv_to_pixmap(mat)
            self.image.setPixmap(
                pixmap.scaled(
                    88,
                    88,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            empty_pixmap = QPixmap(88, 88)
            empty_pixmap.fill(Qt.GlobalColor.transparent)
            self.image.setPixmap(empty_pixmap)

        if match_char_id:
            char_info = self.manager.get_character_info_by_id(match_char_id)
            display_name = char_info["char_name"] if char_info else match_char_id
            self.status.setText(
                self._status_text(self.tr_match_success.format(display_name), confidence)
            )
            if confidence is not None and confidence > 0.95:
                self.btn_act.setEnabled(False)
            else:
                self.btn_act.setEnabled(True)
            self.btn_act.setText(self.tr_add_match_feature_btn)
            self.btn_act.show()
            self.btn_relink.show()
        elif mat is not None:
            self.status.setText(self._status_text(self.tr_unrecognized, confidence))
            self.btn_act.setEnabled(True)
            self.btn_act.setText(self.tr_action_btn)
            self.btn_act.show()
            self.btn_relink.hide()
        else:
            self.show_empty(self.tr_no_image)
            self.btn_act.setEnabled(True)
            self.btn_act.hide()
            self.btn_relink.hide()

    def on_action(self):
        if self.current_match_char_id and self.current_mat is not None:
            self.manager.add_feature_to_character(
                self.current_match_char_id,
                self.current_mat,
                width=self.current_w,
                height=self.current_h,
            )
            self.update_result(
                self.current_mat,
                self.current_w,
                self.current_h,
                self.current_match_char_id,
                self.current_confidence,
            )
            self.btn_act.setText(self.tr_feature_added_btn)
            self.btn_act.setEnabled(False)
            self.btn_relink.hide()
            return

        self.on_relink()

    def on_relink(self):
        if self.current_mat is None:
            return
        dialog = NewCharDialog(self.current_mat, self.manager, self.window())
        with exec_owned_dialog(dialog, owner=self) as accepted:
            if not accepted:
                return
            char_id = save_character_from_dialog(self.manager, dialog)
        if char_id:
            self.manager.add_feature_to_character(
                char_id,
                self.current_mat,
                width=self.current_w,
                height=self.current_h,
            )
            self.update_result(
                self.current_mat,
                self.current_w,
                self.current_h,
                char_id,
                1.0,
            )
            self.btn_act.setText(self.tr_feature_added_btn)
            self.btn_act.setEnabled(False)
            self.btn_relink.hide()


class PresetNameLabel(Label):
    """Single-line preset name that can shrink without widening the page."""

    def __init__(self, parent=None):
        super().__init__("", parent=parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)

    def set_name(self, name: str) -> None:
        self._full_text = name
        self.setToolTip(name)
        self._update_text()

    def _update_text(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width()
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_text()


class PresetSlotRow(QWidget):
    """Clean form for one preset slot with slot badge and searchable selectors."""

    changed = Signal(int)

    def __init__(self, index: int, manager: CustomCharManager, parent=None):
        super().__init__(parent)
        self.index = index
        self.manager = manager
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.slot_label = Label(og.app.tr("{} 号位").format(index + 1), "strong", parent=self)
        layout.addWidget(self.slot_label)

        self.char_combo = SearchableComboBox(self)
        self.char_combo.lineEdit().setPlaceholderText(og.app.tr("输入或选择角色"))

        self.combo_list = SearchableComboBox(self)
        self.combo_list.lineEdit().setPlaceholderText(og.app.tr("输入或选择出招表"))

        # Imported names can be long; let the slot grid determine the field widths.
        for combo in (self.char_combo, self.combo_list):
            combo.setSizeAdjustPolicy(combo.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            layout.addWidget(combo)

        self.char_combo.currentIndexChanged.connect(self._on_character_changed)
        self.combo_list.currentIndexChanged.connect(self._emit_changed)
        self.reload_options()

    def _selected_char_id(self) -> str:
        index = self.char_combo.currentIndex()
        data = self.char_combo.itemData(index) if index >= 0 else ""
        return data if isinstance(data, str) else ""

    def _selected_combo_id(self) -> str:
        index = self.combo_list.currentIndex()
        data = self.combo_list.itemData(index) if index >= 0 else ""
        return data if isinstance(data, str) else ""

    def _set_combo_by_id(self, combo_id: str) -> None:
        index = self.combo_list.findData(combo_id)
        if index >= 0:
            self.combo_list.setCurrentIndex(index)
        else:
            self.combo_list.setCurrentIndex(0)

    def _on_character_changed(self, _index: int) -> None:
        if self._loading:
            return
        char_info = self.manager.get_character_info_by_id(self._selected_char_id())
        self.combo_list.blockSignals(True)
        if char_info is not None and not self._selected_combo_id():
            self._set_combo_by_id(char_info["impl_id"])
        self.combo_list.blockSignals(False)
        self.changed.emit(self.index)

    def _emit_changed(self, _index: int) -> None:
        if not self._loading:
            self.changed.emit(self.index)

    def reload_options(self) -> None:
        char_id, combo_id = self.get_data()
        self._loading = True
        self.char_combo.clear()
        self.char_combo.addItem("", userData="")
        for item_char_id, char_data in self.manager.get_all_characters().items():
            self.char_combo.addItem(char_data["char_name"], userData=item_char_id)
        self.combo_list.clear()
        self.combo_list.addItem("", userData="")
        for combo_name, item_combo_id in self.manager.get_all_impl_items(with_source_prefix=True):
            self.combo_list.addItem(combo_name, userData=item_combo_id)
        self.set_data(char_id, combo_id)
        self._loading = False

    def set_data(self, char_id: str, combo_id: str) -> None:
        self._loading = True
        char_index = self.char_combo.findData(char_id) if char_id else 0
        self.char_combo.setCurrentIndex(char_index if char_index >= 0 else 0)
        combo_index = self.combo_list.findData(combo_id) if combo_id else 0
        self.combo_list.setCurrentIndex(combo_index if combo_index >= 0 else 0)
        self._loading = False

    def get_data(self) -> tuple[str, str]:
        return self._selected_char_id(), self._selected_combo_id()

    def set_editor_enabled(self, enabled: bool) -> None:
        self.char_combo.setEnabled(enabled)
        self.combo_list.setEnabled(enabled)


class TeamManagerTab(CustomTab):
    """Feature binding tools and immediately persisted combat team presets."""

    def __init__(self, manager: CustomCharManager = None, owner=None):
        super().__init__()
        self.owner = owner
        self._executor = None
        self.manager = manager or CustomCharManager()
        self._disposed = False
        self.icon = kit_icon("camera")
        self.last_scan_results = []
        self.task: DebugCharTask | None = None
        self._scan_pending = False
        self.current_preset_id: str | None = None
        self._loading_preset = False

        self.tr_name_tab = self.tr("队伍管理")
        self.tr_scan_btn = self.tr("扫描队伍")
        self.tr_scanning = self.tr("扫描中...")
        self.tr_no_feature = self.tr("未获取到特征")
        self.tr_scan_task_missing = self.tr("角色工具任务不可用")
        self.tr_fill_failed = self.tr("没有可填入的角色")
        self.tr_duplicate_character = self.tr("方案中不能重复选择角色")
        self.tr_preset_save_failed = self.tr("方案名称重复或内容无效")
        self.tr_apply_success = self.tr("已更新 {} 位角色的出招表")
        self.tr_deleted_success = self.tr("已从列表移除")
        # ruff: disable[E501]
        self.tr_scan_tips = self.tr(
            '<p><b style="color: #d83b01;">注意：</b>此面板 <b style="color: #d83b01;">不会</b> 在进入战斗或更换阵容时实时自动刷新或同步显示。<br>'
            "这是个用于向数据库关联或添加 [角色特征] 的工具面板。<br>"
            "点击 [{scan_team}] 后点击 [关联或添加] 特征, 自动战斗时就会识别对应的角色。<br>"
            "如果不想管理 [角色特征], 可以直接使用 [{fixed_team}] 功能。</p>",
        ).format(
            fixed_team=self.tr("队伍方案"),
            scan_team=self.tr("扫描队伍"),
        )
        self.tr_scan_tips = highlight_bracketed_text(self.tr_scan_tips, "#0078d7")
        self.tr_preset_tips = self.tr(
            "<p>用于配置并保存常用的 4 人队伍阵容与出招表, 修改后立即生效。<br>"
            "方案中的 1~4 号位仅用于 [{fixed_preset}] 功能; 已绑定特征的角色放在哪个号位都可以, 不影响识别。<br>"
            "点击 [{apply_preset}] 可一键更新应用方案中各角色的出招表 (未选择出招表则保持原配置不变)。<br>"
            "开启 [{fixed_preset}] 后自动战斗将直接采用此阵容, 无需等待特征识别 (未录入特征的角色亦可直接战斗)。<br>"
            "点击 [{fill_scan}] 可将上方特征扫描识别出的队伍角色快速填入当前方案。</p>",
        ).format(
            apply_preset=self.tr("应用方案"),
            fixed_preset=self.tr("固定"),
            fill_scan=self.tr("填入扫描"),
        )
        self.tr_preset_tips = highlight_bracketed_text(self.tr_preset_tips, "#0078d7")
        # ruff: enable[E501]

        self.vbox = self.vBoxLayout
        self.vbox.setContentsMargins(24, 14, 24, 20)
        self.vbox.setSpacing(12)
        self._build_ui()

        communicate.task.connect(self._on_framework_task_changed)
        self.reload_presets()
        QTimer.singleShot(0, self._scroll_to_top)

    def _build_ui(self) -> None:
        self._add_scan_section()
        self._add_preset_section()

    def _add_scan_section(self) -> None:
        header = QHBoxLayout()
        header.setSpacing(8)

        title_label = Label(self.tr("队伍识别"), "subtitle", parent=self.view)
        header.addWidget(title_label)

        self.scan_info_btn = Button(
            "", variant="subtle", icon="info", size="small", parent=self.view
        )
        self.scan_info_btn.setToolTip(self.tr("提示"))
        self.scan_info_btn.clicked.connect(self.show_scan_flyout)
        header.addWidget(self.scan_info_btn)
        header.addStretch(1)

        self.scan_btn = Button(
            self.tr_scan_btn, variant="primary", icon="refresh", parent=self.view
        )
        self.scan_btn.clicked.connect(self.on_scan_clicked)
        header.addWidget(self.scan_btn)

        self.vbox.addLayout(header)

        self.cards_layout = QHBoxLayout()
        self.cards_layout.setSpacing(12)
        self.slots: list[SlotCard] = []
        for index in range(4):
            card = SlotCard(index, self.manager, self.view)
            self.slots.append(card)
            self.cards_layout.addWidget(card)
        self.vbox.addLayout(self.cards_layout)

    def _add_preset_section(self) -> None:
        header = QHBoxLayout()
        header.setSpacing(8)
        preset_title = Label(self.tr("队伍方案"), "subtitle", parent=self.view)
        header.addWidget(preset_title)

        self.preset_info_btn = Button(
            "", variant="subtle", icon="info", size="small", parent=self.view
        )
        self.preset_info_btn.setToolTip(self.tr("提示"))
        self.preset_info_btn.clicked.connect(self.show_preset_flyout)
        header.addWidget(self.preset_info_btn)
        header.addStretch(1)
        self.vbox.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(16)

        # Left panel: Presets list card
        self.preset_list_card = Card(self.view)
        self.preset_list_card.setMinimumWidth(240)
        self.preset_list_card.setMinimumHeight(300)
        self.preset_list_card.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        list_layout = QVBoxLayout(self.preset_list_card)
        list_layout.setContentsMargins(14, 14, 14, 14)
        list_layout.setSpacing(10)

        list_header = QHBoxLayout()
        list_header.addWidget(Label(self.tr("方案列表"), "strong", parent=self.preset_list_card))
        list_header.addStretch(1)
        self.new_preset_btn = Button(
            "", variant="subtle", icon="plus", size="small", parent=self.preset_list_card
        )
        self.new_preset_btn.clicked.connect(self.on_create_preset)
        self.delete_preset_btn = Button(
            "", variant="subtle", icon="trash", size="small", parent=self.preset_list_card
        )
        self.delete_preset_btn.clicked.connect(self.on_delete_preset)
        list_header.addWidget(self.new_preset_btn)
        list_header.addWidget(self.delete_preset_btn)
        list_layout.addLayout(list_header)

        self.preset_list = SearchableListWidget(self.preset_list_card)
        self.preset_list.setPlaceholderText(self.tr("搜索方案"))
        self.preset_list.list_widget.currentItemChanged.connect(self.on_preset_selected)
        self.preset_list.search_edit.textChanged.connect(self.on_preset_filter_changed)
        list_layout.addWidget(self.preset_list, 1)
        content.addWidget(self.preset_list_card, 1)

        # Right panel: command bar and preset editor card
        right_layout = QVBoxLayout()
        right_layout.setSpacing(12)

        self.preset_command_card = CommandBarCard(self.view)
        self.command_stack_layout = QStackedLayout()
        self.preset_command_card.layout().addLayout(self.command_stack_layout, 1)

        self.preset_command_bar = ThemedToolBar(self.preset_command_card)

        self._add_workshop_actions()

        self.add_character_action = QAction(
            kit_icon("plus"), self.tr("新增角色"), self.preset_command_bar
        )
        self.add_character_action.triggered.connect(self.on_add_character)
        self.fill_from_scan_action = QAction(
            resource_icon("scan-person"), self.tr("填入扫描"), self.preset_command_bar
        )
        self.fill_from_scan_action.triggered.connect(self.on_fill_from_scan)
        self.fixed_action = QAction(
            resource_icon("lock-closed"), self.tr("固定"), self.preset_command_bar
        )
        self.fixed_action.setCheckable(True)
        self.fixed_action.triggered.connect(self.on_toggle_fixed_preset)
        self.apply_action = QAction(
            resource_icon("circle-check"), self.tr("应用方案"), self.preset_command_bar
        )
        self.apply_action.triggered.connect(self.on_apply_preset)

        self.switch_to_community_action = QAction(
            resource_icon("people-community"), self.tr("社区方案"), self.preset_command_bar
        )
        self.switch_to_community_action.triggered.connect(
            lambda: self.command_stack_layout.setCurrentIndex(1)
        )

        self.preset_command_bar.addAction(self.add_character_action)
        self.preset_command_bar.addSeparator()
        self.preset_command_bar.addAction(self.fill_from_scan_action)
        self.preset_command_bar.addAction(self.fixed_action)
        self.preset_command_bar.addSeparator()
        self.preset_command_bar.addAction(self.apply_action)
        self.preset_command_bar.addSeparator()
        self.preset_command_bar.addAction(self.switch_to_community_action)

        self.command_stack_layout.addWidget(self.preset_command_bar)
        self.command_stack_layout.addWidget(self.workshop_command_bar)
        self.command_stack_layout.setCurrentIndex(0)
        right_layout.addWidget(self.preset_command_card)

        self.preset_editor_card = SectionCard(self.tr("方案"), parent=self.view)
        editor_header = self.preset_editor_card.header
        self.preset_name_label = PresetNameLabel(self.preset_editor_card)
        editor_header.addWidget(self.preset_name_label, 1)
        self.rename_preset_btn = LinkButton(
            self.tr("重命名"),
            icon_name="pen",
            size="small",
            parent=self.preset_editor_card,
        )
        self.rename_preset_btn.setAccessibleName(self.tr("重命名"))
        self.rename_preset_btn.clicked.connect(self.on_rename_preset)
        editor_header.addWidget(self.rename_preset_btn)

        grid_layout = QGridLayout()
        grid_layout.setHorizontalSpacing(24)
        grid_layout.setVerticalSpacing(16)

        self.preset_rows: list[PresetSlotRow] = []
        for index in range(4):
            row = PresetSlotRow(index, self.manager, self.preset_editor_card)
            row.changed.connect(self.on_preset_slot_changed)
            self.preset_rows.append(row)
            grid_layout.addWidget(row, index // 2, index % 2)

        self.preset_editor_card.body.addLayout(grid_layout)
        right_layout.addWidget(self.preset_editor_card)
        right_layout.addStretch(1)

        content.addLayout(right_layout, 3)
        self.vbox.addLayout(content, 1)
        self._set_editor_enabled(False)

    def _add_workshop_actions(self) -> None:
        self.workshop_service = WorkshopPackageService(self.manager)
        self.workshop_repository = WorkshopRepository(
            IndexSource("GitHub", **WORKSHOP_REPOSITORY["github"]),
            IndexSource("CNB", **WORKSHOP_REPOSITORY["cnb"]),
        )
        self._background_workers: list[BackgroundCall] = []
        self.workshop_command_bar = ThemedToolBar(self.preset_command_card)

        self.import_package_action = QAction(
            kit_icon("download"), self.tr("导入"), self.workshop_command_bar
        )
        self.export_package_action = QAction(
            kit_icon("share"), self.tr("导出"), self.workshop_command_bar
        )
        self.workshop_action = QAction(
            resource_icon("library"), self.tr("工坊"), self.workshop_command_bar
        )
        self.upload_package_action = QAction(
            resource_icon("cloud-arrow-up"), self.tr("上传"), self.workshop_command_bar
        )
        self.switch_to_preset_action = QAction(
            resource_icon("arrow-exit"), self.tr("返回"), self.workshop_command_bar
        )

        self.import_package_action.triggered.connect(self.on_import_package)
        self.export_package_action.triggered.connect(self.on_export_package)
        self.workshop_action.triggered.connect(self.on_open_workshop)
        self.upload_package_action.triggered.connect(self.on_upload_package)
        self.switch_to_preset_action.triggered.connect(
            lambda: self.command_stack_layout.setCurrentIndex(0)
        )

        self.workshop_command_bar.addAction(self.import_package_action)
        self.workshop_command_bar.addAction(self.export_package_action)
        self.workshop_command_bar.addAction(self.workshop_action)
        self.workshop_command_bar.addAction(self.upload_package_action)
        self.workshop_command_bar.addSeparator()
        self.workshop_command_bar.addAction(self.switch_to_preset_action)

    def _start_background_call(self, action, succeeded, *, finish_before_exit=False) -> None:
        if self._disposed:
            return
        worker = BackgroundCall(action, self, finish_before_exit=finish_before_exit)
        self._background_workers.append(worker)

        def finish(result):
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            if not self._disposed:
                succeeded(result)
            worker.deleteLater()

        def failed(error):
            if worker in self._background_workers:
                self._background_workers.remove(worker)
            if not self._disposed:
                show_bar(
                    self.window(),
                    self.tr("操作失败"),
                    self._workshop_error_text(error),
                    success=False,
                )
            worker.deleteLater()

        worker.succeeded.connect(finish)
        worker.failed.connect(failed)
        worker.start()

    def _workshop_error_text(self, error: object) -> str:
        if isinstance(error, WorkshopInstallError):
            directory = error.details.get("directory", "")
            if error.code == WorkshopInstallErrorCode.EXTERNAL_DIRECTORY_EXISTS:
                return self.tr("外置代码目录“{}”已存在, 请修改目录名称后重试。").format(directory)
            if error.code == WorkshopInstallErrorCode.INVALID_EXTERNAL_DIRECTORY:
                return self.tr("外置代码目录名称无效")
        return workshop_error_text(error, self.tr)

    def navigate_to_workshop(self) -> None:
        self.command_stack_layout.setCurrentIndex(1)
        self.on_open_workshop()

    def on_open_workshop(self) -> None:
        dialog = WorkshopDialog(
            self.workshop_repository,
            self.window(),
            service=self.workshop_service,
            before_update=self._ensure_workshop_update_idle,
        )
        self._active_workshop_dialog = dialog
        dialog.import_requested.connect(self.on_remote_package_requested)
        dialog.packages_updated.connect(self._on_packages_updated)
        try:
            with exec_owned_dialog(dialog, owner=self):
                pass
        finally:
            self._active_workshop_dialog = None

    def _ensure_workshop_update_idle(self) -> None:
        if self._background_workers:
            raise RuntimeError(self.tr("请等待当前导入或导出完成后再更新."))

    def _on_packages_updated(self) -> None:
        self.reload_presets(refresh_options=True)

    def on_import_package(self) -> None:
        if not confirm_external_code_import(self.window()):
            return
        path, _ = QFileDialog.getOpenFileName(self.window(), self.tr("导入数据"), "", "ZIP (*.zip)")
        if not path:
            return
        self._start_background_call(
            lambda: self.workshop_service.load_archive(Path(path)),
            lambda contents: self._show_import_preview(contents, Path(path).name),
        )

    def on_remote_package_requested(self, entry, source) -> None:
        self._start_background_call(
            lambda: self.workshop_service.load_archive(
                self.workshop_repository.download_archive(source, entry)
            ),
            lambda contents: self._show_import_preview(contents, entry.filename),
        )

    def _show_import_preview(self, contents, archive_name: str) -> None:
        dialog = PackageImportDialog(
            contents.package,
            archive_name,
            self.window(),
            directory_exists=self.manager.external_directory_exists,
        )
        with exec_owned_dialog(dialog, owner=self) as accepted:
            if not accepted:
                return
            preset_name, directory = dialog.installation()
        self._start_background_call(
            lambda: self.workshop_service.install_contents(contents, preset_name, directory),
            self._on_package_imported,
        )

    def _on_package_imported(self, imported) -> None:
        self.reload_presets(imported.preset_id, refresh_options=True)
        show_bar(
            self.window(),
            self.tr("导入完成"),
            self.tr("已创建方案: {}").format(imported.preset_name),
        )
        dialog = getattr(self, "_active_workshop_dialog", None)
        if dialog is not None:
            dialog.reload_catalog()

    def _export_slots(self, preset: dict) -> tuple[PackageSlot, ...]:
        slots = []
        for index, raw_slot in enumerate(preset["slots"]):
            impl_id = str(raw_slot.get("impl_id", "")).strip()
            entry = self.workshop_service.registry.get(impl_id) if impl_id else None
            if entry is None:
                continue
            display = {"zh_CN": entry.cn_name, "en_US": entry.en_name}
            if entry.source == "builtin":
                slots.append(PackageSlot(index, "builtin", display, impl_id=impl_id))
            elif entry.source == "external":
                slots.append(
                    PackageSlot(
                        index,
                        "external",
                        display,
                        file_name=f"{impl_id.rsplit('/', 1)[-1]}.py",
                        class_name=entry.char_cls.__name__,
                    )
                )
        return tuple(slots)

    def on_export_package(self) -> None:
        preset = self._current_preset()
        if preset is None:
            return
        defaults = TeamPackage(preset["name"], "", "", "1.0.0", self._export_slots(preset))
        if not defaults.slots:
            show_bar(
                self.window(),
                self.tr("无法导出"),
                self.tr("当前方案没有可导出的出招表."),
                success=False,
            )
            return
        dialog = PackageMetadataDialog(defaults, self.window(), repository=self.workshop_repository)
        with exec_owned_dialog(dialog, owner=self) as accepted:
            if not accepted:
                return
            package = replace(dialog.package(), slots=defaults.slots)
        path, _ = QFileDialog.getSaveFileName(
            self.window(), self.tr("导出社区方案"), default_archive_name(package), "ZIP (*.zip)"
        )
        if not path:
            return
        export_path = Path(path)
        self._start_background_call(
            lambda: self.workshop_service.export_preset(preset["id"], package, export_path),
            lambda _result: show_bar(self.window(), self.tr("导出完成"), export_path.name),
        )

    def on_upload_package(self) -> None:
        QDesktopServices.openUrl(QUrl(WORKSHOP_REPOSITORY["upload_url"]))

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.preset_name_label.setEnabled(enabled)
        self.rename_preset_btn.setEnabled(enabled)
        self.delete_preset_btn.setEnabled(enabled)
        self.add_character_action.setEnabled(True)
        self.apply_action.setEnabled(enabled)
        self.fixed_action.setEnabled(enabled)
        self.fill_from_scan_action.setEnabled(enabled)
        self.export_package_action.setEnabled(enabled)
        self.workshop_action.setEnabled(True)
        self.import_package_action.setEnabled(True)
        self.upload_package_action.setEnabled(True)
        self.switch_to_community_action.setEnabled(True)
        self.switch_to_preset_action.setEnabled(True)
        for row in self.preset_rows:
            row.set_editor_enabled(enabled)
        if not enabled:
            self.preset_name_label.set_name("")
            self.fixed_action.setChecked(False)

    def _scroll_to_top(self) -> None:
        if not self._disposed:
            self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def showEvent(self, event):
        super().showEvent(event)
        if self._disposed:
            return
        self.reload_preset_options()
        QTimer.singleShot(0, self._scroll_to_top)

    @property
    def name(self) -> Literal["CustomTab"]:
        return self.tr_name_tab  # type: ignore

    @property
    def executor(self):
        return self.owner.executor if self.owner else self._executor

    @executor.setter
    def executor(self, value):
        self._executor = value

    def _get_task(self) -> DebugCharTask | None:
        if self.task is None and self.executor is not None:
            self.task = self.get_task(DebugCharTask)
        return self.task

    def _current_preset(self) -> dict | None:
        return next(
            (
                preset
                for preset in self.manager.get_team_presets()
                if preset["id"] == self.current_preset_id
            ),
            None,
        )

    def reload_presets(self, selected_id: str | None = None, *, refresh_options=False) -> None:
        if refresh_options:
            self._reload_slot_options()
        selected_id = selected_id or self.current_preset_id
        presets = self.manager.get_team_presets()
        self.preset_list.list_widget.blockSignals(True)
        self.preset_list.list_widget.clear()
        for preset in presets:
            item = QListWidgetItem(preset["name"])
            item.setData(Qt.ItemDataRole.UserRole, preset["id"])
            if preset.get("is_fixed"):
                fixed_status = self.tr("固定")
                item.setData(COLLECTION_STATUS_ROLE, fixed_status)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, fixed_status)
            self.preset_list.list_widget.addItem(item)
            if preset["id"] == selected_id:
                self.preset_list.list_widget.setCurrentItem(item)
        if (
            self.preset_list.list_widget.currentItem() is None
            and self.preset_list.list_widget.count()
        ):
            self.preset_list.list_widget.setCurrentRow(0)
        self.preset_list.reapply_filter()
        self._select_visible_preset()
        self.preset_list.list_widget.blockSignals(False)
        self.on_preset_selected(self.preset_list.list_widget.currentItem(), None)

    def _select_visible_preset(self) -> None:
        current = self.preset_list.list_widget.currentItem()
        if current is not None and not current.isHidden():
            return
        for index in range(self.preset_list.list_widget.count()):
            item = self.preset_list.list_widget.item(index)
            if not item.isHidden():
                self.preset_list.list_widget.setCurrentItem(item)
                return
        self.preset_list.list_widget.setCurrentItem(None)

    def on_preset_filter_changed(self, _text: str) -> None:
        self._select_visible_preset()

    def _reload_slot_options(self) -> None:
        for row in self.preset_rows:
            row.reload_options()

    def reload_preset_options(self) -> None:
        self._reload_slot_options()
        self.render_current_preset()

    def render_current_preset(self) -> None:
        preset = self._current_preset()
        self._loading_preset = True
        self._set_editor_enabled(preset is not None)
        if preset is not None:
            self.preset_name_label.set_name(preset["name"])
            for index, row in enumerate(self.preset_rows):
                slot = preset["slots"][index]
                row.set_data(slot["char_id"], slot["impl_id"])
            is_fixed = bool(preset.get("is_fixed", False))
            self.fixed_action.setChecked(is_fixed)
        self._loading_preset = False

    def on_preset_selected(self, current, _previous) -> None:
        self.current_preset_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.render_current_preset()

    def on_create_preset(self) -> None:
        preset = self.manager.create_team_preset(self.tr("新建方案"))
        self.reload_presets(preset["id"])

    def on_rename_preset(self) -> None:
        if not self.current_preset_id:
            return
        preset = self._current_preset()
        if not preset:
            return
        existing_names = {
            other["name"]
            for other in self.manager.get_team_presets()
            if other["id"] != self.current_preset_id
        }
        dialog = NameInputDialog(
            preset["name"],
            name_validator=lambda name: name not in existing_names,
            error_message=self.tr_preset_save_failed,
            parent=self.window(),
        )
        with exec_owned_dialog(dialog, owner=self) as accepted:
            if not accepted:
                return
            name = dialog.get_name()
        if name == preset["name"]:
            return
        if not self.manager.update_team_preset(self.current_preset_id, name=name):
            show_bar(self.window(), self.tr("无法保存"), self.tr_preset_save_failed, success=False)
            self.render_current_preset()
            return
        self.reload_presets(self.current_preset_id)

    def _current_slots(self) -> list[dict]:
        return [
            {"char_id": char_id, "impl_id": impl_id}
            for char_id, impl_id in (row.get_data() for row in self.preset_rows)
        ]

    def on_preset_slot_changed(self, _index: int) -> None:
        if self._loading_preset or not self.current_preset_id:
            return
        if not self.manager.update_team_preset(self.current_preset_id, slots=self._current_slots()):
            show_bar(self.window(), self.tr("无法保存"), self.tr_duplicate_character, success=False)
            self.render_current_preset()

    def on_add_character(self) -> None:
        dialog = AddCharacterDialog(self.manager, self.window())
        with exec_owned_dialog(dialog, owner=self) as accepted:
            if accepted and save_character_from_dialog(self.manager, dialog):
                self.reload_preset_options()

    def on_delete_preset(self) -> None:
        preset = self._current_preset()
        if preset is None:
            return

        external_impl_ids = [
            slot["impl_id"] for slot in preset["slots"] if slot["impl_id"].startswith("external:")
        ]
        delete_external_code = False
        if external_impl_ids:
            dialog = Dialog(
                self.window(),
                self.tr("删除方案"),
                self.tr("检测到该方案包含外置代码，是否同时删除对应的代码文件?"),
            )
            dialog.ok_button.setText(self.tr("全部删除"))
            dialog.cancel_button.setText(self.tr("仅删除方案"))
            with exec_owned_dialog(dialog, owner=self) as accepted:
                delete_external_code = accepted

        preset_id = preset["id"]

        def delete_preset() -> list[str] | None:
            if not self.manager.delete_team_preset(preset_id):
                return None
            if delete_external_code:
                return self.manager.delete_external_impls_and_references(external_impl_ids)
            return []

        def on_deleted(failed_impl_ids: list[str] | None) -> None:
            if failed_impl_ids is None:
                return
            if self.current_preset_id == preset_id:
                self.current_preset_id = None
            self.reload_presets(refresh_options=True)
            if failed_impl_ids:
                show_bar(
                    self.window(),
                    self.tr("方案已删除"),
                    self.tr("部分外置代码删除失败: {}").format(
                        ", ".join(impl_id.removeprefix("external:") for impl_id in failed_impl_ids)
                    ),
                    success=False,
                )
            else:
                show_bar(self.window(), self.tr("已删除"), self.tr_deleted_success)

        self._start_background_call(delete_preset, on_deleted, finish_before_exit=True)

    def on_apply_preset(self) -> None:
        if not self.current_preset_id:
            return
        applied = self.manager.apply_team_preset(self.current_preset_id)
        if applied is None:
            return
        self.reload_preset_options()
        show_bar(self.window(), self.tr("已应用"), self.tr_apply_success.format(len(applied)))

    def on_toggle_fixed_preset(self) -> None:
        preset = self._current_preset()
        if preset is None:
            return
        if preset["is_fixed"]:
            self.manager.clear_fixed_team_preset()
            self.reload_presets(preset["id"])
            return
        applied = self.manager.apply_team_preset(preset["id"], fixed=True)
        if applied is None:
            return
        self.reload_presets(preset["id"], refresh_options=True)
        show_bar(self.window(), self.tr("已应用"), self.tr_apply_success.format(len(applied)))

    def on_scan_clicked(self) -> None:
        task = self._get_task()
        if task is None:
            show_bar(self.window(), self.tr("扫描失败"), self.tr_scan_task_missing, success=False)
            return
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText(self.tr_scanning)
        for card in self.slots:
            card.btn_act.hide()
            card.show_empty()
        self._scan_pending = True
        task.scan_team()
        og.app.start_controller.start(task)

    def _on_framework_task_changed(self, task) -> None:
        if self._disposed:
            return
        if task is not self._get_task() or not self._scan_pending:
            return
        if task.running or task.mode is not None:
            return
        self._scan_pending = False
        self.on_scan_done(task.scan_results, task.result_error)

    def on_scan_done(self, results: tuple[TeamScanResult, ...], error: str = "") -> None:
        if self._disposed:
            return
        self.last_scan_results = list(results)
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText(self.tr_scan_btn)
        if error:
            show_bar(self.window(), self.tr("扫描失败"), error, success=False)
            return
        updated_indices = set()
        for result in self.last_scan_results:
            index = result.index
            if 0 <= index < 4:
                self.slots[index].update_result(
                    result.image,
                    result.width,
                    result.height,
                    result.character_id,
                    result.confidence,
                )
                updated_indices.add(index)
        for index in range(4):
            if index not in updated_indices:
                self.slots[index].update_result(None, 0, 0, "")
                self.slots[index].show_empty(self.tr_no_feature)

    def on_fill_from_scan(self) -> None:
        preset = self._current_preset()
        if preset is None:
            return
        slots = [dict(slot) for slot in preset["slots"]]
        filled_count = 0
        for result in self.last_scan_results:
            index = result.index
            char_id = result.character_id
            if not (0 <= index < 4) or not char_id or slots[index]["char_id"]:
                continue
            char_info = self.manager.get_character_info_by_id(char_id)
            if char_info:
                slots[index]["char_id"] = char_id
                if not slots[index]["impl_id"]:
                    slots[index]["impl_id"] = char_info["impl_id"]
                filled_count += 1
        if not filled_count:
            show_bar(self.window(), self.tr("无法填入"), self.tr_fill_failed, success=False)
            return
        if not self.manager.update_team_preset(preset["id"], slots=slots):
            show_bar(self.window(), self.tr("无法保存"), self.tr_duplicate_character, success=False)
            return
        self.render_current_preset()
        show_bar(self.window(), self.tr("已填入"), self.tr("{} 个空槽位").format(filled_count))

    def show_scan_flyout(self) -> None:
        self._show_help("_scan_help", self.tr_scan_tips, self.scan_info_btn)

    def show_preset_flyout(self) -> None:
        self._show_help("_preset_help", self.tr_preset_tips, self.preset_info_btn)

    def _show_help(self, attribute, content, anchor):
        popover = getattr(self, attribute, None)
        if popover is None:
            popover = HelpPopover(self, self.tr("提示"), content)
            setattr(self, attribute, popover)
        popover.toggle_for(anchor)

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._scan_pending = False
        communicate.task.disconnect(self._on_framework_task_changed)
        for worker in self._background_workers:
            worker.retire()
            worker.succeeded.disconnect()
            worker.failed.disconnect()
        self._background_workers.clear()
        for attribute in ("_scan_help", "_preset_help"):
            popover = getattr(self, attribute, None)
            if popover is not None:
                popover.close_popover()
        dialog = getattr(self, "_active_workshop_dialog", None)
        if dialog is not None:
            dialog.reject()
