# pyright: reportCallIssue=error, reportArgumentType=error
# pyright: reportUnnecessaryTypeIgnoreComment=error
"""Static checks for planner helper signatures and public profile types."""

from typing import assert_type

from src.char.BaseChar import BaseChar
from src.char.Support import Support
from src.combat.planner import (
    ActionResult,
    ActionTag,
    CombatContext,
    FieldPreference,
    Role,
    RoleProfile,
)


def check_role_profile_types() -> None:
    profile = RoleProfile(role=Role.MAIN_DPS)
    assert_type(profile.field_preference, FieldPreference)


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
