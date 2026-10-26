from typing import Callable

from typing_extensions import deprecated

from src.char.BaseChar import BaseChar
from src.combat.planner import CombatPlan, Planner, RoleProfile


class Support(BaseChar):
    def describe_role(self):
        return RoleProfile(
            role=Planner.Role.SUPPORT,
            max_field_time=1,
            combat_start_priority=1,
        )

    def register_ultimate(
        self,
        plan: CombatPlan,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "ultimate action available",
        can_execute: Callable[[], bool] | None = None,
        priority_ready: Callable[[], bool] | None = None,
    ) -> None:
        """登记支持角色的 Q, 默认附加 ULTIMATE_ACTION 和 SUPPORT 标签。"""

        super().register_ultimate(
            plan,
            name=name,
            tags={Planner.ActionTag.ULTIMATE_ACTION, Planner.ActionTag.SUPPORT}
            if tags is None
            else tags,
            add_tags=add_tags,
            reason=reason,
            can_execute=can_execute,
            priority_ready=priority_ready,
        )

    def register_skill(
        self,
        plan: CombatPlan,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "skill action available",
        can_execute: Callable[[], bool] | None = None,
        priority_ready: Callable[[], bool] | None = None,
    ) -> None:
        """登记支持角色的 E, 默认附加 SKILL_ACTION 和 SUPPORT 标签。"""

        super().register_skill(
            plan,
            name=name,
            tags={Planner.ActionTag.SKILL_ACTION, Planner.ActionTag.SUPPORT}
            if tags is None
            else tags,
            add_tags=add_tags,
            reason=reason,
            can_execute=can_execute,
            priority_ready=priority_ready,
        )

    @deprecated("Use self.register_ultimate(plan, ...)", category=None)
    def click_ultimate_action(
        self,
        plan: CombatPlan | None = None,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "ultimate action available",
        can_execute=None,
    ):
        """声明支持角色的 Q 动作, 默认附加 `ULTIMATE_ACTION` 和 `SUPPORT` 标签。

        其余参数及执行行为与 `BaseChar.click_ultimate_action` 相同。
        """
        tags = tags or {Planner.ActionTag.ULTIMATE_ACTION, Planner.ActionTag.SUPPORT}
        return super().click_ultimate_action(
            plan,
            name=name,
            tags=tags,
            add_tags=add_tags,
            reason=reason,
            can_execute=can_execute,
        )

    @deprecated("Use self.register_skill(plan, ...)", category=None)
    def click_skill_action(
        self,
        plan: CombatPlan | None = None,
        *,
        name: str | None = None,
        tags: set[Planner.ActionTag] | None = None,
        add_tags: set[Planner.ActionTag] | Planner.ActionTag | None = None,
        reason: str = "skill action available",
        can_execute=None,
    ):
        """声明支持角色的 E 动作, 默认附加 `SKILL_ACTION` 和 `SUPPORT` 标签。

        其余参数及执行行为与 `BaseChar.click_skill_action` 相同。
        """
        tags = tags or {Planner.ActionTag.SKILL_ACTION, Planner.ActionTag.SUPPORT}
        return super().click_skill_action(
            plan,
            name=name,
            tags=tags,
            add_tags=add_tags,
            reason=reason,
            can_execute=can_execute,
        )
