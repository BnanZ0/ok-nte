"""出价价格决策: 出价模式的注册分发与各模式的价格计算实现。

四种出价模式 (自定义 / 每轮指定 / 按系统估价 / 逐口追踪) 的模式声明、配置校验
与算价入口共用同一张注册表, 都收在本模块: 任务入口的 _validate_price_config 与
运行时算价按模式取同一个 BidMode, 新增模式只需在注册表加一项。纯计算规则
(加价公式, 分档换算, 逐口追踪策略, 指定价格解析) 在 auction_price, 这里负责读
配置, 调 OCR 读数, 告警与回退。模块函数的第一个参数 task 是 AutoBidAuctionTask
实例, 允许访问面由 contracts.AuctionBidPriceOps 声明; 模块内部的自家函数直接
调用, 不再绕道任务转发。
"""

from collections.abc import Callable
from dataclasses import dataclass

from src.tasks.auction import price as auction_price
from src.tasks.auction import reading as auction_reading
from src.tasks.auction.contracts import AuctionBidPriceOps
from src.tasks.auction.layout import AuctionBoxes
from src.tasks.auction.options import (
    BID_MODE_CUSTOM,
    BID_MODE_ESTIMATE,
    BID_MODE_LIST,
    BID_MODE_SMART,
    CONF_AUTO_RAISE,
    CONF_BID_PRICES,
    CONF_FIXED_PRICE,
    CONF_HEART_INCREMENT,
    CONF_RAISE_ROUND,
    CONF_RAISE_VALUE,
    CONF_SPECIAL_ROUND,
    CONF_SPECIAL_ROUND_PRICE,
    CONF_SPECIAL_ROUNDS,
    CONF_TIER_BOUNDS,
    CONF_TIER_RATIOS,
    CONF_TIERED_RATIO,
    DEFAULT_TIER_PERCENTS,
    MAX_BID_ROUNDS,
)


class BidPriceUnavailable(Exception):
    """算价模块给不出可输入的价格: 资产路由模式下估价读不出, 又不回退「基础价」。

    出价循环(_stage_bid_loop)按「放弃本次出价」处理, 不计重试; 其他调用方
    未捕获时会向上传播, 由整轮失败路径兜住。
    """


def apply_heart_increment(task: AuctionBidPriceOps, price: int) -> int:
    """检测到永恒之心时在算出的价格上追加固定加价。

    只有本场检测完成且检出心、且配置了正的增量时才生效; 增量为 0(默认)
    或价格无效(<=0)时原样返回, 不改变既有出价行为。显式填写的价格
    (「指定回合价格」/「每轮指定价格」)不经本方法, 不会被抬高。
    """
    if not task._heart_present or price <= 0:
        return price
    increment = task._config_int(
        CONF_HEART_INCREMENT,
        0,
        warn=f"「{CONF_HEART_INCREMENT}」不是整数, 本次按 0 处理(不加成)",
    )
    if increment <= 0:
        return price
    task.log_info(f"检测到永恒之心, 出价在 {price} 上追加 {increment}")
    return price + increment


def resolve_bid_prices(task: AuctionBidPriceOps) -> list[int]:
    """读取 6 个每轮指定价格并解析成可按出价序号直接取用的列表。

    解析规则见 auction_price.resolve_bid_prices: 未设置(0)的回合沿用上一次
    已设置的价格, 第 1 次出价必须有价格, 否则返回空列表由调用方按配置错误处理。
    """
    raw_prices = [task._config_int(key, 0) for key in CONF_BID_PRICES]
    return auction_price.resolve_bid_prices(raw_prices)


