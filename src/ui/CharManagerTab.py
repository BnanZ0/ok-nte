from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (CardWidget, EditableComboBox, FluentIcon,
                            ImageLabel, ListWidget, PrimaryPushButton, InfoBar, InfoBarPosition,
                            PushButton, SubtitleLabel, TextEdit, TitleLabel)

from ok import og
from ok.gui.widget.CustomTab import CustomTab
from src.char.custom.CustomCharManager import CustomCharManager


def cv_to_pixmap(cv_img):
    if cv_img is None or cv_img.size == 0:
        return QPixmap()
    if not cv_img.flags['C_CONTIGUOUS']:
        cv_img = cv_img.copy()
    height, width, channel = cv_img.shape
    bytes_per_line = 3 * width
    qimg = QImage(cv_img.data, width, height, bytes_per_line, QImage.Format_RGB888).rgbSwapped()
    return QPixmap.fromImage(qimg)

def get_builtin_prefix():
    return f"{og.app.tr('[内置代码]')} "

class CharManagerTab(CustomTab):

    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.PEOPLE
        self.manager = CustomCharManager()

        # main layout
        self.main_h_layout = QHBoxLayout(self)
        self.main_h_layout.setContentsMargins(0, 0, 0, 0)

        # Left side: Character list
        self.left_widget = QWidget()
        self.left_v_layout = QVBoxLayout(self.left_widget)
        self.left_v_layout.setContentsMargins(10, 10, 10, 10)
        
        self.list_widget = ListWidget(self)
        self.list_widget.setFixedWidth(200)
        self.list_widget.currentItemChanged.connect(self.on_char_selected)
        
        self.refresh_btn = PushButton(FluentIcon.SYNC, og.app.tr("刷新列表"), self)
        self.refresh_btn.clicked.connect(self.refresh_list)
        
        self.delete_char_btn = PushButton(FluentIcon.DELETE, og.app.tr("删除角色"), self)
        self.delete_char_btn.clicked.connect(self.on_delete_char)
        self.delete_char_btn.setEnabled(False)
        
        self.left_v_layout.addWidget(self.refresh_btn)
        self.left_v_layout.addWidget(self.delete_char_btn)
        self.left_v_layout.addWidget(self.list_widget)

        # Right side: Detail View
        self.detail_widget = QWidget()
        self.detail_v_layout = QVBoxLayout(self.detail_widget)
        self.detail_v_layout.setContentsMargins(20, 20, 20, 20)

        self.char_title = TitleLabel(og.app.tr("👈 请在左侧选择一个角色以管理特征和出招表"))
        self.detail_v_layout.addWidget(self.char_title)

        # === 特征图区 ===
        self.detail_v_layout.addWidget(SubtitleLabel(og.app.tr("已绑定的特征图")))

        self.feature_grid_widget = QWidget()
        self.feature_grid = QGridLayout(self.feature_grid_widget)
        self.detail_v_layout.addWidget(self.feature_grid_widget)

        # === 出招表区 ===
        self.detail_v_layout.addWidget(SubtitleLabel(og.app.tr("出招表 (Combo)")))

        self.combo_h_layout = QHBoxLayout()
        self.combo_select = EditableComboBox()
        self.combo_select.setPlaceholderText(og.app.tr("选择或输入出招表名 (按下回车即可创建)"))
        self.combo_select.currentTextChanged.connect(self.on_combo_changed)
        self.combo_h_layout.addWidget(self.combo_select)

        self.combo_save_btn = PrimaryPushButton(og.app.tr("保存出招表"))
        self.combo_save_btn.clicked.connect(self.on_save_combo)
        self.combo_h_layout.addWidget(self.combo_save_btn)

        self.combo_unbind_btn = PushButton(og.app.tr("解除绑定"))
        self.combo_unbind_btn.clicked.connect(self.on_unbind_combo)
        self.combo_h_layout.addWidget(self.combo_unbind_btn)

        self.combo_delete_btn = PushButton(og.app.tr("删除出招表"))
        self.combo_delete_btn.clicked.connect(self.on_delete_combo)
        self.combo_h_layout.addWidget(self.combo_delete_btn)

        self.detail_v_layout.addLayout(self.combo_h_layout)

        self.combo_text = TextEdit()
        self.combo_text.setPlaceholderText(og.app.tr("例如: intro, e, q, wait(0.5), a(3), z"))
        self.combo_text.setMaximumHeight(100)
        self.detail_v_layout.addWidget(self.combo_text)

        # === 动态指令大全 ===
        self.detail_v_layout.addWidget(SubtitleLabel(og.app.tr("可用指令大全 (由 BaseChar 自动提取)")))
        
        self.doc_content = TextEdit()
        self.doc_content.setReadOnly(True)
        self.doc_content.setPlainText(self.generate_doc())
        self.detail_v_layout.addWidget(self.doc_content)

        self.main_h_layout.addWidget(self.left_widget,1)
        self.main_h_layout.addWidget(self.detail_widget,3)

        self.current_char = None
        self.refresh_list()

    @property
    def name(self):
        return og.app.tr("角色管理")

    def refresh_list(self):
        self.current_char = None
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        chars = self.manager.get_all_characters()
        for c in chars.keys():
            self.list_widget.addItem(c)
        self.list_widget.clearSelection()
        self.list_widget.blockSignals(False)

        self.combo_select.blockSignals(True)
        self.combo_select.clear()
        self.combo_select.addItems(self.manager.get_all_combos())
        self.combo_select.setCurrentIndex(-1)
        self.combo_select.blockSignals(False)
        
        self.on_combo_changed("")

        self.delete_char_btn.setEnabled(False)
        self.char_title.setText(og.app.tr("👈 请在左侧选择一个角色以管理特征和出招表"))
        for i in reversed(range(self.feature_grid.count())):
            widget = self.feature_grid.itemAt(i).widget()
            if widget:
                widget.setParent(None)

    def on_char_selected(self, item):
        if not item:
            return

        self.current_char = item.text()
        char_info = self.manager.get_character_info(self.current_char)
        if not char_info:
            return

        self.delete_char_btn.setEnabled(True)
        self.char_title.setText(self.current_char)
        combo_name = char_info.get("combo_name", "")
        
        self.combo_select.blockSignals(True)
        self.combo_select.setCurrentText(combo_name)
        self.combo_select.blockSignals(False)
        
        # Manually trigger the text change logic to ensure built-in warnings render
        self.on_combo_changed(combo_name)

        # update feature grid
        for i in reversed(range(self.feature_grid.count())):
            widget = self.feature_grid.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        row, col = 0, 0
        feature_ids = char_info.get("feature_ids", [])
        for fid in feature_ids:
            img_mat = self.manager.load_feature_image(fid)
            if img_mat is not None:
                lbl = ImageLabel()
                lbl.setFixedSize(50, 50)
                lbl.setImage(cv_to_pixmap(img_mat).scaled(50, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                card = CardWidget()
                card.setFixedSize(80, 110)
                cv = QVBoxLayout(card)
                cv.setContentsMargins(5, 5, 5, 5)
                cv.setSpacing(2)
                cv.addWidget(lbl, alignment=Qt.AlignCenter)
                del_btn = PushButton(og.app.tr("删除"), card)
                
                # Capture current fid in closure correctly
                def make_deleter(captured_fid):
                    return lambda checked: self.on_delete_feature(captured_fid)
                del_btn.clicked.connect(make_deleter(fid))
                
                cv.addWidget(del_btn)
                self.feature_grid.addWidget(card, row, col)
                col += 1
                if col > 5:
                    col = 0
                    row += 1

    def on_delete_feature(self, fid):
        if self.current_char:
            self.manager.remove_feature_from_character(self.current_char, fid)
            
            # 刷新特征图列表前，需要重新获取下 char info。最简单就是重新选中。
            # 但为了防止无限递归或触发问题，可以直接复用 on_char_selected
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.text() == self.current_char:
                    self.on_char_selected(item)
                    break

    def on_combo_changed(self, text):
        if not text:
            self.combo_text.setText("当前未绑定任何出招表。\n遇到此角色将默认使用基础通用脚本(BaseChar)。")
            self.combo_text.setReadOnly(True)
            self.combo_text.setEnabled(False)
            self.combo_save_btn.setEnabled(True)
            self.combo_unbind_btn.setEnabled(False)
            self.combo_delete_btn.setEnabled(False)
            self.combo_select.setReadOnly(False)
            self.combo_select.setCurrentIndex(-1)
            return

        is_builtin = text.startswith(get_builtin_prefix())
        if is_builtin:
            self.combo_text.setText("此为内建 Python 脚本，不可在此修改。\n请在对应的源文件中直接修改代码。")
            self.combo_text.setReadOnly(True)
            self.combo_text.setEnabled(False)
            self.combo_save_btn.setEnabled(True)
            self.combo_unbind_btn.setEnabled(self.current_char is not None)
            self.combo_delete_btn.setEnabled(False)  # Built-ins cannot be deleted
            self.combo_select.setReadOnly(True)
            return
            
        self.combo_text.setReadOnly(False)
        self.combo_text.setEnabled(True)
        self.combo_save_btn.setEnabled(True)
        self.combo_unbind_btn.setEnabled(self.current_char is not None)
        self.combo_delete_btn.setEnabled(True)
        self.combo_select.setReadOnly(False)
            
        # If the combo matches an existing one, update the text area to show its content
        combo_content = self.manager.get_combo(text)
        if combo_content:
            self.combo_text.setText(combo_content)
        else:
            self.combo_text.clear()

    def on_save_combo(self):
        combo_name = self.combo_select.currentText().strip()
        combo_content = self.combo_text.toPlainText().strip()
        
        is_builtin = combo_name.startswith(get_builtin_prefix())
        
        if is_builtin and not self.current_char:
            # Cannot create a new builtin combo from the UI, so it ignores
            InfoBar.error(
                title=og.app.tr('保存失败'),
                content=og.app.tr('内建脚本不能在这里直接创建！'),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self.window()
            )
            return

        if combo_name:
            if not is_builtin:
                self.manager.add_combo(combo_name, combo_content)
                
            if self.current_char:
                self.manager.add_character(self.current_char, combo_name)
                
            # update combo dropdown
            self.combo_select.blockSignals(True)
            self.combo_select.clear()
            self.combo_select.addItems(self.manager.get_all_combos())
            self.combo_select.setCurrentText(combo_name)
            self.combo_select.blockSignals(False)
            
            InfoBar.success(
                title=og.app.tr('保存成功'),
                content=og.app.tr(f'已成功保存并关联出招表: {combo_name}'),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self.window()
            )

    def on_delete_char(self):
        if not self.current_char:
            return
            
        char_to_delete = self.current_char
        self.manager.delete_character(char_to_delete)
        
        # Reset current selection and refresh UI
        self.current_char = None
        self.delete_char_btn.setEnabled(False)
        self.refresh_list()
        
        InfoBar.success(
            title=og.app.tr('删除成功'),
            content=og.app.tr(f'已成功删除角色: {char_to_delete} 以及关联的特征图'),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self.window()
        )

    def on_unbind_combo(self):
        if not self.current_char:
            return
            
        self.manager.add_character(self.current_char, "")
        
        # 刷新列表和右侧界面
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.text() == self.current_char:
                self.on_char_selected(item)
                break
                
        InfoBar.success(
            title=og.app.tr('解除绑定'),
            content=og.app.tr(f'已解除 {self.current_char} 的出招表绑定'),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self.window()
        )

    def on_delete_combo(self):
        combo_name = self.combo_select.currentText().strip()
        if not combo_name or combo_name.startswith(get_builtin_prefix()):
            return
            
        self.manager.delete_combo(combo_name)
        
        # 解绑所有正在使用该出招表的角色
        for c_name, c_data in self.manager.get_all_characters().items():
            if c_data.get("combo_name") == combo_name:
                self.manager.add_character(c_name, "")
                
        # 刷新出招表下拉列表
        self.combo_select.blockSignals(True)
        self.combo_select.clear()
        self.combo_select.addItems(self.manager.get_all_combos())
        self.combo_select.setCurrentIndex(-1)
        self.combo_select.blockSignals(False)
        
        # 刷新当前角色的内容显示
        if self.current_char:
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.text() == self.current_char:
                    self.on_char_selected(item)
                    break
        else:
            self.on_combo_changed("")
            
        InfoBar.success(
            title=og.app.tr('删除成功'),
            content=og.app.tr(f'已成功删除出招表: {combo_name}'),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self.window()
        )

    def generate_doc(self):
        try:
            from src.char.custom.CustomChar import CustomChar
            docs = CustomChar.get_available_commands()
            text = "可以在出招表中输入以下指令 (以逗号分隔，如: intro, e, q):\n\n"
            for d in docs:
                text += f"•  {d['name']} : {d['doc']}\n"
            return text
        except Exception as e:
            return f"生成文档失败: {e}"
