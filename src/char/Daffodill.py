from src.char.BaseChar import BaseChar
from src.combat.planner import (
    FieldClaim,
)

SKILL_SHORT_TIMEOUT = 2.0


class Daffodill(BaseChar):
    """Daffodill - PURPLE burst character.

    SUB_DPS, SETUP_ONLY: Q first, then the burst window (E attempted at most once
    during the burst), then leave the field. Self-contained and independent of any
    specific team composition. Parry detection is not implemented; readiness is
    approximated through ultimate_available().
    """

    cn_name = "达芙蒂尔"
    element = BaseChar.ElementType.PURPLE
    ULT_BURST_DURATION = 1.5

    def combat_plan(self):
        plan = self.plan()
        self.register_ultimate(plan)
        self.register_skill(plan)
        if self.ultimate_available():
            from src.char.Zankou import Zankou

            if self.get_teammate_by_class(Zankou)[0] is not None:
                plan.add(FieldClaim.high(reason="Daffodill ultimate ready with Zankou teammate"))
        plan.entry = self.perform_entry
        return plan

    def perform_entry(self):
        if self.click_ultimate():
            self._perform_burst()
            return
        self.click_skill()

    def _perform_burst(self):
        """Burst damage window after a successful Q (patterned on Chiz.perform_in_ult).

        - Attack continuously and probe whether E is available.
        - E is really attempted at most once: attempted is separated from used.
        - A reservation-blocked E does not consume the attempted quota.
        - The loop is time-boxed by ``ULT_BURST_DURATION``.
        """
        self.logger.info("burst start")
        timer = self.clock.timer(timeout=self.ULT_BURST_DURATION)
        skill_used = False

        while not timer.expired():
            if not skill_used:
                skill_used = self.click_skill()
            self.normal_attack()
            self.sleep(0.2)

        self.logger.info(f"burst end (skill used={skill_used})")
