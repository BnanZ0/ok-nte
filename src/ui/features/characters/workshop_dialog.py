"""Fluent dialogs used by the community team workshop."""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    HyperlinkButton,
    IndeterminateProgressRing,
    LineEdit,
    PrimaryPushButton,
    PrimarySplitPushButton,
    PushButton,
    RoundMenu,
    SearchLineEdit,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
    TextBrowser,
)

from src.char.custom.CustomCharManager import CustomCharManager
from src.char.workshop.installations import WorkshopInstallation
from src.char.workshop.models import (
    CatalogEntry,
    TeamPackage,
    WorkshopFormatError,
    filter_catalog_entries,
    group_catalog_entries,
    parse_version,
)
from src.char.workshop.repository import IndexSource, WorkshopRepository
from src.char.workshop.service import (
    WorkshopInstallError,
    WorkshopInstallErrorCode,
    WorkshopPackageService,
)
from src.ui.features.characters.safety_dialog import EXTERNAL_CODE_SAFETY_NOTICE
from src.ui.foundation.dialogs import MessageBoxBase
from src.ui.foundation.i18n import is_chinese
from src.ui.foundation.info_bar import show_bar
from src.ui.foundation.widgets.cards import BorderCardWidget
from src.ui.foundation.widgets.search import SearchableComboBox


class ElidedCaptionLabel(CaptionLabel):
    """Single-line caption that keeps the original text available in a tooltip."""

    def __init__(self, text: str = "", parent=None):
        self._full_text = ""
        super().__init__(parent)
        self.set_elided_text(text)

    def set_elided_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_text()

    def _update_text(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideRight,
                max(0, self.contentsRect().width()),
            )
        )

    def resizeEvent(self, event) -> None:
        self._update_text()
        super().resizeEvent(event)


class WorkshopSlotPreviewCard(BorderCardWidget):
    """Compact card to display a slot's character and combo binding in workshop details."""

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.setBorderWidth(1)
        self.index = index
        self.setFixedHeight(50)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        self.title_label = StrongBodyLabel("-", self)
        self.combo_label = ElidedCaptionLabel("-", self)

        layout.addWidget(self.title_label)
        layout.addWidget(self.combo_label)

    def set_slot(self, char_name: str, combo_desc: str) -> None:
        self.title_label.setText(char_name if char_name else "-")
        self.title_label.setToolTip(char_name)
        self.combo_label.set_elided_text(combo_desc if combo_desc else "-")

    def clear(self) -> None:
        self.title_label.setText("-")
        self.title_label.setToolTip("")
        self.combo_label.set_elided_text("-")


