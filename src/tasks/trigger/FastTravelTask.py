import re

from ok import Logger, TriggerTask

from src import text_black_color
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.ui.icons import task_icon
from src.utils import image_utils as iu

logger = Logger.get_logger(__name__)


class FastTravelTask(BaseNTETask, TriggerTask):
    DEFAULT_MATCH_WORDS = ("Teleport", "传送")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": False}
        self.name = "快速传送"
        self.icon = task_icon("teleport")
        self.description = "地图中自动点击传送"
        self.match = self._compile_match_words(self.DEFAULT_MATCH_WORDS)
        self.default_config.update(
            {
                "匹配文字": "",
            }
        )
        self.config_description.update(
            {
                "匹配文字": "供非中/英语用户自定义传送文字, 逗号分隔\n例: Teleport, 传送",
            }
        )

    def run(self):
        if self.scene.is_in_team(self.is_in_team) or not self.find_one(
            Labels.close_button, threshold=0.8
        ):
            return
        if btn := self.find_traval_button():
            self.match = self._configured_match_words()
            to_x = (btn.x + btn.width) / self.width
            results = self.ocr(
                box=self.box_of_screen(0.7438, 0.8736, to_x, 0.9118),
                match=self.match,
                frame_processor=lambda image: iu.create_color_mask(
                    image, text_black_color, invert=True
                ),
            )

            if results:
                self.click_traval_button(results[0])

    @staticmethod
    def _compile_match_words(words):
        return [
            re.compile(re.escape(word.strip()), re.IGNORECASE) for word in words if word.strip()
        ]

    def _configured_match_words(self):
        if config_match := self.config.get("匹配文字"):
            return self._compile_match_words(config_match.split(",")) or self._compile_match_words(
                self.DEFAULT_MATCH_WORDS
            )
        return self._compile_match_words(self.DEFAULT_MATCH_WORDS)