def listed_bid_price(task: AuctionBidPriceOps, bid_count: int) -> int:
    """按出价序号取每轮指定价格, 出价次数超出配置项时沿用最后一次的价格。"""
    prices = resolve_bid_prices(task)
    if not prices:
        raise ValueError(f"每轮指定价格未配置: {CONF_BID_PRICES[0]} 必须大于 0")

    index = min(max(bid_count, 1), len(prices)) - 1
    price = prices[index]
    if bid_count > len(prices):
        # 游戏里一轮最多 6 回合出价, 走到这里说明出价次数与预期不符, 值得告警。
        task.log_warning(
            f"出价序号 {bid_count} 超出每轮指定价格的 {len(prices)} 次出价, "
            f"沿用最后一次价格 {price}"
        )
    else:
        task.log_info(f"第 {bid_count} 次出价使用指定价格 {price}")
    return price


def estimate_bid_price(
    task: AuctionBidPriceOps, boxes: AuctionBoxes | None, deadline: float | None, bid_count: int
) -> int:
    """按出价面板上的当前估价乘倍率出价, 算出的价格低于基础价时按基础价出。

    基础价是该模式的出价下限: 估价×倍率算出的价格低于它时改用它出价,
    估价读不出或算出非正数时也回退到它。估价区域被弹窗或动画遮挡时不应
    中断整场拍卖, 因此只告警并回退。估价数字会先跳动几次才稳定, 所以走
    稳定读取而不是单次 OCR。面板在滚出数字前会先显示 0, 所以用 skip_zero
    把 0 当占位读数继续等, 否则 0 会被 `is None` 之外的假值判断当成
    「识别失败」, 直接回退到基础价。

    资产路由到本模式的场合(见 auction_price.route_mode_on_asset)不继承
    「基础价」: 路由的原因就是固定底价远高于当前场次的藏品价值, 再拿它当
    出价下限会把路由保护完全抵消(估价 1 万也可能出 50 万)。路由后估价
    读不出或算出非正数时抛 BidPriceUnavailable, 由出价循环放弃该口等待
    拍卖结果, 而不是按基础价出高价。
    """
    routed = task._asset_routed_mode is not None
    base_price = 1
    if not routed:
        base_price = task._config_int(
            CONF_FIXED_PRICE,
            1,
            warn=f"「{CONF_FIXED_PRICE}」不是整数, 出价下限与估价读不出时的回退值按 1 计算",
        )
    estimate = None
    if boxes is not None:
        estimate = task._read_stable_asset_value(
            boxes.estimate,
            task._remaining_timeout(deadline, auction_reading.ESTIMATE_STABLE_TIMEOUT),
            "当前估价",
            skip_zero=True,
        )
    if estimate is None:
        if routed:
            raise BidPriceUnavailable(
                f"资产路由模式下第 {bid_count} 口估价识别失败, 不回退「基础价」"
            )
        task.log_warning(f"当前估价识别失败, 第 {bid_count} 次出价回退到基础价 {base_price}")
        return base_price

    ratio = estimate_ratio(task, estimate)
    final_price = auction_price.estimate_price(estimate, ratio)
    if final_price <= 0:
        if routed:
            raise BidPriceUnavailable(
                f"资产路由模式下第 {bid_count} 口按估价 {estimate} 与倍率 {ratio} "
                f"算出无效价格 {final_price}, 不回退「基础价」"
            )
        task.log_warning(
            f"按估价 {estimate} 与倍率 {ratio} 计算出的价格 {final_price} 无效, "
            f"回退到基础价 {base_price}"
        )
        return base_price
    if final_price < base_price:
        task.log_info(
            f"按估价 {estimate} 与倍率 {ratio} 算出的价格 {final_price} 低于基础价, "
            f"使用基础价 {base_price} 出价"
        )
        return base_price

    task.log_info(f"按系统估价出价: 当前估价 {estimate}, 倍率 {ratio}, 出价 {final_price}")
    return final_price


