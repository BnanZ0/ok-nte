"""拍卖藏品出售能力: 模式判定, 间隔与满仓触发, 仓库流程, 品质筛选。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: OCR/输入/日志等框架
API 经它访问; 允许访问面由 contracts.AuctionSellOps 窄协议声明。配置读取
(出售模式/间隔/品质清单)直接经 task.config 走 config_read / 本模块函数,
不经任务转发。出售域的行为常量
由本模块定义, 不再挂回任务类。模块内部的自家函数直接调用, 不再绕道任务
私有方法转发; 品质勾选会把 task 转手给
auction_interaction.atomic_sequence, 转手后的访问面由对方模块自己的契约
约束。

跨轮满仓标记的所有者是任务实例: run_round_end_sell 以三态返回值报告结论,
由任务侧落账 _inventory_stuck, 置位会让下一轮跳过拍卖先重试清理。
置位/清零条件见 run_round_end_sell 的注释。
"""

from ok import TaskDisabledException, WaitFailedException

from src.tasks.auction import config_read as auction_config_read
from src.tasks.auction import interaction as auction_interaction
from src.tasks.auction.contracts import AuctionSellOps
from src.tasks.auction.layout import (
    POS_SETTLE_QUALITY_RED,
    QUALITY_BOXES,
    RE_COLLECTION_INSUFFICIENT,
    RE_ONE_CLICK_SELL,
    RE_POPUP_CLOSE_HINT,
    RE_SELL_LABEL,
    RE_WAREHOUSE,
    AuctionBoxes,
    PostRoundState,
)
from src.tasks.auction.options import (
    ASSIST_SELL_RED,
    CONF_ASSIST_FEATURES,
    CONF_SELL_INTERVAL,
    CONF_SELL_MODE,
    CONF_SELL_QUALITIES,
    CONF_SELL_RED_MAX,
    QUALITY_KEYS,
    SELL_MODE_INTERVAL,
    SELL_MODE_OFF,
    SELL_MODE_ONE_CLICK,
    SELL_MODES,
)

# 品质圆点每点击一次界面会重绘, 间隔太短时后续点击会落空;
# 勾选后读出售价值校验, 读到 0 或读不出时换帧重读, 最多尝试 SELL_SELECT_RETRIES 次.
# 重新勾选不是常规重试手段: 勾选是无条件点击, 再点一次等于全部取反 —— 只有
# 出售价值换帧重读仍为 0 的「残留勾选被点掉」场景, 才由 ensure_sell_value
# 按双重取反有意重勾一次.
SELL_QUALITY_GAP = 0.5
SELL_SELECT_RETRIES = 2

# 「出售价值」在品质圆点刚点完时会短暂变成空白(界面重绘), 读不出时返回 None,
# 调用方必须按「未确认」处理, 不能当成出售成功 (频率数据见 auction-notes 3).
SELL_VALUE_TIMEOUT = 3

# 库存不足提示条出现时机不定, 超时太短会漏掉, 满仓会卡住 (见 auction-notes 3).
# 满仓检测的本体是本模块的 detect_inventory_full; 低保侧的资产观测用 welfare
# 自己的 ASSET_OBSERVE_TIMEOUT, 两边预算独立不共享.
INVENTORY_FULL_TIMEOUT = 3

# 关闭藏品仓库的重试次数: 关不干净会把「出售模式 + 已勾选品质」留给下一轮,
# 残留勾选的取反后果见 auction-notes 5.2。
WAREHOUSE_CLOSE_RETRIES = 3
# 藏品仓库入口与界面标题的等待上限。两者是同一段 UI 就绪过程(点入口 → 界面加载),
# 用同一个上限, 免得调一处漏一处。
WAREHOUSE_LOAD_TIMEOUT = 10

# 结算界面的「一键出售」: 跳过动画刚点完, 按钮本来就该在, 给短超时即可.
ONE_CLICK_SELL_TIMEOUT = 3
# 点完一键出售要等服务端返回才弹出「获得物品」提示条, 给足时间.
POPUP_CLOSE_TIMEOUT = 5


