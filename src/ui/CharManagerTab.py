import platform
import threading
from pathlib import Path

import requests
from ok import og
from ok.ui.qt.tasks.EditTaskTab import CodeEditor
from ok.ui.qt.tasks.PythonHighlighter import PythonHighlighter
from ok.ui.qt.util.app import show_info_bar
from ok.ui.qt.widget.CustomTab import CustomTab
from ok.util.explorer import open_explorer_folder, reveal_in_explorer
from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QGraphicsBlurEffect,
    QHBoxLayout,
    QListWidgetItem,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CommandBar,
    FlowLayout,
    FluentIcon,
    Flyout,
    GroupHeaderCardWidget,
    HeaderCardWidget,
    HyperlinkButton,
    ImageLabel,
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PrimaryToolButton,
    PushButton,
    SearchLineEdit,
    SimpleCardWidget,
    SmoothScrollArea,
    SubtitleLabel,
    TextEdit,
    TitleLabel,
    ToggleButton,
    TransparentToolButton,
    VerticalSeparator,
)

from src.char.core.CharRegistry import char_registry
from src.char.custom.CustomCharManager import EXTERNAL_CHARS_DIR, CustomCharManager
from src.events import communicate
from src.tasks.DebugCharTask import DebugCharTask
from src.ui.features.characters.safety_dialog import confirm_external_code_import
from src.ui.foundation.dialogs import MessageBoxBase
from src.ui.foundation.i18n import is_chinese
from src.ui.foundation.images import cv_to_pixmap
from src.ui.foundation.name_input_dialog import NameInputDialog
from src.ui.foundation.text import highlight_bracketed_text
from src.ui.foundation.widgets.cards import BorderCardWidget
from src.ui.foundation.widgets.search import (
    SearchableComboBox,
    SearchableListWidget,
)


