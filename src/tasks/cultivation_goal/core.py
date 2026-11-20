"""培养目标刷体力的决策内核，不依赖 ok 框架，可离线单测。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import Enum

# 占有量词(3.11+)防止数字段回溯, 长数字串下运行时间保持线性。
RATIO_RE = re.compile(r"(?<![\d/])(\d{1,6}+)\s*/\s*(\d{1,6}+)(?![\d/])")
# 链式写法 `10 / 10 / 20` 不是槽位比例, 先拦掉再提取。
CHAIN_RATIO_RE = re.compile(r"\d++\s*/\s*\d++\s*/\s*\d++")
WEEKLY_RE = re.compile(r"(?<![\d.+-])([0-3])\s*/\s*3(?![\d.])")


@dataclass(frozen=True)
class Ratio:
    owned: int
    required: int
    # 由单串猜出来的比例(如 3150→3/50)标低置信, 规划前要复核; 相等比较不参与。
    guessed: bool = field(default=False, compare=False)

    @property
    def satisfied(self) -> bool:
        return self.owned >= self.required


def repair_swallowed_slash(text: str) -> tuple[Ratio, ...] | None:
    """修复 OCR 把 `/` 认成 `1`（如 `47/60`→`47160`）。

    只接受整串恰有一个 1、两段非空且拥有≤需要的唯一解, 否则返回 None。
    两段都至少两位才视为可信(47160 → 47/60); 一边只有一位(7136、3150)切法
    不唯一, 标 guessed 交给调用方复核, 不当成缺料直接规划。
    """
    digits = unicodedata.normalize("NFKC", text or "").strip()
    if not digits.isdigit() or digits.count("1") != 1:
        return None
    cut = digits.index("1")
    left, right = digits[:cut], digits[cut + 1:]
    if not left or not right or int(left) > int(right):
        return None
    guessed = len(left) < 2 or len(right) < 2
    return (Ratio(int(left), int(right), guessed=guessed),)


def parse_ratios(text: str) -> tuple[Ratio, ...] | None:
    """从一行 OCR 文本抽 xx/xx；读不到返回 None，调用方必须据此停下。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    if CHAIN_RATIO_RE.search(normalized):
        return None
    found = tuple(Ratio(int(left), int(right)) for left, right in RATIO_RE.findall(normalized))
    return found or repair_swallowed_slash(normalized)


