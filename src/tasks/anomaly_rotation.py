"""Stamina-based round robin, independent of game input and configuration storage."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RotationTarget:
    option: str
    task_type: str
    task_id: int
    cost: int
    quota: int

    def __post_init__(self):
        if not isinstance(self.option, str) or not self.option:
            raise ValueError("Invalid rotation target")
        if not isinstance(self.task_type, str) or not self.task_type:
            raise ValueError("Invalid rotation target")
        if type(self.task_id) is not int or self.task_id < 1:
            raise ValueError("Invalid target ID")
        if type(self.cost) is not int or self.cost <= 0:
            raise ValueError("Invalid reward cost")
        if type(self.quota) is not int or self.quota <= 0 or self.quota % self.cost:
            raise ValueError("Quota must contain whole rewards")


def rounded_quota(minimum: int, cost: int) -> int:
    """Zero means one double claim; otherwise meet the minimum in whole rewards."""
    if type(minimum) is not int or minimum < 0 or type(cost) is not int or cost <= 0:
        raise ValueError("Invalid stamina quota")
    return ((minimum + cost - 1) // cost) * cost if minimum else cost * 2


class StaminaRotation:
    def __init__(self, targets, saved=None, initial_option=None):
        self.targets = tuple(targets)
        if not self.targets:
            raise ValueError("轮换序列不能为空")
        if len({target.option for target in self.targets}) != len(self.targets):
            raise ValueError("轮换序列不能包含重复目标")
        self.signature = [[target.option, target.cost, target.quota] for target in self.targets]
        self.index = next(
            (index for index, target in enumerate(self.targets) if target.option == initial_option),
            0,
        )
        self.spent = 0
        if isinstance(saved, dict) and saved.get("plan") == self.signature:
            index, spent = saved.get("index"), saved.get("spent")
            if type(index) is int and 0 <= index < len(self.targets):
                target = self.targets[index]
                if type(spent) is int and 0 <= spent < target.quota and spent % target.cost == 0:
                    self.index, self.spent = index, spent

    @property
    def current(self):
        return self.targets[self.index]

    @property
    def remaining(self):
        return self.current.quota - self.spent

    def record_claim(self, units):
        if type(units) is not int or units not in (1, 2):
            raise ValueError("Invalid reward units")
        amount = units * self.current.cost
        if amount > self.remaining:
            raise ValueError("Claim exceeds the current target quota")
        self.spent += amount
        if self.spent == self.current.quota:
            self.index = (self.index + 1) % len(self.targets)
            self.spent = 0

    def snapshot(self):
        return {
            "plan": [row.copy() for row in self.signature],
            "index": self.index,
            "spent": self.spent,
        }