def normalize_mode(raw_mode: str) -> str:
    """出售模式取值兜底: 未知值一律按「不出售」处理, 避免脏配置意外清空仓库。"""
    return raw_mode if raw_mode in SELL_MODES else SELL_MODE_OFF


def uses_collection_sell(mode: str) -> bool:
    """本轮是否需要走「藏品仓库」出售流程: 满仓检测 + 品质勾选 + 确认出售。

    「拍卖成功一键出售」用游戏自带的按钮在结算界面直接卖, 不碰仓库, 也不做满仓检测,
    因此不算这条流程 —— 见 sell_on_settlement_screen。
    """
    return mode not in (SELL_MODE_OFF, SELL_MODE_ONE_CLICK)


def read_quality_list(config: dict, key: str) -> list[str]:
    """读取某个出售品质清单, 值不是列表时按空清单处理。"""
    raw = config.get(key, [])
    if not isinstance(raw, (list, tuple)):
        return []
    return [name for name in QUALITY_KEYS if name in raw]


def read_sell_qualities(config: dict) -> list[str]:
    """读取本轮要出售的品质清单(勾选即出售, 清洗规则见 read_quality_list)。

    曾按当日低保阶段在双清单间自动切换, 已合并为单一「出售品质」配置;
    低保与资产的联动说明见配置项描述与 auction-notes 4。
    """
    return read_quality_list(config, CONF_SELL_QUALITIES)


def is_selection_confirmed(
    selected: int, sell_value: int | None, *, require_sale: bool = False
) -> bool | None:
    """判断品质勾选是否被出售价值证实。

    Args:
        selected: 实际点击勾选的品质数量.
        sell_value: 读到的出售价值, None 表示读不出.
        require_sale: 本次出售是否必须真的清掉藏品(满仓时无法继续出价).

    Returns:
        True: 读到正数, 勾选确实生效; 或本来就没有要出售的品质且不要求出售.
        False: 读到了 0; 或要求出售却一个品质都没勾上.
        None: 读不出(界面重绘中的空白态), 无法判断 —— 调用方必须按「未确认」处理.
    """
    if selected <= 0:
        # 没有勾选任何品质: 本来就不该清掉藏品, 但要求出售时不能算成功 ——
        # 否则会把满仓标记清掉, 仓库一件没腾却报告成功, 之后每轮出价都失败.
        return not require_sale
    if sell_value is None:
        return None
    return sell_value > 0


def detect_inventory_full(
    task: AuctionSellOps, boxes: AuctionBoxes, timeout: float
) -> bool | None:
    """检测主界面的库存不足提示, 命中表示满仓无法继续拍卖。

    这是可选的观测步骤: 没有可用时间时按「未检测」处理(返回 None), 不抛异常,
    否则单轮 deadline 用尽会让整个结算后处理崩掉。

    注意不能返回 False: False 的语义是「确定没满仓」, 写进 PostRoundState 后
    轮次末尾的 run_round_end_sell 会因为「不是 None」而不再补测, 满仓会被静默漏掉
    —— 之后每轮出价都失败, 却永远不触发清理。返回 None 时调用方(那里 deadline
    为空, 有完整的 INVENTORY_FULL_TIMEOUT 可用)会重新检测。
    """
    if timeout <= 0:
        task.log_debug("满仓检测没有可用时间, 跳过本次检测")
        return None

    found = task.wait_ocr(
        box=boxes.insufficient,
        match=RE_COLLECTION_INSUFFICIENT,
        time_out=timeout,
        settle_time=0.5,
        raise_if_not_found=False,
    )
    if found:
        task.log_info("检测到库存不足提示, 当前处于满仓状态")
    return bool(found)


