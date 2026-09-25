import re
from ok import TaskDisabledException

from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from ok import TaskDisabledException, og
from src.events import ConfirmationRequested, communicate

INST = "在NPC旁边启动任务，脚本会自动交互并循环刷星票。"
EN_INST = "Start near the NPC. The script will auto-interact and loop."
DART_HINT = (
    "该模式会较高频抢夺鼠标。如需停止，请使用热键暂停 ok-nte, 再手动停止任务。\n"
    "当前热键: {hotkey} (如无法确认当前热键则点击取消, 主动确认后再运行)"
)
class DartTask(NTEOneTimeTask, BaseNTETask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动飞镖"
        self.description = "在npc旁边启动自动飞镖并循环刷星票"
        self.add_rounds_config(default=0)

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("DartTask error", e)
            raise

    def do_run(self):
        try:
            hotkey = og.executor.basic_options.get("Start/Stop")
        except Exception:
            hotkey = "--"
        confirmation = ConfirmationRequested(
            self.tr(self.name),
            self.tr(DART_HINT).format(hotkey=hotkey),
            rich_text=False,
            hide_cancel=False,
            close_delay_seconds=3,
        )
        communicate.confirmation_requested.emit(confirmation)
        if not confirmation.wait_for_response():
            return 
        self.start_rounds()
        self.interact_with_npc()
        while self.begin_round():          # 返回 False 表示达到循环次数
            self.play()
            self.add_success()             # 记录本轮成功

        self.finish_rounds() 

    def interact_with_npc(self): 
        self.log_info("等待NPC交互UI出现...")
        self.wait_ocr(x=0.56, y=0.50, width=0.25, height=0.15,match=re.compile("售票员"), raise_if_not_found=True, time_out=15)
        self.send_key("f", after_sleep=1)
        self.sleep(1.5)
        self.operate_click(0.74, 0.90, after_sleep=1)
        self.log_info("开始循环刷星票。")

    def play(self):
        self.log_info("开始本轮飞镖游戏...")
        self.sleep(3.5)  # 等待游戏开始
        while True:
            if self.find_retry_button():
                self.log_info("本轮结束")
                break
            self.click(key="left")
            self.sleep(0.15)
        self.sleep(0.5)
        if self.has_remaining_rounds():
            self.operate_click(0.60, 0.85, after_sleep=1)    # 还有下一轮，点"再次进行"
        else:
            self.operate_click(0.38, 0.85, after_sleep=1)     # 最后一轮，点"撤离"
            self.log_info("已完成全部循环，点击撤离")
    def find_retry_button(self):
        boxes = self.ocr(x=0.50, y=0.80, width=0.30, height=0.15, match=re.compile("再试一次"))
        if boxes:
            return boxes[0]
        return None





    

     
