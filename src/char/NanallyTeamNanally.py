import time

from src.char.BaseChar import BaseChar


class NanallyTeamNanally(BaseChar):
    SKILL_WAIT_TIMEOUT = 10.0
    SKILL_MIN_DURATION = 10.0
    CYCLE_WAIT_TIMEOUT = 30.0
    ATTACK_INTERVAL = 0.1
    NEXT_BUILTIN_KEY = "char_nanally_team_sakiri"

    def do_perform(self):
        if not self.skill_available():
            self.continues_normal_attack_until(
                self.skill_available,
                time_out=self.SKILL_WAIT_TIMEOUT,
            )

        self.click_skill()
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
