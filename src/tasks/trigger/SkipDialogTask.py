from ok import Logger, TriggerTask

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.utils import image_utils as iu

logger = Logger.get_logger(__name__)


class SkipDialogTask(TriggerTask, BaseNTETask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": False}
        self.confirm_dialog_checked = False
        self.has_eye_time = 0
        self.skip = None
        self.trigger_interval = 0.5
        self.skip_message_hold = False
        self.name = "任务跳过对话"
        self.description = "点击时将短暂控制物理鼠标"

    def run(self):
        if self.scene.in_team(self.is_in_team):
            return
        if self.check_skip():
            return
        if self.check_dialog_click():
            return
        if self.skip_message():
            return

    def check_dialog_click(self):
        if self.find_one(
            Labels.dialog_history, threshold=0.8, box=self.default_box.dialog_icon_box
        ):
            if result := self.find_one(Labels.dialog_click, threshold=0.8, vertical_variance=0.02):
                self.click(result, after_sleep=0.1)
                return True

    def skip_message(self):
        if self.find_one(Labels.message):
            if message_dialog := self.find_one(
                Labels.message_dialog, vertical_variance=0.2, horizontal_variance=0.01
            ):
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

    def skip_confirm(self):
        if skip_button := self.find_one(Labels.skip_quest_confirm, threshold=0.8):
            # sleep 0.2 to stable click skip button
            self.sleep(0.2)
            self.click(0.4508, 0.5194)
            self.click(skip_button, after_sleep=0.1)
            if not self.find_one(Labels.skip_quest_confirm, threshold=0.8):
                return True
        if self.is_in_team():
            return True

    def find_skip(self):
        return self.find_one(
            Labels.skip_dialog,
            horizontal_variance=0.02,
            threshold=0.75,
            frame_processor=iu.isolate_dialog_to_white,
        )

    def try_click_skip(self):
        skipped = False
        while skip := self.find_skip():
            logger.info("Click Skip Dialog")
            self.click(skip)
            skipped = True
        return skipped

    def check_skip(self):
        if self.try_click_skip():
            return self.wait_until(self.skip_confirm, time_out=5, raise_if_not_found=False)

    def click(self, *args, **kwargs):
        kwargs.setdefault("move", True)
        kwargs.setdefault("down_time", 0.01)
        kwargs.setdefault("after_sleep", 0.4)
        return super().click(*args, **kwargs)