class BackgroundCall(QObject):
    """Run a blocking callable and return its result through Qt signals."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, action: Callable[[], object], parent=None):
        super().__init__(parent)
        self._action = action

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.succeeded.emit(self._action())
        except Exception as error:
            self.failed.emit(error)


def workshop_error_text(error: object, tr: Callable[[str], str]) -> str:
    if isinstance(error, WorkshopInstallError):
        messages = {
            WorkshopInstallErrorCode.FILES_CHANGED: tr("文件列表已变化, 无法自动更新, 请重新导入."),
            WorkshopInstallErrorCode.LOCAL_SOURCE_CHANGED: tr("本地源码已修改, 已跳过自动更新."),
            WorkshopInstallErrorCode.INSTALLATION_MISSING: tr("本地安装文件缺失, 请重新导入."),
            WorkshopInstallErrorCode.INVALID_INSTALLATIONS: tr(
                "无法读取工坊安装记录, 请检查 workshop_installs.json."
            ),
            WorkshopInstallErrorCode.PACKAGE_MISMATCH: tr(
                "下载的方案与更新信息不一致, 请刷新工坊后重试."
            ),
        }
        if error.code in messages:
            return messages[error.code]
    return str(error) or error.__class__.__name__


class PackageMetadataDialog(MessageBoxBase):
    def __init__(
        self,
        defaults: TeamPackage,
        parent=None,
        *,
        repository: WorkshopRepository | None = None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.viewLayout.setSpacing(6)
        title_row = QHBoxLayout()
        title_row.addWidget(SubtitleLabel(self.tr("导出队伍"), self))
        title_row.addStretch(1)
        self.fill_button = HyperlinkButton(self)
        self.fill_button.setText(self.tr("从工坊填入"))
        self.fill_button.setVisible(repository is not None)
        self.fill_button.clicked.connect(self._fill_from_workshop)
        title_row.addWidget(self.fill_button)
        self.viewLayout.addLayout(title_row)

        self.viewLayout.addWidget(BodyLabel(self.tr("名称"), self))
        self.name_edit = LineEdit(self)
        self.name_edit.setText(defaults.name)
        self.viewLayout.addWidget(self.name_edit)

        self.viewLayout.addWidget(BodyLabel(self.tr("描述"), self))
        self.description_edit = QTextEdit(self)
        self.description_edit.setPlainText(defaults.description)
        self.description_edit.setFixedHeight(160)
        self.viewLayout.addWidget(self.description_edit)

        self.viewLayout.addWidget(BodyLabel(self.tr("作者"), self))
        self.author_edit = LineEdit(self)
        self.author_edit.setText(defaults.author)
        self.viewLayout.addWidget(self.author_edit)

        self.viewLayout.addWidget(BodyLabel(self.tr("版本"), self))
        self.version_edit = LineEdit(self)
        self.version_edit.setPlaceholderText("1.0.0")
        self.version_edit.setToolTip("major.minor.patch (1.0.0)")
        self.version_edit.setText(defaults.version)
        self.viewLayout.addWidget(self.version_edit)

        self.yesButton.setText(self.tr("导出"))
        self.cancelButton.setText(self.tr("取消"))

        self.name_edit.textChanged.connect(self._validate)
        self.author_edit.textChanged.connect(self._validate)
        self.version_edit.textChanged.connect(self._validate)
        self.widget.setMinimumWidth(540)
        self._validate()

    def _fill_from_workshop(self) -> None:
        if self.repository is None:
            return
        dialog = WorkshopDialog(self.repository, self.window(), metadata_only=True)
        if not dialog.exec():
            return
        package = dialog.selected_package()
        if package is None:
            return
        self.name_edit.setText(package.name)
        self.description_edit.setPlainText(package.description)
        self.author_edit.setText(package.author)
        self.version_edit.setText(package.version)

    def _validate(self) -> None:
        try:
            parse_version(self.version_edit.text().strip())
            valid_version = True
        except WorkshopFormatError:
            valid_version = False
        self.version_edit.setError(not valid_version)
        self.yesButton.setEnabled(
            bool(
                self.name_edit.text().strip() and self.author_edit.text().strip() and valid_version
            )
        )

    def package(self) -> TeamPackage:
        return TeamPackage(
            self.name_edit.text().strip(),
            self.description_edit.toPlainText().strip(),
            self.author_edit.text().strip(),
            self.version_edit.text().strip(),
            (),
        )


class PackageImportDialog(MessageBoxBase):
    def __init__(
        self,
        package: TeamPackage,
        archive_name: str,
        parent=None,
        directory_exists: Callable[[str], bool] | None = None,
    ):
        super().__init__(parent)
        self._directory_exists = directory_exists
        self.viewLayout.setSpacing(6)
        self.viewLayout.addWidget(SubtitleLabel(package.name, self))
        summary = f"{self.tr('作者')}: {package.author}\n{self.tr('版本')}: {package.version}"
        self.viewLayout.addWidget(BodyLabel(summary, self))
        members = ", ".join(package.members("zh_CN" if is_chinese() else "en_US"))
        self.viewLayout.addWidget(CaptionLabel(f"{self.tr('成员')}: {members}", self))

        self.viewLayout.addWidget(StrongBodyLabel(self.tr("本地方案名称"), self))
        self.preset_name_edit = LineEdit(self)
        self.preset_name_edit.setText(package.name)
        self.viewLayout.addWidget(self.preset_name_edit)

        self.viewLayout.addWidget(StrongBodyLabel(self.tr("外置代码目录"), self))
        self.directory_edit = LineEdit(self)
        try:
            self.directory_edit.setText(
                CustomCharManager.validate_external_directory(Path(archive_name).stem.strip())
            )
        except ValueError:
            self.directory_edit.setText("community_team")
        self.viewLayout.addWidget(self.directory_edit)

        self.error_label = CaptionLabel("", self)
        self.viewLayout.addWidget(self.error_label)

        self.preset_name_edit.textChanged.connect(self._validate)
        self.directory_edit.textChanged.connect(self._validate)
        self.widget.setMinimumWidth(480)
        self.yesButton.setText(self.tr("导入"))
        self.cancelButton.setText(self.tr("取消"))
        self._validate()

    def _validate(self) -> None:
        try:
            directory = CustomCharManager.validate_external_directory(self.directory_edit.text())
            if self._directory_exists is not None and self._directory_exists(directory):
                valid_directory = False
                error = self.tr("外置代码目录“{}”已存在, 请修改目录名称后重试。").format(directory)
            else:
                valid_directory = True
                error = ""
        except ValueError:
            valid_directory = False
            error = self.tr("外置代码目录名称无效")
        valid = bool(self.preset_name_edit.text().strip()) and valid_directory
        self.yesButton.setEnabled(valid)
        self.error_label.setTextColor(QColor("#FF0000"), QColor("#FF0000"))
        self.error_label.setText(error)
        self.error_label.setVisible(bool(error))

    def installation(self) -> tuple[str, str]:
        return (
            self.preset_name_edit.text().strip(),
            CustomCharManager.validate_external_directory(self.directory_edit.text()),
        )


class WorkshopDialog(MessageBoxBase):
    """Browse a remote static catalog without mixing remote I/O into TeamManagerTab."""

    import_requested = Signal(object, object)
    packages_updated = Signal()

    def __init__(
        self,
        repository: WorkshopRepository,
        parent=None,
        *,
        metadata_only: bool = False,
        service: WorkshopPackageService | None = None,
        before_update: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.repository = repository
        self.metadata_only = metadata_only
        self.service = service
        self.before_update = before_update or (lambda: None)
        self.installations: list[WorkshopInstallation] = []
        self._updating = False
        self._catalog_loading = False
        self._cancel_update = threading.Event()
        self.is_chinese = is_chinese()
        self.entries: list[CatalogEntry] = []
        self.versions: dict[tuple[str, str], tuple[CatalogEntry, ...]] = {}
        self.current_source: IndexSource | None = None
        self._worker: BackgroundCall | None = None
        self.viewLayout.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.title_label = SubtitleLabel(self)
        title_row.addWidget(self.title_label)

        self.loading_ring = IndeterminateProgressRing(self)
        self.loading_ring.setFixedSize(16, 16)
        self.status_label = CaptionLabel("", self)
        title_row.addWidget(self.loading_ring)
        title_row.addWidget(self.status_label)
        self.safety_label = CaptionLabel(self)
        title_row.addStretch(1)
        self.viewLayout.addLayout(title_row)
        self.viewLayout.addWidget(self.safety_label)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setPlaceholderText(self.tr("搜索名称, 说明, 作者或角色"))
        self.role_combo = SearchableComboBox(self)
        self.role_combo.setPlaceholderText(self.tr("全部角色"))
        self.role_combo.setMinimumWidth(150)
        self.author_combo = SearchableComboBox(self)
        self.author_combo.setPlaceholderText(self.tr("全部作者"))
        self.author_combo.setMinimumWidth(150)
        self.refresh_button = PushButton(FluentIcon.SYNC, self.tr("刷新"), self)
        for widget in (self.search_edit, self.role_combo, self.author_combo, self.refresh_button):
            toolbar.addWidget(widget)
        self.update_all_button = PushButton(FluentIcon.UPDATE, self.tr("一鍵更新"), self)
        self.update_all_button.setVisible(service is not None and not metadata_only)
        self.update_all_button.setEnabled(False)
        toolbar.addWidget(self.update_all_button)
        self.viewLayout.addLayout(toolbar)

        # Left-right split layout
        split_layout = QHBoxLayout()
        split_layout.setSpacing(12)

        # Left: compact table with user-resizable columns
        self.table = TableWidget(self)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("名称"),
                self.tr("角色阵容"),
                self.tr("作者"),
                self.tr("版本"),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setColumnWidth(0, 166)
        self.table.setColumnWidth(1, 270)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 80)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.horizontalHeader().setStretchLastSection(True)
        split_layout.addWidget(self.table, 3)

        # Right: detail panel (fixed width avoids relayout jitter on selection)
        self.detail_card = SimpleCardWidget(self)
        self.detail_card.setFixedWidth(420)
        detail_layout = QVBoxLayout(self.detail_card)
        detail_layout.setContentsMargins(18, 14, 18, 14)
        detail_layout.setSpacing(8)

        self.detail_title = SubtitleLabel(self.tr("选择一个方案"), self.detail_card)
        self.detail_meta = CaptionLabel("", self.detail_card)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)

        detail_layout.addWidget(StrongBodyLabel(self.tr("队伍配置"), self.detail_card))
        slots_grid = QGridLayout()
        slots_grid.setSpacing(8)
        self.slot_cards: list[WorkshopSlotPreviewCard] = []
        for index in range(4):
            card = WorkshopSlotPreviewCard(index, self.detail_card)
            self.slot_cards.append(card)
            slots_grid.addWidget(card, index // 2, index % 2)
        detail_layout.addLayout(slots_grid)

        detail_layout.addWidget(StrongBodyLabel(self.tr("方案说明"), self.detail_card))
        self.detail_body = TextBrowser(self.detail_card)
        self.detail_body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.detail_body.setPlainText(self.tr("工坊会显示来自公开仓库的 ZIP 方案."))
        detail_layout.addWidget(self.detail_body, 1)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)
        self.version_combo = ComboBox(self.detail_card)
        self.version_combo.setEnabled(False)
        self.version_combo.setMinimumWidth(95)
        self.version_combo.setToolTip(self.tr("版本"))
        action_layout.addWidget(self.version_combo, alignment=Qt.AlignmentFlag.AlignVCenter)
        action_layout.addStretch(1)

        self.import_button = PrimaryPushButton(
            FluentIcon.DOWNLOAD, self.tr("导入"), self.detail_card
        )
        self.import_button.setEnabled(False)
        self.update_button = PrimarySplitPushButton(
            FluentIcon.UPDATE, self.tr("更新"), self.detail_card
        )
        self.update_button.hide()
        self.update_button.setEnabled(False)
        self.import_menu = RoundMenu(parent=self.update_button)
        self.import_action = QAction(FluentIcon.DOWNLOAD.icon(), self.tr("导入"), self.import_menu)
        self.import_action.triggered.connect(self._request_import)
        self.import_menu.addAction(self.import_action)
        self.update_button.setFlyout(self.import_menu)
        self.update_button.dropButton.setToolTip(self.tr("导入"))
        self.update_button.dropButton.setAccessibleName(self.tr("导入"))
        action_layout.addWidget(self.update_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        action_layout.addWidget(self.import_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        detail_layout.addLayout(action_layout)
        split_layout.addWidget(self.detail_card)

        self.viewLayout.addLayout(split_layout, 1)

        self.widget.setGraphicsEffect(None)
        self.widget.setMinimumSize(1100, 600)
        self._configure_mode()

        self.search_edit.textChanged.connect(self._apply_filter)
        self.role_combo.currentTextChanged.connect(self._apply_filter)
        self.author_combo.currentTextChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._show_selection)
        self.version_combo.currentIndexChanged.connect(self._show_version)
        self.refresh_button.clicked.connect(lambda: self.reload_catalog(force_refresh=True))
        self.import_button.clicked.connect(self._request_import)
        self.update_button.clicked.connect(self._request_update)
        self.update_all_button.clicked.connect(lambda: self._start_updates(self.entries))
        self.reload_catalog(force_refresh=service is not None)

    def _configure_mode(self) -> None:
        if self.metadata_only:
            self.title_label.setText(self.tr("从工坊填入"))
            self.safety_label.setText(self.tr("选择方案与版本, 填入名称、描述、作者和版本."))
            self.import_button.hide()
            self.yesButton.setText(self.tr("填入信息"))
            self.yesButton.setEnabled(False)
            self.cancelButton.setText(self.tr("Cancel"))
        else:
            self.title_label.setText(self.tr("工坊"))
            self.safety_label.setText(
                self.tr("社区方案含外置代码; 导入后会以本软件权限运行, 请仅导入可信来源.")
            )
            self.safety_label.setTextColor(QColor("#FF0000"), QColor("#FF0000"))
            self.safety_label.setToolTip(EXTERNAL_CODE_SAFETY_NOTICE)
            self.yesButton.hide()
            self.cancelButton.setText(self.tr("关闭"))

    def reload_catalog(self, force_refresh: bool = False) -> None:
        if self._updating or self._catalog_loading:
            return
        self._catalog_loading = True
        self.refresh_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.import_action.setEnabled(False)
        self.update_button.setEnabled(False)
        self.update_all_button.setEnabled(False)
        self.loading_ring.show()
        self.status_label.setText(self.tr("正在加载工坊..."))

        def load():
            installs = self.service.get_installations() if self.service else []
            catalog = self.repository.fetch_catalog(self.is_chinese, force_refresh)
            return (*catalog, installs)

        self._worker = BackgroundCall(load, self)
        self._worker.succeeded.connect(self._catalog_loaded)
        self._worker.failed.connect(self._catalog_failed)
        self._worker.start()

    def _catalog_loaded(
        self,
        result: tuple[list[CatalogEntry], IndexSource]
        | tuple[list[CatalogEntry], IndexSource, list[WorkshopInstallation]],
    ) -> None:
        self._catalog_loading = False
        entries, self.current_source = result[:2]
        if len(result) == 3:
            self.installations = result[2]
        self.versions = group_catalog_entries(entries)
        self.entries = [versions[0] for versions in self.versions.values()]
        self._rebuild_filters()
        self._apply_filter()
        self.refresh_button.setEnabled(True)
        self.loading_ring.hide()
        self.status_label.setText(self.tr("已加载 {} 个方案").format(len(self.entries)))

    def _catalog_failed(self, error: object) -> None:
        self._catalog_loading = False
        self.current_source = None
        self.entries = []
        self.versions = {}
        self._apply_filter()
        self.refresh_button.setEnabled(True)
        self.loading_ring.hide()
        self.status_label.setText(self.tr("加载失败"))
        show_bar(self, self.tr("加载失败"), workshop_error_text(error, self.tr), success=False)

    def _rebuild_filters(self) -> None:
        current_role = self.role_combo.currentText()
        current_author = self.author_combo.currentText()
        self.role_combo.blockSignals(True)
        self.author_combo.blockSignals(True)
        self.role_combo.clear()
        self.author_combo.clear()
        self.role_combo.addItem("")
        self.author_combo.addItem("")
        roles = sorted(
            {
                slot.display_name("zh_CN" if self.is_chinese else "en_US")
                for entry in self.entries
                for slot in entry.package.slots
            }
        )
        authors = sorted({entry.package.author for entry in self.entries})
        for role in roles:
            self.role_combo.addItem(role)
        for author in authors:
            self.author_combo.addItem(author)
        self.role_combo.setCurrentText(current_role)
        self.author_combo.setCurrentText(current_author)
        self.role_combo.blockSignals(False)
        self.author_combo.blockSignals(False)

    def _apply_filter(self, *_args) -> None:
        visible = filter_catalog_entries(
            self.entries,
            self.search_edit.text(),
            self.role_combo.currentText(),
            self.author_combo.currentText(),
        )
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, entry in enumerate(visible):
            package = entry.package
            self.table.insertRow(row)
            members = ", ".join(
                slot.display["zh_CN"] if self.is_chinese else slot.display["en_US"]
                for slot in package.slots
            )
            values = [
                package.name,
                members,
                package.author,
                package.version,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        if self.table.rowCount():
            self.table.selectRow(0)
        self.table.blockSignals(False)
        self._show_selection()

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.2f} KB"
        return f"{size / (1024 * 1024):.2f} MB"

    def _show_selection(self) -> None:
        item = self.table.item(self.table.currentRow(), 0)
        entry = cast(CatalogEntry, item.data(Qt.ItemDataRole.UserRole)) if item else None
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        if entry is not None:
            for version in self.versions[entry.package.identity]:
                self.version_combo.addItem(version.package.version, userData=version)
        self.version_combo.setEnabled(self.version_combo.count() > 1)
        self.version_combo.blockSignals(False)
        self._show_version()

    def _show_version(self, *_args) -> None:
        entry = cast(CatalogEntry | None, self.version_combo.currentData())
        self.import_button.setEnabled(
            entry is not None
            and self.current_source is not None
            and not self._updating
            and not self._catalog_loading
        )
        self._refresh_update_actions()
        if self.metadata_only:
            self.yesButton.setEnabled(entry is not None)
        if entry is None:
            self.detail_title.setText(self.tr("选择一个方案"))
            self.detail_meta.setText("")
            self.detail_body.setPlainText(self.tr("调整筛选条件或稍后刷新工坊."))
            for card in self.slot_cards:
                card.clear()
            return
        package = entry.package
        self.detail_title.setText(package.name)
        meta_line1 = (
            f"{self.tr('作者')}: {package.author}  ·  "
            f"{self.tr('版本')}: {package.version}  ·  "
            f"{self.tr('大小')}: {self._format_size(entry.size)}"
        )
        meta_line2 = (
            f"{self.tr('更新时间')}: {entry.updated_at.replace('T', ' ').removesuffix('Z')}"
        )
        self.detail_meta.setText(f"{meta_line1}\n{meta_line2}")
        self.detail_body.setPlainText(package.description or self.tr("没有说明."))
        slot_map = {slot.index: slot for slot in package.slots}
        for index in range(4):
            slot = slot_map.get(index)
            if slot:
                char_name = slot.display_name("zh_CN" if self.is_chinese else "en_US")
                kind_text = self.tr("内置") if slot.kind == "builtin" else self.tr("外置")
                combo_info = slot.impl_id if slot.kind == "builtin" else slot.file_name
                combo_desc = f"[{kind_text}] {combo_info}" if combo_info else f"[{kind_text}]"
                self.slot_cards[index].set_slot(char_name, combo_desc)
            else:
                self.slot_cards[index].clear()

    def selected_package(self) -> TeamPackage | None:
        entry = cast(CatalogEntry | None, self.version_combo.currentData())
        return entry.package if entry is not None else None

    def validate(self) -> bool:
        if self.metadata_only:
            return self.selected_package() is not None
        return super().validate()

    def _request_import(self) -> None:
        if self.metadata_only or self._updating or self._catalog_loading:
            return
        entry = self.version_combo.currentData()
        if entry is not None and self.current_source is not None:
            self.import_requested.emit(entry, self.current_source)

    def _refresh_update_actions(self) -> None:
        available = (
            self.service is not None
            and self.current_source is not None
            and not self.metadata_only
            and not self._updating
            and not self._catalog_loading
        )
        selected = self.version_combo.currentData()
        latest = self.versions[selected.package.identity][0] if selected else None
        installed = [
            item
            for item in self.installations
            if latest and item.identity == latest.package.identity
        ]
        pending = (
            selected == latest
            and latest is not None
            and any(item.can_update(latest.package) for item in installed)
        )
        show_update = bool(self.service is not None and not self.metadata_only and pending)
        self.update_button.setVisible(show_update)
        self.import_button.setVisible(not self.metadata_only and not show_update)
        self.update_button.setToolTip(
            self.tr("更新到 {}").format(latest.package.version) if pending else ""
        )
        self.update_button.setEnabled(bool(available and pending))
        self.import_action.setEnabled(self.import_button.isEnabled())
        self.update_all_button.setEnabled(
            bool(
                available
                and any(
                    item.can_update(entry.package)
                    for item in self.installations
                    for entry in self.entries
                )
            )
        )

    def _request_update(self) -> None:
        selected = self.version_combo.currentData()
        if selected and self.update_button.isEnabled():
            self._start_updates([selected])

    def _start_updates(self, entries: list[CatalogEntry]) -> None:
        if self.service is None or self.current_source is None or self._updating:
            return
        try:
            self.before_update()
        except Exception as error:
            show_bar(self, self.tr("更新失败"), workshop_error_text(error, self.tr), success=False)
            return
        self._updating = True
        self._cancel_update.clear()
        self.refresh_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self._refresh_update_actions()
        self.loading_ring.show()
        self.status_label.setText(self.tr("正在更新出招表..."))
        source = self.current_source

        def update():
            result = self.service.update_from_catalog(
                entries, self.repository, source, self.before_update, self._cancel_update.is_set
            )
            return result, self.service.get_installations()

        self._worker = BackgroundCall(update, self)
        self._worker.succeeded.connect(self._updates_finished)
        self._worker.failed.connect(self._updates_failed)
        self._worker.start()

    def _finish_update(self) -> None:
        self._updating = False
        self.loading_ring.hide()
        self.refresh_button.setEnabled(True)
        self.cancelButton.setEnabled(True)
        self._show_version()

    def _updates_finished(
        self,
        result: tuple[
            tuple[list[WorkshopInstallation], list[tuple[str, Exception]]],
            list[WorkshopInstallation],
        ],
    ) -> None:
        (updated, failures), self.installations = result
        self._finish_update()
        summary = self.tr("已更新 {} 份方案, {} 项失败").format(len(updated), len(failures))
        self.status_label.setText(summary)
        details = [f"{name}: {workshop_error_text(error, self.tr)}" for name, error in failures]
        if details:
            show_bar(self, self.tr("更新失败"), "\n".join(details), success=False)
        if updated:
            self.packages_updated.emit()
        if self._cancel_update.is_set():
            super().reject()

    def _updates_failed(self, error: object) -> None:
        self._finish_update()
        self.status_label.setText(self.tr("更新失败"))
        show_bar(self, self.tr("更新失败"), workshop_error_text(error, self.tr), success=False)
        if self._cancel_update.is_set():
            super().reject()

    def reject(self) -> None:
        self._cancel_update.set()
        if self._updating:
            self.cancelButton.setEnabled(False)
        else:
            super().reject()
