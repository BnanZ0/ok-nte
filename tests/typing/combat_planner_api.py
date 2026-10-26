# pyright: reportCallIssue=error, reportArgumentType=error
# pyright: reportUnnecessaryTypeIgnoreComment=error
"""Static checks for planner helper signatures and public profile types."""

from typing import assert_type, override

from src.char.BaseChar import BaseChar
from src.char.Support import Support
from src.combat.planner import (
    ActionResult,
    ActionTag,
    CombatContext,
    CombatPlan,
    CombatPlanner,
    CombatWindow,
    FieldPreference,
    Planner,
    Role,
    RoleProfile,
    SwitchDecision,
    SwitchInGuard,
)


class ModernPlannerChar(BaseChar):
    @override
    def combat_plan(self) -> CombatPlan:
        return super().combat_plan()

    @override
    def combat_policies(self) -> None:
        super().combat_policies()

    @override
    def switch_in_guard(self, from_char: BaseChar, has_intro: bool) -> SwitchInGuard:
        return super().switch_in_guard(from_char, has_intro)


def check_planner_boundaries(planner: CombatPlanner, char: BaseChar) -> None:
    assert_type(planner.context_for(char), CombatContext)
    assert_type(planner.decide_switch(current_char=char), SwitchDecision)
    assert_type(planner.perform_current_char(current_char=char), ActionResult | None)


def check_role_profile_types() -> None:
    profile = RoleProfile(role=Role.MAIN_DPS)
    assert_type(profile.field_preference, FieldPreference)


def check_registered_method_signatures(char: BaseChar, teammate: BaseChar) -> None:
    plan = char.plan()
    assert_type(char.register_skill(plan, can_execute=char.should_instant_cycle), None)
    assert_type(char.register_ultimate(plan), None)
    assert_type(char.register_skill(plan, can_execute=lambda: True), None)
    assert_type(char.click_skill(down_time=0.3, post_sleep=0.2), bool)
    assert_type(char.click_ultimate(send_click=False), bool)
    assert_type(char.combat_context, CombatContext)
    char.combat_context.request_route([teammate.click_skill, char], timeout=5)
    char.combat_context.request_route(
        [
            Planner.Route.optional(char.click_ultimate),
            char.click_skill,
            Planner.Route.action(teammate, slot=Planner.ActionSlot.SKILL),
            Planner.Route.action(teammate, tags={ActionTag.TEAM_BUFF}, optional=True),
            Planner.Route.reaction(char, teammate, optional=False),
        ]
    )
    char.click_skill(down_time="invalid")  # pyright: ignore[reportArgumentType]
    char.click_ultimate(send_clik=False)  # pyright: ignore[reportCallIssue]
    assert_type(Planner.Route.action(char, slot=Planner.ActionSlot.SKILL), Planner.Route)
    Planner.Route.action(char, slot="skill")  # pyright: ignore[reportArgumentType]
    Planner.Route.action(char, tags={"team_buff"})  # pyright: ignore[reportArgumentType]
    Planner.Route.action(char, slot=Planner.ActionSlot.SKILL, optional="yes")  # pyright: ignore[reportArgumentType]
    Planner.Route.reaction(char, teammate, optional="yes")  # pyright: ignore[reportArgumentType]
    char.register_skill(plan, can_execute=lambda context: True)  # pyright: ignore[reportArgumentType]


def check_window_and_reservation_signatures(char: BaseChar, teammate: BaseChar) -> None:
    context = char.combat_context
    window = context.open_window(until=lambda: False)
    assert_type(window, CombatWindow)
    route = window.request_route([Planner.Route.optional(teammate.click_ultimate), teammate])
    hold = window.reserve_actions([Planner.Reserve.action(teammate, slot=Planner.ActionSlot.SKILL)])
    if route is not None:
        route.on_fulfilled(lambda: None)
    if hold is not None:
        assert_type(hold.is_pending, bool)
    assert_type(Planner.Reserve.action(char, slot=Planner.ActionSlot.SKILL), Planner.Reserve)
    Planner.Reserve.action(char, slot="skill")  # pyright: ignore[reportArgumentType]


def check_action_signatures(char: BaseChar, context: CombatContext) -> None:
    plan = char.plan()
    skill = char.click_skill_action()
    ultimate = char.click_ultimate_action()
    assert_type(skill(down_time=0.3, post_sleep=0).run(context), ActionResult)
    assert_type(ultimate(send_click=False, wait_if_no_cd=0.5).run(context), ActionResult)
    assert_type(ultimate(send_click=False).repeat_for_entry().run(context), ActionResult)

    skill = plan.add(skill(down_time=0.3, post_sleep=0.2))
    ultimate = plan.add(ultimate(send_click=False))
    assert_type(skill.run(context), ActionResult)
    assert_type(skill(down_time=0.1).run(context), ActionResult)
    assert_type(ultimate(wait_if_no_cd=0.5).run(context), ActionResult)
    assert_type(skill.repeat_for_entry()(down_time=0.1).run(context), ActionResult)

    char.click_skill_action(plan, down_time=0.3)  # pyright: ignore[reportCallIssue]
    char.click_skill_action(plan, execute=lambda _: True)  # pyright: ignore[reportCallIssue]
    char.click_ultimate_action(plan, execute=lambda _: True)  # pyright: ignore[reportCallIssue]
    ultimate(send_clik=False)  # pyright: ignore[reportCallIssue]
    ultimate(send_click="invalid")  # pyright: ignore[reportArgumentType]
    skill(down_time="invalid")  # pyright: ignore[reportArgumentType]
    skill(slot="custom")  # pyright: ignore[reportCallIssue]

    def perform(context: CombatContext, *, duration: float = 0.5, send_click: bool = True) -> bool:
        return duration > 0 and send_click

    burst = char.planner_action(tags=ActionTag.DAMAGE, execute=perform)
    burst = plan.add(burst(duration=0.8, send_click=False))
    assert_type(burst(duration=0.1).repeat_for_entry().run(context), ActionResult)
    assert_type(burst.repeat_for_entry()(send_click=True).run(context), ActionResult)
    burst(unknown=0.8)  # pyright: ignore[reportCallIssue]
    burst(duration="invalid")  # pyright: ignore[reportArgumentType]


def check_support_signatures(char: Support, context: CombatContext) -> None:
    plan = char.plan()
    skill = char.click_skill_action()
    ultimate = char.click_ultimate_action()
    skill = plan.add(skill(down_time=0.3))
    ultimate = plan.add(ultimate(send_click=False))
    assert_type(skill(post_sleep=0.2).run(context), ActionResult)
    assert_type(ultimate(wait_if_no_cd=0.5).run(context), ActionResult)
    skill(down_time="invalid")  # pyright: ignore[reportArgumentType]
    ultimate(send_clik=False)  # pyright: ignore[reportCallIssue]
    char.click_skill_action(plan, execute=lambda _: True)  # pyright: ignore[reportCallIssue]
    char.click_ultimate_action(plan, execute=lambda _: True)  # pyright: ignore[reportCallIssue]


def check_support_registration_signatures(char: Support) -> None:
    plan = char.plan()
    assert_type(char.register_skill(plan, add_tags=ActionTag.HIGH_PRIORITY), None)
    assert_type(char.register_ultimate(plan, tags=set()), None)
    assert_type(char.register_arc(plan), None)
    assert_type(char.click_skill(down_time=0.3), bool)
