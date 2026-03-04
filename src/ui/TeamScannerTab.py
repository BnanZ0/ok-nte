import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (BodyLabel, CardWidget, ComboBox, FluentIcon,
                            ImageLabel, LineEdit, MessageBoxBase,
                            PrimaryPushButton, SubtitleLabel)

from ok import og
from ok.gui.widget.CustomTab import CustomTab
from src.char.custom.CustomCharManager import CustomCharManager
from src.tasks.trigger.AutoCombatTask import AutoCombatTask, scanner_signals


def cv_to_pixmap(cv_img):
    if cv_img is None or cv_img.size == 0:
        return QPixmap()
    # 修复 BufferError: underlying buffer is not C-contiguous
    if not cv_img.flags['C_CONTIGUOUS']:
        cv_img = cv_img.copy()
    height, width, channel = cv_img.shape
    bytes_per_line = 3 * width
    qimg = QImage(cv_img.data, width, height, bytes_per_line, QImage.Format_RGB888).rgbSwapped()
    return QPixmap.fromImage(qimg)


class NewCharDialog(MessageBoxBase):
    def __init__(self, mat, manager: CustomCharManager, parent=None):
        super().__init__(parent)
        self.manager = manager

        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel(og.app.tr("记录新特征"), self), alignment=Qt.AlignCenter)

        img_label = ImageLabel()
        img_label.setImage(cv_to_pixmap(mat).scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.viewLayout.addWidget(img_label, alignment=Qt.AlignCenter)

        self.name_input = LineEdit()
        self.name_input.setPlaceholderText(og.app.tr("输入或选择关联的角色名称"))
        self.viewLayout.addWidget(self.name_input)
        
        # 提示现有的角色列表
        self.existing_chars = list(self.manager.get_all_characters().keys())
        # To do a simple dropdown for existing, combining ComboBox
        self.char_combo = ComboBox()
        self.char_combo.setPlaceholderText(og.app.tr("或从已有角色中选择..."))
        self.char_combo.addItems([""] + self.existing_chars)
        self.char_combo.currentTextChanged.connect(self._on_char_select)
        self.viewLayout.addWidget(self.char_combo)

        self.combo_list = ComboBox()
        self.combo_list.setPlaceholderText(og.app.tr("选择绑定的出招表 (可选)"))
        self.combo_list.addItems([""] + self.manager.get_all_combos())
        self.viewLayout.addWidget(self.combo_list)

        self.widget.setMinimumWidth(320)

    def _on_char_select(self, text):
        if text:
            self.name_input.setText(text)
            char_info = self.manager.get_character_info(text)
            if char_info and char_info.get("combo_name"):
                self.combo_list.setText(char_info.get("combo_name"))

    def get_data(self):
        char_name = self.name_input.text().strip()
        combo_name = self.combo_list.currentText().strip()
        return char_name, combo_name


class SlotCard(CardWidget):
    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.manager = CustomCharManager()

        self.vbox = QVBoxLayout(self)
        self.title = SubtitleLabel(og.app.tr(f"号位 {index + 1}"))
        self.image = ImageLabel()
        self.image.setFixedSize(120, 120)
        self.status = BodyLabel(og.app.tr("点击上方按钮扫描..."))
        self.btn_act = PrimaryPushButton(og.app.tr("未识别，关联新特征"), self)
        self.btn_act.hide()

        self.vbox.addWidget(self.title, alignment=Qt.AlignCenter)
        self.vbox.addWidget(self.image, alignment=Qt.AlignCenter)
        self.vbox.addWidget(self.status, alignment=Qt.AlignCenter)
        self.vbox.addWidget(self.btn_act, alignment=Qt.AlignCenter)

        self.btn_act.clicked.connect(self.on_action)
        self.current_mat = None

    def update_result(self, mat, match_name):
        self.current_mat = mat
        if mat is not None:
            pixmap = cv_to_pixmap(mat)
            self.image.setImage(pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.image.clear()

        if match_name:
            self.status.setText(og.app.tr(f"匹配成功: {match_name}"))
            self.btn_act.hide()
        elif mat is not None:
            self.status.setText(og.app.tr("未能识别该特征"))
            self.btn_act.show()
        else:
            self.status.setText(og.app.tr("无画面"))
            self.btn_act.hide()

    def on_action(self):
        dialog = NewCharDialog(self.current_mat, self.manager, self.window())
        if dialog.exec():
            char_name, combo_name = dialog.get_data()
            if char_name and self.current_mat is not None:
                self.manager.add_feature_to_character(char_name, self.current_mat)
                self.manager.add_character(char_name, combo_name)
                self.update_result(self.current_mat, char_name)


class TeamScannerTab(CustomTab):

    def __init__(self):
        super().__init__()
        self.icon = FluentIcon.CAMERA
        self.logger.info("Init TeamScannerTab")
        
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(20, 20, 20, 20)
        self.vbox.setSpacing(20)

        # Header
        self.header = SubtitleLabel(og.app.tr("队伍角色扫描"))
        self.scan_btn = PrimaryPushButton(FluentIcon.SYNC, og.app.tr("扫描当前队伍屏幕槽位"))
        self.scan_btn.setFixedWidth(250)
        self.scan_btn.clicked.connect(self.on_scan_clicked)

        self.vbox.addWidget(self.header)
        self.vbox.addWidget(self.scan_btn)

        # Cards Layout
        self.cards_layout = QHBoxLayout()
        self.slots = []
        for i in range(4):
            card = SlotCard(i, self)
            self.slots.append(card)
            self.cards_layout.addWidget(card)

        self.vbox.addLayout(self.cards_layout)
        self.vbox.addStretch(1)

        # Connect Signal
        scanner_signals.scan_done.connect(self.on_scan_done)

    @property
    def name(self):
        return og.app.tr("扫描队伍")

    def on_scan_clicked(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText(og.app.tr("扫描中..."))
        for card in self.slots:
            card.status.setText(og.app.tr("正在分析..."))
            card.btn_act.hide()
        self.get_task(AutoCombatTask).scan_team()

    def on_scan_done(self, results):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText(og.app.tr("扫描当前队伍屏幕槽位"))

        if not results:
            for card in self.slots:
                card.status.setText(og.app.tr("未获取到特征"))
            return

        for res in results:
            idx = res.get("index")
            mat = res.get("mat")
            match_name = res.get("match")
            if 0 <= idx < 4:
                self.slots[idx].update_result(mat, match_name)