def probe_inventory_banner(task: AuctionSellOps, boxes: AuctionBoxes) -> bool:
    """单帧探测主界面的库存不足横幅, 命中表示此刻满仓信号在屏上。

    横幅在拍卖结束回到主界面时一闪而过(2026-10-05 用户实测, 见 auction-notes
    5.2), detect_inventory_full 的等待窗口开始时可能已经消失; 探针供标题确认后
    与弹窗轮询中按帧调用, 命中结论由调用方落账, 本函数不做记录也不打日志。
    """
    return bool(
        task.ocr(box=boxes.insufficient, match=RE_COLLECTION_INSUFFICIENT, log=False)
    )


def _sell_red_enabled(task: AuctionSellOps) -> bool:
    """「拍卖成功出售红」是否勾选; 脏配置(非列表)按未勾选处理。

    与任务侧 _assist_enabled 同规: 少卖一次品质是可恢复的, 不该因脏配置去点
    不存在的按钮。
    """
    selected = task.config.get(CONF_ASSIST_FEATURES, ())
    return isinstance(selected, list) and ASSIST_SELL_RED in selected


def _sell_red_allowed(task: AuctionSellOps, result_value: int | None, sell_limit: int) -> bool:
    """「低于此价才卖红」判定: 成交价值低于上限才放行出售红。

    result_value 是结算面板已读的当前估价(None = 未读出); 未读出按不放行处理
    并告警 —— 判定失败的保守方向是保留红色藏品, 少卖可手动补卖。放行返回 True,
    拦截返回 False(调用方跳过出售红, 也不走「获得物品」处理 —— 没有出售就没有
    提示条)。
    """
    if result_value is None:
        task.log_warning("成交价值未读出, 无法核对「低于此价才卖红」, 红色藏品保留不卖")
        return False
    if result_value >= sell_limit:
        task.log_info(
            f"成交价值 {result_value} 不低于「低于此价才卖红」的 {sell_limit}, 红色藏品保留不卖"
        )
        return False
    task.log_info(f"成交价值 {result_value} 低于「低于此价才卖红」的 {sell_limit}, 执行出售红")
    return True


def detect_one_click_sell(
    task: AuctionSellOps, boxes: AuctionBoxes, deadline: float
) -> list:
    """结算界面「一键出售」按钮的 OCR 检测, 返回命中框列表(空 = 未识别到)。

    按钮只在拍卖成功(拍到东西)后才出现, 是结算读数之外唯一可靠的成交依据:
    settle.finish_auction 以命中与否决定结算读数是否计为成交(含截图门控,
    见 auction-notes 4), sell_on_settlement_screen 以命中框作为点击目标。
    识别区域与首字「一」丢字容错见 auction-notes 2.5/3。单轮 deadline 用尽
    时按未命中处理, 由调用方按各自语境补充日志。
    """
    timeout = task._optional_timeout(deadline, ONE_CLICK_SELL_TIMEOUT)
    if timeout is None:
        return []
    return task.wait_ocr(
        box=boxes.one_click_sell,
        match=RE_ONE_CLICK_SELL,
        time_out=timeout,
        raise_if_not_found=False,
        settle_time=0.5,
    )


