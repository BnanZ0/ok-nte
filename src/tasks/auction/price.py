"""拍卖价格纯逻辑: OCR 数值解析, 每轮指定价格, 加价计算, 估价换算, 按键序列,
逐口追踪模式(参考脚本策略)。

本模块全部是无 IO 的纯函数: 输入配置值与原始 OCR 读数, 输出解析/计算结果,
不依赖任务实例、不读配置、不发日志。配置读取、OCR 副作用、告警与回退基础价
仍由 AutoBidAuctionTask 协调; 新增价格规则优先在这里表达, 便于直接单测。
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from src.tasks.auction.layout import FULLWIDTH_NUMERIC, PAD_SHORTCUTS, RE_INPUT_RANGE
from src.tasks.auction.options import (
    BID_MODE_ESTIMATE,
    BID_MODE_SMART,
    CONF_BID_PRICES,
    MAX_BID_ROUNDS,
    RAISE_MODE_MULTIPLE,
    RAISE_MODE_PERCENT,
)


def parse_asset_value(raw_text: str) -> int | None:
    """统一解析资产 OCR 文本, 返回整数或 None。

    处理流程:
    1. 全角数字与全角逗号转半角.
    2. 修正常见 OCR 错误 (O -> 0, l/I -> 1).
    3. 提取数字.
    4. 转换为 int, 失败时返回 None.
    """
    normalized = raw_text.translate(FULLWIDTH_NUMERIC)
    corrected = normalized.replace("l", "1").replace("I", "1").replace("O", "0")
    digits = re.sub(r"[^\d]", "", corrected)
    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


def parse_price_hint_cap(raw_text: str) -> int | None:
    """从「可输入范围0~N」提示文本解析输入上限 N, 返回整数或 None。

    N 是键盘未输入时游戏实时给出的可输入上限(即当前资产), 出价钳制用它作
    与资产框独立的复核读数源 (背景见 auction-notes 5.1)。取最后一个 ~ 后的
    数字组; 没有任何 ~(如输入框回显)或 ~ 后没有数字(~ 被 OCR 丢掉、前后
    数字粘连)时返回 None, 调用方按「提示不可用」回退, 不猜。
    """
    matches = RE_INPUT_RANGE.findall(raw_text)
    if not matches:
        return None
    return parse_asset_value(matches[-1])


def is_partial_number_text(raw_text: str) -> bool:
    """判断 OCR 文本是否为「首位数字被漏读」的残缺读数。

    估价按千位分隔显示, 逗号前面必须有数字; 首位数字在区域最左侧, OCR 对它的
    识别不稳定。把它当结果会按低一个数量级的价格出价 (案例见 auction-notes 2.1)。
    """
    normalized = raw_text.translate(FULLWIDTH_NUMERIC)
    digits_and_commas = re.sub(r"[^\d,]", "", normalized)
    return digits_and_commas.startswith(",")


def has_inconsistent_grouping(raw_text: str) -> bool:
    """判断带千位分隔符的读数是否「位数与逗号不自洽」。

    千位分隔的合法形式是首位分组 1~3 位、其余每组恰 3 位: `1,234` / `12,345` /
    `123,456`。`1,23` / `12,3,456` 这类说明 OCR 丢了或多了字符。两个例外按
    「无害」放行: 首位分组缺失(`,643`, 首位漏读, 由 is_partial_number_text
    拦截)与首位分组超长(`1234,567`, 多为丢了一个逗号 —— 逗号不改变数字个数,
    解析值仍正确)。只拦带逗号的读数, 无逗号的末位丢失靠贴边检测发现
    (见 auction-notes 2.2, 双重损坏读数的已知限制也在该节)。
    """
    normalized = raw_text.translate(FULLWIDTH_NUMERIC)
    digits_and_commas = re.sub(r"[^\d,]", "", normalized)
    if "," not in digits_and_commas:
        return False
    groups = digits_and_commas.split(",")
    if groups[0] == "" or len(groups[0]) > 3:
        # 首位分组缺失由 is_partial_number_text 拦; 超长按「丢一个逗号」放行,
        # 逗号不改变数字个数, 拒绝反而会丢掉正确读数。
        return False
    return any(len(group) != 3 for group in groups[1:])


def price_key_sequence(price_str: str) -> list[str]:
    """把价格字符串切分为按键序列, 可一次输入的 0000 / 00 优先整体输入。

    按**最长优先**做前缀匹配, 而不是只在「剩余整串恰好等于快捷键」时才用:
    后者会把 `1000000` 切成 `1 0 0 0000`(4 键), 前缀匹配切成 `1 0000 00`(3 键),
    而每次点击都带 after_sleep —— 少按一键就少一次 0.2 秒的等待。
    """
    shortcuts = sorted(PAD_SHORTCUTS, key=len, reverse=True)
    keys: list[str] = []
    index = 0
    while index < len(price_str):
        for shortcut in shortcuts:
            if price_str.startswith(shortcut, index):
                keys.append(shortcut)
                index += len(shortcut)
                break
        else:
            keys.append(price_str[index])
            index += 1
    return keys


def resolve_bid_prices(raw_prices: Sequence[int]) -> list[int]:
    """把 6 个每轮指定价格的原始配置值解析成可按出价序号直接取用的列表。

    每个价格对应一次出价: 第 1 次用「第1次出价价格」, 第 2 次用「第2次出价价格」, 依此类推。
    未设置(0)的回合沿用上一次已设置的价格, 因此只填前几次也能正常工作。
    第 1 次出价必须有价格, 否则返回空列表, 由调用方按配置错误处理。
    """
    resolved: list[int] = []
    current = 0
    for raw in raw_prices:
        if raw > 0:
            current = raw
        resolved.append(current)

    if not resolved or resolved[0] <= 0:
        return []
    return resolved


def validate_bid_prices(raw_prices: Sequence[int]) -> list[int]:
    """解析每轮指定价格并校验末次出价必须更高, 非法时抛 ValueError。

    游戏中第 6 次出价必须高于第 5 次, 否则这一次出价会被系统拒绝;
    留 0 沿用上一次价格会导致两者相等, 所以第 6 次必须显式填写。
    """
    prices = resolve_bid_prices(raw_prices)
    if not prices:
        raise ValueError(f"每轮指定价格未配置: {CONF_BID_PRICES[0]} 必须大于 0")
    if len(prices) < 2:
        return prices

    last_key = CONF_BID_PRICES[len(prices) - 1]
    previous_key = CONF_BID_PRICES[len(prices) - 2]
    last, previous = prices[-1], prices[-2]
    if last > previous:
        return prices

    # 区分「没填」和「填小了」, 两种情况用户要做的修改不一样。
    if raw_prices[len(prices) - 1] <= 0:
        raise ValueError(
            f"{last_key} 未设置: 沿用上一次的价格 {previous} 不会高于 "
            f"{previous_key}, 请显式填写一个更大的值"
        )
    raise ValueError(f"{last_key} ({last}) 必须大于 {previous_key} ({previous})")


def validate_fixed_price(raw_price: object) -> int:
    """校验自定义模式「基础价」: 必须能解析成正整数, 返回解析结果, 非法时抛 ValueError。

    出价面板打开后才发现非法配置, 会以每轮 3 次重试的方式空转, 所以要在任务入口拦下。
    """
    try:
        base_price = int(raw_price)
    except (TypeError, ValueError):
        raise ValueError(f"基础价配置非法: {raw_price!r}") from None
    if base_price <= 0:
        raise ValueError(f"基础价必须为正整数, 当前: {raw_price!r}")
    return base_price


def validate_raise_value(raw_value: object) -> Decimal:
    """校验「加价数值」: 必须按 Decimal 解析为正有限数, 返回解析结果, 非法时抛 ValueError。

    解析口径必须与运行时回退读数的 _config_decimal 一致, 否则非法字符串会在
    运行时回退成 0 静默按基础价出价 (见 auction-notes 5.1); 加价的语义就是往上加,
    0 或负数在入口一并拦下。
    """
    try:
        value = Decimal(str(raw_value))
    except (ArithmeticError, ValueError):
        raise ValueError(f"加价数值配置非法: {raw_value!r}") from None
    if not value.is_finite():
        raise ValueError(f"加价数值配置非法: {raw_value!r}")
    if value <= 0:
        raise ValueError(f"加价数值必须为正数, 当前: {raw_value!r}")
    return value


def validate_raise_round(raw_round: object) -> None:
    """校验「加价回合数」: 有值时必须能按 int 解析且不为负, 非法时抛 ValueError。

    解析口径与运行时的 _config_int 一致: 运行时对解析失败静默回退 0(= 第 1 口
    就按加价算), 负数会让 raise_offset 的偏移前移 (填 -1 时第 1 口就按第 3 口
    的加价算), 两者都改变真实出价, 与基础价/加价数值同口径在入口拦下
    (见 auction-notes 5.1)。键缺失(None)不算脏值: 运行时同样按 0 处理。
    """
    if raw_round is None:
        return
    try:
        value = int(raw_round)
    except (TypeError, ValueError):
        raise ValueError(f"加价回合数配置非法: {raw_round!r}") from None
    if value < 0:
        raise ValueError(f"加价回合数不能为负数, 当前: {raw_round!r}")


def validate_special_round_price(raw_price: object) -> int:
    """校验自定义模式「指定回合价格」: 必须能解析成正整数, 返回解析结果。

    与基础价同口径在入口拦下: 运行时 _config_int 对非法值静默回退 0,
    special_round_price 对 ≤0 一律返回 None, 整个指定回合功能静默失效
    (见 auction-notes 5.1)。
    """
    try:
        price = int(raw_price)
    except (TypeError, ValueError):
        raise ValueError(f"指定回合价格配置非法: {raw_price!r}") from None
    if price <= 0:
        raise ValueError(f"指定回合价格必须为正整数, 当前: {raw_price!r}")
    return price


def validate_special_rounds(raw_rounds: object) -> list[int]:
    """校验自定义模式「指定回合」: 逐项按 int 解析, 须非空且取值在单场口数上限内。

    对原始配置值解析, 不经过会吞脏值的 _config_int_list(任一项非法返回空
    列表, 所有指定回合静默丢失); 越界回合(游戏单场最多 MAX_BID_ROUNDS 口)
    永不命中, 同属静默失效, 一并在入口拦下 (见 auction-notes 5.1)。
    """
    if not isinstance(raw_rounds, (list, tuple)):
        raise ValueError(f"指定回合配置非法: {raw_rounds!r}")
    rounds: list[int] = []
    for raw in raw_rounds:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"指定回合配置非法: {raw_rounds!r}") from None
        if not 1 <= value <= MAX_BID_ROUNDS:
            raise ValueError(
                f"指定回合取值只能是 1~{MAX_BID_ROUNDS} (单场最多 {MAX_BID_ROUNDS} 口), "
                f"当前: {raw_rounds!r}"
            )
        rounds.append(value)
    if not rounds:
        raise ValueError("已启用指定回合单独出价但未勾选任何回合, 请勾选回合或关闭该开关")
    return rounds


def raise_offset(bid_count: int, raise_round: int) -> int | None:
    """第 bid_count 次出价的加价偏移次数, 从 1 开始; 未到加价回合时返回 None。

    raise_round=0 表示第 1 次出价就按加价算; N 表示第 N 次起才开始加价,
    之前的出价直接使用基础价 (调用方据此返回, 不告警)。
    """
    if raise_round > 0 and bid_count < raise_round:
        return None
    return bid_count if raise_round == 0 else bid_count - raise_round + 1


def raise_price(
    base_price: int,
    offset: int,
    *,
    mode: str,
    raise_value: Decimal,
) -> int | None:
    """按加价方式计算基础价经过 offset 次加价后的价格。

    全程用 Decimal: float 在倍率模式的 `value ** offset` 上会溢出
    (背景见 auction-notes 5.1)。

    Returns:
        计算出的整数价格; 结果超出可表示范围(溢出/非有限值)时返回 None,
        由调用方告警并回退基础价。返回值仍可能 <= 0, 有效性由调用方校验。
    """
    base = Decimal(str(base_price))
    try:
        if mode == RAISE_MODE_MULTIPLE:
            # 指数增长: 基础价 * (倍率 ^ offset).
            result = base * (raise_value**offset)
        elif mode == RAISE_MODE_PERCENT:
            # 线性增长: 基础价 * (1 + 百分比 / 100 * offset).
            result = base * (Decimal(1) + raise_value / 100 * offset)
        else:  # 自定义
            # 线性增长: 基础价 + 自定义值 * offset.
            result = base + raise_value * offset
        # 量化必须留在 try 内: 大指数仍是有限值但超精度, 到 quantize 才抛
        # InvalidOperation (见 auction-notes 5.1); 位数粗筛提前挡掉大数.
        rounded = (
            result.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            if result.adjusted() < 30
            else None
        )
        if rounded is None:
            result = None
        else:
            result = rounded
    except (ArithmeticError, InvalidOperation, ValueError):
        result = None

    # 溢出/精度异常时 result 为 None, 或退化成非有限值(NaN / Infinity).
    if result is None or not result.is_finite():
        return None
    # 已在 try 内完成量化, 这里直接取整.
    return int(result)


def estimate_price(estimate: int, ratio: float) -> int:
    """估价 x 倍率, 与加价计算同口径: Decimal 乘法后按 ROUND_HALF_UP 取整。"""
    result = Decimal(str(estimate)) * Decimal(str(ratio))
    return int(result.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tiered_estimate_ratio(
    estimate: int, bounds: Sequence[int], ratios: Sequence[Decimal]
) -> tuple[int, Decimal] | None:
    """按估价区间查分档倍率, 返回 (1 起始的档位序号, 倍率)。

    取第一个满足 估价 < 上限 的档, 上限 <= 0 视为无上限档(恒可命中); 估价恰等于
    某档上限时落入下一档, 与参考实现的区间语义一致。没有任何档命中(通常意味着
    上限配置有洞, 已被入口校验拦截)时返回 None, 由调用方回退单一倍率并告警。

    bounds / ratios 按档位一一配对(zip 短者截断): 只填了部分档位时, 未配置的
    档不参与命中, 而不是当成倍率 0。
    """
    for index, (bound, ratio) in enumerate(zip(bounds, ratios), start=1):
        if bound <= 0 or estimate < bound:
            return index, ratio
    return None


def tier_value_to_ratio(value: Decimal) -> Decimal:
    """百分比取值 -> 有效倍率: 出价 = 估价×值/100(填 109 = 1.09 倍)。"""
    return value / Decimal(100)


def special_round_price(
    bid_count: int, special_rounds: Iterable[int], special_price: int
) -> int | None:
    """当前出价序号命中「指定回合」且单独价格有效(> 0)时返回该价格, 否则 None。"""
    return special_price if special_price > 0 and bid_count in special_rounds else None


# --- 逐口追踪模式 (参考按键精灵脚本移植, 数值未经实机验证, 见 auction-notes 7) ---

# 逐口追踪策略只规划前 5 口出价, 第 5 口之后不再跟进(参考脚本行为, 比游戏的
# 6 口上限少一口): 策略的固定价格表与底价都只覆盖前 5 口。
SMART_MAX_BIDS = 5
# 参考脚本对出价的硬顶。
SMART_BID_CEILING = 30_000_000


@dataclass
class SmartRoundState:
    """逐口追踪模式的每局状态, 由任务层在每轮拍卖开始时整体重建。

    estimates 以 1 起始的出价序号为键记录每口读到的估价。
    """

    estimates: dict[int, int] = field(default_factory=dict)
    special_134: bool = False
    first_high_capped: bool = False


def smart_delta(state: SmartRoundState, round_no: int) -> int | None:
    """第 round_no 口相对上一口的估价增量; 上一口估价缺失时返回 None。

    估价识别失败的那口会回退基础价、不进 estimates, 之后各口的增量就拿不到
    前值 —— 与参考脚本一致按「数据不足」处理, 而不是拿回退值凑数。
    """
    previous = state.estimates.get(round_no - 1)
    current = state.estimates.get(round_no)
    if previous is None or current is None:
        return None
    return current - previous


def smart_growth(state: SmartRoundState, round_no: int) -> float:
    """第 round_no 口的估价增长率(增量 / 本口估价), 口径与参考脚本一致。

    增量缺失或本口估价为 0 时按 0 处理: 参考脚本在这里用真值短路, 增长率
    退化成 0 只影响潜力判定与底价档位, 不中断策略计算。
    """
    delta = smart_delta(state, round_no)
    base = state.estimates.get(round_no)
    if delta is None or not base:
        return 0.0
    return delta / base


def smart_update_state(state: SmartRoundState, round_no: int, estimate: int) -> None:
    """记录本口估价并更新特殊物品标志(134 万物品)。

    与参考脚本一致: 134 万按「首口估价」或「任一相邻口增量」判定。识别失败
    回退基础价的那口没有 estimates 记录, 相关检测按数据不足静默降级, 由任务层告警。
    """
    state.estimates[round_no] = estimate
    first = state.estimates.get(1)
    if first is not None and first >= 1_340_000:
        state.special_134 = True
    for previous_round in (1, 2, 3, 4):
        delta = smart_delta(state, previous_round + 1)
        if delta is not None and delta >= 1_340_000:
            state.special_134 = True


def smart_bid_price(state: SmartRoundState, round_no: int, estimate: int) -> int:
    """按参考脚本策略计算第 round_no 口的出价。

    按当前估价分四档: <500 万走「估价 + 底价 + 潜力加成」的逐口追踪表;
    500 万以上按参考脚本的固定价格表(第 5 口起用 估价+200 万 兜底)。
    首口高价保护一局只触发一次(first_high_capped), 把前 3 口的高价出价
    压到保护上限, 防止开局就在贵重物品上重仓。结果钳制到 [1, 30M]。
    """
    if estimate < 5_000_000:
        bid = _smart_low_value_bid(state, round_no, estimate)
    elif estimate < 10_000_000:
        bid = {1: 5_000_000, 2: 6_000_000, 3: 7_000_000, 4: estimate + 1_000_000}.get(
            round_no, estimate + 2_000_000
        )
    elif estimate < 20_000_000:
        bid = {1: 5_000_000, 2: 8_000_000, 3: 10_000_000, 4: estimate + 1_000_000}.get(
            round_no, estimate + 2_000_000
        )
    else:
        bid = {1: 6_700_000, 2: 11_110_000, 3: 15_550_000, 4: 18_880_000}.get(
            round_no, estimate + 2_000_000
        )

    if not state.first_high_capped and round_no in (1, 2, 3) and estimate > 5_000_000:
        if round_no == 1 or (round_no in (2, 3) and estimate > 10_000_000):
            bid = min(bid, 5_000_000)
        else:
            bid = min(bid, 4_000_000)
        state.first_high_capped = True

    return max(1, min(bid, SMART_BID_CEILING))


def _smart_mid_reserve(state: SmartRoundState, g3: float) -> int:
    """第 3 口底价: 按第 3 口增长率分档。"""
    reserve = 130_000 if g3 < 0.40 else 180_000
    if state.special_134:
        reserve = max(reserve, 300_000)
    return reserve


def _smart_late_reserve(state: SmartRoundState, g3: float, g4: float) -> int:
    """第 4/5 口底价: 基础底价叠加第 4 口增量加成。"""
    reserve = 100_000 if g3 < 0.40 else 160_000
    if g4 >= 0.15:
        reserve += 60_000
    elif g4 >= 0.05:
        reserve += 30_000
    if state.special_134:
        reserve = max(reserve, 280_000)
    return reserve


def _smart_low_value_bid(state: SmartRoundState, round_no: int, estimate: int) -> int:
    """<500 万档的逐口追踪出价: 估价 + 各口底价 + 潜力加成。

    R4/R5 的「小仓」物品(首口估价 <20 万、前 3 口无增长, 也没有特殊标记)
    不追加底价, 贴着估价出价 —— 参考脚本对这类物品放弃争抢。
    """
    first = state.estimates.get(1)
    g3 = smart_growth(state, 3)
    first_potential = first is not None and first >= 400_000
    third_potential = g3 >= 0.40
    potential = first_potential or third_potential or state.special_134
    high_potential = first_potential and third_potential

    reserve = 0
    bonus = 0
    if round_no == 1:
        reserve = 340_000
    elif round_no == 2:
        reserve = 270_000
        if state.special_134:
            reserve = max(reserve, 450_000)
    elif round_no == 3:
        reserve = _smart_mid_reserve(state, g3)
        bonus = 100_000 if high_potential else 50_000 if potential else 0
    else:
        small_bad = (
            first is not None
            and first < 200_000
            and g3 < 0.40
            and not state.special_134
        )
        if small_bad:
            return estimate
        reserve = _smart_late_reserve(state, g3, smart_growth(state, 4))
        if round_no == 5:
            bonus = 150_000 if high_potential else 100_000 if potential else 0
        else:
            bonus = 120_000 if high_potential else 80_000 if potential else 0
    return estimate + reserve + bonus


# --- 资产路由: 资产跌破门槛时按表把出价模式切到低风险模式 ---

# 「巨物小吱4123」的固定出价表按高级场贵重拍品设计, 账号在高级场亏损掉到低级场后,
# 固定表底价远高于低级场藏品价值, 会持续亏损; 资产跌破该线时按 ASSET_MODE_ROUTES
# 把出价模式路由到低风险模式 (门槛取值与单向锁存决策见 auction-notes 7)。
ASSET_MODE_ROUTE_THRESHOLD = 1_000_000

# 资产跌破门槛时的模式路由表; 其他模式需要同等保护时在这里加一项。
ASSET_MODE_ROUTES: dict[str, str] = {
    BID_MODE_SMART: BID_MODE_ESTIMATE,
}


def route_mode_on_asset(mode: str, asset_value: int | None) -> str | None:
    """资产跌破门槛时返回 mode 应路由到的出价模式, 其余情况返回 None。

    asset_value 为 None(未读出)或不低于门槛时不触发; 「低于」不含等于,
    恰好等于门槛视为仍安全。mode 不在 ASSET_MODE_ROUTES 内时返回 None,
    维持原模式。
    """
    if asset_value is None or asset_value >= ASSET_MODE_ROUTE_THRESHOLD:
        return None
    return ASSET_MODE_ROUTES.get(mode)