def parse_weekly_remaining(text: str) -> int | None:
    """读周本 n/3 剩余次数；OCR 出现多个不同数字时视为不确定。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    counts = {int(match.group(1)) for match in WEEKLY_RE.finditer(normalized)}
    return counts.pop() if len(counts) == 1 else None


class Action(str, Enum):
    WEEKLY = "weekly"
    MATERIAL = "material"
    CONSOLE = "console"
    STOP = "stop"


@dataclass(frozen=True)
class MaterialRow:
    tab: str
    row_index: int
    cost: int
    landing: str = "domain"
    ratios: tuple[Ratio, ...] | None = None

    @property
    def satisfied(self) -> bool | None:
        if self.ratios is None:
            return None
        return all(ratio.satisfied for ratio in self.ratios)

    @property
    def label(self) -> str:
        return f"{self.tab}-第{self.row_index + 1}行"


def row_gap(row: MaterialRow) -> int | None:
    """这一行还差多少材料：各材料缺口之和；读数无效返回 None。"""
    if row.ratios is None:
        return None
    return sum(max(0, ratio.required - ratio.owned) for ratio in row.ratios)


def diff_ownage(prev, curr) -> int | None:
    """两轮间的拥有量增量；槽位数变化返回 None。"""
    if not prev or not curr or len(prev) != len(curr):
        return None
    return sum(max(0, new.owned - old.owned) for old, new in zip(prev, curr))


def estimate_runs(gap: int | None, gain: int | None) -> int | None:
    """按上一局实际产出估算还差几局；没产出记录或产出为 0 时不可估。"""
    if gap is None or gain is None or gap <= 0 or gain <= 0:
        return None
    return -(-gap // gain)


def plan_burst(
    gap: int | None,
    per_claim: float | None,
    stamina: int,
    cost: int,
    limit: int,
    safety: int = 1,
) -> int:
    """本轮最多连打几局；只在有产出且缺口明显大于单局产出时连打，留 safety 份回页确认。"""
    if gap is None or per_claim is None:
        return 0
    if gap <= 0 or per_claim < 1 or cost <= 0 or limit <= 0 or stamina < cost:
        return 0
    by_gap = int(max(0, gap - safety) // per_claim)
    return max(0, min(limit, by_gap, stamina // cost))


COMBINE_RATIO = 3


def fold_up(tiers, ratio: int = COMBINE_RATIO) -> int:
    """多档材料折算为最高档等价数量；tiers 从高到低排列（紫,蓝,绿）。

    从最低档逐级 3:1 合并进位，余量不足一个合成单位就留在本档不计数：
    (0,2,3)→1（3绿合1蓝、2+1=3蓝合1紫），(3,6,9)→6，(0,2,0)→0，(0,0,8)→0。
    """
    values = [max(0, int(v)) for v in (tiers or ())]
    for index in range(len(values) - 1, 0, -1):
        values[index - 1] += values[index] // ratio
    return values[0] if values else 0


def effective_gap(row: MaterialRow, extras=None) -> int | None:
    """考虑 extras（按槽位的额外可用量）后的缺口，无 extras 时等于 row_gap。"""
    if row.ratios is None:
        return None
    gap = 0
    for index, ratio in enumerate(row.ratios):
        extra = 0
        if extras and index < len(extras):
            extra = max(0, int(extras[index] or 0))
        gap += max(0, ratio.required - ratio.owned - extra)
    return gap



class LootLedger:
    """开局读一次面板，之后靠掉落增量维护的内存账本。"""

    def __init__(self, stamina: int, rows: tuple[MaterialRow, ...]):
        self.stamina = int(stamina)
        self.rows = tuple(rows)
        self._owned = {
            row.label: None if row.ratios is None else tuple(r.owned for r in row.ratios)
            for row in self.rows
        }
        # 各行累计掉落, 供掉落统计使用。
        self.dropped = {
            row.label: tuple(0 for _ in row.ratios) if row.ratios is not None else ()
            for row in self.rows
        }

    def apply_loot(self, label: str, counts) -> bool:
        """counts 按行内材料槽位顺序给出增量; 槽位数对不上就拒绝, 不猜。"""
        owned = self._owned.get(label)
        if owned is None or counts is None or len(owned) != len(counts):
            return False
        updated = tuple(max(0, int(value) + int(extra)) for value, extra in zip(owned, counts))
        self._owned[label] = updated
        self.dropped[label] = tuple(
            got + max(0, int(extra)) for got, extra in zip(self.dropped.get(label) or (), counts)
        )
        new_rows = []
        for row in self.rows:
            if row.label == label and row.ratios is not None:
                ratios = tuple(
                    Ratio(value, ratio.required) for value, ratio in zip(updated, row.ratios)
                )
                new_rows.append(replace(row, ratios=ratios))
            else:
                new_rows.append(row)
        self.rows = tuple(new_rows)
        return True

    def spend(self, cost: int):
        self.stamina = max(0, self.stamina - max(0, int(cost)))

    def as_state(
        self,
        rounds_done: int,
        weekly_remaining: int,
        weekly_blocked: bool,
        skip_labels,
    ) -> PlanState:
        return PlanState(
            stamina=self.stamina,
            weekly_remaining=weekly_remaining,
            rows=self.rows,
            rounds_done=rounds_done,
            weekly_blocked=weekly_blocked,
            skip_labels=tuple(skip_labels),
        )


@dataclass(frozen=True)
class PlanConfig:
    weekly_cost: int = 60
    console_cost: int = 40
    weekly_limit: int = 3
    max_rounds: int = 30
    prefer_weekly: bool = True
    daily_target: int | None = None


@dataclass(frozen=True)
class PlanState:
    stamina: int
    weekly_remaining: int
    rows: tuple[MaterialRow, ...] = ()
    rounds_done: int = 0
    weekly_blocked: bool = False
    read_failed: bool = False
    # 本次已失败的行，不重复尝试。
    skip_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    row: MaterialRow | None = None
    cost: int = 0


def effective_stamina(current: int, daily_target: int | None) -> int:
    """今日剩余目标与当前体力取小，两者都不是硬编码 180。"""
    if current < 0:
        return -1
    if daily_target is None:
        return current
    return max(0, min(current, daily_target))


def decide(state: PlanState, cfg: PlanConfig | None = None) -> Decision:
    """周本优先 → 未达标材料 → 全满刷空幕 → 体力不够下一次则停。"""
    cfg = cfg or PlanConfig()
    stamina = effective_stamina(state.stamina, cfg.daily_target)

    if stamina < 0:
        return Decision(Action.STOP, "体力读数无效")
    if state.read_failed:
        return Decision(Action.STOP, "材料页读取失败, fail-closed")
    if state.rounds_done >= cfg.max_rounds:
        return Decision(Action.STOP, "达到最大循环轮次")

    costs = [cfg.console_cost, cfg.weekly_cost, *(row.cost for row in state.rows)]
    if stamina < min(costs):
        return Decision(Action.STOP, f"体力 {stamina} 不够任何一次 ({min(costs)})")

    if (
        cfg.prefer_weekly
        and not state.weekly_blocked
        and state.weekly_remaining > 0
        and stamina >= cfg.weekly_cost
    ):
        return Decision(
            Action.WEEKLY,
            f"周本本周剩余 {state.weekly_remaining}/{cfg.weekly_limit} 次",
            cost=cfg.weekly_cost,
        )

    uncertain = [row for row in state.rows if row.satisfied is None]
    if uncertain:
        names = ", ".join(row.label for row in uncertain)
        return Decision(Action.STOP, f"材料行读不出: {names}")

    # 周本行按缺口驱动(设计基准: 即使周本次数/读数不可用, 缺料仍尝试巡礼,
    # 领奖确认失败后由 skip 兜底), 不在这里排除。
    pending = [
        row
        for row in state.rows
        if row.satisfied is False and row.label not in state.skip_labels
    ]
    for row in pending:
        if stamina >= row.cost:
            return Decision(Action.MATERIAL, f"{row.label} 未达标", row=row, cost=row.cost)
    if pending:
        cheapest = min(row.cost for row in pending)
        return Decision(Action.STOP, f"体力 {stamina} 不够剩余材料副本 ({cheapest})")
    if any(row.satisfied is False for row in state.rows):
        skipped = ", ".join(row.label for row in state.rows if row.satisfied is False)
        return Decision(Action.STOP, f"剩余材料行本次均已失败: {skipped}")

    if stamina >= cfg.console_cost:
        return Decision(Action.CONSOLE, "培养目标材料全满, 刷空幕", cost=cfg.console_cost)
    return Decision(Action.STOP, f"体力 {stamina} 不够空幕 ({cfg.console_cost})")


def next_weekly_remaining(state: PlanState, claimed: bool) -> int:
    """只有确认领奖成功才扣次数，避免把假成功记成已跑。"""
    if not claimed or state.weekly_remaining <= 0:
        return state.weekly_remaining
    return state.weekly_remaining - 1


def map_reward_to_counts(
    items: Iterable[tuple[str, int]],
    slot_keys: Iterable[str],
) -> tuple[int, ...] | None:
    """把奖励卡 (材料 key, 数量) 摊到一行槽位；key 不在槽位里返回 None。"""
    slots = tuple(slot_keys)
    if not slots:
        return None
    counts = [0] * len(slots)
    matched = False
    for key, amount in items:
        try:
            index = slots.index(key)
        except ValueError:
            continue
        counts[index] += max(0, int(amount))
        matched = True
    return tuple(counts) if matched else None
