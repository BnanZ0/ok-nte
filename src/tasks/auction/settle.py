"""拍卖结算阶段: 等待结算界面, 跳过动画, 一键出售, 退出拍卖并回主界面。

模块函数的第一个参数 actions 是 SettleActions: 冻结的窄动作接口, 截图/点击/
日志等框架 API 与跨域动作(出售/结算后观测/补货)经它访问, 本模块不持有任务
对象。结算后的多领域观测编排放在 auction.post_round, 由任务侧注入; 等待循环
与收尾的先后约束见 auction-notes 5.7。
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from ok import WaitFailedException

from src.tasks.auction.layout import RE_EXIT, RE_MAIN_TITLE, RE_SKIP, AuctionBoxes
from src.tasks.auction.options import SELL_MODE_ONE_CLICK

# 结算阶段实测远短于该上限, 保留用于界面卡死时兜底; 与 RESULT_MAX_LOOPS 的
# 关系见 auction-notes 3。
RESULT_TIMEOUT = 90
# 循环次数上限与 RESULT_TIMEOUT 是互补的两重保险, 不是重复, 两个都不要删
# (实测依据见 auction-notes 3)。
RESULT_MAX_LOOPS = 180
# 结算画面存在跳过动画已出现而退出按钮尚未渲染的中间态, 等待不能太短.
EXIT_BUTTON_TIMEOUT = 10
# 主界面「即刻落槌」标题的等待上限: 等控件出现, 与其他阶段的同类等待语义独立。
MAIN_TITLE_TIMEOUT = 15
# 标题被弹窗遮罩压住时, 关掉弹窗后重探标题的等待上限: 弹窗关闭后标题立即可读,
# 上限只防渲染卡顿。
MAIN_TITLE_REPROBE_TIMEOUT = 5


@dataclass(frozen=True)
class SettleActions:
    """结算阶段所需的界面与跨域动作, 由任务侧在进入阶段时装配。

    finish_auction 指向任务侧的同名入口: 一方面让等待循环与收尾解耦后仍保持
    现有测试 seam, 另一方面收尾内部需要再次装配动作(观测/出售各自有独立预算)。
    """

    is_match_screen: Callable[[AuctionBoxes], bool]
    is_bid_screen: Callable[[AuctionBoxes], bool]
    ocr: Callable[..., list]
    wait_ocr: Callable[..., list]
    next_frame: Callable[[], object]
    sleep: Callable[[float], None]
    operate_click: Callable[..., object]
    wait_operate_click: Callable[..., bool]
    remaining_timeout: Callable[[float | None, float], float]
    read_result_value: Callable[[AuctionBoxes, float], int | None]
    record_result_value: Callable[[int, bool], None]
    detect_one_click_sell: Callable[[AuctionBoxes, float], list]
    sell_mode: Callable[[], str]
    sell_on_settlement: Callable[[AuctionBoxes, float, int | None], None]
    observe_post_round: Callable[[AuctionBoxes, float], None]
    run_post_round: Callable[[AuctionBoxes, float], None]
    dismiss_notice: Callable[..., bool]
    finish_auction: Callable[[AuctionBoxes, list, float], None]
    poll_interval: float
    log_info: Callable[[str], None]
    log_warning: Callable[[str], None]


def run_settle(actions: SettleActions, boxes: AuctionBoxes, deadline: float) -> bool:
    """结果阶段: 等待结算, 处理跳过动画或返回匹配界面。

    只有「等待结算」受 RESULT_TIMEOUT 约束; 检测到结束状态之后的收尾动作
    (跳过动画 / 一键出售 / 退出拍卖 / 结算后观测) 一律用整轮 deadline。
    两者不能共用一个截止时间, 「主界面判定必须排在跳过动画之前」的约束,
    见 auction-notes 5.7。

    Returns:
        bool: True 表示拍卖结束; False 表示结算时又回到出价界面(下一轮出价)。
    """
    actions.log_info("结算阶段开始, 等待拍卖结果")
    result_deadline = min(deadline, time.monotonic() + RESULT_TIMEOUT)
    loop_count = 0

    while loop_count < RESULT_MAX_LOOPS and time.monotonic() < result_deadline:
        loop_count += 1
        actions.next_frame()

        # match 判定必须在 skip 之前: skip 区域与主界面标题框高度重叠, 画面
        # 已经回到主界面时先命中 skip 会误走收尾流程, 找不到退出按钮而抛异常.
        if actions.is_match_screen(boxes):
            actions.log_info("返回匹配界面")
            actions.observe_post_round(boxes, deadline)
            return True

        skip_results = actions.ocr(box=boxes.skip_area, match=RE_SKIP)
        if skip_results:
            actions.finish_auction(boxes, skip_results, deadline)
            return True

        if actions.is_bid_screen(boxes):
            actions.log_info("进入下一轮出价")
            return False

        actions.sleep(actions.poll_interval)

    raise WaitFailedException("结算阶段超时, 未检测到结束状态")


def finish_auction(
    actions: SettleActions, boxes: AuctionBoxes, skip_results: list, deadline: float
) -> None:
    """拍卖结束处理: 跳过动画, 一键出售, 退出拍卖, 并在主界面执行结算后的辅助操作。"""
    actions.log_info("检测到跳过动画")
    actions.operate_click(skip_results, after_sleep=0.5)

    # 结算价值在点跳过后、一键出售前读: 面板此刻完整可见, 一键出售后面板可能
    # 随藏品被卖掉而变化。读不出只少一条记录, 不影响后续流程。
    result_value = actions.read_result_value(boxes, deadline)
    if result_value is not None:
        # 成交判据是「一键出售」按钮 OCR 命中(= 拍卖成功), 不是结算读数本身:
        # 流拍的结算面板也可能读出非零数字, 只凭数值会误计成交并误触发截图
        # (见 auction-notes 4)。检测排在读数之后: 读数预算已让面板渲染稳定;
        # 读不出时不检测, 不给流拍轮次白增等待。
        sold = bool(actions.detect_one_click_sell(boxes, deadline))
        actions.record_result_value(result_value, sold)

    # 「拍卖成功一键出售」必须在退出拍卖之前完成: 一键出售按钮只在结算界面存在,
    # 且只由出售模式驱动 (「成交价值出售上限」已删除, 见 auction-notes 4)。
    # 已读的成交价值透传给出售: 「低于此价才卖红」用它决定卖不卖红 (见 auction-notes 7)。
    if actions.sell_mode() == SELL_MODE_ONE_CLICK:
        actions.sell_on_settlement(boxes, deadline, result_value)

    exit_button = actions.wait_operate_click(
        boxes.exit,
        RE_EXIT,
        actions.remaining_timeout(deadline, EXIT_BUTTON_TIMEOUT),
        after_sleep=0.5,
    )
    if not exit_button:
        raise WaitFailedException("退出拍卖按钮未出现")
    actions.log_info("退出拍卖")

    actions.log_info("等待主界面稳定")
    # 「我的资产」在结算等界面也会出现, 改用只在拍卖主界面出现的「即刻落槌」判定.
    main_title = actions.wait_ocr(
        box=boxes.main_title,
        match=RE_MAIN_TITLE,
        time_out=actions.remaining_timeout(deadline, MAIN_TITLE_TIMEOUT),
        raise_if_not_found=False,
        settle_time=0.5,
        post_action=lambda: actions.sleep(0.5),
    )
    if not main_title and actions.dismiss_notice(boxes, deadline, "回主界面后"):
        # 「获得物品」弹窗的全屏遮罩会压暗主界面标题的 OCR (见 auction-notes 5.5):
        # 探到并关掉弹窗后重探一次标题, 读到就照常走结算后处理, 不再整段跳过
        # 本轮收尾(满仓清理/资产观测/低保领取)。
        reprobe = min(MAIN_TITLE_REPROBE_TIMEOUT, deadline - time.monotonic())
        if reprobe > 0:
            main_title = actions.wait_ocr(
                box=boxes.main_title,
                match=RE_MAIN_TITLE,
                time_out=reprobe,
                raise_if_not_found=False,
                settle_time=0.5,
            )
    if not main_title:
        actions.log_warning("主界面「即刻落槌」标题未识别, 跳过本轮结算后处理")
        return

    actions.log_info("退出拍卖后回到主界面, 开始结算后处理")
    actions.run_post_round(boxes, deadline)
