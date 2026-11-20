"""Kit dialogs used by the community team workshop."""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ok.ui.qt.kit import Button, Card, ComboBox, Dialog, Label, ProgressRing, icon, theme
from ok.ui.qt.kit.theme import set_style_property
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QSignalBlocker,
    QSize,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
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
from src.ui.foundation.dialogs import FormDialog, exec_owned_dialog
from src.ui.foundation.i18n import is_chinese, translate
from src.ui.foundation.info_bar import show_bar
from src.ui.foundation.widgets.button_styles import LinkButton
from src.ui.foundation.widgets.cards import SectionCard
from src.ui.foundation.widgets.search import SearchableComboBox
from src.ui.foundation.widgets.table import ThemedTable
from src.ui.icons import resource_icon


class ElidedCaptionLabel(Label):
    """Single-line caption that keeps the original text available in a tooltip."""

    def __init__(self, text: str = "", parent=None):
        self._full_text = ""
        super().__init__("", "caption", parent)
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


class WorkshopKeywordEdit(QLineEdit):
    """Single-line editable keyword chips for the workshop filter panel."""

    keywords_changed = Signal()

    def __init__(self, translate: Callable[[str], str], parent=None):
        super().__init__(parent)
        self.translate = translate
        self.keywords: list[str] = []
        self.setMinimumWidth(240)
        self.setPlaceholderText(translate("输入关键词后按 Enter 添加"))
        self.setAccessibleName(translate("阵容关键词"))
        self.chip_scroll = QScrollArea(self)
        self.chip_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.chip_scroll.viewport().setAutoFillBackground(False)
        self.chip_scroll.viewport().installEventFilter(self)
        self.chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chip_scroll.setFixedHeight(26)
        self.chip_scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.chip_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chip_view = QWidget()
        self.chip_layout = QHBoxLayout(self.chip_view)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setSpacing(4)
        self.chip_scroll.setWidget(self.chip_view)
        self.chip_view.setAutoFillBackground(False)
        self._refresh_chip_style()
        theme.changed.connect(self._refresh_chip_style)
        self.chip_scroll.hide()

    def _refresh_chip_style(self) -> None:
        hover = theme.color("control_hover").name()
        self.chip_view.setStyleSheet(
            "QToolButton#removeWorkshopKeyword { background: transparent; border: 0; padding: 0; }"
            "QToolButton#removeWorkshopKeyword"
            f":hover {{ background: {hover}; border-radius: 4px; }}"
        )

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            keyword = self.text().strip()
            if keyword and keyword not in self.keywords:
                self.keywords.append(keyword)
                self._rebuild_chips()
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)

    def clear_keywords(self) -> None:
        self.clear()
        self.keywords.clear()
        self._rebuild_chips()

    def _remove_keyword(self, keyword: str) -> None:
        self.keywords.remove(keyword)
        self._rebuild_chips()
        self.setFocus()

    def _rebuild_chips(self) -> None:
        while self.chip_layout.count():
            widget = self.chip_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        for keyword in self.keywords:
            chip = Card(self.chip_view, look="inset")
            chip.setFixedHeight(24)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            layout = QHBoxLayout(chip)
            layout.setContentsMargins(8, 0, 2, 0)
            layout.setSpacing(2)
            label = ElidedCaptionLabel(keyword, chip)
            label.setFixedWidth(min(130, label.fontMetrics().horizontalAdvance(keyword) + 2))
            close = QToolButton(chip)
            close.setObjectName("removeWorkshopKeyword")
            close.setAutoRaise(True)
            close.setIcon(icon("x"))
            close.setCursor(Qt.CursorShape.PointingHandCursor)
            close.setFixedSize(24, 20)
            close.setIconSize(QSize(16, 16))
            close.setToolTip(self.translate("移除关键词 {}").format(keyword))
            close.setAccessibleName(close.toolTip())
            close.clicked.connect(lambda _checked=False, word=keyword: self._remove_keyword(word))
            layout.addWidget(label)
            layout.addWidget(close)
            self.chip_layout.addWidget(chip)
            chip.show()
        self.chip_view.setFixedSize(self.chip_layout.sizeHint())
        self._update_chip_width()
        self.chip_scroll.horizontalScrollBar().setValue(
            self.chip_scroll.horizontalScrollBar().maximum()
        )
        self.keywords_changed.emit()

    def _update_chip_width(self) -> None:
        width = min(self.chip_layout.sizeHint().width(), max(0, self.width() - 180))
        self.chip_scroll.setFixedWidth(width)
        self.chip_scroll.move(6, max(0, (self.height() - self.chip_scroll.height()) // 2))
        self.chip_scroll.setVisible(bool(self.keywords))
        self.setTextMargins(width + 6 if self.keywords else 0, 0, 0, 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_chip_width()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.chip_scroll.viewport() and event.type() == QEvent.Type.Wheel:
            bar = self.chip_scroll.horizontalScrollBar()
            if bar.maximum():
                delta = event.angleDelta()
                bar.setValue(bar.value() - (delta.x() or delta.y()))
                event.accept()
                return True
        return super().eventFilter(watched, event)


class WorkshopSlotPreviewCard(Card):
    """Compact card to display a slot's character and combo binding in workshop details."""

    def __init__(self, index: int, parent=None):
        super().__init__(parent, look="inset")
        self.index = index
        self.setFixedHeight(50)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        self.title_label = Label("-", "strong", self)
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


class _FinishBeforeExit(QThread):
    """Keep a durable operation alive through normal application shutdown."""

    def __init__(self, app: QCoreApplication, action: Callable[[], None]):
        super().__init__(app)
        self._action = action
        self.finished.connect(self.deleteLater)
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.wait)

    def run(self) -> None:
        self._action()

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.Close
            and isinstance(watched, QWidget)
            and watched.windowType() == Qt.WindowType.Window
        ):
            # Window close handlers can start shutdown before aboutToQuit.
            self.wait()
        return False


class BackgroundCall(QObject):
    """Run a blocking callable and return its result through Qt signals."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(
        self, action: Callable[[], object], parent=None, *, finish_before_exit: bool = False
    ):
        super().__init__(parent)
        self._action = action
        self._finish_before_exit = finish_before_exit
        self._retired = threading.Event()
        self.destroyed.connect(self._retired.set)

    def start(self) -> None:
        if self._finish_before_exit:
            _FinishBeforeExit(QCoreApplication.instance(), self._run).start()
        else:
            threading.Thread(target=self._run, daemon=True).start()

    def retire(self) -> None:
        """Detach a pending result when its page is being rebuilt or closed."""
        self._retired.set()

    def _run(self) -> None:
        try:
            result = self._action()
        except Exception as error:
            if not self._retired.is_set():
                try:
                    self.failed.emit(error)
                except RuntimeError:
                    pass  # The owner may be destroyed between the check and emit.
        else:
            if not self._retired.is_set():
                try:
                    self.succeeded.emit(result)
                except RuntimeError:
                    pass


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


class PackageMetadataDialog(Dialog):
    def __init__(
        self,
        defaults: TeamPackage,
        parent=None,
        *,
        repository: WorkshopRepository | None = None,
    ):
        super().__init__(parent, "", width=540)
        self.title_label.setText(translate("导出队伍"))
        self.repository = repository
        self.body.setSpacing(6)
        panel_layout = self.panel.layout()
        title_index = panel_layout.indexOf(self.title_label)
        panel_layout.removeWidget(self.title_label)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.fill_button = LinkButton(translate("从工坊填入"), parent=self.panel, size="small")
        self.fill_button.setVisible(repository is not None)
        self.fill_button.clicked.connect(self._fill_from_workshop)
        title_row.addWidget(self.fill_button)
        panel_layout.insertLayout(title_index, title_row)

        self.body.addWidget(Label(translate("名称"), parent=self.panel))
        self.name_edit = QLineEdit(self.panel)
        self.name_edit.setText(defaults.name)
        self.body.addWidget(self.name_edit)

        self.body.addWidget(Label(translate("描述"), parent=self.panel))
        self.description_edit = QTextEdit(self)
        self.description_edit.setPlainText(defaults.description)
        self.description_edit.setFixedHeight(160)
        self.body.addWidget(self.description_edit)

        self.body.addWidget(Label(translate("作者"), parent=self.panel))
        self.author_edit = QLineEdit(self.panel)
        self.author_edit.setText(defaults.author)
        self.body.addWidget(self.author_edit)

        self.body.addWidget(Label(translate("版本"), parent=self.panel))
        self.version_edit = QLineEdit(self.panel)
        self.version_edit.setObjectName("workshopVersion")
        self.version_edit.setPlaceholderText("1.0.0")
        self.version_edit.setToolTip("major.minor.patch (1.0.0)")
        self.version_edit.setText(defaults.version)
        self.body.addWidget(self.version_edit)
        self._refresh_version_style()
        theme.changed.connect(self._refresh_version_style)

        self.ok_button.setText(translate("导出"))
        self.cancel_button.setText(translate("取消"))

        self.name_edit.textChanged.connect(self._validate)
        self.author_edit.textChanged.connect(self._validate)
        self.version_edit.textChanged.connect(self._validate)
        self._validate()

    def _fill_from_workshop(self) -> None:
        if self.repository is None:
            return
        dialog = WorkshopDialog(self.repository, self.window(), metadata_only=True)
        with exec_owned_dialog(dialog, owner=self) as accepted:
            if not accepted:
                return
            package = dialog.selected_package()
            if package is None:
                return
            self.name_edit.setText(package.name)
            self.description_edit.setPlainText(package.description)
            self.author_edit.setText(package.author)
            self.version_edit.setText(package.version)

    def _refresh_version_style(self) -> None:
        color = theme.color("danger").name()
        self.version_edit.setStyleSheet(
            f'QLineEdit#workshopVersion[invalid="true"]:enabled {{ border-color: {color}; }}'
        )

    def _validate(self) -> None:
        try:
            parse_version(self.version_edit.text().strip())
            valid_version = True
        except WorkshopFormatError:
            valid_version = False
        set_style_property(self.version_edit, "invalid", not valid_version)
        self.ok_button.setEnabled(
            bool(
                self.name_edit.text().strip() and self.author_edit.text().strip() and valid_version
            )
        )

    def validate(self) -> bool:
        return self.ok_button.isEnabled()

    def package(self) -> TeamPackage:
        return TeamPackage(
            self.name_edit.text().strip(),
            self.description_edit.toPlainText().strip(),
            self.author_edit.text().strip(),
            self.version_edit.text().strip(),
            (),
        )


class PackageImportDialog(FormDialog):
    def __init__(
        self,
        package: TeamPackage,
        archive_name: str,
        parent=None,
        directory_exists: Callable[[str], bool] | None = None,
    ):
        super().__init__(parent, package.name, width=480)
        self._directory_exists = directory_exists
        self.body.setSpacing(6)
        summary = f"{translate('作者')}: {package.author}\n{translate('版本')}: {package.version}"
        self.body.addWidget(Label(summary, parent=self.panel))
        members = ", ".join(package.members("zh_CN" if is_chinese() else "en_US"))
        self.body.addWidget(Label(f"{translate('成员')}: {members}", "caption", self.panel))

        self.body.addWidget(Label(translate("本地方案名称"), "strong", self.panel))
        self.preset_name_edit = QLineEdit(self.panel)
        self.preset_name_edit.setText(package.name)
        self.body.addWidget(self.preset_name_edit)

        self.body.addWidget(Label(translate("外置代码目录"), "strong", self.panel))
        self.directory_edit = QLineEdit(self.panel)
        try:
            self.directory_edit.setText(
                CustomCharManager.validate_external_directory(Path(archive_name).stem.strip())
            )
        except ValueError:
            self.directory_edit.setText("community_team")
        self.body.addWidget(self.directory_edit)

        self.error_label = Label("", "caption", self.panel, word_wrap=True)
        self.error_label.setProperty("tone", "danger")
        self.body.addWidget(self.error_label)

        self.preset_name_edit.textChanged.connect(self._validate)
        self.directory_edit.textChanged.connect(self._validate)
        self.ok_button.setText(translate("导入"))
        self.cancel_button.setText(translate("取消"))
        self._validate()

    def _validate(self) -> None:
        try:
            directory = CustomCharManager.validate_external_directory(self.directory_edit.text())
            if self._directory_exists is not None and self._directory_exists(directory):
                valid_directory = False
                error = translate("外置代码目录“{}”已存在, 请修改目录名称后重试。").format(
                    directory
                )
            else:
                valid_directory = True
                error = ""
        except ValueError:
            valid_directory = False
            error = translate("外置代码目录名称无效")
        valid = bool(self.preset_name_edit.text().strip()) and valid_directory
        self.ok_button.setEnabled(valid)
        self.error_label.setText(error)
        self.error_label.setVisible(bool(error))

    def validate(self) -> bool:
        return self.ok_button.isEnabled()

    def installation(self) -> tuple[str, str]:
        return (
            self.preset_name_edit.text().strip(),
            CustomCharManager.validate_external_directory(self.directory_edit.text()),
        )


class WorkshopDialog(Dialog):
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
        super().__init__(parent, "", width=1120)
        self.repository = repository
        self.metadata_only = metadata_only
        self.service = service
        self.before_update = before_update or (lambda: None)
        self.installations: list[WorkshopInstallation] = []
        self._updating = False
        self._catalog_loading = False
        self._cancel_update = threading.Event()
        self.destroyed.connect(self._cancel_update.set)
        self.is_chinese = is_chinese()
        self.entries: list[CatalogEntry] = []
        self.versions: dict[tuple[str, str], tuple[CatalogEntry, ...]] = {}
        self.current_source: IndexSource | None = None
        self._worker: BackgroundCall | None = None
        self.body.setSpacing(12)

        panel_layout = self.panel.layout()
        title_index = panel_layout.indexOf(self.title_label)
        panel_layout.removeWidget(self.title_label)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.title_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.loading_ring = ProgressRing(size=16, parent=self.panel)
        self.loading_ring.hide()
        self.status_label = Label("", "caption", self.panel)
        title_row.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.loading_ring, alignment=Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        panel_layout.insertLayout(title_index, title_row)
        self.safety_label = Label("", "caption", self.panel, word_wrap=True)
        self.body.addWidget(self.safety_label)

        self.tools_card = SectionCard(parent=self.panel)
        # Keep the toolbar at the top while the dialog reclaims the collapsed space.
        self.tools_card.layout().addStretch(1)
        self.tools_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tools_card.body.setContentsMargins(14, 6, 14, 10)
        toolbar = self.tools_card.header
        toolbar.setSpacing(8)
        self.search_edit = QLineEdit(self.tools_card)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(translate("搜索名称, 说明, 作者或角色"))
        self.search_edit.setMinimumWidth(280)
        self.search_edit.setMaximumWidth(480)
        self.filter_button = Button(
            translate("筛选"), icon="chevron-down", variant="subtle", parent=self.tools_card
        )
        self.filter_button.setToolTip(translate("展开筛选"))
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.filter_button)
        self.refresh_button = Button(
            translate("刷新"), icon="refresh", variant="subtle", parent=self.tools_card
        )
        self.update_all_button = Button(
            translate("一鍵更新"),
            icon=resource_icon("circle-fading-arrow-up"),
            variant="subtle",
            parent=self.tools_card,
        )
        if service is None or metadata_only:
            self.update_all_button.hide()
        self.update_all_button.setEnabled(False)
        toolbar.addStretch(1)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.update_all_button)

        self.filter_panel = QWidget(self.tools_card)
        fields = QHBoxLayout(self.filter_panel)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(20)
        lineup_field = QHBoxLayout()
        lineup_field.setSpacing(8)
        lineup_field.addWidget(Label(translate("阵容关键词"), parent=self.filter_panel))
        self.keyword_edit = WorkshopKeywordEdit(translate, self.filter_panel)
        lineup_field.addWidget(self.keyword_edit, 1)
        fields.addLayout(lineup_field, 3)
        author_field = QHBoxLayout()
        author_field.setSpacing(8)
        author_field.addWidget(Label(translate("作者"), parent=self.filter_panel))
        self.author_combo = SearchableComboBox(self.filter_panel)
        self.author_combo.lineEdit().setPlaceholderText(translate("全部作者"))
        self.author_combo.setMinimumWidth(150)
        self.author_combo.setAccessibleName(translate("作者"))
        author_field.addWidget(self.author_combo, 1)
        fields.addLayout(author_field, 1)
        self.clear_filters_button = LinkButton(translate("清空筛选"), parent=self.filter_panel)
        fields.addWidget(self.clear_filters_button)
        self.tools_card.body.addWidget(self.filter_panel)
        self.tools_card.set_body_visible(False)
        self.body.addWidget(self.tools_card)
        self.filter_button.clicked.connect(
            lambda: self._toggle_filters(self.tools_card.body_widget.isHidden())
        )
        self.keyword_edit.keywords_changed.connect(self._update_filter_button)
        self.keyword_edit.textChanged.connect(self._update_filter_button)
        self.author_combo.currentTextChanged.connect(self._update_filter_button)
        self.clear_filters_button.clicked.connect(self._clear_filters)
        self._update_filter_button()

        # Left-right split layout
        split_layout = QHBoxLayout()
        split_layout.setSpacing(12)

        # Left: compact table with user-resizable columns
        self.table = ThemedTable(self.panel)
        self.table.setMinimumHeight(260)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            [
                translate("名称"),
                translate("角色阵容"),
                translate("作者"),
                translate("版本"),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(16, 16))
        self.table.setColumnWidth(0, 166)
        self.table.setColumnWidth(1, 274)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 76)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        split_layout.addWidget(self.table, 3)

        # Right: detail panel (fixed width avoids relayout jitter on selection)
        self.detail_card = Card(self.panel)
        self.detail_card.setFixedWidth(420)
        detail_layout = QVBoxLayout(self.detail_card)
        detail_layout.setContentsMargins(18, 14, 18, 14)
        detail_layout.setSpacing(8)

        self.detail_title = Label(translate("选择一个方案"), "subtitle", self.detail_card)
        self.detail_meta = Label("", "caption", self.detail_card)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)

        detail_layout.addWidget(Label(translate("队伍配置"), "strong", self.detail_card))
        slots_grid = QGridLayout()
        slots_grid.setSpacing(8)
        self.slot_cards: list[WorkshopSlotPreviewCard] = []
        for index in range(4):
            card = WorkshopSlotPreviewCard(index, self.detail_card)
            self.slot_cards.append(card)
            slots_grid.addWidget(card, index // 2, index % 2)
        detail_layout.addLayout(slots_grid)

        detail_layout.addWidget(Label(translate("方案说明"), "strong", self.detail_card))
        self.detail_body = QTextBrowser(self.detail_card)
        self.detail_body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.detail_body.setPlainText(translate("工坊会显示来自公开仓库的 ZIP 方案."))
        detail_layout.addWidget(self.detail_body, 1)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)
        self.version_combo = ComboBox(parent=self.detail_card)
        self.version_combo.setEnabled(False)
        self.version_combo.setMinimumWidth(95)
        self.version_combo.setToolTip(translate("版本"))
        action_layout.addWidget(self.version_combo, alignment=Qt.AlignmentFlag.AlignVCenter)
        action_layout.addStretch(1)

        self.import_button = Button(
            translate("导入"), icon="download", variant="primary", parent=self.detail_card
        )
        self.import_button.setEnabled(False)
        self.update_controls = QWidget(self.detail_card)
        self.update_controls.hide()
        update_layout = QHBoxLayout(self.update_controls)
        update_layout.setContentsMargins(0, 0, 0, 0)
        update_layout.setSpacing(0)
        self.update_button = Button(
            translate("更新"),
            icon=resource_icon("circle-fading-arrow-up", color="on_accent"),
            variant="primary",
            parent=self.update_controls,
        )
        self.update_button.setEnabled(False)
        self.update_button.setStyleSheet(
            "Button { border-top-right-radius: 0; border-bottom-right-radius: 0; }"
        )
        self.import_menu = QMenu(self.update_controls)
        self.import_action = QAction(icon("download"), translate("导入"), self.import_menu)
        self.import_action.triggered.connect(self._request_import)
        self.import_menu.addAction(self.import_action)
        self.update_menu_button = Button(
            icon="chevron-down", variant="primary", parent=self.update_controls
        )
        self.update_menu_button.setFixedWidth(28)
        self.update_menu_button.setStyleSheet("""
            Button {
                border-top-left-radius: 0;
                border-bottom-left-radius: 0;
                border-left-color: rgba(255, 255, 255, 65);
            }
            Button:disabled { border-left-color: rgba(127, 127, 127, 55); }
        """)
        self.update_menu_button.setToolTip(translate("导入"))
        self.update_menu_button.setAccessibleName(translate("导入"))
        self.update_menu_button.clicked.connect(self._open_import_menu)
        update_layout.addWidget(self.update_button)
        update_layout.addWidget(self.update_menu_button)
        action_layout.addWidget(self.update_controls, alignment=Qt.AlignmentFlag.AlignVCenter)
        action_layout.addWidget(self.import_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        detail_layout.addLayout(action_layout)
        split_layout.addWidget(self.detail_card)

        self.body.addLayout(split_layout, 1)

        self.panel.setMinimumHeight(620)
        self._configure_mode()

        self.search_edit.textChanged.connect(self._apply_filter)
        self.keyword_edit.keywords_changed.connect(self._apply_filter)
        self.author_combo.currentTextChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._show_selection)
        self.version_combo.currentIndexChanged.connect(self._show_version)
        self.refresh_button.clicked.connect(lambda: self.reload_catalog(force_refresh=True))
        self.import_button.clicked.connect(self._request_import)
        self.update_button.clicked.connect(self._request_update)
        self.update_all_button.clicked.connect(lambda: self._start_updates(self.entries))
        theme.changed.connect(self._refresh_update_indicators)
        self.reload_catalog(force_refresh=service is not None)

    def _configure_mode(self) -> None:
        if self.metadata_only:
            self.title_label.setText(translate("从工坊填入"))
            self.safety_label.setText(translate("选择方案与版本, 填入名称、描述、作者和版本."))
            self.import_button.hide()
            self.ok_button.setText(translate("填入信息"))
            self.ok_button.setEnabled(False)
            self.cancel_button.setText(translate("Cancel"))
        else:
            self.title_label.setText(translate("工坊"))
            self.safety_label.setText(
                translate("社区方案含外置代码; 导入后会以本软件权限运行, 请仅导入可信来源.")
            )
            self.safety_label.setToolTip(EXTERNAL_CODE_SAFETY_NOTICE)
            set_style_property(self.safety_label, "tone", "danger")
            self.ok_button.hide()
            self.cancel_button.setText(translate("关闭"))
        self._refresh_update_indicators()

    def reload_catalog(self, force_refresh: bool = False) -> None:
        if self._updating or self._catalog_loading:
            return
        self._catalog_loading = True
        self.ok_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.import_action.setEnabled(False)
        self.update_button.setEnabled(False)
        self.update_all_button.setEnabled(False)
        self.loading_ring.show()
        self.status_label.setText(translate("正在加载工坊..."))

        def load():
            installs = self.service.get_installations() if self.service else []
            catalog = self.repository.fetch_catalog(self.is_chinese, force_refresh)
            return (*catalog, installs)

        self._worker = BackgroundCall(load, self)
        self._worker.succeeded.connect(self._catalog_loaded)
        self._worker.failed.connect(self._catalog_failed)
        self._worker.start()

    def _catalog_loaded(
        self, result: tuple[list[CatalogEntry], IndexSource, list[WorkshopInstallation]]
    ) -> None:
        self._catalog_loading = False
        entries, self.current_source, self.installations = result
        self.versions = group_catalog_entries(entries)
        self.entries = [versions[0] for versions in self.versions.values()]
        self._rebuild_filters()
        self._apply_filter()
        self.refresh_button.setEnabled(True)
        self.loading_ring.hide()

    def _catalog_failed(self, error: object) -> None:
        self._catalog_loading = False
        self.current_source = None
        self.entries = []
        self.versions = {}
        self._apply_filter()
        self.refresh_button.setEnabled(True)
        self.loading_ring.hide()
        self.status_label.setText(translate("加载失败"))
        show_bar(self, translate("加载失败"), workshop_error_text(error, translate), success=False)

    def _rebuild_filters(self) -> None:
        current_author = self.author_combo.currentText()
        self.author_combo.blockSignals(True)
        self.author_combo.clear()
        self.author_combo.addItem("")
        authors = sorted({entry.package.author for entry in self.entries})
        for author in authors:
            self.author_combo.addItem(author)
        self.author_combo.setCurrentText(current_author)
        self.author_combo.blockSignals(False)
        self._update_filter_button()

    def _toggle_filters(self, expanded: bool) -> None:
        focused = self.focusWidget()
        if not expanded and focused is not None and self.filter_panel.isAncestorOf(focused):
            self.filter_button.setFocus()
        self.tools_card.set_body_visible(expanded)
        self.filter_button.set_icon("chevron-up" if expanded else "chevron-down")
        self.filter_button.setToolTip(translate("收起筛选") if expanded else translate("展开筛选"))

    def _update_filter_button(self, *_args) -> None:
        count = len(self.keyword_edit.keywords) + bool(self.author_combo.currentText().strip())
        text = translate("筛选")
        if count:
            text += f" ({count})"
        self.filter_button.setText(text)
        self.clear_filters_button.setEnabled(bool(count or self.keyword_edit.text()))

    def _clear_filters(self) -> None:
        with QSignalBlocker(self.keyword_edit), QSignalBlocker(self.author_combo):
            self.keyword_edit.clear_keywords()
            self.author_combo.setCurrentText("")
        self._update_filter_button()
        self._apply_filter()

    def _apply_filter(self, *_args) -> None:
        selected = self.version_combo.currentData()
        visible = filter_catalog_entries(
            self.entries,
            keyword=self.search_edit.text(),
            author=self.author_combo.currentText(),
            role_keywords=self.keyword_edit.keywords,
        )
        selected_row = 0
        preferred_version = None
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, entry in enumerate(visible):
            package = entry.package
            if selected is not None and package.identity == selected.package.identity:
                selected_row = row
                preferred_version = selected.package.version
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
        self._refresh_update_indicators()
        if self.table.rowCount():
            self.table.selectRow(selected_row)
        self.table.blockSignals(False)
        self._show_selection(preferred_version)
        if not self._catalog_loading and not self._updating:
            filtered = bool(
                self.search_edit.text().strip()
                or self.keyword_edit.keywords
                or self.author_combo.currentText().strip()
            )
            self.status_label.setText(
                translate("显示 {} / {} 个方案").format(len(visible), len(self.entries))
                if filtered
                else translate("已加载 {} 个方案").format(len(self.entries))
            )

    def _refresh_update_indicators(self, *_args) -> None:
        update_icon = resource_icon("arrow-up", color="accent")
        # Reserve the same decoration space for rows without an update marker.
        spacer = QPixmap(self.table.iconSize())
        spacer.fill(Qt.GlobalColor.transparent)
        empty_icon = QIcon(spacer)
        for row in range(self.table.rowCount()):
            entry = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            item = self.table.item(row, 3)
            versions = sorted(
                {
                    installed.version
                    for installed in self.installations
                    if self.service is not None
                    and not self.metadata_only
                    and installed.can_update(entry.package)
                },
                key=parse_version,
            )
            tooltip = (
                translate("已安装: {installed}\n最新版本: {latest}").format(
                    installed=", ".join(versions), latest=entry.package.version
                )
                if versions
                else entry.package.version
            )
            item.setIcon(update_icon if versions else empty_icon)
            item.setToolTip(tooltip)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, tooltip)

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.2f} KB"
        return f"{size / (1024 * 1024):.2f} MB"

    def _show_selection(self, preferred_version: str | None = None) -> None:
        item = self.table.item(self.table.currentRow(), 0)
        entry = cast(CatalogEntry, item.data(Qt.ItemDataRole.UserRole)) if item else None
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        if entry is not None:
            for version in self.versions[entry.package.identity]:
                self.version_combo.addItem(version.package.version, userData=version)
                if version.package.version == preferred_version:
                    self.version_combo.setCurrentIndex(self.version_combo.count() - 1)
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
            self.ok_button.setEnabled(
                entry is not None and not self._catalog_loading and not self._updating
            )
        if entry is None:
            self.detail_title.setText(
                translate("没有匹配的方案")
                if self.entries and not self.table.rowCount()
                else translate("选择一个方案")
            )
            self.detail_meta.setText("")
            self.detail_body.setPlainText(translate("调整筛选条件或稍后刷新工坊."))
            for card in self.slot_cards:
                card.clear()
            return
        package = entry.package
        self.detail_title.setText(package.name)
        meta_line1 = (
            f"{translate('作者')}: {package.author}  ·  "
            f"{translate('版本')}: {package.version}  ·  "
            f"{translate('大小')}: {self._format_size(entry.size)}"
        )
        meta_line2 = (
            f"{translate('更新时间')}: {entry.updated_at.replace('T', ' ').removesuffix('Z')}"
        )
        self.detail_meta.setText(f"{meta_line1}\n{meta_line2}")
        self.detail_body.setPlainText(package.description or translate("没有说明."))
        slot_map = {slot.index: slot for slot in package.slots}
        for index in range(4):
            slot = slot_map.get(index)
            if slot:
                char_name = slot.display_name("zh_CN" if self.is_chinese else "en_US")
                kind_text = translate("内置") if slot.kind == "builtin" else translate("外置")
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
            return self.ok_button.isEnabled() and self.selected_package() is not None
        return False

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
        pending_version = (
            latest.package.version
            if selected == latest
            and latest is not None
            and any(item.can_update(latest.package) for item in installed)
            else None
        )
        pending = pending_version is not None
        show_update = bool(self.service is not None and not self.metadata_only and pending)
        self.update_controls.setVisible(show_update)
        self.update_menu_button.setEnabled(self.import_button.isEnabled())
        self.import_button.setVisible(not self.metadata_only and not show_update)
        self.update_button.setToolTip(
            translate("更新到 {}").format(pending_version) if pending else ""
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

    def _open_import_menu(self) -> None:
        controls = self.update_controls
        self.import_menu.setFixedWidth(controls.width())
        self.import_menu.popup(controls.mapToGlobal(QPoint(0, controls.height() + 4)))

    def _request_update(self) -> None:
        selected = self.version_combo.currentData()
        if selected and self.update_button.isEnabled():
            self._start_updates([selected])

    def _start_updates(self, entries: list[CatalogEntry]) -> None:
        service = self.service
        source = self.current_source
        if service is None or source is None or self._updating:
            return
        try:
            self.before_update()
        except Exception as error:
            show_bar(
                self, translate("更新失败"), workshop_error_text(error, translate), success=False
            )
            return
        self._updating = True
        self._cancel_update.clear()
        self.refresh_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self._refresh_update_actions()
        self.loading_ring.show()
        self.status_label.setText(translate("正在更新出招表..."))

        def update():
            result = service.update_from_catalog(
                entries, self.repository, source, self.before_update, self._cancel_update.is_set
            )
            return result, service.get_installations()

        self._worker = BackgroundCall(update, self)
        self._worker.succeeded.connect(self._updates_finished)
        self._worker.failed.connect(self._updates_failed)
        self._worker.start()

    def _finish_update(self) -> None:
        self._updating = False
        self.loading_ring.hide()
        self.refresh_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self._show_version()

    def _updates_finished(
        self,
        result: tuple[
            tuple[list[WorkshopInstallation], list[tuple[str, Exception]]],
            list[WorkshopInstallation],
        ],
    ) -> None:
        (updated, failures), self.installations = result
        self._refresh_update_indicators()
        self._finish_update()
        summary = translate("已更新 {} 份方案, {} 项失败").format(len(updated), len(failures))
        self.status_label.setText(summary)
        details = [f"{name}: {workshop_error_text(error, translate)}" for name, error in failures]
        if details:
            show_bar(self, translate("更新失败"), "\n".join(details), success=False)
        if updated:
            self.packages_updated.emit()
        if self._cancel_update.is_set():
            super().reject()

    def _updates_failed(self, error: object) -> None:
        self._finish_update()
        self.status_label.setText(translate("更新失败"))
        show_bar(self, translate("更新失败"), workshop_error_text(error, translate), success=False)
        if self._cancel_update.is_set():
            super().reject()

    def reject(self) -> None:
        self._cancel_update.set()
        if self._updating:
            self.cancel_button.setEnabled(False)
        else:
            super().reject()