def smart_bid_price(
    task: AuctionBidPriceOps, boxes: AuctionBoxes | None, deadline: float | None
) -> int:
    """逐口追踪模式出价: 稳定读估价后按参考脚本策略计算, 读不出回退基础价。

    估价读取与「按系统估价」同一套稳定读取; 估价识别失败回退基础价时,
    这一口不进估价序列(见 auction_price.smart_update_state), 后续口的
    增量检测按数据不足降级。算出的价至少抬高到上一口 +1: 游戏拒绝不高于
    当前最高价的出价, 连续 3 次失败会丢掉整轮; 界面读不到他人的最高价,
    「自己上一口」是能拿到的最可靠的必要下界。
    """
    base_price = task._config_int(
        CONF_FIXED_PRICE,
        1,
        warn=f"「{CONF_FIXED_PRICE}」不是整数, 出价下限与估价读不出时的回退值按 1 计算",
    )
    bid_count = task.current_bid_count + 1
    estimate = None
    if boxes is not None:
        estimate = task._read_stable_asset_value(
            boxes.estimate,
            task._remaining_timeout(deadline, auction_reading.ESTIMATE_STABLE_TIMEOUT),
            "当前估价",
            skip_zero=True,
        )
    if estimate is None:
        task.log_warning(f"当前估价识别失败, 第 {bid_count} 次出价回退到基础价 {base_price}")
        return base_price

    state = task._smart_state
    auction_price.smart_update_state(state, bid_count, estimate)
    bid = auction_price.smart_bid_price(state, bid_count, estimate)
    if task.last_bid_price is not None and bid <= task.last_bid_price:
        task.log_warning(
            f"{BID_MODE_SMART}出价 {bid} 不高于上一口 {task.last_bid_price}, "
            f"抬高到 {task.last_bid_price + 1}"
        )
        bid = task.last_bid_price + 1
    task.log_info(
        f"{BID_MODE_SMART}: 第 {bid_count} 口估价 {estimate}, 出价 {bid}, "
        f"134 万物品标记: {'是' if state.special_134 else '否'}"
    )
    return bid


def estimate_ratio(task: AuctionBidPriceOps, estimate: int) -> float:
    """读取估价应使用的有效倍率: 分档开关打开时按区间查档, 关闭时按估价原价。

    分档取值一律按百分比解释(出价=估价×值/100, 填 109 即 1.09 倍)。
    没有任何档命中(估价超出全部上限)时按估价原价出价并告警, 提示用户
    检查分档上限是否覆盖当前估价, 不让出价中断在配置问题上。
    """
    if not task.config.get(CONF_TIERED_RATIO, False):
        return 1.0

    tiered = auction_price.tiered_estimate_ratio(
        estimate, tier_bounds(task), tier_ratios(task)
    )
    if tiered is None:
        task.log_warning(
            f"估价 {estimate} 超出全部分档上限, 按估价原价出价; "
            "请检查「分档N估价上限」是否覆盖当前估价"
        )
        return 1.0
    index, percent = tiered
    tier_ratio = auction_price.tier_value_to_ratio(percent)
    task.log_info(f"估价 {estimate} 命中分档 {index}, 百分比 {percent}, 有效倍率 {tier_ratio}")
    return float(tier_ratio)


def tier_bounds(task: AuctionBidPriceOps) -> list[int]:
    """读取 6 档估价上限, 非法值按 0(无上限)处理, 严谨性由入口校验保证。"""
    return [task._config_int(key, 0) for key in CONF_TIER_BOUNDS]


def tier_ratios(task: AuctionBidPriceOps) -> list:
    """读取 6 档百分比取值, 缺失/非法时回退该档默认(参考攻略档), 严谨性由入口校验保证。"""
    return [
        task._config_decimal(key, DEFAULT_TIER_PERCENTS[index - 1])
        for index, key in enumerate(CONF_TIER_RATIOS, start=1)
    ]


