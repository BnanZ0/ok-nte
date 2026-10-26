from src.char.BaseChar import BaseChar
from src.combat.planner import (
    FieldClaim,
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

    def combat_plan(self):
        plan = self.plan()
        self.register_skill(
            plan,
            add_tags=Planner.ActionTag.HIGH_PRIORITY,
            can_execute=self._can_use_skill,
            priority_ready=self.should_instant_cycle,
        )
        self.register_ultimate(plan)

        if self._should_return_from_ult():
            plan.add(FieldClaim.low(reason="in ultimate"))

        plan.entry = self.perform_entry
        return plan

    def _can_use_skill(self):
        return bool(self.in_ult) or self.should_instant_cycle()

    def perform_entry(self):
        if self.in_ult is None:
            self.in_ult = bool(
                self.task.wait_until(
                    lambda: self.task.find_one(Labels.blackbird_ult_2),
                    post_action=self.click_with_interval,
                    time_out=0.5,
                )
            )

        if self.in_ult:
            self.click_ultimate()
            self.in_ult = False

        if self.click_skill() and not self.claim_after_skill:
            self.claim_after_skill = True
            return

        self.claim_after_skill = False

        if self.click_ultimate():
            self.in_ult = True
            self.perform_in_ult()
            self.combat_context.request_route(
                self.get_teammates_by_role(Planner.Role.MAIN_DPS),
                reason="Blackbird request dps",
            )

    def _should_return_from_ult(self):
        if not self.in_ult:
            return False
        return True

    def perform_in_ult(self):
        self.logger.info("start perform_in_ult")
        timer = self.clock.timer(timeout=self.ULT_DURATION)
        skill_count = 0
        while not timer.expired():
            if timer.elapsed() > 1 and not self.ultimate_available(False):
                break
            if skill_count < self.ENH_SKILL_COUNT and self.click_skill():
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

    def click_skill(
        self,
        down_time: float = 0.05,
        post_sleep: float = 0,
        send_click: bool = True,
        time_out: float = 0,
    ) -> bool:
        ret = super().click_skill(down_time, post_sleep, send_click, time_out)
        if not self.in_ult and ret:
            self.task.wait_until(self.is_cycle_full, time_out=1)
        return ret
