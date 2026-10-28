from ok import TaskDisabledException

from src.Labels import Labels
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.tasks.RecordTask import RecordTask
from src.tasks.trigger.SkipDialogTask import SkipDialogTask

INST = "进入比赛后开始任务"
EN_INST = "Start the mission after entering the game"

RECORD_INS = (
    "记录点击球员的操作，分为两个步骤：\n1. 点击球员1\n2. 点击球员2\n\n※ 请勿点击[开始比赛]"
)

EN_RECORD_INS = (
    "The player click operation is recorded in two steps:\n"
    "1. Click on player 1\n"
    "2. Click on player 2\n\n"
    "※ Do not click [Start Match]"
)

RECORD_NOTIFICATION = (
    "未找到已录制的操作。在可以选择球员时将会出现录制提示\n"
    "请按照指引，仔细完成操作录制。"
)

EN_RECORD_NOTIFICATION = (
    "No recorded action found. A recording prompt will appear when you can select a player.\n"
    "Please follow the instructions carefully to complete the action recording."
)


class VolleyballTask(NTEOneTimeTask, RecordTask):
    CONF_MODE = "模式"
    CONF_REPEAT_RECEIVE_PASS = "复读接球/传球"
    MODE_EXP = "刷经验"
    MODE_AUTO = "自动闯关"
    MODE_SUP = "辅助扣发球"
    MODE_WC = "自动周赛"
    MODES = [MODE_EXP, MODE_AUTO, MODE_WC]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "排球之星"
        self.group_name = "都市闲趣"
        self.default_config.update(
            {
                self.CONF_MODE: self.MODE_EXP,
                self.CONF_REPEAT_RECEIVE_PASS: True,
            }
        )
        self.config_type.update(
            {
                self.CONF_MODE: {
                    "type": "drop_down",
                    "options": self.MODES,
                    "sub_configs": {
                        self.MODE_WC: [
                            self.CONF_REPEAT_RECEIVE_PASS,
                            self.CONF_RESET_RECORD,
                        ]
                    },
                }
            }
        )
        self.instructions = INST if self.is_chinese() else EN_INST
        self.sleep_check_interval = 0.2
        self._play_count = 0
        self._wc_next = False
        self.RECORD_NOTIFICATION = (
            RECORD_NOTIFICATION if self.is_chinese() else EN_RECORD_NOTIFICATION
        )

    def show_notification(self):
        if self.config.get(self.CONF_MODE) == self.MODE_WC:
            super().show_notification()

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("VolleyballTask error", e)
            raise

    def do_run(self):
        return self.auto_play()

    def sleep_check(self):
        super().sleep_check()
        if self.check_monthly_card():
            self.handle_monthly_card()

    def auto_play(self):
        self._play_count = 0
        skip_task = self.get_task_by_class(SkipDialogTask)
        switch_key = False
        key = "j"
        in_game = True
        while True:
            if self.find_exit():
                if not in_game:
                    self._wc_next = False
                    self.log_info("round begin")
                    in_game = True
                    self.wait_until(self.find_exit, settle_time=1, time_out=1.5)
                    if self.is_service():
                        self.log_info("is service")
                        self.send_key("j")
                        self.sleep(2.5)
                        self.send_key("k")
                    else:
                        self.log_info("not service")

                if self.is_spike():
                    self.log_info("in spike")
                    self.wait_until(lambda: not self.is_spike(), time_out=1)
                    self.sleep(0.6)
                    self.send_key("k")

                key, switch_key = self.play_once(key, switch_key)
            else:
                if in_game:
                    self.log_info("round end")
                in_game = False
                self.handle_match_end()
                skip_task.check_skip()
            self.sleep(0.1)

    def play_once(self, key, switch_key):
        match (
            self.config.get(self.CONF_MODE),
            self.config.get(self.CONF_REPEAT_RECEIVE_PASS, False),
        ):
            case (self.MODE_EXP | self.MODE_AUTO, _) | (self.MODE_WC, True):
                if self._play_count >= 4:
                    self.sleep(0.5)
                    self.send_key("a", down_time=0.1)
                    self.sleep(0.1)
                    self.send_key("s", down_time=0.1)
                    self._play_count = 0
                    return key, switch_key

                if self.send_key(key, interval=0.5):
                    self._play_count += 1
                    return ("j" if switch_key else "k"), not switch_key
            case (self.MODE_SUP, _):
                pass
            case (self.MODE_WC, _):
                pass
        return key, switch_key

    def handle_match_end(self):
        match self.config.get(self.CONF_MODE):
            case self.MODE_EXP:
                if box := self.find_one(Labels.volleyball_restart):
                    self.operate_click(box, after_sleep=0.5)
            case self.MODE_AUTO:
                if box := self.find_one(Labels.volleyball_restart):
                    self.sleep(1)
                    boxes = self.find_feature(
                        Labels.volleyball_star,
                        box=self.get_box_by_name(Labels.box_volleyball_stars),
                    )
                    if len(boxes) == 3:
                        box = self.find_one(Labels.volleyball_next) or self.find_one(
                            Labels.volleyball_restart
                        )
                if box:
                    self.operate_click(box, after_sleep=0.5)
            case self.MODE_SUP:
                pass
            case self.MODE_WC:
                if box := self.find_one(Labels.volleyball_wc_next):
                    self._wc_next = True
                    self.operate_click(box, after_sleep=0.5)
                elif wc_start := self.find_one(
                    Labels.volleyball_wc_start, horizontal_variance=0.05, vertical_variance=0.05
                ):
                    if empty_player := self.find_one(Labels.volleyball_empty_player):
                        self.operate_click(empty_player, after_sleep=1)
                        record_instruction = RECORD_INS if self.is_chinese() else EN_RECORD_INS
                        self.record_or_replay_operations(
                            2, instruction_text=record_instruction, include_scroll=False
                        )
                        self.sleep(1)
                    self.operate_click(wc_start, after_sleep=0.5)
                elif self._wc_next:
                    self.click(0.9859, 0.9764, interval=1)

    def is_service(self):
        from src import text_white_color

        upper = self.box_of_screen(0.947, 0.405, 0.965, 0.419)
        lower = self.box_of_screen(0.947, 0.514, 0.965, 0.530)
        upper_white = self.calculate_color_percentage(text_white_color, upper)
        lower_white = self.calculate_color_percentage(text_white_color, lower)
        return upper_white > lower_white

    def is_spike(self):
        box = self.box_of_screen(0.8562, 0.8500, 0.9137, 0.9243, hcenter=True)
        return self.calculate_color_percentage(spike_bule_color, box) > 0.06


spike_bule_color = {
    "r": (71, 100),
    "g": (155, 175),
    "b": (167, 187),
}