def special_round_price(task: AuctionBidPriceOps, bid_count: int) -> int | None:
    """启用指定回合单独出价时返回该回合价格, 否则返回 None。

    启动时配置已校验, 运行中改坏只能在这里按回退值静默失效, 靠告警提示
    (见 auction-notes 5.1)。
    """
    if not task.config.get(CONF_SPECIAL_ROUND, False):
        return None

    special_rounds = task._config_int_list(
        CONF_SPECIAL_ROUNDS,
        warn=f"「{CONF_SPECIAL_ROUNDS}」含非法项, 指定回合单独出价不生效, 按常规价格出价",
    )
    out_of_range = [r for r in special_rounds if not 1 <= r <= MAX_BID_ROUNDS]
    if out_of_range:
        task.log_warning(
            f"「{CONF_SPECIAL_ROUNDS}」含超出 1~{MAX_BID_ROUNDS} 的回合 {out_of_range}, "
            "这些回合单独出价不会命中, 按常规价格出价"
        )
        special_rounds = [r for r in special_rounds if 1 <= r <= MAX_BID_ROUNDS]
    special_price = task._config_int(
        CONF_SPECIAL_ROUND_PRICE,
        0,
        warn=f"「{CONF_SPECIAL_ROUND_PRICE}」不是整数, 指定回合单独出价不生效, 按常规价格出价",
    )
    if special_price <= 0:
        task.log_warning(
            f"「{CONF_SPECIAL_ROUND_PRICE}」不是正整数"
            f"({task.config.get(CONF_SPECIAL_ROUND_PRICE)!r}), "
            "指定回合单独出价不生效, 按常规价格出价"
        )
    price = auction_price.special_round_price(bid_count, special_rounds, special_price)
    if price is not None:
        task.log_info(f"指定回合 {bid_count} 使用单独价格 {price}")
    return price


def raise_price(task: AuctionBidPriceOps, base_price: int, bid_count: int) -> int:
    """按配置的加价方式计算第 bid_count 次出价的价格。

    加价回合与三种方式的计算规则、Decimal 溢出防护见 auction_price;
    这里负责读取配置、告警与回退基础价。
    """
    mode = task._raise_mode()
    value = task._config_decimal(CONF_RAISE_VALUE, "0")
    raise_round = task._config_int(
        CONF_RAISE_ROUND,
        0,
        warn=f"「{CONF_RAISE_ROUND}」不是非负整数, 本次按 0 处理(第 1 口起即按加价计算)",
    )

    # 未到配置的加价回合, 直接使用基础价.
    offset = auction_price.raise_offset(bid_count, raise_round)
    if offset is None:
        return base_price

    final_price = auction_price.raise_price(base_price, offset, mode=mode, raise_value=value)
    if final_price is None:
        task.log_warning(
            f"加价计算结果超出可表示范围, 回退到基础价 {base_price} "
            f"(模式 {mode}, 数值 {value}, 加价偏移 {offset})"
        )
        return base_price

    if final_price <= 0:
        task.log_warning(f"计算出的价格 {final_price} 无效, 回退到基础价 {base_price}")
        final_price = base_price

    task.log_info(
        f"自动加价计算: 基础价 {base_price}, 模式 {mode}, 数值 {value}, "
        f"出价序号 {bid_count}, 加价偏移 {offset}, 出价 {final_price}"
    )
    return final_price


# --- 各模式的配置校验 (非法抛 ValueError, 任务入口直接终止) ---

