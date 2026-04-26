import time

from src.char.BaseChar import BaseChar


class NanallyTeamNanally(BaseChar):
    """娜娜莉队中的娜娜莉固定轮转脚本。"""

    SKILL_WAIT_TIMEOUT = 12.0
    SKILL_MIN_DURATION = 12.0
    CYCLE_WAIT_TIMEOUT = 30.0
    ATTACK_INTERVAL = 0.1
    NEXT_BUILTIN_KEY = "char_nanally_team_sakiri"

    def do_perform(self):
        """执行娜娜莉的站场循环，满足条件后切到早雾。"""
        skill_ready = self.skill_available()
        if not skill_ready:
            skill_ready = self.continues_normal_attack_until(
                self.skill_available,
                time_out=self.SKILL_WAIT_TIMEOUT,
            )

        if not skill_ready:
            self.logger.warning("NanallyTeamNanally skill wait timed out, fallback to switch")
            self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
            return

        clicked, _, _ = self.click_skill()
        if not clicked:
            self.logger.warning("NanallyTeamNanally failed to activate skill, fallback to switch")
            self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
            return

        skill_start = time.time()

        while True:
            elapsed = self.time_elapsed_accounting_for_freeze(skill_start)
            cycle_full = self.is_cycle_full()
            if elapsed >= self.SKILL_MIN_DURATION and cycle_full:
                break
            if elapsed >= self.CYCLE_WAIT_TIMEOUT:
                self.logger.warning(
                    "NanallyTeamNanally wait cycle full timed out, fallback to switch"
                )
                break
            if self.ultimate_available():
                self.click_ultimate()
                continue
            self.click()
            self.sleep(self.ATTACK_INTERVAL)

        self.queue_switch_to_builtin_char(self.NEXT_BUILTIN_KEY)
