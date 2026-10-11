"""拍卖出价执行: 单次出价尝试的完整动作序列。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: 资产读取、价格输入、
放弃与仪器等能力都经任务侧适配器访问, 允许访问面由 contracts.AuctionBidOps
窄协议声明。出价循环与结果等待属阶段协调, 留在任务类 (_stage_bid_loop /
_wait_bid_outcome); 本模块只承载「一次竞价动作」的领域规则: 资产为零的
二次确认、出价高于资产的钳制、无心弃局、仪器先于出价按钮使用、出价
确认后的表情发送、键盘弹出判定与价格输入的先后顺序。
出价序号与上轮出价的落账不在本模块: 键盘确认价经任务侧 _input_fixed_price
落账 last_bid_price, current_bid_count 由出价循环维护。
"""

from ok import WaitFailedException

from src.tasks.auction import reading as auction_reading
from src.tasks.auction.contracts import AuctionBidOps
from src.tasks.auction.layout import RE_BID, RE_BID_PANEL_READY, AuctionBoxes


def attempt_bid(task: AuctionBidOps, boxes: AuctionBoxes, deadline: float) -> bool:
    """单次出价尝试: 包含资产识别和出价面板确认。

    失败时抛出 WaitFailedException, 由调用方决定是否重试。
    Returns:
        bool: True 表示已提交出价; False 表示本次未出价即放弃 —— 资产两次
        读 0, 或展柜未检出永恒之心且勾选了「无心放弃本场」。
    """
    # 等待确认后的加载动画完成, 再判断资产值.
    reading = task._reading_ops()
    asset_value = reading.asset_once(
        reading,
        boxes.asset_value,
        task._remaining_timeout(deadline, auction_reading.ASSET_OCR_TIMEOUT),
    )
    if asset_value is None:
        task.log_warning("资产值识别失败, 准备重试本次出价")
        raise WaitFailedException("资产值未识别")

    # 资产为 0 时放弃本轮出价; 单次误读就放弃整场拍卖代价过高, 放弃前需二次确认.
    if asset_value == 0:
        confirm_value = reading.asset_once(
            reading,
            boxes.asset_value,
            task._remaining_timeout(deadline, auction_reading.ASSET_OCR_TIMEOUT),
        )
        if confirm_value is None:
            task.log_warning("资产值二次识别失败, 准备重试本次出价")
            raise WaitFailedException("资产值未识别")
        if confirm_value != 0:
            task.log_warning(f"资产二次识别为 {confirm_value}, 首次读数 0 判定为误读, 继续出价")
            asset_value = confirm_value
        else:
            task.log_warning("当前资产值两次识别均为 0, 放弃本轮出价")
            if not task._abandon_current_auction(boxes, deadline):
                raise WaitFailedException("放弃出价失败")
            return False

    task.log_debug(f"当前资产值为 {asset_value}, 继续执行出价")

    # 永恒之心检测与「无心弃局」必须在数字键盘弹出前做: 键盘弹窗会盖住展柜
    # 下沿与放弃按钮所在的按钮带, 弹出后再检测/弃局会读不到、点不到
    # (见 auction-notes 5.7)。检测每场只做一次, 之后本场直接复用结论。
    task._check_heart_once(boxes)
    if task._should_abandon_without_heart():
        task.log_info("展柜未检测到永恒之心, 按配置放弃本场拍卖")
        if not task._abandon_current_auction(boxes, deadline):
            raise WaitFailedException("放弃出价失败")
        return False

    # 仪器在按「出价」之前使用 (实机顺序, 见 auction-notes 7): 出价界面上直接
    # 点「仪器」打开列表按槽位使用, 关闭后再点「出价」走键盘输价, 仪器列表全程
    # 不与数字键盘同屏。弃局路径都已在前面 return, 弃局的场不消耗仪器;
    # 仪器按「每口」消耗, 同一口的重试由任务侧按出价序号去重 (见 _use_instrument_once)。
    task._use_instrument_once(boxes, deadline)

    # 数字键盘已经弹出时 BOX_BID 被弹窗盖住, 在 boxes.bid 上等 RE_BID 只会
    # 超时 (见 auction-notes 2.7); 面板已就绪时直接跳过出价按钮。
    keypad_open = task._bid_panel_open(boxes)
    if keypad_open:
        task.log_debug("数字面板已打开, 跳过出价按钮")
        found = True
    else:
        task.log_debug("等待出价按钮")
        found = task._wait_operate_click(
            boxes.bid,
            RE_BID,
            task._remaining_timeout(deadline, 10),
        )
    if not found:
        task.log_warning("出价按钮未出现, 准备重试本次出价")
        raise WaitFailedException("出价按钮未出现")

    task.log_debug("点击出价")
    panel_ready = task.wait_ocr(
        box=boxes.bid_confirm,
        match=RE_BID_PANEL_READY,
        time_out=task._remaining_timeout(deadline, 5),
        raise_if_not_found=False,
        settle_time=0.5,
    )
    if not panel_ready:
        task.log_warning("数字面板未出现, 准备重试本次出价")
        raise WaitFailedException("数字面板未出现")

    task.log_debug("数字面板加载完成")
    price = task._calculate_auction_price(boxes, deadline)
    price = cap_price_to_asset(task, price, asset_value, boxes, deadline)
    task._input_fixed_price(boxes, price=price, deadline=deadline)

    bid_confirmed = task.wait_until(
        lambda: not task._is_bid_screen(boxes),
        time_out=task._remaining_timeout(deadline, 5),
        settle_time=0.5,
        raise_if_not_found=False,
    )
    if not bid_confirmed:
        raise WaitFailedException("出价确认失败: 出价按钮仍存在")

    task._send_emote_once()

    return True


