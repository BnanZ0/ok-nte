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


# 兔子洞六个空幕节点；下标 +1 就是异象界域的「空幕序号」。
# 前四个来自实拍（养成指南四行 + 卡带详情「获取途径」），后两个来自攻略站对照表，
# 名称只用于日志/报告，真正决定去向的是下标，因此不影响进本。
CONSOLE_DUNGEONS = (
    "钟表把戏",
    "雕塑展馆",
    "纬线织机",
    "守卫萝卜",
    "心智图谱",
    "轨道之夜",
)

# 空幕套装 → 兔子洞节点。来源是游戏内卡带详情页的「获取途径」= 兔子洞·<节点名>。
# 养成指南「空幕」页只写套装名（如「音速蓝刺猬」x4），不写节点，只能靠这张表反查。
# 同一套装在攻略站/旧译里有别名，一并收录：别名认不出就等于这轮白不刷。
CONSOLE_SETS = {
    # 钟表把戏
    "失落光芒": "钟表把戏",
    "迪亚波罗斯": "钟表把戏",
    # 雕塑展馆
    "恶魔之血：诅咒": "雕塑展馆",
    "街头拳王": "雕塑展馆",
    # 纬线织机
    "森林萤火之心": "纬线织机",
    "真红：双生蝶": "纬线织机",
    # 守卫萝卜
    "缇娅的夜间酒馆": "守卫萝卜",
    "西雅的夜酒馆": "守卫萝卜",
    "守卫王国": "守卫萝卜",
    "王国卫士": "守卫萝卜",
    # 心智图谱
    "音速蓝刺猬": "心智图谱",
    "静谧山庄": "心智图谱",
    "宁静庄园": "心智图谱",
    # 轨道之夜
    "影之信条": "轨道之夜",
    "小小大冒险": "轨道之夜",
    "微小的大冒险": "轨道之夜",
}

_NON_WORD_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]")


def _name_key(text: str) -> str:
    """去掉「」、空格、x4 后缀这类噪音，只留字词本身。"""
    return _NON_WORD_RE.sub("", text or "")


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
        """够不够按「折成最低档的总件数」判 —— 合成与拆解都是 3:1, 档位只是同一材料的形态。"""
        if self.ratios is None:
            return None
        owned, required = lowest_units(self.ratios)
        return owned >= required

    @property
    def label(self) -> str:
        return f"{self.tab}-第{self.row_index + 1}行"


def row_gap(row: MaterialRow) -> int | None:
    """这一行还差多少件**最低档**材料（各档按 3:1 统一折算）；读数无效返回 None。"""
    if row.ratios is None:
        return None
    owned, required = lowest_units(row.ratios)
    return max(0, required - owned)


def diff_ownage(prev, curr) -> int | None:
    """两轮间的拥有量增量, 折成最低档件数；槽位数变化返回 None。"""
    if not prev or not curr or len(prev) != len(curr):
        return None
    return sum(
        max(0, new.owned - old.owned) * COMBINE_RATIO ** index
        for index, (old, new) in enumerate(zip(prev, curr))
    )


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


def lowest_units(ratios, ratio: int = COMBINE_RATIO) -> tuple[int, int]:
    """把「拥有 / 需求」都折成最低档件数, 返回 (拥有, 需求)。

    ratios 顺序与界面一致（左→右 = 低档→高档）, 第 i 档 1 件 = 最低档 ratio**i 件。
    游戏里合成(3 低→1 高)和拆解(1 高→3 低)都允许, 所以档位只是同一材料的形态,
    只需比总量: `48/8、32/40、16/13` = 288 件 vs 245 件, 已经够了, 不该再刷。
    反过来 `0/15、60/0` 里高档多出来的能拆下来补低档, 也算够 —— 逐档比会误判成缺 15 件。
    """
    owned = sum(max(0, int(r.owned)) * ratio ** index for index, r in enumerate(ratios))
    required = sum(max(0, int(r.required)) * ratio ** index for index, r in enumerate(ratios))
    return owned, required


