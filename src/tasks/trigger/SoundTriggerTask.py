from ok import Logger, TriggerTask

from src.sound_trigger.SoundCombatContext import SoundCombatContext
from src.tasks.BaseNTETask import BaseNTETask
from src.ui.icons import task_icon

logger = Logger.get_logger(__name__)


class SoundTriggerTask(BaseNTETask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": False}
        self.trigger_interval = 0.1
        self.name = "声音闪避反击"
        self.icon = task_icon("sound")
        self.description = "未处于自动战斗时, 响应声音闪避或反击"
        self.async_sound_action = True

    def run(self):
        context = SoundCombatContext()
        if not self.scene.is_in_team(self.is_in_team) or not self.can_sound_trigger():
            context.clear_task_if(self)
            return
        self._apply_sound_config(context)

    def can_sound_trigger(self):
        allowed = (
            self.enabled
            and self.sound_config.get("Enable Sound Trigger", True)
            and not self._is_onetime_task_running()
            and not self.scene.in_combat()
        )
        return allowed

    def disable(self):
        SoundCombatContext().clear_task_if(self)
        super().disable()

    def _is_onetime_task_running(self):
        current_task = self.executor.current_task
        return current_task in self.executor.onetime_tasks and current_task.running

    def _apply_sound_config(self, context: SoundCombatContext):
        dodge_all_attacks = self.sound_config.get("Dodge All Attacks", True)
        dodge_thresh = self._clip_threshold(self.sound_config.get("Dodge Threshold"), 0.13)
        counter_thresh = self._clip_threshold(
            self.sound_config.get("Counter Attack Threshold"), 0.12
        )

        context.update_config(True, dodge_all_attacks, dodge_thresh, counter_thresh)
        context.update_task(self)

    @staticmethod
    def _clip_threshold(value, default):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(1.0, value))
