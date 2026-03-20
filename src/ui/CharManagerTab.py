from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout,
                               QVBoxLayout, QWidget, QFileDialog)

from qfluentwidgets import (CardWidget, EditableComboBox, FluentIcon,
                            ImageLabel, ListWidget, PrimaryPushButton, InfoBar, InfoBarPosition,
                            PushButton, SubtitleLabel, TextEdit, TitleLabel)

from ok import og
from ok.gui.widget.CustomTab import CustomTab
from src.char.custom.CustomCharManager import CustomCharManager
from src.ui.TeamScannerTab import cv_to_pixmap
import json
import zipfile
import shutil
import subprocess
from pathlib import Path


def get_builtin_prefix():
    return f"{og.app.tr('[内置代码]')} "

class CharManagerTab(CustomTab):

    def __init__(self):
        super().__init__()
        get_builtin_prefix()
        self.tr_save_success = og.app.tr('保存成功')
        self.tr_combo_msg = og.app.tr('出招表: {}')
        self.tr_del_success = og.app.tr('删除成功')
        self.tr_del_char_msg = og.app.tr('已成功删除角色: {} 以及关联的特征图')
        self.tr_unbind_success = og.app.tr('解除绑定')
        self.tr_unbind_msg = og.app.tr('已解除 {} 的出招表绑定')
        self.tr_import_data = og.app.tr("导入数据")
        self.tr_import_failed = og.app.tr("导入失败")
        self.tr_import_success = og.app.tr("导入成功")
        self.tr_import_msg = og.app.tr("已导入 {} 个文件")
        
        self.tr_name = og.app.tr('角色管理')
        self.tr_choose_char = og.app.tr('👈 请在左侧选择一个角色以管理特征和出招表')
        self.tr_delete = og.app.tr('删除')
        self.tr_unbound_text = og.app.tr('当前未绑定任何出招表。\n遇到此角色将默认使用基础通用脚本(BaseChar)。')
        self.tr_builtin_text = og.app.tr('此为内建 Python 脚本，不可在此修改。\n请在对应的源文件中直接修改代码。')
        
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

        self.import_btn = PushButton(FluentIcon.DOWNLOAD, self.tr_import_data, self)
        self.import_btn.clicked.connect(self.on_import_data)

        self.export_btn = PushButton(FluentIcon.SHARE, og.app.tr("导出数据"), self)
        self.export_btn.clicked.connect(self.on_export_data)
        
        self.left_v_layout.addWidget(self.refresh_btn)
        self.left_v_layout.addWidget(self.delete_char_btn)
        self.left_v_layout.addWidget(self.import_btn)
        self.left_v_layout.addWidget(self.export_btn)
        self.left_v_layout.addWidget(self.list_widget)

        # Right side: Detail View
        self.detail_widget = QWidget()
        self.detail_v_layout = QVBoxLayout(self.detail_widget)
        self.detail_v_layout.setContentsMargins(20, 20, 20, 20)

        self.char_title = TitleLabel(self.tr_choose_char)
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
        self.combo_h_layout.addWidget(self.combo_select, 1)

        self.combo_unbind_btn = PushButton(FluentIcon.LINK, og.app.tr("解除绑定"))
        self.combo_unbind_btn.clicked.connect(self.on_unbind_combo)
        self.combo_h_layout.addWidget(self.combo_unbind_btn)

        self.combo_delete_btn = PushButton(FluentIcon.DELETE, og.app.tr("删除"))
        self.combo_delete_btn.clicked.connect(self.on_delete_combo)
        self.combo_h_layout.addWidget(self.combo_delete_btn)

        self.detail_v_layout.addLayout(self.combo_h_layout)

        self.combo_text = TextEdit()
        self.combo_text.setPlaceholderText("skill,wait(0.5),l_click(3),ultimate")
        self.combo_text.setMaximumHeight(100)
        self.detail_v_layout.addWidget(self.combo_text)

        self.combo_actions_layout = QHBoxLayout()
        self.combo_actions_layout.addStretch(1)

        self.combo_test_btn = PushButton(FluentIcon.PLAY_SOLID, og.app.tr("运行一次测试"))
        self.combo_test_btn.clicked.connect(self.on_test_combo)
        self.combo_actions_layout.addWidget(self.combo_test_btn)

        self.combo_save_btn = PrimaryPushButton(FluentIcon.SAVE, og.app.tr("保存出招表"))
        self.combo_save_btn.clicked.connect(self.on_save_combo)
        self.combo_actions_layout.addWidget(self.combo_save_btn)

        self.detail_v_layout.addLayout(self.combo_actions_layout)

        self.detail_v_layout.addWidget(SubtitleLabel(og.app.tr("可用指令")))
        
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
        return self.tr_name

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
        self.char_title.setText(self.tr_choose_char)
        for i in reversed(range(self.feature_grid.count())):
            widget = self.feature_grid.itemAt(i).widget()
            if widget:
                widget.setParent(None)

    def on_export_data(self):
        downloads_path = Path.home() / "Downloads"
        base_name = "ok-nte-custom"
        extension = ".zip"
        zip_path = downloads_path / f"{base_name}{extension}"
        
        counter = 1
        while zip_path.exists():
            zip_path = downloads_path / f"{base_name} ({counter}){extension}"
            counter += 1
            
        source_dir = Path.cwd() / "custom_chars"
        
        if not source_dir.is_dir():
            return
            
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path in source_dir.rglob("*"):
                if file_path.is_file():
                    zipf.write(file_path, file_path.relative_to(Path.cwd()))
                    
        subprocess.run(f'explorer /select,"{zip_path.resolve()}"')

    def on_import_data(self):
        downloads_path = Path.home() / "Downloads"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr_import_data,
            str(downloads_path),
            "Zip Files (*.zip)"
        )
        if not file_path:
            return

        try:
            imported = self._import_custom_data_zip(Path(file_path))
        except Exception as e:
            InfoBar.error(
                title=self.tr_import_failed,
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self.window()
            )
            self.logger.error(str(e))
            return

        # Reload DB from disk and refresh UI
        self.manager.load_db()
        self.manager.validate_db()
        self.refresh_list()

        InfoBar.success(
            title=self.tr_import_success,
            content=self.tr_import_msg.format(imported),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self.window()
        )

    def _import_custom_data_zip(self, zip_path: Path) -> int:
        if not zip_path.is_file():
            raise ValueError("文件不存在")

        def norm(name: str) -> str:
            return name.replace("\\", "/").lstrip("/")

        with zipfile.ZipFile(zip_path, "r") as zipf:
            infos = [i for i in zipf.infolist() if not i.is_dir()]
            custom_infos = []
            has_db = False
            db_info = None
            for info in infos:
                name = norm(info.filename)
                if not name.startswith("custom_chars/"):
                    continue

                parts = [p for p in name.split("/") if p]
                if not parts or parts[0] != "custom_chars":
                    raise ValueError("不支持的导入格式")
                if any(p == ".." or ":" in p for p in parts):
                    raise ValueError("不安全的压缩包路径")

                if "/".join(parts) == "custom_chars/db.json":
                    has_db = True
                    db_info = info
                custom_infos.append((info, parts))

            if not has_db:
                raise ValueError("仅支持导入导出数据的 zip（缺少 custom_chars/db.json）")
            if not custom_infos:
                raise ValueError("压缩包内没有可导入的数据")

            try:
                json.loads(zipf.read(db_info).decode("utf-8"))
            except Exception:
                raise ValueError("仅支持导入导出数据的 zip（custom_chars/db.json 无效）")

            dest_root = Path.cwd().resolve()
            imported = 0
            for info, parts in custom_infos:
                target = (dest_root / Path(*parts)).resolve()
                if not target.is_relative_to(dest_root):
                    raise ValueError("不安全的压缩包路径")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipf.open(info, "r") as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                imported += 1

        return imported

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
            img_mat, w, h = self.manager.load_feature_image(fid)
            if img_mat is not None:
                lbl = ImageLabel()
                lbl.setFixedSize(50, 50)
                lbl.setImage(cv_to_pixmap(img_mat).scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                card = CardWidget()
                card.setFixedSize(80, 110)
                cv = QVBoxLayout(card)
                cv.setContentsMargins(5, 5, 5, 5)
                cv.setSpacing(2)
                cv.addWidget(lbl, alignment=Qt.AlignCenter)
                del_btn = PushButton(self.tr_delete, card)
                
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
            self.combo_text.setText(self.tr_unbound_text)
            self.combo_text.setReadOnly(True)
            self.combo_text.setEnabled(False)
            self.combo_save_btn.setEnabled(True)
            self.combo_unbind_btn.setEnabled(False)
            self.combo_delete_btn.setEnabled(False)
            self.combo_test_btn.setEnabled(False)
            self.combo_select.setReadOnly(False)
            self.combo_select.setCurrentIndex(-1)
            return

        is_builtin = text.startswith(get_builtin_prefix())
        if is_builtin:
            self.combo_text.setText(self.tr_builtin_text)
            self.combo_text.setReadOnly(True)
            self.combo_text.setEnabled(False)
            self.combo_save_btn.setEnabled(self.current_char is not None)
            self.combo_unbind_btn.setEnabled(self.current_char is not None)
            self.combo_delete_btn.setEnabled(False)  # Built-ins cannot be deleted
            self.combo_test_btn.setEnabled(False)
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

        # Update test button state
        self.combo_test_btn.setEnabled(True)

    def on_test_combo(self):
        if not self.combo_text.toPlainText().strip():
            return
        og.app.start_controller.handler.post(self._run_combo_test)

    def _run_combo_test(self):
        og.app.start_controller.do_start()
        from src.char.custom.CustomChar import CustomChar
        from src.tasks.trigger.AutoCombatTask import AutoCombatTask
        task = self.get_task(AutoCombatTask)
        if not task:
            return
            
        test_char = CustomChar(task=task, index=0, char_name="TEST_CHAR")
        test_char.combo_str = self.combo_text.toPlainText().strip()
        test_char._compile_combo()
        test_char.perform()

    def on_save_combo(self):
        combo_name = self.combo_select.currentText().strip()
        combo_content = self.combo_text.toPlainText().strip()
        
        is_builtin = combo_name.startswith(get_builtin_prefix())
        
        if is_builtin and not self.current_char:
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
                title=self.tr_save_success,
                content=self.tr_combo_msg.format(combo_name),
                orient=Qt.Orientation.Horizontal,
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
            title=self.tr_del_success,
            content=self.tr_del_char_msg.format(char_to_delete),
            orient=Qt.Orientation.Horizontal,
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
            title=self.tr_unbind_success,
            content=self.tr_unbind_msg.format(self.current_char),
            orient=Qt.Orientation.Horizontal,
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
            title=self.tr_del_success,
            content=self.tr_combo_msg.format(combo_name),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self.window()
        )

    def generate_doc(self):
        try:
            from src.char.custom.CustomChar import CustomChar
            docs = CustomChar.get_available_commands()
            text = "可以在出招表中输入以下指令 (以逗号分隔):\n\n"
            for cmd in docs:
                text += f"▶ 【{cmd.name}】\n"
                text += f"    • 参数: {cmd.params or '无'}\n"
                text += f"    • 说明: {cmd.doc or '无'}\n"
                text += f"    • 示例: {cmd.example or cmd.name}\n\n"
            return text
        except Exception as e:
            return f"生成文档失败: {e}"