def claims_needed(gap: int | None, per_claim: float | None, tiers: int = 1) -> float | None:
    """把「最低档缺口」换成还差几份副本产出; 没有产出记录时按「一份≈1 件最高档」估。"""
    if gap is None:
        return None
    unit = per_claim if per_claim and per_claim > 0 else float(COMBINE_RATIO ** max(0, tiers - 1))
    return gap / unit



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
        weekly_known: bool = False,
    ) -> PlanState:
        return PlanState(
            stamina=self.stamina,
            weekly_remaining=weekly_remaining,
            rows=self.rows,
            rounds_done=rounds_done,
            weekly_blocked=weekly_blocked,
            weekly_known=weekly_known,
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
    # 周本次数是否真的读过面板。关掉「周本刷取」时 remaining 恒 0 但含义是未知,
    # 不能据此判定「次数用完」, 否则异能第2行缺料时会被误跳过。
    weekly_known: bool = False
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
    """周本优先 → 未达标材料 → 空幕（优先级最低、无次数上限）→ 体力不够下一次则停。

    空幕排在最后不是因为它差, 而是因为它**没有次数上限**: 只有它能在「所有材料都够、
    或剩下的行都已经推不动」时继续把体力花掉, 所以它必须永远垫底, 用来兜住
    「有体力却没副本可打」的情况。
    """
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

    # 周本次数用完 = 这一行本周已达上限, 不能再算「未达标」。
    # 否则它永远排在 pending 首位, 每轮都要白跑一次巡礼才跳过, 空幕兜底根本到不了。
    # 只在真的读过面板时才这样判: 关掉「周本刷取」时 remaining 恒 0 但那是未知。
    exhausted = {
        row.label
        for row in state.rows
        if row.landing == "weekly" and state.weekly_known and state.weekly_remaining <= 0
    }
    pending = [
        row
        for row in state.rows
        if row.satisfied is False
        and row.label not in state.skip_labels
        and row.label not in exhausted
    ]
    for row in pending:
        if stamina >= row.cost:
            return Decision(Action.MATERIAL, f"{row.label} 未达标", row=row, cost=row.cost)
    if pending:
        cheapest = min(row.cost for row in pending)
        return Decision(Action.STOP, f"体力 {stamina} 不够剩余材料副本 ({cheapest})")
    unfinished = [
        row.label for row in state.rows if row.satisfied is False and row.label not in exhausted
    ]
    if unfinished:
        return Decision(Action.STOP, f"剩余材料行本次均已失败: {', '.join(unfinished)}")

    if stamina >= cfg.console_cost:
        if exhausted:
            reason = f"{', '.join(sorted(exhausted))} 缺料但周本次数已用完, 交给空幕消耗体力"
        else:
            reason = "培养目标材料全满, 交给空幕消耗体力"
        return Decision(Action.CONSOLE, reason, cost=cfg.console_cost)
    return Decision(Action.STOP, f"体力 {stamina} 不够空幕 ({cfg.console_cost})")


def next_weekly_remaining(state: PlanState, claimed: bool) -> int:
    """只有确认领奖成功才扣次数，避免把假成功记成已跑。"""
    if not claimed or state.weekly_remaining <= 0:
        return state.weekly_remaining
    return state.weekly_remaining - 1


def match_console_set(text: str) -> tuple[str, str] | None:
    """在 OCR 文本里认收录的空幕套装，返回 (关卡, 命中套装名)。

    套装名带「」、后面跟 x4，冒号有全半角差异，统一去噪后做包含匹配；
    命中多个取最长 —— 「恶魔之血」这种前半段也能命中，不能让短串抢先。
    冒号后半段常被 OCR 吞掉，再补一次「冒号前」的宽松匹配兜底。
    """
    plain = _name_key(text)
    if not plain:
        return None
    hits = []
    for set_name, dungeon in CONSOLE_SETS.items():
        key = _name_key(set_name)
        if key and key in plain:
            hits.append((len(key), dungeon, set_name))
    if not hits:
        for set_name, dungeon in CONSOLE_SETS.items():
            head = _name_key(re.split(r"[：:]", set_name)[0])
            if len(head) >= 4 and head in plain:
                hits.append((len(head), dungeon, set_name))
    if not hits:
        return None
    _, dungeon, set_name = max(hits)
    return dungeon, set_name


def console_index_for(names: Iterable[str]) -> tuple[int | None, str]:
    """按「推荐套装 → 当前套装」顺序认关卡，返回 (0 基序号, 说明)。

    推荐套装优先（角色该穿的那套），当前套装兜底（已穿对时也照常刷）。
    都认不出就返回 None —— 宁可这轮不刷空幕，也不按 1 号盲刷白花 40 体力。
    """
    labels = ("推荐套装", "当前套装")
    texts = list(names)[: len(labels)]
    for label, text in zip(labels, texts):
        hit = match_console_set(text)
        if hit:
            dungeon, set_name = hit
            index = CONSOLE_DUNGEONS.index(dungeon)
            return index, f"{label}「{set_name}」→ {dungeon} (序号 {index + 1})"
    shown = " / ".join(f"{label}={text or '空'}" for label, text in zip(labels, texts))
    return None, f"套装名未收录({shown})"


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