def validate_tiered_ratio(task: AuctionBidPriceOps) -> None:
    """开启「估价分档百分比」时校验档位配置, 非法直接终止任务。

    上限有洞(估价落在洞里)会让 estimate_ratio 回退单一倍率, 出价偏离用户
    预期; 取值非正数会算出无效价格。这些都必须在入口拦下, 而不是在出价时
    靠告警回退。
    """
    if not task.config.get(CONF_TIERED_RATIO, False):
        return

    bounds = tier_bounds(task)
    for index, bound in enumerate(bounds, start=1):
        if bound < 0:
            raise ValueError(
                f"分档{index}估价上限不能为负: {task.config.get(CONF_TIER_BOUNDS[index - 1])!r}"
            )
        if bound == 0 and index != len(bounds):
            # 非数字配置经 _config_int 也回退成 0, 把原始值带上便于定位.
            raise ValueError(
                f"只有最后一档允许上限填 0(无上限), 分档{index}: "
                f"{task.config.get(CONF_TIER_BOUNDS[index - 1])!r}"
            )
    for previous, current in zip(bounds, bounds[1:]):
        # previous 为 0 已被上面的循环拦截; current 为 0 只会是末档, 无需比较.
        if current and current <= previous:
            raise ValueError(f"分档估价上限必须严格递增: 上一档 {previous}, 下一档 {current}")

    tier_values = []
    for index, key in enumerate(CONF_TIER_RATIOS, start=1):
        value = task._config_decimal(key, DEFAULT_TIER_PERCENTS[index - 1])
        if not value.is_finite() or value <= 0:
            raise ValueError(f"分档{index}百分比必须为正数, 当前: {task.config.get(key)!r}")
        tier_values.append(value)

    # 百分比取值会除以 100: 填 1 是 1%(0.01 倍), 不是 1 倍 (案例见 auction-notes 4)。
    for index, value in enumerate(tier_values, start=1):
        if value < 20:
            task.log_warning(
                f"「估价分档百分比」下分档{index}取值 {value} 表示只出估价的 "
                f"{value}%, 出价会远低于估价; 参考攻略按估价分档填 109/103/90/80/70/50"
            )


def warn_if_fixed_price_invalid(task: AuctionBidPriceOps) -> None:
    """「基础价」非正整数时提前告警, 供依赖基础价的估价类两个模式复用。

    「按系统估价」算出的价格低于「基础价」时按它出价, 估价读不出时也回退它,
    逐口追踪在估价读不出时回退它: 非正整数会让那次出价因「非法价格」连续
    失败 3 次丢掉整轮。正常配置下不拦任务(算出价高于基础价时它根本用不到),
    只提前把后果说清楚。
    """
    try:
        fallback = int(task.config.get(CONF_FIXED_PRICE))
    except (TypeError, ValueError):
        fallback = 0
    if fallback <= 0:
        task.log_warning(
            f"「{CONF_FIXED_PRICE}」不是正整数"
            f"({task.config.get(CONF_FIXED_PRICE)!r}), "
            "估价读不出时回退的出价将无法输入, 该次出价会失败"
        )


def _validate_custom(task: AuctionBidPriceOps) -> None:
    """自定义价格模式的入口校验: 基础价必填, 自动加价/指定回合开启时才校验各自配置。"""
    auction_price.validate_fixed_price(task.config.get(CONF_FIXED_PRICE))
    if task.config.get(CONF_AUTO_RAISE, False):
        auction_price.validate_raise_value(task.config.get(CONF_RAISE_VALUE))
        auction_price.validate_raise_round(task.config.get(CONF_RAISE_ROUND))
    if task.config.get(CONF_SPECIAL_ROUND, False):
        auction_price.validate_special_rounds(task.config.get(CONF_SPECIAL_ROUNDS))
        auction_price.validate_special_round_price(task.config.get(CONF_SPECIAL_ROUND_PRICE))


def _validate_list(task: AuctionBidPriceOps) -> None:
    auction_price.validate_bid_prices([task._config_int(key, 0) for key in CONF_BID_PRICES])


def _validate_estimate(task: AuctionBidPriceOps) -> None:
    validate_tiered_ratio(task)
    warn_if_fixed_price_invalid(task)


def _validate_smart(task: AuctionBidPriceOps) -> None:
    # 与「按系统估价」同口径: 估价读不出时回退基础价, 只提前说清后果,
    # 不拦任务; 其余策略数值是参考脚本内置的, 没有用户可配项。
    warn_if_fixed_price_invalid(task)


# --- 各模式的算价 (bid_count 从 1 开始, 基于成功出价次数 + 1) ---

