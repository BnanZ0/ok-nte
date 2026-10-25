from src.char.BaseChar import BaseChar
from src.combat.planner import (
    CombatContext,
    FieldClaim,
    FollowupStep,
    Planner,
    RoleProfile,
)
from src.Labels import Labels


class Blackbird(BaseChar):
    cn_name = "黑羽"
    element = BaseChar.ElementType.BLUE
    ULT_DURATION = 15
    ENH_SKILL_COUNT = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.in_ult = None
        self.claim_after_skill = False

    def describe_role(self):
        return RoleProfile(role=Planner.Role.SUB_DPS)

    def combat_plan(self, context):
        plan = self.plan()
        skill = self.click_skill_action(
            plan,
            add_tags=Planner.ActionTag.HIGH_PRIORITY,
            can_execute=self.should_instant_cycle,
        )
        ultimate = self.click_ultimate_action(plan)

        if self._should_return_from_ult():
            plan.add(FieldClaim.low(reason="in ultimate"))

        def entry():

            if self.in_ult is None:
                self.in_ult = bool(
                    self.task.wait_until(
                        lambda: self.task.find_one(Labels.blackbird_ult_2),
                        post_action=self.click_with_interval,
                        time_out=0.5,
                    )
                )

            if self.in_ult:
                yield ultimate
                self.in_ult = False

            if (yield skill) and not self.claim_after_skill:
                self.claim_after_skill = True
                return

            self.claim_after_skill = False

            if (yield ultimate.repeat_for_entry()):
                self.in_ult = True
                self.perform_in_ult(context)
                steps = [
                    FollowupStep.for_switch(char, reason="Blackbird request dps")
                    for char in self.get_teammates_by_role(Planner.Role.MAIN_DPS)
                ]
                if steps:
                    context.request_route(steps)

        plan.entry = entry
        return plan

    def _should_return_from_ult(self):
        if not self.in_ult:
            return False
        return True

    def perform_in_ult(self, context: CombatContext):
        self.logger.info("start perform_in_ult")
        start = self.clock.now()
        skill_count = 0
        while (elapsed := self.clock.now() - start) < self.ULT_DURATION:
            if elapsed > 1 and not self.ultimate_available(False):
                break
            if (
                skill_count < self.ENH_SKILL_COUNT
                and context.is_slot_available(self, Planner.ActionSlot.SKILL)
                and self.click_skill()
            ):
                skill_count += 1
            if skill_count >= self.ENH_SKILL_COUNT:
                break
            self.normal_attack()
            self.sleep(0.1)
        self.logger.info("end perform_in_ult")

    def reset_state(self):
        super().reset_state()
        self.in_ult = None
        self.claim_after_skill = False

    def on_combat_end(self, chars):
        super().on_combat_end(chars)
        self.in_ult = None
        self.claim_after_skill = False
        self.switch_other_char()

    @property
    def ultimate_freeze_wait(self):
        return 2 if self.in_ult else 2.6

    def click_skill(self, down_time = 0.05, post_sleep = 0, send_click = True, time_out = 0):
        ret = super().click_skill(down_time, post_sleep, send_click, time_out)
        if not self.in_ult and ret:
            self.task.wait_until(self.is_cycle_full, time_out=1)
        return ret
