"""资产路由: 资产跌破门槛时把出价模式路由到目标模式, 本次运行内单向锁存。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例, 允许访问面由
contracts.AuctionRoutingOps 窄协议声明。锁存状态 (_asset_routed_mode /
_asset_route_prechecked) 的所有者是任务实例 (会话级, 不随轮次复位, 读侧
是任务类的 _bid_mode), 本模块只读写结论; 模式规则表与门槛在 auction_price。
"""

import time

from src.tasks.auction import price as auction_price
from src.tasks.auction import welfare as auction_welfare
from src.tasks.auction.contracts import AuctionRoutingOps
from src.tasks.auction.layout import AuctionBoxes
from src.tasks.auction.options import BID_MODE_CUSTOM, CONF_BID_MODE

# 资产路由前的复核读数预算: 路由是本次运行单向锁存的高后果决定, 低于门槛的
# 读数要重读一次确认才采信 (见 confirm_low_reading 与 auction-notes 7)。
ASSET_ROUTE_CONFIRM_TIMEOUT = 3


def observe_and_route(
    task: AuctionRoutingOps, boxes: AuctionBoxes, deadline: float
) -> int | None:
    """读取主界面资产值并做资产路由检查, 读取实现见 auction_welfare.observe_main_asset。

    资产只在结算扣款与出售回款后变化, 结算后观测是每轮唯一必经的新读数点,
    路由检查挂在这里; 出价面板的资产读取在 bid 契约内, 不另挂回调。
    路由是单向锁存的高后果决定, 低于门槛的读数先经 confirm_low_reading
    复核, 低保金等消费方拿到的仍是原读数。
    """
    value = auction_welfare.observe_main_asset(task, boxes, deadline)
    route_mode_on_asset(task, confirm_low_reading(task, boxes, deadline, value))
    return value


def confirm_low_reading(
    task: AuctionRoutingOps,
    boxes: AuctionBoxes,
    deadline: float | None,
    value: int | None,
) -> int | None:
    """低于路由门槛的读数复核一次再采信, 返回交给路由判定的值(不通过时 None)。

    主界面资产读数存在首位漏读的残缺形态(7,284 读成 ,284/284, 见
    auction-notes 2.3/5.1), 单帧读数不足以支撑单向锁存; 复核读数(读数层
    带残缺放大补拍)仍低于门槛才把原值交给路由。复核读不出或不支持时返回
    None, 本轮不路由, 下轮结算后观测重新评估。
    """
    if value is None or value >= auction_price.ASSET_MODE_ROUTE_THRESHOLD:
        return value
    timeout = task._optional_timeout(deadline, ASSET_ROUTE_CONFIRM_TIMEOUT)
    if timeout is None:
        task.log_warning(f"资产 {value} 低于门槛但复核没有可用时间, 本次不路由")
        return None
    ops = task._reading_ops()
    confirm = ops.asset_once(ops, boxes.main_asset, timeout)
    if confirm is None or confirm >= auction_price.ASSET_MODE_ROUTE_THRESHOLD:
        task.log_warning(
            f"资产 {value} 低于门槛但复核读数为 {confirm}, 按残缺读数处理, 本次不路由"
        )
        return None
    return value


def route_before_first_bid(task: AuctionRoutingOps, boxes: AuctionBoxes) -> None:
    """首轮出价前补一次资产路由检查, 会话内至多执行一次。

    结算后观测(observe_and_route)最早在首轮结算后才跑, 用户带着低于
    门槛的资产直接进场时, 首轮会按原模式(高级场固定表)出价; 这里在入场
    确认后、首轮出价前读一次资产提前锁存。配置模式不在路由表内时本次
    运行永远不可能路由, 直接返回不做无谓读数; 读不出(None)不触发,
    首轮结算后的常规观测仍会兜住。
    """
    if task._asset_route_prechecked:
        return
    task._asset_route_prechecked = True
    if task.config.get(CONF_BID_MODE, BID_MODE_CUSTOM) not in auction_price.ASSET_MODE_ROUTES:
        return
    deadline = time.monotonic() + auction_welfare.ASSET_OBSERVE_TIMEOUT
    observe_and_route(task, boxes, deadline)


def route_mode_on_asset(task: AuctionRoutingOps, value: int | None) -> None:
    """资产跌破门槛时把出价模式路由到目标模式, 本次运行内单向锁存。

    规则表与门槛在 auction_price.route_mode_on_asset; 判断基于用户配置的
    模式, 命中后写 _asset_routed_mode 供 _bid_mode 覆盖, 不改写配置。
    已锁存时直接返回: 资产回升不切回, 也不再触发。
    """
    if task._asset_routed_mode is not None:
        return
    config_mode = task.config.get(CONF_BID_MODE, BID_MODE_CUSTOM)
    routed = auction_price.route_mode_on_asset(config_mode, value)
    if routed is None:
        return
    task._asset_routed_mode = routed
    task.info_set("出价模式(生效)", routed)
    task.log_info(
        f"资产 {value} 低于 {auction_price.ASSET_MODE_ROUTE_THRESHOLD}, "
        f"本次运行出价模式路由到「{routed}」(配置「{config_mode}」不变)"
    )
