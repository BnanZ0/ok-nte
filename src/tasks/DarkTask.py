import re

from ok import TaskDisabledException

from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.tasks.RecordTask import RecordTask

RECORD_INS = (
    "记录点击目标关卡的操作，分为两个步骤：\n"
    "1. 点击滚动条至[目标活动]可见 (若不需要则点击[目标活动])\n"
    "2. 点击[目标活动]\n\n"
    "※ 请勿点击[开始比赛]"
)


class DarkTask(NTEOneTimeTask, RecordTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "黑暗赛车"
        self.description = "自动执行黑暗赛车,请在大世界开始执行"
        self.add_rounds_config()
        self.tr(RECORD_INS)

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("DarkTask error", e)
            raise

    def do_run(self):
        self.start_rounds()
        while self.begin_round():
            self.one_time()
            self.add_success()

            self.sleep(0.1)
        self.finish_rounds()

    def one_time(self):
        self.send_key("f4", after_sleep=3)
        self.record_or_replay_operations(2, instruction_text=self.tr(RECORD_INS))
        self.sleep(2)
        self.operate_click(0.8995, 0.9546, after_sleep=2)
        self.go()
        while not self.ocr(
            x=0.7839, y=0.8769, to_x=0.9792, to_y=0.9806, match=re.compile(r".*?\((\d+)\).*")
        ):
            self.sleep(1)
        self.operate_click(0.8995, 0.9546, after_sleep=1)
        while not self.in_world():
            self.sleep(1)

    def go(self):
        key_down = False
        self.wait_in_team(time_out=120, settle_time=2)
        start_time = self.clock.now()
        try:
            while True:
                elapsed = self.clock.now() - start_time

                # 剩余时间
                remain = 150 - elapsed

                if remain <= 0:
                    break

                if elapsed > 20 and elapsed < 40:
                    if not key_down:
                        key_down = True
                        self.send_key_down("w")
                        self.sleep(0.2)
                    self.send_key("lshift")
                    self.sleep(0.1)
                    self.send_key("space")
                    self.sleep(0.25)
                    self.send_key("space")
                    self.sleep(1)
                else:
                    if key_down:
                        key_down = False
                        self.send_key_up("w")
                self.sleep(1)
        finally:
            if key_down:
                key_down = False
                self.send_key_up("w")
        self.wait_until(lambda: not self.is_in_team(), time_out=600)