def sell_on_settlement_screen(
    task: AuctionSellOps,
    boxes: AuctionBoxes,
    deadline: float,
    result_value: int | None = None,
) -> None:
    """结算界面「一键出售」: 点游戏自带的按钮卖掉本局藏品, 再关掉「获得物品」提示。

    按钮只在拍卖成功(拍到东西)后才出现, 找不到就跳过 —— 流拍时本来就没有可卖的
    东西, 不该让整轮失败。品质红(勾选「拍卖成功出售红」时)也只在按钮确认出现后
    才点: 流拍的结算界面没有品质圆点行, 不能盲点那个坐标。

    result_value 是结算面板已读的成交价值(当前估价), 由 settle.finish_auction 透传:
    「低于此价才卖红」> 0 时, 成交价值低于它才执行出售红, 不低于或读不出则红色
    藏品保留不卖 —— 判定失败的保守方向是保留, 少卖可手动补卖, 误卖贵重红不可逆
    (比较对象与沿革见 auction-notes 7)。

    这条路径不碰藏品仓库, 也不做满仓检测: 一键出售是游戏自己的整包出售, 没有品质
    勾选, 也读不到「出售价值」, 因此不复用仓库出售流程。

    关提示条分两步: 先用「点击空白区域关闭」确认提示条出现了, 再点提示条以外的
    空白区域 —— 游戏要求点的是空白区域, 不是那句提示文字本身。

    单轮 deadline 用尽时按「没时间」跳过, 而不是抛异常: 结算已经完成, 不该因为
    时间不够把整轮判成失败。
    """
    sell_timeout = task._optional_timeout(deadline, ONE_CLICK_SELL_TIMEOUT)
    if sell_timeout is None:
        task.log_warning("单轮时间已用尽, 跳过结算界面的「一键出售」")
        return
    # 先确认按钮出现(= 拍卖成功)再动品质圆点; 点击目标保持检测返回的 OCR 命中框,
    # 与 _wait_operate_click 的行为一致。
    found = detect_one_click_sell(task, boxes, deadline)
    if not found:
        task.log_info("结算界面未出现「一键出售」, 跳过(流拍时没有可出售的藏品)")
        return
    if _sell_red_enabled(task):
        cfg = auction_config_read.ConfigReader(task.config, task.log_warning)
        sell_limit = cfg.read_int(
            CONF_SELL_RED_MAX,
            0,
            warn="「低于此价才卖红」配置无效, 按不限处理",
        )
        if sell_limit > 0 and not _sell_red_allowed(task, result_value, sell_limit):
            return
        task.operate_click(*POS_SETTLE_QUALITY_RED, after_sleep=0.5)
        task.log_info("已点选品质红, 一键出售只卖红色品质藏品")
    task.operate_click(found, after_sleep=1)
    task.log_info("已点击一键出售")

    popup_timeout = task._optional_timeout(deadline, POPUP_CLOSE_TIMEOUT)
    if popup_timeout is None:
        task.log_warning("单轮时间已用尽, 「获得物品」提示未处理")
        return
    # 只检测不点击: 要关掉提示条, 得点提示条以外的空白区域, 而不是提示文字本身.
    hint = task.wait_ocr(
        box=boxes.popup_close_hint,
        match=RE_POPUP_CLOSE_HINT,
        time_out=popup_timeout,
        raise_if_not_found=False,
        settle_time=0.5,
    )
    if not hint:
        task.log_warning("「获得物品」提示未出现, 退出拍卖前请留意残留弹窗")
        return
    task.operate_click(boxes.popup_blank, after_sleep=0.5)
    task.log_info("已点击空白区域关闭「获得物品」提示")


