
from ok import Logger, TriggerTask
from src import text_black_color
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.utils import image_utils as iu

logger = Logger.get_logger(__name__)


class FastTravelTask(BaseNTETask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": False}
        self.name = "快速传送"
        self.description = "地图中自动点击传送"
        self.match = ["Teleport", "传送"]

    def run(self):
        if self.scene.in_team(self.is_in_team):
            return
        travel = self.find_one(
            Labels.skip_quest_confirm, box=self.box_of_screen(0.7246, 0.8535, 0.8089, 0.9313)
        )
        if travel:
            results = self.ocr(
                box=self.box_of_screen(0.7281, 0.8576, 0.9891, 0.9250),
                match=self.match,
                frame_processor=lambda image: iu.create_color_mask(
                    image, text_black_color, invert=True
                )
            )

            if results:
                self.click_traval_button(travel)
                return True
