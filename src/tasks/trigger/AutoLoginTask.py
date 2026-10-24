from ok import Logger, TriggerTask

from src.tasks.BaseNTETask import BaseNTETask

logger = Logger.get_logger(__name__)


class AutoLoginTask(BaseNTETask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.trigger_interval = 5
        self.name = "自动登录游戏"
        self.description = "游戏启动后自动登录游戏"

    def run(self):
        if self.scene.logged_in():
            return
        elif self.scene.is_in_team(self.is_in_team):
            self.scene.set_logged_in()
        else:
            self.check_login()