class CharManagerTab(CustomTab):
    doc_translation_ready = Signal(str, str)

    def __init__(self, owner=None):
        super().__init__()
        self.owner = owner
        self._executor = None
        self.manager = CustomCharManager()

        self.tr_combo_title = self.tr("出招表")
        self.tr_save_success = self.tr("保存成功")
        self.tr_combo_msg = self.tr("{combo}: {} 绑定成功").replace("{combo}", self.tr_combo_title)
        self.tr_del_success = self.tr("删除成功")
        self.tr_del_combo_msg = self.tr("{combo}: {} 删除成功").replace(
            "{combo}", self.tr_combo_title
        )
        self.tr_del_char_msg = self.tr("已成功删除角色: {} 以及关联的特征图")
        self.tr_unbind_success = self.tr("解除绑定")
        self.tr_unbind_msg = self.tr("已解除 {} 的{combo}绑定").replace(
            "{combo}", self.tr_combo_title
        )
        self.tr_import_data = self.tr("导入数据")
        self.tr_export_data = self.tr("导出数据")
        self.tr_open_external_chars_folder = self.tr("打开外置代码目录")
        self.tr_show_builtin = self.tr("显示内置")
        self.tr_copy_to_external = self.tr("复制为外置")
        self.tr_batch_delete = self.tr("批量删除")
        self.tr_copy_success = self.tr("复制成功")
        self.tr_copy_failed = self.tr("复制失败")
        self.tr_copy_success_msg = self.tr("已成功创建外置代码: {}")
        self.tr_copy_dialog_title = self.tr("复制为外置代码")
        self.tr_copy_directory = self.tr("目录")
        self.tr_copy_file_name = self.tr("脚本文件名 (.py)")
        self.tr_copy_dialog_hint = self.tr(
            "将在 external_chars/ 目录下生成独立的 Python 脚本供您编辑与调试."
        )
        self.tr_code_source_unavailable = self.tr("无法读取 Python 脚本.")
        self.tr_external_save_failed = self.tr("外置代码应用失败")
        self.tr_external_save_msg = self.tr("已应用外置代码: {}")
        self.tr_ask_ai = self.tr("询问AI")
        self.tr_ask_ai_copied = self.tr("AI提示模版已复制。请粘贴到AI聊天机器人中。")
        self.tr_data_backup = self.tr("数据备份")
        self.tr_user_data = self.tr("用户数据")
        self.tr_data_manager_hint = self.tr("ZIP 格式备份; 导入将覆盖当前数据")
        self.tr_external_chars_hint = self.tr(
            "手动修改 Python 代码后, 需点击 [{refresh}] 生效"
        ).format(refresh=self.tr("刷新列表"))
        self.tr_import_failed = self.tr("导入失败")
        self.tr_import_success = self.tr("导入成功")
        self.tr_import_msg = self.tr("已导入 {} 个文件")
        self.tr_combo_invalid_title = self.tr("{combo}语法错误").replace(
            "{combo}", self.tr_combo_title
        )
        self.tr_edit_char_name = self.tr("重命名")
        self.tr_rename_failed_title = self.tr("重命名失败")
        self.tr_rename_failed = self.tr("角色名称无效或已存在")
        self.tr_rename_msg = self.tr("角色已重命名为: {}")

        self.tr_name = self.tr("角色管理")
        self.tr_choose_char = self.tr("请在左侧选择一个角色以管理特征和{combo}").replace(
            "{combo}", self.tr_combo_title
        )
        self.tr_first_time_hint = self.tr("初次使用请先至 [{team_mgmt}] 进行设置").format(
            team_mgmt=self.tr("队伍管理")
        )
        self.tr_delete = self.tr("删除")
        self.tr_combo_tips = self.tr(
            "除了选择内建存在的 [{combo}] 外,您也可以自己输入名称来建立自己的 [文本型{combo}]。"
        ).format(combo=self.tr_combo_title)
        self.tr_combo_tips += "<br><br>" + self.tr(
            "<b>外置代码用法:</b><br>"
            "在 [{data_manager}] 中点击 [{show_builtin}], "
            "选中内置角色后点击 [{copy_external}] 创建可编辑副本。<br>"
            "回到此页, 选择 {external_code} 脚本, "
            "修改 Python 代码后点击 [{apply}] 保存并绑定到当前角色。<br>"
            "也可在 [{data_manager}] 中点击 [{open_folder}] 手动编辑 .py 文件, "
            "保存后点击 [{refresh}] 重新加载。"
            "每个文件须且仅须定义一个 BaseChar 子类。<br>"
            "外置代码会以本软件相同权限运行, 请仅使用可信来源。"
        ).format(
            data_manager=self.tr("资料管理"),
            show_builtin=self.tr_show_builtin,
            copy_external=self.tr_copy_to_external,
            external_code=self.manager.get_external_prefix(),
            apply=self.tr("应用更改"),
            open_folder=self.tr_open_external_chars_folder,
            refresh=self.tr("刷新列表"),
        )
        self.tr_combo_tips = highlight_bracketed_text(self.tr_combo_tips, "#0078d7")
        self.tr_unbound_text = self.tr(
            "当前未绑定任何{combo}.\n遇到此角色将默认使用基础通用脚本(BaseChar)."
        ).replace("{combo}", self.tr_combo_title)
        self.tr_no_match_cmd = self.tr("没有找到匹配的指令。")

        self.icon = FluentIcon.PEOPLE
        self.task: DebugCharTask | None = None
        self._combo_test_pending = False
        self._doc_cache_by_locale = {}
        self._doc_cache = None
        self._pending_command = ""
        self._doc_translation_pending_locales = set()
        self._all_characters = {}
        self.doc_translation_ready.connect(
            self._on_doc_translation_ready, Qt.ConnectionType.QueuedConnection
        )
        communicate.task.connect(self._on_framework_task_changed)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)  # 设置为单次触发
        self._filter_timer.timeout.connect(self._run_doc_filter)

        # main layout
        self.main_h_layout = QHBoxLayout(self)
        self.main_h_layout.setContentsMargins(20, 20, 20, 20)
        self.main_h_layout.setSpacing(16)

        # Left side: Character list
        self.left_widget = SimpleCardWidget(self)
        self.left_v_layout = QVBoxLayout(self.left_widget)
        self.left_v_layout.setContentsMargins(14, 14, 14, 14)
        self.left_v_layout.setSpacing(10)

        self.char_list_widget = SearchableListWidget(self)
        self.char_list_widget.setPlaceholderText(self.tr("搜索角色"))
        self.char_list_widget.currentItemChanged.connect(self.on_char_selected)

        self.refresh_btn = PushButton(FluentIcon.SYNC, self.tr("刷新列表"), self)
        self.refresh_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.refresh_btn.clicked.connect(self.on_refresh_btn_clicked)

        self.delete_char_btn = PushButton(FluentIcon.DELETE, self.tr("删除角色"), self)
        self.delete_char_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.delete_char_btn.clicked.connect(self.on_delete_char)
        self.delete_char_btn.setEnabled(False)

        self.data_manager_btn = PushButton(FluentIcon.FOLDER, self.tr("资料管理"), self)
        self.data_manager_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.data_manager_btn.clicked.connect(self.show_data_manager)

        self.workshop_btn = PushButton(FluentIcon.BOOK_SHELF, self.tr("工坊"), self)
        self.workshop_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.workshop_btn.clicked.connect(self.on_open_workshop)

        self.left_v_layout.addWidget(self.refresh_btn)
        self.left_v_layout.addWidget(self.delete_char_btn)
        self.left_v_layout.addWidget(self.data_manager_btn)
        self.left_v_layout.addWidget(self.workshop_btn)
        self.left_v_layout.addWidget(self.char_list_widget, 1)

        # Right side: Detail View
        self.detail_widget = QWidget()
        self.detail_v_layout = QVBoxLayout(self.detail_widget)
        self.detail_v_layout.setContentsMargins(0, 0, 0, 0)

        self.title_h_layout = QHBoxLayout()

        self.char_title = TitleLabel(self.tr_choose_char)
        self.char_title.setWordWrap(True)
        self.title_h_layout.addWidget(self.char_title)

        self.char_name_edit_btn = TransparentToolButton(FluentIcon.EDIT)
        self.char_name_edit_btn.setToolTip(self.tr_edit_char_name)
        self.char_name_edit_btn.clicked.connect(self.on_edit_char_name)
        self.char_name_edit_btn.hide()
        self.title_h_layout.addWidget(
            self.char_name_edit_btn,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.title_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.title_h_layout.addItem(self.title_spacer)
        self.detail_v_layout.addLayout(self.title_h_layout)

        self.char_subtitle = SubtitleLabel(self.tr_first_time_hint)
        self.char_subtitle.setWordWrap(True)
        self.char_subtitle.setTextColor(QColor("#FF0000"), QColor("#FF0000"))
        self.detail_v_layout.addWidget(self.char_subtitle)

        # === 特征图区 ===

        # 1. 准备核心内容
        self.feature_grid_widget = QWidget()
        self.feature_grid_widget.installEventFilter(self)
        self.feature_grid = FlowLayout(self.feature_grid_widget)

        # 2. 准备滚动卷轴，并把内容包进去
        self.feature_scroll = SmoothScrollArea()
        self.feature_scroll.setWidgetResizable(True)
        self.feature_scroll.setWidget(self.feature_grid_widget)

        # 3. 准备最外层，并把卷轴包进去
        self.feature_scroll_card = SimpleCardWidget()
        self.feature_scroll_card.setMinimumHeight(20)
        self.feature_scroll_card.setMaximumHeight(20)
        self.feature_scroll_card.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self.feature_scroll_card_layout = QVBoxLayout(self.feature_scroll_card)
        self.feature_scroll_card_layout.setContentsMargins(2, 2, 2, 2)
        self.feature_scroll_card_layout.addWidget(self.feature_scroll)

        # 4. set style
        self.feature_scroll.enableTransparentBackground()

        self.detail_v_layout.addWidget(self.feature_scroll_card, stretch=3)

        # === 出招表区 ===
        self.combo_title_layout = QHBoxLayout()
        self.combo_title_label = SubtitleLabel(self.tr_combo_title)
        self.combo_title_layout.addWidget(self.combo_title_label)

        self.combo_info_btn = TransparentToolButton(FluentIcon.INFO, self)
        self.combo_info_btn.clicked.connect(self.show_combo_flyout)

        btn_layout = QVBoxLayout()
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.addWidget(
            self.combo_info_btn,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.combo_title_layout.addLayout(btn_layout)
        self.combo_title_layout.addStretch(1)

        self.detail_v_layout.addLayout(self.combo_title_layout)

        # 出招表卡片 (方案 A: 左右双翼工作台)
        self.combo_card = SimpleCardWidget(self.detail_widget)
        self.combo_card_layout = QHBoxLayout(self.combo_card)
        self.combo_card_layout.setContentsMargins(14, 14, 14, 14)
        self.combo_card_layout.setSpacing(14)

        # ----------------------------------------------------
        # 左翼：主操作区 (区块 1 + 区块 2 + 区块 4)
        # ----------------------------------------------------
        self.combo_main_widget = QWidget(self.combo_card)
        self.combo_main_layout = QVBoxLayout(self.combo_main_widget)
        self.combo_main_layout.setContentsMargins(0, 0, 0, 0)
        self.combo_main_layout.setSpacing(10)

        # 【区块 1：下拉选择 + 管理 + 可用指令开关】
        self.combo_header_layout = QHBoxLayout()
        self.combo_header_layout.setContentsMargins(0, 0, 0, 0)
        self.combo_header_layout.setSpacing(8)

        self.combo_select = SearchableComboBox(self.combo_main_widget)
        self.combo_select.setPlaceholderText(
            self.tr("下拉选择, 或直接输入名称以创建新{combo}").replace(
                "{combo}", self.tr_combo_title
            )
        )
        self.combo_select.currentTextChanged.connect(self.on_combo_changed)
        self.combo_header_layout.addWidget(self.combo_select, 1)

        self.combo_unbind_btn = PushButton(
            FluentIcon.LINK, self.tr_unbind_success, self.combo_main_widget
        )
        self.combo_unbind_btn.setEnabled(False)
        self.combo_unbind_btn.clicked.connect(self.on_unbind_combo)
        self.combo_header_layout.addWidget(self.combo_unbind_btn)

        self.combo_doc_btn = ToggleButton(
            FluentIcon.DOCUMENT, self.tr("可用指令"), self.combo_main_widget
        )
        self.combo_doc_btn.setChecked(False)
        self.combo_doc_btn.toggled.connect(self.toggle_doc_wing)
        self.combo_header_layout.addWidget(self.combo_doc_btn)

        self.combo_main_layout.addLayout(self.combo_header_layout)

        # 【区块 2：出招表编辑区】
        self.combo_text = CodeEditor(self.combo_main_widget)
        self.combo_text.setLineWrapMode(PlainTextEdit.LineWrapMode.NoWrap)
        self.combo_text.setPlaceholderText("skill,wait(0.5),l_click(3),ultimate")
        self.combo_text.setMinimumHeight(140)
        self.highlighter = PythonHighlighter(self.combo_text.document())
        self.combo_main_layout.addWidget(self.combo_text, 1)

        # 【区块 4：动作按钮组 (靠右排列)】
        self.combo_actions_layout = QHBoxLayout()
        self.combo_actions_layout.addStretch(1)

        self.ask_ai_btn = PushButton(FluentIcon.ROBOT, self.tr_ask_ai, self.combo_main_widget)
        self.ask_ai_btn.setEnabled(False)
        self.ask_ai_btn.clicked.connect(self.on_ask_ai)
        self.combo_actions_layout.addWidget(self.ask_ai_btn)

        self.combo_test_btn = PushButton(
            FluentIcon.PLAY_SOLID, self.tr("运行一次测试"), self.combo_main_widget
        )
        self.combo_test_btn.clicked.connect(self.on_test_combo)
        self.combo_actions_layout.addWidget(self.combo_test_btn)

        self.combo_save_btn = PrimaryPushButton(
            FluentIcon.SAVE, self.tr("应用更改"), self.combo_main_widget
        )
        self.combo_save_btn.clicked.connect(self.on_save_combo)
        self.combo_actions_layout.addWidget(self.combo_save_btn)

        self.combo_main_layout.addLayout(self.combo_actions_layout)

        self.combo_card_layout.addWidget(self.combo_main_widget, 6)

        # 原生 Fluent 垂直分割线
        self.doc_divider = VerticalSeparator(self.combo_card)
        self.combo_card_layout.addWidget(self.doc_divider)

        # ----------------------------------------------------
        # 右翼：区块 3【可用指令纯文本参考区】
        # ----------------------------------------------------
        self._init_doc_wing()
        self.combo_card_layout.addWidget(self.doc_wing, 4)
        self.toggle_doc_wing(False)

        self.detail_v_layout.addWidget(self.combo_card, stretch=4)

        self.main_h_layout.addWidget(self.left_widget, 1)
        self.main_h_layout.addWidget(self.detail_widget, 4)

        self.current_char_id = None
        self.current_char_name = None
        self.refresh_list()

    def eventFilter(self, watched, event: QEvent):
        if hasattr(self, "feature_grid_widget") and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._update_feature_widget_height)

        return super().eventFilter(watched, event)

    @property
    def name(self):  # type: ignore
        return self.tr_name

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

    def _on_framework_task_changed(self, task) -> None:
        if task is not self._get_task() or not self._combo_test_pending:
            return
        if task.running or task.mode is not None:
            return
        self._combo_test_pending = False
        self.on_combo_changed(self.combo_select.currentText())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_list()

    def refresh_list(self):
        select = self.char_list_widget.currentItem()
        select_id = select.data(Qt.ItemDataRole.UserRole) if select else None
        self.current_char_id = None
        self.current_char_name = None
        self._all_characters = self.manager.get_all_characters()
        self.char_list_widget.setUpdatesEnabled(False)
        self.char_list_widget.clear()
        for char_id, char_data in self._all_characters.items():
            item = QListWidgetItem(char_data["char_name"])
            item.setData(Qt.ItemDataRole.UserRole, char_id)
            self.char_list_widget.addItem(item)
        self.char_list_widget.reapply_filter()
        self.char_list_widget.setUpdatesEnabled(True)

        # Test Code: Add dummy items
        # for i in range(20):
        #     self.char_list_widget.addItem(f"测试角色 {i}")

        if self.char_list_widget.count() != 0:
            self.char_subtitle.hide()
        else:
            self.char_subtitle.show()

        self._reload_combo_options()

        self.on_combo_changed("")

        self.delete_char_btn.setEnabled(False)
        self.char_title.setText(self.tr_choose_char)
        self.char_title.setWordWrap(True)
        self.title_spacer.changeSize(0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.title_h_layout.invalidate()
        self.char_name_edit_btn.hide()
        for i in reversed(range(self.feature_grid.count())):
            layout_item = self.feature_grid.takeAt(i)  # 1. 从布局中取回 QLayoutItem
            if layout_item:
                layout_item.deleteLater()

        if select_id:
            for i in range(self.char_list_widget.count()):
                item = self.char_list_widget.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == select_id and not item.isHidden():
                    self.char_list_widget.setCurrentItem(item)
                    break
        else:
            self.char_list_widget.setCurrentItem(None)

        QTimer.singleShot(0, self._update_feature_widget_height)

    def on_refresh_btn_clicked(self):
        char_registry.rescan_external()
        self.refresh_list()

    def on_export_data(self):
        downloads_path = Path.home() / "Downloads"
        base_name = "ok-nte-custom"
        extension = ".zip"
        zip_path = downloads_path / f"{base_name}{extension}"

        counter = 1
        while zip_path.exists():
            zip_path = downloads_path / f"{base_name} ({counter}){extension}"
            counter += 1

        if not self.manager.export_custom_data(zip_path):
            return

        reveal_in_explorer(zip_path)

    def on_open_workshop(self) -> None:
        if self.owner and hasattr(self.owner, "navigate_to_workshop"):
            self.owner.navigate_to_workshop()

    def show_data_manager(self):
        dialog = MessageBoxBase(self.window())
        dialog.widget.setMinimumWidth(860)
        dialog.widget.setMinimumHeight(740)
        dialog.viewLayout.setSpacing(14)

        # Title bar: '资料管理' label
        title_label = SubtitleLabel(self.tr("资料管理"), dialog)
        dialog.viewLayout.addWidget(title_label)

        # === Card 1: 出招表 (原生 HeaderCardWidget) ===
        combo_card = HeaderCardWidget(dialog)
        combo_card.setTitle(self.tr("出招表"))
        combo_card.headerView.setFixedHeight(48)
        combo_card.headerLayout.removeWidget(combo_card.headerLabel)

        combo_card.headerLayout.addWidget(combo_card.headerLabel)
        refresh_hint = CaptionLabel(self.tr_external_chars_hint, combo_card)
        combo_card.headerLayout.addSpacing(14)
        combo_card.headerLayout.addWidget(refresh_hint)

        combo_card.headerLayout.addStretch(1)

        cnb_doc_url = "https://cnb.cool/BnanZ0/ok-nte-update/-/blob/main/docs/zh-CN/development/combat-planner.md"
        gh_doc_url = (
            "https://github.com/BnanZ0/ok-nte/blob/main/docs/en/development/combat-planner.md"
        )
        doc_url = cnb_doc_url if is_chinese() else gh_doc_url
        doc_btn = HyperlinkButton(doc_url, self.tr("编写指南"), combo_card, FluentIcon.DOCUMENT)
        combo_card.headerLayout.addWidget(doc_btn)

        combo_view_layout = QVBoxLayout()
        combo_view_layout.setContentsMargins(0, 0, 0, 0)
        combo_view_layout.setSpacing(10)
        combo_card.viewLayout.setContentsMargins(20, 12, 20, 16)
        combo_card.viewLayout.addLayout(combo_view_layout)

        # CommandBar in SimpleCardWidget for visual hierarchy
        command_card = SimpleCardWidget(combo_card)
        command_layout = QHBoxLayout(command_card)
        command_layout.setContentsMargins(10, 6, 10, 6)

        command_bar = CommandBar(command_card)
        command_bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        command_bar.setButtonTight(True)

        toggle_builtin_action = QAction(FluentIcon.VIEW.icon(), self.tr_show_builtin, command_bar)
        toggle_builtin_action.setCheckable(True)
        toggle_builtin_action.setChecked(False)

        copy_builtin_action = QAction(FluentIcon.COPY.icon(), self.tr_copy_to_external, command_bar)
        copy_builtin_action.setEnabled(False)

        batch_delete_action = QAction(FluentIcon.DELETE.icon(), self.tr_batch_delete, command_bar)
        batch_delete_action.setEnabled(False)

        open_folder_action = QAction(
            FluentIcon.FOLDER.icon(), self.tr_open_external_chars_folder, command_bar
        )

        command_bar.addAction(toggle_builtin_action)
        command_bar.addSeparator()
        command_bar.addAction(copy_builtin_action)
        command_bar.addSeparator()
        command_bar.addAction(batch_delete_action)
        command_bar.addSeparator()
        command_bar.addAction(open_folder_action)

        command_layout.addWidget(command_bar, 1)
        combo_view_layout.addWidget(command_card)

        # Master-Detail layout: 5:5 split between List and Preview
        content_layout = QHBoxLayout()
        content_layout.setSpacing(12)

        # Left column: Combo list (50% width)
        combo_list_widget = SearchableListWidget(combo_card)
        combo_list_widget.setPlaceholderText(
            self.char_list_widget.search_edit.placeholderText().replace(
                self.tr("角色"), self.tr_combo_title
            )
        )
        combo_list_widget.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        combo_list_widget.list_widget.setMinimumHeight(320)
        content_layout.addWidget(combo_list_widget, 1)

        # Right column: Detail preview (50% width, read-only state)
        editor_text = TextEdit(combo_card)
        editor_text.setPlaceholderText(self.tr_unbound_text)
        editor_text.setReadOnly(True)
        editor_text.setMinimumHeight(320)
        content_layout.addWidget(editor_text, 1)

        combo_view_layout.addLayout(content_layout, 1)
        dialog.viewLayout.addWidget(combo_card, 1)

        # === Card 2: 数据备份 (原生 GroupHeaderCardWidget) ===
        data_card = GroupHeaderCardWidget(dialog)
        data_card.setTitle(self.tr_data_backup)

        export_data_btn = PushButton(FluentIcon.SHARE, self.tr_export_data, data_card)
        import_data_btn = PushButton(FluentIcon.DOWNLOAD, self.tr_import_data, data_card)

        backup_btn_widget = QWidget(data_card)
        backup_btn_layout = QHBoxLayout(backup_btn_widget)
        backup_btn_layout.setContentsMargins(0, 0, 0, 0)
        backup_btn_layout.setSpacing(10)
        backup_btn_layout.addWidget(export_data_btn)
        backup_btn_layout.addWidget(import_data_btn)

        data_card.addGroup(
            FluentIcon.ZIP_FOLDER,
            self.tr_user_data,
            self.tr_data_manager_hint,
            backup_btn_widget,
        )
        dialog.viewLayout.addWidget(data_card, 0)

        # Fluent standard bottom button bar: only Close button
        dialog.yesButton.setText(self.tr("关闭"))
        dialog.cancelButton.hide()

        dialog.setProperty("combos_modified", False)

        def populate_combos(target_selected_id=None, select_first=False):
            combo_list_widget.list_widget.clear()
            show_builtin = toggle_builtin_action.isChecked()
            if show_builtin:
                items = self.manager.get_all_impl_items(with_source_prefix=True)
            else:
                items = self._get_manageable_impl_items()

            target_item = None
            for combo_name, combo_id in items:
                item = QListWidgetItem(combo_name)
                item.setData(Qt.ItemDataRole.UserRole, combo_id)
                combo_list_widget.list_widget.addItem(item)
                if target_selected_id and combo_id == target_selected_id:
                    target_item = item

            if target_item:
                target_item.setSelected(True)
                combo_list_widget.list_widget.scrollToItem(target_item)
            elif select_first and combo_list_widget.list_widget.count():
                combo_list_widget.list_widget.setCurrentRow(0)
            on_selection_changed()

        def on_selection_changed():
            selected_items = combo_list_widget.list_widget.selectedItems()
            if not selected_items:
                editor_text.clear()
                editor_text.setPlaceholderText(self.tr_unbound_text)
                copy_builtin_action.setEnabled(False)
                batch_delete_action.setEnabled(False)
                return

            builtin_selected = [
                it
                for it in selected_items
                if self.manager.is_builtin_impl(it.data(Qt.ItemDataRole.UserRole))
            ]
            deletable_selected = [
                it
                for it in selected_items
                if not self.manager.is_builtin_impl(it.data(Qt.ItemDataRole.UserRole))
            ]

            copy_builtin_action.setEnabled(len(selected_items) == 1 and len(builtin_selected) == 1)
            batch_delete_action.setEnabled(len(deletable_selected) > 0)

            if len(selected_items) == 1:
                item = selected_items[0]
                cid = item.data(Qt.ItemDataRole.UserRole)
                if self.manager.is_builtin_impl(cid):
                    source = self.manager.get_builtin_impl_source(cid)
                    editor_text.setPlainText(source or self.tr_code_source_unavailable)
                elif self.manager.is_registered_impl(cid):
                    source = self.manager.get_external_impl_source(cid)
                    editor_text.setPlainText(source or self.tr_code_source_unavailable)
                else:
                    content = self.manager.get_combo(cid)
                    editor_text.setPlainText(content or "")
            else:
                names = [it.text() for it in selected_items]
                editor_text.setPlainText("\n• ".join([""] + names).strip())

        combo_list_widget.list_widget.itemSelectionChanged.connect(on_selection_changed)
        toggle_builtin_action.triggered.connect(lambda: populate_combos(select_first=True))
        open_folder_action.triggered.connect(self.on_open_external_chars_folder)

        def on_copy_builtin():
            selected_items = combo_list_widget.list_widget.selectedItems()
            if len(selected_items) != 1:
                return
            item = selected_items[0]
            cid = item.data(Qt.ItemDataRole.UserRole)
            if not self.manager.is_builtin_impl(cid):
                return

            default_filename = cid.removeprefix("builtin:").capitalize()

            copy_dialog = MessageBoxBase(dialog)
            copy_dialog.widget.setMinimumWidth(440)
            copy_dialog.viewLayout.setSpacing(10)

            copy_dialog_title = SubtitleLabel(self.tr_copy_dialog_title, copy_dialog)
            copy_dialog.viewLayout.addWidget(copy_dialog_title)

            copy_hint = CaptionLabel(self.tr_copy_dialog_hint, copy_dialog)
            copy_hint.setWordWrap(True)
            copy_dialog.viewLayout.addWidget(copy_hint)

            directory_title = BodyLabel(self.tr_copy_directory, copy_dialog)
            copy_dialog.viewLayout.addWidget(directory_title)
            directory_edit = LineEdit(copy_dialog)
            copy_dialog.viewLayout.addWidget(directory_edit)
            copy_dialog.yesButton.setEnabled(False)
            directory_edit.textChanged.connect(
                lambda directory: copy_dialog.yesButton.setEnabled(bool(directory.strip()))
            )

            file_title = BodyLabel(self.tr_copy_file_name, copy_dialog)
            copy_dialog.viewLayout.addWidget(file_title)
            file_edit = LineEdit(copy_dialog)
            file_edit.setText(default_filename)
            copy_dialog.viewLayout.addWidget(file_edit)

            if not copy_dialog.exec():
                return

            directory = directory_edit.text().strip()
            new_filename = file_edit.text().strip()
            success, new_impl_id, err = self.manager.copy_builtin_to_external(
                cid, directory, new_filename
            )
            if not success:
                InfoBar.error(
                    title=self.tr_copy_failed,
                    content=err or "",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=dialog,
                )
                return

            dialog.setProperty("combos_modified", True)
            populate_combos(target_selected_id=new_impl_id)
            show_info_bar(
                dialog,
                self.tr_copy_success_msg.format(new_impl_id.removeprefix("external:")),
                self.tr_copy_success,
            )

        copy_builtin_action.triggered.connect(on_copy_builtin)

        def on_batch_delete():
            selected_items = combo_list_widget.list_widget.selectedItems()
            if not selected_items:
                return

            deletable_items = []
            for item in selected_items:
                cid = item.data(Qt.ItemDataRole.UserRole)
                cname = item.text()
                if self.manager.is_registered_impl(cid):
                    if self.manager.delete_external_impl_and_references(cid):
                        deletable_items.append((cid, cname))
                else:
                    self.manager.delete_combo(cid)
                    deletable_items.append((cid, cname))

            if not deletable_items:
                return

            dialog.setProperty("combos_modified", True)
            populate_combos()
            editor_text.clear()

            deleted_names = ", ".join([name for _, name in deletable_items])
            show_info_bar(dialog, self.tr_del_combo_msg.format(deleted_names), self.tr_del_success)

        batch_delete_action.triggered.connect(on_batch_delete)

        def on_import_and_refresh():
            self.on_import_data()
            dialog.setProperty("combos_modified", True)
            populate_combos(select_first=True)
            editor_text.clear()

        import_data_btn.clicked.connect(on_import_and_refresh)
        export_data_btn.clicked.connect(self.on_export_data)

        populate_combos()

        dialog.exec()

        if dialog.property("combos_modified"):
            self._reload_combo_options()
            if self.current_char_id:
                self._render_right_panel()
            else:
                self.on_combo_changed("")

    show_combo_manager_dialog = show_data_manager

    def _init_doc_wing(self):
        self.doc_wing = QWidget(self.combo_card)
        self.doc_wing_layout = QVBoxLayout(self.doc_wing)
        self.doc_wing_layout.setContentsMargins(0, 0, 0, 0)
        self.doc_wing_layout.setSpacing(10)

        # 【区块 3 头部：与左侧控制栏单行齐平对齐】
        doc_header = QHBoxLayout()
        doc_header.setContentsMargins(0, 0, 0, 0)
        doc_header.setSpacing(8)

        self.doc_search = SearchLineEdit(self.doc_wing)
        self.doc_search.setPlaceholderText(
            self.char_list_widget.search_edit.placeholderText().replace(
                self.tr("角色"), self.tr("指令")
            )
        )
        self.doc_search.setClearButtonEnabled(True)
        doc_header.addWidget(self.doc_search, 1)

        self.doc_wing_layout.addLayout(doc_header)

        # 【区块 3 纯文本展示区：TextEdit (只读纯文本)】
        self.doc_text_edit = TextEdit(self.doc_wing)
        self.doc_text_edit.setReadOnly(True)
        raw_doc = self._doc_cache_by_locale.get(
            self._locale_name(), self._doc_cache
        ) or self.generate_doc(start_translation=False)
        self.doc_text_edit.setPlainText(raw_doc)
        self.doc_wing_layout.addWidget(self.doc_text_edit, 1)

        self.doc_search.textChanged.connect(self._filter_doc_commands)

    def toggle_doc_wing(self, show: bool | None = None):
        if show is None:
            show = not self.doc_wing.isVisible()
        self.doc_wing.setVisible(show)
        if hasattr(self, "doc_divider"):
            self.doc_divider.setVisible(show)
        if self.combo_doc_btn.isChecked() != show:
            self.combo_doc_btn.setChecked(show)
        if show:
            self.generate_doc(start_translation=True)
            self._filter_doc_commands(self.doc_search.text())

    def show_doc_dialog(self):
        self.toggle_doc_wing(True)

    def _get_manageable_impl_items(self) -> list[tuple[str, str]]:
        return [
            (name, impl_id)
            for name, impl_id in self.manager.get_all_impl_items(with_source_prefix=True)
            if not self.manager.is_builtin_impl(impl_id)
        ]

    def on_open_external_chars_folder(self):
        open_explorer_folder(EXTERNAL_CHARS_DIR)

    def on_import_data(self):
        if not confirm_external_code_import(self):
            return
        downloads_path = Path.home() / "Downloads"
        file_path, _ = QFileDialog.getOpenFileName(
            self, self.tr_import_data, str(downloads_path), "ZIP (*.zip)"
        )
        if not file_path:
            return

        try:
            imported = self.manager.import_custom_data(file_path)
        except Exception as e:
            InfoBar.error(
                title=self.tr_import_failed,
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self.window(),
            )
            self.logger.error(str(e))
            return

        # Scan imported external code before migrating its persisted implementation IDs.
        char_registry.rescan_external()
        self.manager.load_db()
        self.manager.validate_db()
        self.refresh_list()

        show_info_bar(self.window(), self.tr_import_msg.format(imported), self.tr_import_success)

    def on_char_selected(self, item):
        if not item:
            self.current_char_id = None
            self.current_char_name = None
            self.char_title.setText(self.tr_choose_char)
            self.char_title.setWordWrap(True)
            self.title_spacer.changeSize(0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            self.title_h_layout.invalidate()
            self.char_name_edit_btn.hide()
            return
        self.current_char_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_char_name = item.text()
        self._render_right_panel()

    def _reload_combo_options(self):
        self.combo_select.blockSignals(True)
        self.combo_select.clear()
        for combo_name, combo_id in self.manager.get_all_impl_items(with_source_prefix=True):
            self.combo_select.addItem(combo_name, userData=combo_id)
        self.combo_select.setCurrentIndex(-1)
        self.combo_select.blockSignals(False)

    def _resolve_combo_id(self, text: str | None = None) -> str:
        if text is None:
            text = self.combo_select.currentText()
        text = text.strip()
        idx = self.combo_select.findText(text)
        if idx >= 0:
            data = self.combo_select.itemData(idx)
            if isinstance(data, str):
                return data
        return ""

    def _set_combo_selection_by_id(self, combo_id: str):
        combo_name = self.manager.get_impl_name(combo_id, with_source_prefix=True)
        self.combo_select.blockSignals(True)
        idx = self.combo_select.findData(combo_id)
        if idx >= 0:
            self.combo_select.setCurrentIndex(idx)
        else:
            self.combo_select.setCurrentText(combo_name)
        self.combo_select.blockSignals(False)

    def _render_right_panel(self):
        if not self.current_char_id:
            return
        char_info = self.manager.get_character_info_by_id(self.current_char_id)
        if not char_info:
            return

        self.delete_char_btn.setEnabled(True)
        self.char_title.setText(self.current_char_name)
        self.char_title.setWordWrap(False)
        self.title_spacer.changeSize(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.title_h_layout.invalidate()
        self.char_name_edit_btn.show()
        combo_id = char_info["impl_id"]
        combo_name = self.manager.get_impl_name(combo_id, with_source_prefix=True)
        self._set_combo_selection_by_id(combo_id)

        # Manually trigger the text change logic to ensure the implementation source renders.
        self.on_combo_changed(combo_name, combo_id)

        # update feature grid
        while self.feature_grid.count() > 0:
            item: FeatureCard = self.feature_grid.takeAt(0)
            if item:
                item.deleteLater()

        feature_ids = char_info["feature_ids"]
        for fid in feature_ids:
            img_mat, _, _ = self.manager.load_feature_image(fid)
            if img_mat is not None:
                card = FeatureCard(fid, img_mat, self.on_delete_feature)
                self.feature_grid.addWidget(card)

        # Test Code: Add dummy items
        # for i in range(20):
        #     test_fid = f"test_feature_{i}"
        #     if img_mat is not None:
        #         card = FeatureCard(test_fid, img_mat, lambda fid: None)
        #         self.feature_grid.addWidget(card)

        QTimer.singleShot(0, self._update_feature_widget_height)

    def on_delete_feature(self, fid):
        if self.current_char_id:
            self.manager.remove_feature_from_character(self.current_char_id, fid)
            self._render_right_panel()

    def on_combo_changed(self, combo_name, combo_id=None):
        has_bound_combo = bool(
            self.current_char_id and self.manager.get_character_impl_id_by_id(self.current_char_id)
        )
        self.combo_unbind_btn.setEnabled(has_bound_combo)

        if combo_name == "":
            self.combo_text.setPlainText(self.tr_unbound_text)
            self.combo_text.setReadOnly(True)
            self.combo_text.setEnabled(False)
            self.combo_save_btn.setEnabled(True)
            self.ask_ai_btn.setEnabled(False)
            self.combo_test_btn.setEnabled(False)
            self.combo_select.setText(combo_name)
            self.combo_select.setReadOnly(False)
            self.combo_select.setCurrentIndex(-1)
            return

        if combo_id is None:
            combo_id = self._resolve_combo_id(combo_name)

        if self.manager.is_builtin_impl(combo_id):
            source = self.manager.get_builtin_impl_source(combo_id)
            self.combo_text.setPlainText(source or self.tr_code_source_unavailable)
            self.combo_text.setReadOnly(True)
            self.combo_text.setEnabled(True)
            self.combo_save_btn.setEnabled(self.current_char_id is not None)
            self.ask_ai_btn.setEnabled(False)
            self.combo_test_btn.setEnabled(getattr(og.app, "debug", False))
            self.combo_select.setReadOnly(False)
            return

        is_code_impl = self.manager.is_registered_impl(combo_id)
        if is_code_impl:
            source = self.manager.get_external_impl_source(combo_id)
            self.combo_text.setPlainText(source or self.tr_code_source_unavailable)
            self.combo_text.setReadOnly(False)
            self.combo_text.setEnabled(True)
            self.combo_save_btn.setEnabled(bool(source))
            self.ask_ai_btn.setEnabled(bool(source))
            self.combo_test_btn.setEnabled(getattr(og.app, "debug", False))
            self.combo_select.setReadOnly(False)
            return

        self.combo_text.setReadOnly(False)
        self.combo_text.setEnabled(True)
        self.combo_save_btn.setEnabled(True)
        self.ask_ai_btn.setEnabled(False)
        self.combo_select.setReadOnly(False)

        # If the combo matches an existing one, update the text area to show its content
        combo_content = self.manager.get_combo(combo_id)
        if combo_content:
            self.combo_text.setPlainText(combo_content)
        else:
            self.combo_text.clear()

        # Update test button state
        self.combo_test_btn.setEnabled(True)

    def on_ask_ai(self):
        combo_id = self._resolve_combo_id(self.combo_select.currentText())
        if self.manager.is_builtin_impl(combo_id) or not self.manager.is_registered_impl(combo_id):
            return
        entry = char_registry.get(combo_id)
        if entry is None or entry.source != "external":
            return

        source = self.combo_text.toPlainText().strip()
        if not source:
            return
        class_name = entry.char_cls.__name__
        SOURCE = f"```python\n{source}\n```"
        BASE_CHAR_URL = (
            "https://raw.githubusercontent.com/BnanZ0/ok-nte/refs/heads/main/src/char/BaseChar.py"
        )
        COMBAT_PLANNER_URL = "https://raw.githubusercontent.com/BnanZ0/ok-nte/refs/heads/main/docs/zh-CN/development/combat-planner.md"
        prompt = self.tr(
            "{SOURCE}\n\n"
            "我想实现: \n\n"
            "请修改上面完整的 {class_name} 角色自动化代码。\n\n"
            "只返回整个文件完整修改后的 Python 代码，不要返回补丁或解释。\n"
            "保持类名为 {class_name}。保留仍然需要的 import。\n\n"
            "在思考辅助方法、任务 API、状态、切人、冷却和战斗流程时, 请参考: \n"
            "BaseChar 角色基类: {BASE_CHAR_URL}\n"
            "Combat Planner 开发指南: {COMBAT_PLANNER_URL}"
        ).format(
            SOURCE=SOURCE,
            class_name=class_name,
            BASE_CHAR_URL=BASE_CHAR_URL,
            COMBAT_PLANNER_URL=COMBAT_PLANNER_URL,
        )
        QApplication.clipboard().setText(prompt)
        show_info_bar(self.window(), self.tr_ask_ai_copied, self.tr_copy_success)

    def on_test_combo(self):
        combo_input = self.combo_select.currentText().strip()
        combo_id = self._resolve_combo_id(combo_input)
        is_code_impl = self.manager.is_registered_impl(combo_id)
        combo_source = self.combo_text.toPlainText()

        if (
            is_code_impl
            and not self.manager.is_builtin_impl(combo_id)
            and combo_source != self.manager.get_external_impl_source(combo_id)
        ):
            InfoBar.warning(
                title=self.tr("应用更改"),
                content=self.tr("请先应用外置代码更改后再运行测试."),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self.window(),
            )
            return

        if not is_code_impl:
            combo_content = combo_source.strip()
            if not combo_content:
                return
            from src.char.custom.CustomChar import CustomChar

            is_valid, error = CustomChar.validate_combo_syntax(combo_content)
            if not is_valid:
                InfoBar.error(
                    title=self.tr_combo_invalid_title,
                    content=error or "",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3500,
                    parent=self.window(),
                )
                return
        task = self._get_task()
        if task is None:
            InfoBar.error(
                title=self.tr_combo_invalid_title,
                content=self.tr("角色工具任务未注册"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self.window(),
            )
            return
        self._combo_test_pending = True
        self.combo_test_btn.setEnabled(False)
        task.test_combo(
            self.current_char_id,
            self._resolve_combo_id(combo_input),
            self.combo_text.toPlainText().strip(),
        )
        og.app.start_controller.start(task)

    def on_save_combo(self):
        combo_input = self.combo_select.currentText().strip()
        combo_source = self.combo_text.toPlainText()
        combo_content = combo_source.strip()
        combo_id = self._resolve_combo_id(combo_input)
        combo_name = self.manager.get_impl_name(combo_id, with_source_prefix=True)
        if not combo_name:
            combo_name = combo_input

        is_builtin_impl = self.manager.is_builtin_impl(combo_id)
        is_code_impl = self.manager.is_registered_impl(combo_id)

        if is_builtin_impl and not self.current_char_id:
            return

        if combo_input:
            if is_code_impl and not is_builtin_impl:
                success, error = self.manager.update_external_impl_source(combo_id, combo_source)
                if not success:
                    InfoBar.error(
                        title=self.tr_external_save_failed,
                        content=error,
                        orient=Qt.Orientation.Horizontal,
                        isClosable=True,
                        position=InfoBarPosition.TOP,
                        duration=3500,
                        parent=self.window(),
                    )
                    return
                combo_name = self.manager.get_impl_name(combo_id, with_source_prefix=True)
            elif not is_code_impl:
                from src.char.custom.CustomChar import CustomChar

                is_valid, error = CustomChar.validate_combo_syntax(combo_content)
                if not is_valid:
                    InfoBar.error(
                        title=self.tr_combo_invalid_title,
                        content=error or "",
                        orient=Qt.Orientation.Horizontal,
                        isClosable=True,
                        position=InfoBarPosition.TOP,
                        duration=3500,
                        parent=self.window(),
                    )
                    return
                if combo_id:
                    self.manager.update_combo(combo_id, combo_content)
                else:
                    combo_id = self.manager.add_combo(combo_input, combo_content)

            if self.current_char_id:
                self.manager.update_character(
                    self.current_char_id, self.current_char_name, combo_id
                )

            # update combo dropdown
            self._reload_combo_options()
            self._set_combo_selection_by_id(combo_id)

            show_info_bar(
                self.window(),
                (
                    self.tr_external_save_msg.format(combo_name)
                    if is_code_impl and not is_builtin_impl
                    else self.tr_combo_msg.format(combo_name)
                ),
                self.tr_save_success,
            )

    def on_delete_char(self):
        if not self.current_char_id:
            return

        char_to_delete = self.current_char_id
        char_name_to_delete = self.current_char_name
        self.manager.delete_character(char_to_delete)

        # Reset current selection and refresh UI
        self.current_char_id = None
        self.current_char_name = None
        self.delete_char_btn.setEnabled(False)
        self.refresh_list()

        show_info_bar(
            self.window(), self.tr_del_char_msg.format(char_name_to_delete), self.tr_del_success
        )

    def _show_edit_dialog(self, old_name):
        current_char_id = self.current_char_id
        existing_names = {
            char_data["char_name"]
            for char_id, char_data in self.manager.get_all_characters().items()
            if char_id != current_char_id
        }
        dialog = NameInputDialog(
            old_name,
            name_validator=lambda name: name not in existing_names,
            error_message=self.tr_rename_failed,
            parent=self.window(),
        )
        if dialog.exec():
            new_name = dialog.get_name()
            if new_name and new_name != old_name:
                return new_name, True
        return old_name, False

    def on_edit_char_name(self):
        if not self.current_char_id:
            return

        old_name = self.current_char_name
        new_name, ok = self._show_edit_dialog(old_name)
        if not ok:
            return

        if not new_name.strip() or new_name == old_name:
            return

        if not self.manager.update_character(self.current_char_id, char_name=new_name):
            InfoBar.error(
                title=self.tr_rename_failed_title,
                content=self.tr_rename_failed,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self.window(),
            )
            return

        self.refresh_list()
        items = self.char_list_widget.findItems(new_name, Qt.MatchFlag.MatchExactly)
        if items:
            self.char_list_widget.setCurrentItem(items[0])

        show_info_bar(self.window(), self.tr_rename_msg.format(new_name), self.tr_save_success)

    def on_unbind_combo(self):
        if not self.current_char_id:
            return

        self.manager.update_character(self.current_char_id, impl_id="")

        # 刷新列表和右侧界面
        self._render_right_panel()

        show_info_bar(
            self.window(), self.tr_unbind_msg.format(self.current_char_name), self.tr_unbind_success
        )

    def on_delete_combo(self):
        combo_name = self.combo_select.currentText().strip()
        combo_id = self._resolve_combo_id(combo_name)
        if not combo_id or self.manager.is_registered_impl(combo_id):
            return

        self.manager.delete_combo(combo_id)

        # 解绑所有正在使用该出招表的角色
        for c_id, c_data in self.manager.get_all_characters().items():
            if c_data.get("impl_id", "") == combo_id:
                self.manager.update_character(c_id, impl_id="")

        # 刷新出招表下拉列表
        self._reload_combo_options()

        # 刷新当前角色的内容显示
        if self.current_char_id:
            self._render_right_panel()
        else:
            self.on_combo_changed("")

        show_info_bar(self.window(), self.tr_del_combo_msg.format(combo_name), self.tr_del_success)

    def generate_doc(self, start_translation: bool = True):
        try:
            from src.char.custom.CustomChar import CustomChar

            docs = CustomChar.get_available_commands()
            text = "可以在出招表中输入以下指令 (以英文逗号 [ , ] 分隔):\n\n"
            translatable_text = text
            empty_text = "无"
            protected_literals = {}
            delimiter_literal = "[ , ]"
            delimiter_token = "__COMMA_SEPARATOR_LITERAL__"
            protected_literals[delimiter_token] = delimiter_literal
            translatable_text = translatable_text.replace(delimiter_literal, delimiter_token)
            for index, cmd in enumerate(docs):
                cmd_name = str(cmd.name)
                cmd_example = str(cmd.example or cmd_name)
                cmd_doc = str(cmd.doc or empty_text)
                if getattr(cmd, "if_capable", False):
                    cmd_doc += "（可用于 if 条件）"
                name_token = f"__CMD_NAME_{index}__"
                example_token = f"__CMD_EXAMPLE_{index}__"
                protected_literals[name_token] = cmd_name
                protected_literals[example_token] = cmd_example

                text += f"▶ 【 {cmd_name} 】\n"
                text += f"    • 参数: {cmd.params or empty_text}\n"
                text += f"    • 说明: {cmd_doc}\n"
                text += f"    • 示例: {cmd_example}\n\n"

                translatable_text += f"▶ 【 {name_token} 】\n"
                translatable_text += f"    • 参数: {cmd.params or empty_text}\n"
                translatable_text += f"    • 说明: {cmd_doc}\n"
                translatable_text += f"    • 示例: {example_token}\n\n"

            syntax_guide = CustomChar.get_combo_syntax_guide()
            text += f"{syntax_guide}\n"
            translatable_text += f"{syntax_guide}\n"

            self._doc_cache = text
            locale_name = self._locale_name()
            if not locale_name or locale_name == "zh_CN":
                return text

            if locale_name in self._doc_cache_by_locale:
                return self._doc_cache_by_locale[locale_name]

            if not start_translation:
                return text

            if locale_name not in self._doc_translation_pending_locales:
                self._doc_translation_pending_locales.add(locale_name)
                self._start_doc_translation(
                    text, translatable_text, locale_name, protected_literals
                )
            return "[Translating with Google...]\n\n" + text
        except Exception as e:
            return f"生成文档失败: {e}"

    def _filter_doc_commands(self, command=""):
        self._pending_command = command
        self._filter_timer.start(300)

    def _run_doc_filter(self):
        command = self._pending_command
        content = self._doc_cache_by_locale.get(self._locale_name(), self._doc_cache)
        if not isinstance(content, str) or not hasattr(self, "doc_text_edit"):
            return

        filter_text = command.strip().lower()
        if not filter_text:
            self.doc_text_edit.setPlainText(content)
            return

        filtered_lines = []
        include_block = False

        for line in content.splitlines():
            if line.startswith("▶"):
                include_block = filter_text in line

            if include_block:
                filtered_lines.append(line)

        self.doc_text_edit.setPlainText("\n".join(filtered_lines) or self.tr_no_match_cmd)

    def _start_doc_translation(
        self,
        source_text: str,
        translatable_text: str,
        locale_name: str,
        protected_literals: dict[str, str],
    ):
        threading.Thread(
            target=self._translate_doc_worker,
            args=(source_text, translatable_text, locale_name, protected_literals),
            daemon=True,
        ).start()

    def _translate_doc_worker(
        self,
        source_text: str,
        translatable_text: str,
        locale_name: str,
        protected_literals: dict[str, str],
    ):
        try:
            target_lang = locale_name.replace("_", "-")
            translated_text = self._google_translate_text(translatable_text, target_lang)
            translated_text = self._restore_protected_literals(translated_text, protected_literals)
            translated_text = f"[Translated by Google]\n\n{translated_text}"
        except Exception as translate_error:
            self.logger.warning(
                f"Google translate failed for locale '{locale_name}': {translate_error}"
            )
            translated_text = (
                "[Google Translate unavailable, showing zh_CN source text]\n\n" + source_text
            )
        self.doc_translation_ready.emit(locale_name, translated_text)

    @Slot(str, str)
    def _on_doc_translation_ready(self, locale_name: str, translated_text: str):
        self._doc_translation_pending_locales.discard(locale_name)
        self._doc_cache_by_locale[locale_name] = translated_text
        if self._locale_name() == locale_name and hasattr(self, "doc_text_edit"):
            self._filter_doc_commands(self.doc_search.text())

    @staticmethod
    def _locale_name() -> str:
        app = getattr(og, "app", None)
        if app and hasattr(app, "locale"):
            try:
                return app.locale.name()
            except Exception:
                return ""
        return ""

    @staticmethod
    def _google_translate_text(text: str, target_lang: str) -> str:
        os_info = platform.system()
        ua = (
            f"Mozilla/5.0 ({os_info}) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        response = requests.post(
            "https://translate.googleapis.com/translate_a/single",
            data={
                "client": "gtx",
                "sl": "auto",
                "tl": target_lang,
                "dt": "t",
                "q": text,
            },
            headers={"User-Agent": ua},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        segments = data[0] if isinstance(data, list) and data else []
        translated = "".join(
            segment[0]
            for segment in segments
            if isinstance(segment, list) and segment and segment[0]
        )
        if not translated:
            raise ValueError("Google translate returned empty content")
        return translated

    @staticmethod
    def _restore_protected_literals(text: str, literals: dict[str, str]) -> str:
        restored = text
        for token, value in literals.items():
            restored = restored.replace(token, value)
        return restored

    def show_combo_flyout(self):
        Flyout.create(
            icon=InfoBarIcon.INFORMATION,
            title=self.tr("提示"),
            content=self.tr_combo_tips,
            target=self.combo_info_btn,
            parent=self,
            isClosable=False,
        )

    def _update_feature_widget_height(self):
        layout = self.feature_grid_widget.layout()
        if layout.count() > 0:
            last_item = layout.itemAt(layout.count() - 1)
            h = last_item.geometry().bottom() + layout.contentsMargins().bottom()
        else:
            h = 20
        final_h = max(20, min(h + 5, 225))
        self.feature_scroll_card.setMaximumHeight(final_h)


class FeatureCard(BorderCardWidget):
    def __init__(self, fid, img_mat, delete_callback, parent=None):
        super().__init__(parent)
        self.setBorderWidth(2)
        self.fid = fid
        self.delete_callback = delete_callback

        # 1. 图片组件
        self.lbl = ImageLabel()
        self.lbl.setImage(
            cv_to_pixmap(img_mat).scaled(
                70,
                70,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

        # 2. 布局
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self.lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        # 3. 删除按钮
        self.del_btn = PrimaryToolButton(FluentIcon.CLOSE, self)
        self.del_btn.hide()
        self.del_btn.setFixedSize(30, 30)
        self.del_btn.clicked.connect(lambda: self.delete_callback(self.fid))

        # 4. 设置初始尺寸
        lbl_size = self.lbl.sizeHint()
        self.setFixedSize(lbl_size.width() + 30, lbl_size.height() + 30)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        center_x = (self.width() - self.del_btn.width()) // 2
        center_y = (self.height() - self.del_btn.height()) // 2
        self.del_btn.move(center_x, center_y)

    def enterEvent(self, e):
        blur = QGraphicsBlurEffect(self)
        blur.setBlurRadius(15)
        self.lbl.setGraphicsEffect(blur)
        self.del_btn.show()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.lbl.setGraphicsEffect(None)
        self.del_btn.hide()
        super().leaveEvent(e)