def run_round_end_sell(
    task: AuctionSellOps,
    boxes: AuctionBoxes,
    deadline: float | None = None,
    state: PostRoundState | None = None,
) -> bool | None:
    """按出售模式决定本轮是否出售藏品。

    「满仓时清理」只在主界面出现库存不足提示时出售; 「按间隔出售」每 N 轮出售一次,
    且间隔没到就满仓时提前出售(库存不足无法继续出价, 等间隔会卡住拍卖)。
    「不出售」与「拍卖成功一键出售」不走这里: 后者在结算界面就卖完了。

    仅应在拍卖结束回到主界面后调用, 否则仓库入口 OCR 无法命中。

    Returns:
        bool | None: 跨轮满仓标记的落账结论, 由任务侧写回 _inventory_stuck;
        None 表示本轮没有出售动作(模式不走这里 / 间隔与满仓都未命中), 标记
        保持原值; True 仍满仓 / False 已清掉; WaitFailedException 向上传播时
        同样不落账 (误置与误清的代价见 auction-notes 5.2)。
    """
    mode = normalize_mode(str(task.config.get(CONF_SELL_MODE, SELL_MODE_OFF)))
    if not uses_collection_sell(mode):
        # 「不出售」不碰仓库; 「一键出售」在结算界面就卖完了(见 sell_on_settlement_screen),
        # 与轮次末尾无关.
        return None

    sell_interval = 0
    if mode == SELL_MODE_INTERVAL:
        cfg = auction_config_read.ConfigReader(task.config, task.log_warning)
        sell_interval = cfg.read_int(
            CONF_SELL_INTERVAL,
            0,
            warn="出售间隔次数配置无效, 按满仓清理处理",
        )
        if sell_interval <= 0:
            # 间隔无效时退化成「满仓时清理」, 而不是直接不出售.
            task.log_warning("出售间隔次数未设置, 本次按满仓清理处理")

    state = state or PostRoundState()
    inventory_full = state.inventory_full
    if inventory_full is None:
        # 结算后观测没测出满仓结论(含当时 deadline 用尽)时在这里补测:
        # 本方法在轮次末尾调用, deadline 为空, 有完整的 INVENTORY_FULL_TIMEOUT 可用。
        # 预算耗尽(None)按「未测出」处理, 轮次末尾的满仓复核会再次裁决。
        budget = task._optional_timeout(deadline, INVENTORY_FULL_TIMEOUT)
        inventory_full = (
            None if budget is None else detect_inventory_full(task, boxes, budget)
        )

    reached_interval = sell_interval > 0 and task.current_round % sell_interval == 0
    if not (reached_interval or inventory_full):
        return None

    if reached_interval:
        task.log_info(f"第 {task.current_round} 轮到达出售间隔 {sell_interval}, 执行定期出售")
    else:
        task.log_info("检测到满仓提示, 提前执行藏品出售")

    # 出售失败固定退出: 不再放宽清单(连续失败放宽已删除, 见 auction-notes 4),
    # 跨轮满仓标记由任务侧落账, 这里只报告结论。三种结果必须区分:
    # - True(已卖掉): 返回 False(已清掉), 恢复正常拍卖;
    # - None / WaitFailedException: 结果未知(确认出售可能已点击), 返回满仓现状
    #   让下一轮先重试清理, 而不是当成已清理去空烧匹配阶段; 异常路径不返回,
    #   任务侧不落账;
    # - False: 明确失败, 同样返回满仓现状 —— 下一轮的满仓复核(见任务侧
    #   _run_single_round)会用库存提示重新裁决, 不会靠卖掉保留品质脱困。
    # 满仓现状(True/False/None)原样返回: None(未测出)由任务侧按「无新结论」
    # 不落账, 不得压成 False 把「未测出」落账成「已清掉」。
    qualities = read_sell_qualities(task.config)
    try:
        sold = run_collections(
            task,
            boxes,
            deadline,
            qualities,
            require_sale=bool(inventory_full),
        )
    except WaitFailedException as e:
        # 结果未知: 异常向上传播, 任务侧不落账 (误置的后果见 auction-notes 5.2),
        # 由 _try_sell_collections 兜底并放弃本轮出售。
        task.log_warning(f"藏品出售超出预算, 结果未知: {e}")
        raise
    if sold:
        return False
    if sold is None:
        task.log_warning("藏品出售结果未知")
    return inventory_full


