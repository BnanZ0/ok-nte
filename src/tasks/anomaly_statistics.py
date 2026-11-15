"""Confirmed reward-claim counts, distinct from material drop quantities."""

from copy import deepcopy


class FarmingStatistics:
    def __init__(self, saved=None):
        self.targets = {}
        if not isinstance(saved, dict):
            return
        for target, counts in saved.items():
            if not isinstance(target, str) or not isinstance(counts, dict):
                continue
            if all(
                type(counts.get(key)) is int and counts[key] >= 0
                for key in ("single", "double", "stamina")
            ):
                self.targets[target] = {key: counts[key] for key in ("single", "double", "stamina")}

    def record(self, target, units, cost):
        if type(units) is not int or units not in (1, 2) or type(cost) is not int or cost <= 0:
            raise ValueError("Invalid confirmed claim")
        counts = self.targets.setdefault(target, {"single": 0, "double": 0, "stamina": 0})
        counts["double" if units == 2 else "single"] += 1
        counts["stamina"] += cost * units

    @property
    def stamina(self):
        return sum(counts["stamina"] for counts in self.targets.values())

    def snapshot(self):
        return deepcopy(self.targets)
