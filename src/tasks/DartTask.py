from ok import TaskDisabledException

from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class DartTask(NTEOneTimeTask, BaseNTETask):
    RETRY = (0.632, 0.808, 0.688, 0.909)
    QUIT = (0.412, 0.801, 0.482, 0.912)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "噗卡乐园 - 命中幸运星"
        self.description = "在可进行NPC交互的位置启动任务"
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
        self.start_rounds()
        self.interact_with_npc()
        while self.begin_round():  # 返回 False 表示达到循环次数
            self.play()

        self.finish_rounds()

    def interact_with_npc(self):
        if self.is_in_team():
            self.log_info("等待NPC交互UI出现...")
            self.wait_until(
                self.find_interac,
                time_out=10,
                raise_if_not_found=True,
            )
            self.wait_until(
                lambda: not self.is_in_team(),
                pre_action=lambda: self.send_interac(handle_claim=False),
                settle_time=0.5,
                time_out=10,
                raise_if_not_found=True,
            )
        self.wait_click_confirm(range=(0.784, 0.863, 0.855, 0.948))
        self.sleep(3.5)
        self.log_info("开始循环刷星票。")

    def play(self):
        self.log_info("开始本轮飞镖游戏...")
        while True:
            if self.find_confirm(self.box_of_screen(*self.RETRY, hcenter=True)):
                self.log_info("本轮结束")
                break
            self.click()
            self.sleep(0.15)
        self.sleep(0.5)
        self.add_success()
        if self.has_remaining_rounds():
            if self.wait_click_confirm(range=self.RETRY, time_out=4, raise_if_not_found=False):
                self.sleep(3.5)
        else:
            self.wait_click_confirm(range=self.QUIT, time_out=4, raise_if_not_found=False)
            self.log_info("已完成全部循环，点击撤离")
