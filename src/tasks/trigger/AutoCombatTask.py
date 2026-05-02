import time

from ok import Logger, TriggerTask
from PySide6.QtCore import QObject, Signal
from qfluentwidgets import FluentIcon

from src.char.CharFactory import get_char_feature_by_pos
from src.char.custom.CustomCharManager import CustomCharManager
from src.combat.BaseCombatTask import BaseCombatTask, CharDeadException, NotInCombatException
from src.sound_trigger.SoundCombatContext import SoundCombatContext


class ScannerSignals(QObject):
    scan_done = Signal(list, str)


scanner_signals = ScannerSignals()

logger = Logger.get_logger(__name__)


class AutoCombatTask(BaseCombatTask, TriggerTask):
    txt_team_not_exist = "队伍不存在"
    txt_team_not_enough = "队伍人数少于2人"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.trigger_interval = 0.1
        self.name = "自动战斗"
        self.description = "受《异环》UI的特殊性影响, 部分场景下存在识别稳定性波动"
        self.icon = FluentIcon.CALORIES
        self.last_is_click = False
        self.default_config.update(
            {
                "自动目标": True,
            }
        )
        self.config_description = {
            "自动目标": "关闭时仅在中键选中敌人且画面识别到 'Lv' 文字时开启战斗",
        }
        self.op_index = 0
        self.origin_func = {}
        if self._app is not None:
            self.tr(self.txt_team_not_exist)
            self.tr(self.txt_team_not_enough)

    def run(self):
        logger.info("AutoCombatTask: run() called")
        ret = False
        in_team = self.scene.is_in_team(self.is_in_team)
        logger.info(f"AutoCombatTask: is_in_team={in_team}")
        if not in_team:
            logger.debug("AutoCombatTask: Not in team, returning early")
            return

        logger.info("AutoCombatTask: In team, checking combat state")
        combat_start = time.time()
        
        try:
            in_combat = self.in_combat()
            logger.info(f"AutoCombatTask: in_combat={in_combat}")
            while in_combat:
                ret = True
                try:
                    char = self.get_current_char()
                    char.perform()
                except CharDeadException:
                    self.log_error("Characters dead", notify=True)
                    break
                except NotInCombatException as e:
                    logger.info(f"auto_combat_task_out_of_combat {int(time.time() - combat_start)} {e}")
                    break
                in_combat = self.in_combat()
            if ret:
                self.combat_end()
        finally:
            pass

    def scan_team(self):
        self.log_info("开始扫描当前队伍...")
        in_team, _, count = self.in_team()
        if not in_team or count == 0:
            scanner_signals.scan_done.emit([], self.tr(self.txt_team_not_exist))
            self.log_info("队伍不存在, 扫描结束")
            return
        if count < 2:
            scanner_signals.scan_done.emit([], self.tr(self.txt_team_not_enough))
            self.log_info("队伍人数少于2人, 扫描结束")
            return

        manager = CustomCharManager()
        results = []
        frame = self.frame
        for i in range(count):
            feature_mat, w, h = get_char_feature_by_pos(self, i, frame=frame)
            if feature_mat is not None and feature_mat.size > 0:
                is_match, match_name, confidence = manager.match_feature(self, feature_mat)
                name = match_name if is_match else None
                results.append(
                    {"index": i, "mat": feature_mat, "width": w, "height": h, "match": name}
                )
                self.log_debug(f"char_{i + 1}: {name}, confidence={confidence:.2f}")
        scanner_signals.scan_done.emit(results, "")
        self.log_info("扫描完成！")
