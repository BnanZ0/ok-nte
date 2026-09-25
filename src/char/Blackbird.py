from src.char.BaseChar import BaseChar
from src.combat.planner import (
    ActionIntent,
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
    ULT_RETURN_LEAD_TIME = 4.0
    ENH_SKILL_COUNT = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.in_ult = None
        self.claim_after_skill = False
        self.no_dps_teammate = False

    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.SUB_DPS, field_preference=Planner.FieldPreference.SUB_DPS
        )

    def combat_plan(self, context):
        claims = []
        ultimate = self.click_ultimate_action()
        skill = self.click_skill_action(
            add_tags=Planner.ActionTag.HIGH_PRIORITY, can_execute=self.should_use_skill
        )

        if not self.claim_after_skill and self.skill_available():
            claims.append(FieldClaim.normal(reason="skill instant cycle"))

        if self.claim_after_skill and self.ultimate_available():
            claims.append(FieldClaim.normal(reason="skill succeeded"))

        if self.no_dps_teammate and self._should_return_from_ult():
            claims.append(FieldClaim.strict(reason="ultimate window ending"))

        def entry():
            self.claim_after_skill = False
            self.no_dps_teammate = False

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

            if (yield skill):
                self.claim_after_skill = True
                return

            if (yield ultimate.repeat_for_entry()):
                self.in_ult = True
                self.perform_in_ult(context, skill)
                dps_list = self.get_teammates_by_role(Planner.Role.MAIN_DPS)
                steps = []
                for char in dps_list:
                    steps.append(FollowupStep.for_switch(char, "Blackbird request dps"))
                if steps:
                    context.request_route(steps, return_to_source=True)
                else:
                    self.no_dps_teammate = True

        return self.plan(skill, ultimate, claims=claims, entry=entry)

    def should_use_skill(self, context: CombatContext = None):
        return (
            not self.has_element_reaction_teammate()
            or not self.is_cycle_full()
            or (
                context is not None
                and context.strict_route_wants_action(self, slot=Planner.ActionSlot.SKILL)
            )
        )

    def _should_return_from_ult(self):
        if not self.in_ult or self.last_ultimate_time <= 0:
            return False
        elapsed = self.time_elapsed_accounting_for_freeze(self.last_ultimate_time)
        return elapsed >= self.ULT_DURATION - self.ULT_RETURN_LEAD_TIME

    def perform_in_ult(self, context: CombatContext, skill: ActionIntent):
        self.logger.info("start perform_in_ult")
        start = self.now()
        skill_count = 0
        while (elapsed := self.now() - start) < self.ULT_DURATION:
            if elapsed > 1 and not self.ultimate_available(False):
                break
            if (
                skill_count < self.ENH_SKILL_COUNT
                and context.is_action_allowed(self, skill)
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
        self.no_dps_teammate = False

    def on_combat_end(self, chars):
        super().on_combat_end(chars)
        self.in_ult = None
        self.claim_after_skill = False
        self.no_dps_teammate = False
        self.switch_other_char()

    def _wait_ultimate_unfreeze(self, start, click=True):
        if self.in_ult:
            return super()._wait_ultimate_unfreeze(start, click)
        wait = 2.6
        if click:
            self.continues_normal_attack(wait)
        else:
            self.sleep(wait)
        duration = self.now() - start
        self.add_freeze_duration(start, duration)
        return duration
