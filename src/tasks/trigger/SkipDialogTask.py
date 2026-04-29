import time
from ok import Logger, TriggerTask

from src import text_white_color
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.utils import game_filters as gf

logger = Logger.get_logger(__name__)


class SkipDialogTask(TriggerTask, BaseNTETask):
    DEFAULT_MOVE = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": False}
        self.confirm_dialog_checked = False
        self.has_eye_time = 0
        self.skip = None
        self.trigger_interval = 0.5
        self.skip_message_hold = False
        self._check_confirm_timer = 0
        self.name = "任务跳过对话"
        self.description = "点击时将短暂控制物理鼠标"
        self.default_config.update(
            {
                "跳过剧情": True,
                "自动消息": True,
            }
        )

    def run(self):
        if self.scene.in_team(self.is_in_team):
            return
        if self.config.get("跳过剧情") and self.in_story():
            if self.check_skip():
                return
            if self.check_options():
                return
            self.check_dialog_click()

        if self.config.get("自动消息"):
            if self.skip_message():
                return

    def in_story(self):
        return self.find_one(Labels.auto_play) or self.find_skip() or self.find_dialog_history()

    def check_options(self):
        if boxes := self.find_feature(
            Labels.dialog_history, box=self.box_of_screen(0.6887, 0.5160, 0.7121, 0.7764),
            threshold=0.6
        ):
            boxes.sort(key=lambda b: b.y)
            top_box = boxes[0]
            bottom_box = boxes[-1]
            if self.calculate_color_percentage(option_pink_color, top_box.scale(2)) > 0.3:
                self.click(bottom_box, after_sleep=0.1)
            return True
        return False

    def find_dialog_history(self):
        return self.find_one(
            Labels.dialog_history, threshold=0.8, box=self.default_box.dialog_icon_box
        )

    def check_dialog_click(self):
        if self.find_dialog_history():
            if result := self.find_one(Labels.dialog_click, threshold=0.8, vertical_variance=0.02):
                self.click(result, down_time=0.01, after_sleep=0.1)
                return True

    def skip_message(self):
        if self.find_one(Labels.message):
            if self.find_message_dialog():
                self.sleep(0.1)
                message_dialog = self.find_message_dialog()
                if message_dialog:
                    self.click(message_dialog, down_time=0.02, after_sleep=1)
                    self.log_info(f"click {message_dialog}")
            # else:
            #     try:
            #         self.mouse_down(0.4223, 0.7236)
            #         self.wait_until(
            #             lambda: (
            #                 self.find_one(
            #                     Labels.message_dialog,
            #                     vertical_variance=0.15,
            #                     horizontal_variance=0.01,
            #                 )
            #                 or not self.find_one(Labels.message)
            #             ),
            #             time_out=30,
            #         )
            #     finally:
            #         self.mouse_up(0.4223, 0.7236)

    def find_message_dialog(self):
        return self.find_one(Labels.message_dialog, vertical_variance=0.2, horizontal_variance=0.01)

    def skip_confirm(self):
        if skip_button := self.find_one(Labels.skip_quest_confirm, threshold=0.8):
            # sleep 0.2 to stable click skip button
            now = time.time()
            self.wait_until(
                lambda: self.calculate_color_percentage(skip_confirm_color, skip_button) > 0.4,
                time_out=6,
            )
            if time.time() - now < 2.5:
                self.sleep(0.2)
                self.click(0.4508, 0.5194, down_time=0.01, after_sleep=0.4)
            self.click(skip_button, down_time=0.01, after_sleep=0.5)
            if not self.find_one(Labels.skip_quest_confirm, threshold=0.8):
                return True
        if self.is_in_team():
            return True

    def find_skip(self):
        return self.find_one(
            Labels.skip_dialog,
            horizontal_variance=0.02,
            threshold=0.75,
            frame_processor=gf.isolate_dialog_to_white,
        )

    def try_click_skip(self):
        skipped = False
        while skip := self.find_skip():
            logger.info("Click Skip Dialog")
            self.click(skip, down_time=0.01, after_sleep=0.4)
            skipped = True
        return skipped

    def check_skip(self):
        if self.try_click_skip():
            self._check_confirm_timer = time.time() + 3
        if self._check_confirm_timer > time.time():
            return self.skip_confirm()
        else:
            self._check_confirm_timer = 0


skip_confirm_color = {
    "r": (208, 217),
    "g": (208, 217),
    "b": (208, 217),
}

option_pink_color = {
    "r": (235, 250),
    "g": (75, 85),
    "b": (140, 145),
}