def cap_price_to_asset(
    task: AuctionBidOps,
    price: int,
    asset_value: int,
    boxes: AuctionBoxes,
    deadline: float,
) -> int:
    """算出价高于当前资产时钳到资产: 游戏输入上限就是资产, 超限输入必被钳制。

    键盘校验重读的是被钳制后的显示值, 拿超限价格进输入只会以相同价格重试到
    整轮失败 (见 auction-notes 5.1), 所以在输入前钳到资产改出全部资产。
    钳制前三源复核资产读数 (见 auction-notes 5.1): 首次读数之外, 复读资产框
    一次, 并读键盘未输入时「可输入范围0~N」提示的上限 N —— N 是游戏实时
    给出的可输入上限, 与资产框互为独立读数源, 不在裁框边缘, 不受资产框
    首位截断影响 (7,284 被截成 284 后两读一致放行的事故即靠它纠正)。
    截断只丢前导位不会增值, 正读数取最大是最完整读数; 多源一致偏高的误读
    会被输入校验的回显比对打回, 方向是响亮失败不是静默不足额。复核为 0 或
    读出 None 的源不采纳; 复核修正后价格变为可负担则按原算出价出。两个复核
    源都不可用时仍沿用首次读数钳制(不阻断出价), 告警留痕, 取舍见
    auction-notes 5.1。
    """
    if price <= asset_value:
        return price
    reading = task._reading_ops()
    confirm_value = reading.asset_once(
        reading,
        boxes.asset_value,
        task._remaining_timeout(deadline, auction_reading.ASSET_OCR_TIMEOUT),
    )
    hint_cap = reading.input_range_once(
        reading,
        boxes.price_result,
        task._remaining_timeout(deadline, auction_reading.INPUT_RANGE_CAP_OCR_TIMEOUT),
    )
    if not confirm_value and not hint_cap:
        task.log_warning(
            f"资产复核读数 {confirm_value} 与输入范围提示 {hint_cap} 均不可用, "
            f"按未复核的首次读数 {asset_value} 钳制"
        )
    reads = [value for value in (asset_value, confirm_value, hint_cap) if value]
    best = max(reads) if reads else asset_value
    if best != asset_value:
        task.log_info(
            f"资产复核读数 {best} 与首次读数 {asset_value} 不一致, 改用复核读数"
        )
        asset_value = best
        if price <= asset_value:
            return price
    task.log_info(
        f"算出出价 {price} 高于当前资产 {asset_value}, 游戏输入上限为资产, 改出全部资产"
    )
    return asset_value