def run_collections(
    task: AuctionSellOps,
    boxes: AuctionBoxes,
    deadline: float | None = None,
    sell_qualities: list[str] | tuple[str, ...] = (),
    *,
    require_sale: bool = False,
) -> bool | None:
    """尝试出售藏品, deadline 为空时保持定期清理分支的原有行为。

    sell_qualities 是本次要卖掉的品质清单(由调用方读「出售品质」配置算好), 勾选即出售。

    require_sale 表示本次出售必须真的清掉藏品(满仓时无法继续出价)。此时「一个品质
    都没勾上」不能再算成功 —— 那会把满仓标记清掉, 之后每轮出价都失败却不再重试清理。

    Returns:
        True: 出售确认生效(读到正数), 或没有勾选品质且本次不要求出售。
        False: 确认失败: 已勾选品质但出售价值确认为 0(含重勾后仍为 0),
            或要求出售却一个品质都没勾上 —— 这类读数是「没清掉藏品」的直接证据。
        None: 本次尝试没有产生可信证据: 出售价值读不出(确认出售已点击, 结果未知),
            或流程根本没走通(仓库入口/标题未就绪, 流程异常)。出售是否生效未知,
            调用方必须与超时同等对待, 不能当成明确失败。
    """
    task.log_info("开始执行藏品出售流程")
    try:
        # 满仓等提示弹窗会盖住仓库入口, 先兜掉再找入口.
        task._dismiss_notice_popup(boxes, deadline, "出售流程开始前")

        warehouse_button = task._wait_operate_click(
            boxes.warehouse_btn,
            RE_WAREHOUSE,
            task._remaining_timeout(deadline, WAREHOUSE_LOAD_TIMEOUT),
        )
        if not warehouse_button:
            task.log_warning("藏品仓库入口未出现, 取消出售流程")
            return None
        task._bounded_sleep(deadline, 1)
        task.log_debug("藏品仓库入口已点击")

        if not task.wait_ocr(
            box=boxes.warehouse_title,
            match=RE_WAREHOUSE,
            time_out=task._remaining_timeout(deadline, WAREHOUSE_LOAD_TIMEOUT),
            raise_if_not_found=False,
            settle_time=0.5,
        ):
            task.log_warning("藏品仓库界面加载失败, 取消出售流程")
            return None
        task.log_debug("藏品仓库界面加载完成")

        # 上次出售未走完会把仓库留在出售模式, 此时圆钮位置是「取消」,
        # 再点会退出出售模式 (后果见 auction-notes 5.2)。
        if is_sell_mode(task, boxes, task._remaining_timeout(deadline, 1)):
            task.log_warning("藏品仓库已处于出售模式(上次出售未走完), 跳过点击出售")
        else:
            task.operate_click(boxes.sell, after_sleep=0)
            task._bounded_sleep(deadline, 1)

        selected = select_quality_filters(task, deadline, sell_qualities)
        # 残留勾选会让这一遍无条件点击全部取反, ensure_sell_value 读到 0 时会重勾一次兜住.
        sell_value = ensure_sell_value(task, boxes, deadline, selected, sell_qualities)
        # 只有读到正数才算勾选生效: 读到 0 或读不出(界面重绘中的空白态)都不能算成功,
        # 否则会在毫无证据的情况下打印「藏品出售完成」, 掩盖「一个品质都没勾上」.
        selection_ok = is_selection_confirmed(selected, sell_value, require_sale=require_sale)

        task.operate_click(boxes.confirm_sell, after_sleep=0)
        task._bounded_sleep(deadline, 1.5)
        task.log_debug("已点击确认出售")

        task.operate_click(boxes.blank, after_sleep=0)
        task._bounded_sleep(deadline, 0.5)
        close_warehouse(task, boxes)
        task._bounded_sleep(deadline, 1)

        # 出售所得由服务端发放, 「获得物品」弹窗可能在确认出售之后才淡入
        # (同属「点击空白」类, 见 auction-notes 5.5), 收仓回到主界面后兜一次。
        # 探测刻意排在收仓之后: 它的提示类通道会点「确认」, 不能在仓库界面上跑;
        # 先于收仓弹出的弹窗会吞掉第一次关闭点击, 由 close_warehouse 的重试自愈。
        task._dismiss_notice_popup(boxes, deadline, "仓库出售完成后")

        if selection_ok is None:
            task.log_warning("出售价值未读出, 本次出售是否清掉藏品无法确认")
            return None
        if not selection_ok:
            if selected <= 0:
                task.log_warning("没有勾选任何品质, 本次出售没有清掉任何藏品")
            else:
                task.log_warning("品质勾选未生效, 本次出售没有清掉任何藏品")
            return False
        if selected <= 0:
            task.log_info("没有需要出售的品质, 本次出售未清掉藏品")
            return True
        task.log_info("藏品出售完成")
        return True
    except TaskDisabledException:
        raise
    except WaitFailedException:
        # 单轮超时要向上传播, 但界面得收拾干净再走: 残留的出售模式与勾选态会
        # 污染下一轮 (取反后果见 auction-notes 5.2)。
        close_warehouse(task, boxes)
        raise
    except Exception as e:
        task._log_aux_error("藏品出售失败", e)
        close_warehouse(task, boxes)
        # 异常点可能在「确认出售」之后, 出售是否已生效未知, 与超时同理返回 None.
        return None


