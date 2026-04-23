import time
import re
from qfluentwidgets import FluentIcon
from src.Labels import Labels
from ok import TriggerTask, Logger
from src.tasks.BaseNTETask import BaseNTETask

logger = Logger.get_logger(__name__)


class AutoSkipDialogTask(BaseNTETask, TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.name = "自动跳过剧情"
        self.icon = FluentIcon.ACCEPT

    def run(self):
        if self.find_one(Labels.skip_dialog, horizontal_variance=0.05):
            self.log_info("检测到跳过对话框，正在自动跳过...")
            self.sleep(0.1)
            self.send_key('esc', after_sleep=0.1)  # 确认使用send_key：esc为系统通用退出键，非游戏可配置热键
            start = time.time()
            self.clicked_confirm = False
            while time.time() - start < 3:
                self.next_frame()
                no_ask = self.ocr(match=re.compile("不再"), box=self.box.center, log=True)
                confirm = self.ocr(match="确认", box=self.box_of_screen(2326/3840, 1285/2160,2642/3840, 1393/2160), log=True)
                if no_ask:
                    self.click(no_ask, after_sleep=0.1)
                if confirm:
                    self.click(confirm, after_sleep=0.4)
                    self.clicked_confirm = True
                elif self.clicked_confirm:
                    self.log_debug('AutoSkipDialogTask no confirm break')
                    break
                elif self.find_one(Labels.skip_dialog, horizontal_variance=0.05):
                    self.send_key('esc', after_sleep=0.1)
        elif result:= self.find_one(Labels.dialog_one_click, horizontal_variance=0.05):
            self.click(result,after_sleep=0.1)
            self.log_info("检测到点击键，正在自动跳过...")
        self.next_frame()