def _price_custom(
    task: AuctionBidPriceOps, boxes: AuctionBoxes | None, deadline: float | None, bid_count: int
) -> int:
    """自定义价格: 基准价为自定义价格, 启用自动加价时按模式计算加价结果;
    启用指定回合单独出价且当前序号在勾选列表中时直接使用该价格。

    基础价每口活读以跟进运行中的改动; 运行中改坏会静默回退 1, 只能靠告警提示
    (见 auction-notes 5.1)。
    """
    base_price = task._config_int(
        CONF_FIXED_PRICE,
        1,
        warn=f"「{CONF_FIXED_PRICE}」不是正整数, 本次出价按回退值 1 计算",
    )

    special_price = special_round_price(task, bid_count)
    if special_price is not None:
        return special_price

    if not task.config.get(CONF_AUTO_RAISE, False):
        return apply_heart_increment(task, base_price)

    return apply_heart_increment(task, raise_price(task, base_price, bid_count))


def _price_list(
    task: AuctionBidPriceOps, boxes: AuctionBoxes | None, deadline: float | None, bid_count: int
) -> int:
    """每轮指定价格: 按出价序号从配置列表依次取用。"""
    return listed_bid_price(task, bid_count)


def _price_estimate(
    task: AuctionBidPriceOps, boxes: AuctionBoxes | None, deadline: float | None, bid_count: int
) -> int:
    """按系统估价: 读取出价面板上的当前估价并乘以倍率, 低于基础价按基础价出。"""
    return apply_heart_increment(task, estimate_bid_price(task, boxes, deadline, bid_count))


def _price_smart(
    task: AuctionBidPriceOps, boxes: AuctionBoxes | None, deadline: float | None, bid_count: int
) -> int:
    """逐口追踪: 参考脚本整场策略, 按每口估价增量判定潜力后查固定底价表。"""
    return apply_heart_increment(task, smart_bid_price(task, boxes, deadline))


@dataclass(frozen=True)
class BidMode:
    """一种出价模式: 校验与算价入口随模式一起声明, 两处分发共用同一张表。"""

    key: str
    validate: Callable[[AuctionBidPriceOps], None]
    price: Callable[[AuctionBidPriceOps, AuctionBoxes | None, float | None, int], int]


BID_MODE_REGISTRY: dict[str, BidMode] = {
    BID_MODE_LIST: BidMode(BID_MODE_LIST, _validate_list, _price_list),
    BID_MODE_ESTIMATE: BidMode(BID_MODE_ESTIMATE, _validate_estimate, _price_estimate),
    BID_MODE_SMART: BidMode(BID_MODE_SMART, _validate_smart, _price_smart),
    BID_MODE_CUSTOM: BidMode(BID_MODE_CUSTOM, _validate_custom, _price_custom),
}


def _resolve_mode(mode: str) -> BidMode:
    """按模式名取注册表项, 未知值抛带可选值清单的 ValueError。

    这是真实花钱的决策, 不能让脏配置按用户没选的策略出价; 入口校验与运行时
    算价统一在这里拦 (背景见 auction-notes 4)。
    """
    bid_mode = BID_MODE_REGISTRY.get(mode)
    if bid_mode is None:
        raise ValueError(
            f"出价模式配置非法: {mode!r}, 可选值: {', '.join(BID_MODE_REGISTRY)}"
        )
    return bid_mode


def validate_price_config(mode: str, task: AuctionBidPriceOps) -> None:
    """按出价模式校验价格配置, 非法(含未知模式)抛 ValueError。"""
    _resolve_mode(mode).validate(task)


def calculate_price(
    task: AuctionBidPriceOps, boxes: AuctionBoxes | None = None, deadline: float | None = None
) -> int:
    """计算当前出价应该输入的价格。

    价格来源由出价模式决定, 各模式语义见注册表对应的 price 函数;
    检测到永恒之心时, 固定加价只叠加在「算出来的」价格上
    (自定义/按系统估价/逐口追踪), 「每轮指定价格」与「指定回合价格」
    是用户显式填写的价格, 原样使用。出价序号从 1 开始计数, 基于成功出价次数 + 1。
    """
    mode = task._bid_mode()
    return _resolve_mode(mode).price(task, boxes, deadline, task.current_bid_count + 1)
