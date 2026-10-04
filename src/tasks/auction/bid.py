"""拍卖出价执行: 单次出价尝试的完整动作序列。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: 资产读取、价格输入、
放弃与仪器等能力都经任务侧适配器访问, 允许访问面由 contracts.AuctionBidOps
窄协议声明。出价循环与结果等待属阶段协调, 留在任务类 (_stage_bid_loop /
_wait_bid_outcome); 本模块只承载「一次竞价动作」的领域规则: 资产为零的
二次确认、无心弃局、仪器先于出价按钮使用、键盘弹出判定与价格输入的先后顺序。
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
    asset_value = task._read_asset_value(
        boxes.asset_value,
        task._remaining_timeout(deadline, auction_reading.ASSET_OCR_TIMEOUT),
    )
    if asset_value is None:
        task.log_warning("资产值识别失败, 准备重试本次出价")
        raise WaitFailedException("资产值未识别")

    # 资产为 0 时放弃本轮出价; 单次误读就放弃整场拍卖代价过高, 放弃前需二次确认.
    if asset_value == 0:
        confirm_value = task._read_asset_value(
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
    task._input_fixed_price(boxes, deadline=deadline)

    bid_confirmed = task.wait_until(
        lambda: not task._is_bid_screen(boxes),
        time_out=task._remaining_timeout(deadline, 5),
        settle_time=0.5,
        raise_if_not_found=False,
    )
    if not bid_confirmed:
        raise WaitFailedException("出价确认失败: 出价按钮仍存在")

    return True
