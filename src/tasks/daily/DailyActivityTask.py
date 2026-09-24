import re

from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.daily.ActivityPanelMixin import ActivityPanelMixin
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class DailyActivityTask(ActivityPanelMixin, NTEOneTimeTask, BaseNTETask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "每日活跃检查"
        self.description = "检查每日活跃度是否达到100, 不消耗资源或领取奖励"
        self.visible = False

    def run(self):
        super().run()
        if not self.do_run():
            raise RuntimeError("每日活跃度未达标或无法读取")

    def do_run(self) -> bool:
        self.ensure_main()
        if not self.open_activity_panel():
            return False
        activity_box = self.box_of_screen(
            0.184, 0.188, 0.256, 0.255, name="activity", hcenter=True
        )
        pattern = re.compile(r"^\s*(\d{1,3})(?:\s*/\s*100)?\s*$")
        results = self.ocr(box=activity_box, match=pattern)
        values = set()
        for result in results:
            match = pattern.fullmatch(result.name)
            if match and 0 <= int(match.group(1)) <= 100:
                values.add(int(match.group(1)))
        if len(values) != 1:
            self.log_error("无法明确读取每日活跃度")
            return False
        activity = values.pop()
        self.info_set("daily activity", activity)
        self.log_info(f"每日活跃度: {activity}/100")
        return activity >= 100
