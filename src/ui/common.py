from typing import Any, Union

from ok import og
from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QStringListModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    EditableComboBox,
    FluentIcon,
    FluentIconBase,
    IconWidget,
    ListWidget,
    SearchLineEdit,
)


def get_tr(text):
    if og.app is None:
        return text
    return og.app.tr(text)


COMBO = get_tr("出招表")
TEAM_MANAGEMENT = get_tr("队伍管理")


def cv_to_pixmap(cv_img):
    if cv_img is None or getattr(cv_img, "size", 0) == 0:
        return QPixmap()
    if not cv_img.flags["C_CONTIGUOUS"]:
        cv_img = cv_img.copy()
    height, width = cv_img.shape[:2]
    channels = cv_img.shape[2] if len(cv_img.shape) > 2 else 1
    bytes_per_line = channels * width

    if channels == 3:
        qimg = QImage(
            cv_img.data, width, height, bytes_per_line, QImage.Format.Format_RGB888
        ).rgbSwapped()
    elif channels == 4:
        qimg = QImage(
            cv_img.data, width, height, bytes_per_line, QImage.Format.Format_RGBA8888
        ).rgbSwapped()
    else:
        qimg = QImage(cv_img.data, width, height, bytes_per_line, QImage.Format.Format_Grayscale8)

    return QPixmap.fromImage(qimg)


class CharManagerSignals(QObject):
    refresh_tab = Signal()


char_manager_signals = CharManagerSignals()


class SearchableComboBox(EditableComboBox):
    """
    基于 PySide6 的可搜寻下拉框
    继承自 EditableComboBox，实现输入关键字自动过滤清单范围
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_items = []
        self._setup_search_engine()

    def _setup_search_engine(self):
        completer = QCompleter(self.search_items)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.setCompleter(completer)

    def addItem(
        self, text: str, icon: Union[QIcon, str, FluentIconBase, None] = None, userData: Any = None
    ):
        """重写以同步更新搜寻清单"""
        super().addItem(text, icon, userData)
        self.search_items.append(text)
        self._sync_completer_model()

    def _sync_completer_model(self):
        """同步内部资料模型至补全器"""
        completer = self.completer()
        model = QStringListModel(self.search_items, completer)
        completer.setModel(model)

    def clear(self):
        """清空时同步重置搜寻引擎"""
        super().clear()
        self.search_items.clear()
        self._sync_completer_model()


class SearchableListWidget(QWidget):
    """
    可搜索的列表控件，包含搜索框和列表
    利用 __getattr__ 自动转发方法，免去写大量包装方法的烦恼。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # 创建布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 搜索框
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        # 列表
        self.list_widget = ListWidget(self)
        layout.addWidget(self.list_widget)

        self.setLayout(layout)

    def setPlaceholderText(self, text: str):
        """特例：搜索框的方法需要保留在这里"""
        self.search_edit.setPlaceholderText(text)

    def setFixedWidth(self, width: int):
        """特例：需要同时作用于两个子控件的方法"""
        super().setFixedWidth(width)  # 设置自身的宽度
        self.list_widget.setFixedWidth(width)
        self.search_edit.setFixedWidth(width)

    def _apply_filter(self, keyword: str):
        """
        更优的过滤方式：隐藏/显示 Item，而不是清空重建。
        这样即使 Item 包含 Icon 或复杂的 UserData 也不会丢失。
        """
        normalized = keyword.strip().lower()

        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            # 如果关键字在文本中，hidden 为 False（显示）；否则为 True（隐藏）
            should_hide = normalized not in item.text().lower()
            item.setHidden(should_hide)

    def __getattr__(self, name):
        if hasattr(self.list_widget, name):
            return getattr(self.list_widget, name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


class SmoothSearchBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(35)
        self.setLayout(QHBoxLayout(self))
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        # 1. 使用 IconWidget 代替 ToolButton
        # IconWidget 看起来就是一个纯粹的图标，没有背景色，不会有禁用变暗的问题
        self.icon_label = IconWidget(FluentIcon.SEARCH, self)
        self.icon_label.setFixedSize(15, 15)

        # 2. 搜索框
        self.search_edit = SearchLineEdit(self)
        self.search_edit.setFixedWidth(0)  # 初始宽度为0

        self.layout().addWidget(self.search_edit)
        self.layout().addWidget(self.icon_label)

        # 安装事件过滤器，让整个容器响应鼠标悬停
        self.setMouseTracking(True)

        self.icon_label_anim = QPropertyAnimation(self.icon_label, b"maximumWidth")
        self.icon_label_anim.finished.connect(self._on_icon_anim_finished)

        self.should_hide_icon = False
        self.textChanged = self.search_edit.textChanged

    def _on_icon_anim_finished(self):
        # 2. 只有当锁打开时才执行 hide
        if self.should_hide_icon:
            self.icon_label.hide()

    def enterEvent(self, event):
        if self.search_edit.text():
            super().enterEvent(event)
            return

        self.should_hide_icon = True  # 开启隐藏锁

        # 停止动画并设置参数
        self.icon_label_anim.stop()
        self.icon_label_anim.setDuration(100)
        self.icon_label_anim.setStartValue(self.icon_label.width())
        self.icon_label_anim.setEndValue(0)

        # 3. 准备搜索框动画
        self.anim = QPropertyAnimation(self.search_edit, b"maximumWidth")
        self.anim.setDuration(300)
        self.anim.setStartValue(self.search_edit.width())
        self.anim.setEndValue(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.icon_label_anim.start()
        self.anim.start()
        self.search_edit.setFocus()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.search_edit.text():
            self.should_hide_icon = False

            # 停止并清理之前的连接
            if hasattr(self, "anim"):
                self.anim.stop()
            if hasattr(self, "icon_label_anim"):
                self.icon_label_anim.stop()

            # 搜索框收起
            self.anim = QPropertyAnimation(self.search_edit, b"maximumWidth")
            self.anim.setDuration(300)
            self.anim.setStartValue(self.search_edit.width())
            self.anim.setEndValue(0)
            self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            # 图标重现
            self.icon_label.show()
            self.icon_label.setMaximumWidth(15)

            self.anim.start()
            self.search_edit.clearFocus()

        super().leaveEvent(event)
