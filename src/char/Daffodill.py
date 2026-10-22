from src.char.BaseChar import BaseChar
from src.combat.planner import (
    ActionIntent,
    CombatContext,
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

    def combat_plan(self, context: CombatContext):
        plan = self.plan()
        ultimate = self.click_ultimate_action(plan)
        skill = self.click_skill_action(plan)
        if self.ultimate_available():
            from src.char.Zankou import Zankou

            if self.get_teammate_by_class(Zankou)[0] is not None:
                plan.add(FieldClaim.high(reason="Daffodill ultimate ready with Zankou teammate"))

        def entry():
            if (yield ultimate):
                self._perform_burst(context, skill)
                return
            yield skill

        plan.entry = entry
        return plan

    def _perform_burst(self, context: CombatContext, skill: ActionIntent):
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
            if not skill_used and context.is_action_allowed(self, skill):
                skill_used = self.click_skill()
            self.normal_attack()
            self.sleep(0.2)

        self.logger.info(f"burst end (skill used={skill_used})")