def close_warehouse(task: AuctionSellOps, boxes: AuctionBoxes) -> None:
    """关掉藏品仓库界面, 让出售模式和里面的勾选状态一起复位。

    出售流程无论成功还是异常退出都要走到这一步。关闭按钮是无文字图标,
    只能按调用点确认含义; 在主界面点它是空操作, 所以异常发生在「还没打开仓库」
    的阶段时也安全。点完要确认仓库真的关了(标题消失)再重试, 关不掉时只告警
    不抛 —— 这里是异常收尾路径, 再抛异常会盖掉真正的失败原因
    (残留后果见 auction-notes 5.2)。
    """
    for attempt in range(1, WAREHOUSE_CLOSE_RETRIES + 1):
        task.operate_click(boxes.close, after_sleep=0.5)
        if not is_warehouse_open(task, boxes):
            return
        task.log_warning(f"第 {attempt}/{WAREHOUSE_CLOSE_RETRIES} 次点击关闭后藏品仓库仍未收起")
    task.log_warning("藏品仓库界面多次尝试后仍未关闭, 下一轮可能受残留勾选影响")


def is_warehouse_open(task: AuctionSellOps, boxes: AuctionBoxes) -> bool:
    """检测藏品仓库界面是否还在, 复用标题区域的 OCR。"""
    return bool(task.ocr(box=boxes.warehouse_title, match=RE_WAREHOUSE, log=False))


def is_sell_mode(task: AuctionSellOps, boxes: AuctionBoxes, timeout: float) -> bool:
    """检测藏品仓库是否已经处于出售模式。

    出售模式下才会出现「出售价值」条, 用它区分初始视图和出售模式;
    初始视图的同一位置是空网格, OCR 不会命中, 因此读不到就按未进入处理。
    """
    return bool(
        task.wait_ocr(
            box=boxes.sell_label,
            match=RE_SELL_LABEL,
            time_out=timeout,
            raise_if_not_found=False,
            settle_time=0.5,
        )
    )


def select_quality_filters(
    task: AuctionSellOps,
    deadline: float | None,
    sell_qualities: list[str] | tuple[str, ...] = (),
) -> int:
    """勾选要出售的品质按钮, 返回实际点击次数。

    勾选即出售: sell_qualities 里的品质点选, 其余一律保留。清单由调用方读
    「出售品质」配置算好(read_sell_qualities), 这里不再读配置。

    本函数是「无条件点击」: 对同一个品质调用两次会把刚勾上的状态点掉, 常规
    读数校验失败只能换帧重读; 唯一的例外是出售价值换帧重读仍为 0 的「残留
    勾选被点掉」场景, 由 ensure_sell_value 有意再调一次本函数重勾(双重取反
    恢复干净视图)。
    勾选是必须连续的逐点序列, 期间挂起月卡钩子, 点击被吞会让出售读数误判。
    """
    sell = set(sell_qualities)
    clicked = 0
    # 逐项「保留/选择」只留在 DEBUG; INFO 记一次汇总, 避免品质多时每轮刷屏.
    task.log_info(
        f"勾选出售品质: {', '.join(sell_qualities) if sell_qualities else '无'}"
    )

    with auction_interaction.atomic_sequence(task, reason="品质勾选"):
        for quality_name, quality_pos in zip(QUALITY_KEYS, QUALITY_BOXES):
            if quality_name not in sell:
                task.log_debug(f"保留{quality_name}")
                continue
            task.log_debug(f"选择{quality_name}")
            task.operate_click(task.box_of_screen(*quality_pos), after_sleep=0)
            task._bounded_sleep(deadline, SELL_QUALITY_GAP)
            clicked += 1

    return clicked


