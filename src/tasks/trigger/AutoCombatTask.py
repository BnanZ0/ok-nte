from ok import Logger, TriggerTask

from src.combat.BaseCombatTask import BaseCombatTask, NotInCombatException

logger = Logger.get_logger(__name__)


class AutoCombatTask(BaseCombatTask, TriggerTask):
    CONF_USE_ULT = "使用终结技"
    CONF_AUTO_TARGET = "自动目标"
    CONF_COMBAT_START_PRIORITY = "启用开战优先级"
    CONF_RETARGET_TURN = "脱战旋转索敌"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.trigger_interval = 0.1
        self.name = "自动战斗"
        self.description = "适用于大世界、轨外之境、999夜等各类场景与副本"
        self.default_config.update(
            {
                self.CONF_AUTO_TARGET: True,
                self.CONF_USE_ULT: True,
                self.CONF_COMBAT_START_PRIORITY: True,
                self.CONF_RETARGET_TURN: False,
            }
        )
        self.config_description = {
            self.CONF_AUTO_TARGET: "关闭时仅在中键选中敌人且画面识别到关键特征时开启战斗",
            self.CONF_COMBAT_START_PRIORITY: "关闭时不按角色执行开战首切",
        }

    def switch_to_combat_start_char(self):
        if not self.config.get(self.CONF_COMBAT_START_PRIORITY, True):
            logger.info("combat start priority disabled by config")
            return
        super().switch_to_combat_start_char()

    def run(self):
        if not self.scene.is_in_team(self.is_in_team):
            return

        if not self.in_combat():
            return

        try:
            self.combat_session.use_ultimate = self.config.get(self.CONF_USE_ULT, True)
            with self.retarget_turn_policy(enable=self.config.get(self.CONF_RETARGET_TURN, False)):
                self.begin_combat_session()
                while self.in_combat():
                    self.get_current_char(raise_exception=True).perform()
        except NotInCombatException as e:
            logger.info(
                f"Out of combat {int(self.clock.now() - self.combat_session.combat_start)} {e}"
            )
        finally:
            self.combat_end()