def ensure_sell_value(
    task: AuctionSellOps,
    boxes: AuctionBoxes,
    deadline: float | None,
    selected: int,
    sell_qualities: list[str] | tuple[str, ...] = (),
) -> int | None:
    """校验品质勾选是否真的生效: 读数没到位时先换帧重读, 仍是 0 才重勾一次。

    sell_qualities 与首次勾选用的是同一批品质, 重勾必须原样传回去。

    这里读「出售价值」来验证勾选生效(读到正数说明生效)。读到 0 和读不出要分开
    处理, 两者的成因不同 (完整推导见 auction-notes 5.2):

    - **读不出**(界面重绘期间的空白态)只换帧重读, 绝不能重勾 —— 重勾会把
      刚勾上的品质点掉(双重取反), 让「重试」必然失败;
    - **换帧后仍是 0**, 说明仓库里本来就有残留勾选且恰好被这一遍点击全部点掉,
      再点一遍目标集合能把状态拉回来, 且只会卖掉本该出售的品质。

    Returns:
        读到正数时返回该数值; 其余情况返回最后一次读数(0 或 None), 由调用方判定。
    """
    if selected <= 0:
        # 没有任何品质需要出售, 不必校验, 也不必花时间读数值.
        return None

    retries = max(SELL_SELECT_RETRIES, 1)
    value: int | None = None
    for attempt in range(retries):
        if attempt > 0:
            # 必须换帧: 两次读取落在同一帧上会读到同样的空白值.
            task.next_frame()
            task._bounded_sleep(deadline, SELL_QUALITY_GAP)
        value = read_sell_value(task, boxes, task._remaining_timeout(deadline, SELL_VALUE_TIMEOUT))
        if value is None:
            # 界面重绘中的空白态, 重读一次再放弃.
            task.log_warning("出售价值未读出, 无法确认品质勾选是否生效")
            continue
        if value > 0:
            task.log_info(f"品质勾选生效, 出售价值 {value}")
            return value
        task.log_warning(f"已勾选 {selected} 个品质但出售价值为 {value}, 换帧后重读")

    if value == 0:
        # 换帧重读后仍是 0, 才按「残留勾选被这一遍点掉」处理. 只重勾一次:
        # 若本来就是干净的初始视图(目标品质都没藏品), 重勾得到空集, 与不重勾
        # 的结果一样(都卖不掉), 不会更糟; 无限重试没有意义.
        task.log_warning("换帧重读后出售价值仍为 0, 按残留勾选被点掉处理, 重新勾选一次")
        select_quality_filters(task, deadline, sell_qualities)
        value = read_sell_value(task, boxes, task._remaining_timeout(deadline, SELL_VALUE_TIMEOUT))
        if value is None:
            task.log_warning("重新勾选后出售价值未读出, 本次出售是否生效无法确认")
        elif value <= 0:
            task.log_warning(f"重新勾选后出售价值仍为 {value}, 本次出售不会清掉任何藏品")
        else:
            task.log_info(f"重新勾选后品质勾选生效, 出售价值 {value}")
    return value


def read_sell_value(task: AuctionSellOps, boxes: AuctionBoxes, timeout: float) -> int | None:
    """读取出售模式下的「出售价值」数值, 未识别或解析失败时返回 None。"""
    ops = task._reading_ops()
    return ops.asset_once(ops, boxes.sell_value, timeout, "出售价值")
