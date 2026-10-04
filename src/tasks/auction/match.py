"""拍卖匹配与确认阶段: 识别四态界面, 点击开始匹配, 掉线探测, 确认出价。

模块函数的第一个参数 actions 是 MatchActions: 冻结的窄动作接口, OCR/点击/
日志等框架 API 与回场续跑经它访问, 本模块不持有任务对象。匹配阶段的局部
预算与整轮 deadline 的关系见 run_match 文档; 阶段推进的合法性由
auction_round 的状态机裁决, 本模块只产出界面状态事件。
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from ok import WaitFailedException

from src.tasks.auction.layout import RE_CONFIRM, RE_MATCH, AuctionBoxes, AuctionState

# 匹配阶段是局部预算, 每次进入 run_match 都重新计一次 —— 确认失败后会再调一次,
# 所以匹配阶段累计上限是 2 x MATCH_TIMEOUT, 只由整轮 deadline 兜底。
MATCH_TIMEOUT = 120
# 点击成功后界面切换很快, 点击后短时间内按钮仍在原位就说明这次点击没有生效,
# 立刻重试比白等 MATCH_CLICK_TIMEOUT 划算 (实测见 auction-notes 3)。
MATCH_PROBE_TIMEOUT = 3
# 界面确实在切换(加载动画)时等待后续界面出现的上限。
MATCH_CLICK_TIMEOUT = 30
# 循环次数上限与 MATCH_TIMEOUT 是互补的两重保险, 不是重复, 两个都不要删
# (实测依据见 auction-notes 3)。
MATCH_MAX_LOOPS = 120
# 匹配阶段每 N 次轮询探一次大世界(约 2 秒): 判定要跑一次旋转模板匹配 + 一次血条模板
# 匹配, 比 OCR 贵, 不能每 0.5 秒调一次; 探测只在点击「开始匹配」之后、界面迟迟不变化时
# 才开始.
WORLD_PROBE_INTERVAL = 4
# 等待「确认出价」后的出价界面加载完成。与 BID_RESULT_TIMEOUT 数值相同但语义不同:
# 那个是等「一次出价的结果」(见任务侧 _stage_bid_loop), 这个是等界面渲染出来, 不要合并。
BID_SCREEN_TIMEOUT = 60
# 空闲轮询时探测阻塞弹窗的间隔(轮)与单次预算(秒): 「获得物品」弹窗会在回到主界面
# 后延迟发放(晚于结算后处理的两处探测窗口, 实测见 auction-notes 5.5), 四态判定被
# 压暗背景挡住全不命中, 不处理就空转到 MATCH_TIMEOUT。弹窗显示后是静态画面,
# 单帧 OCR 即可命中, 预算不必用 NOTICE_POPUP_TIMEOUT。
POPUP_PROBE_INTERVAL = 4
POPUP_PROBE_TIMEOUT = 1


@dataclass(frozen=True)
class MatchActions:
    """匹配与确认阶段所需的界面动作与回场续跑入口, 由任务侧装配。"""

    is_bid_screen: Callable[[AuctionBoxes], bool]
    is_confirm_screen: Callable[[AuctionBoxes], bool]
    is_skip_screen: Callable[[AuctionBoxes], bool]
    is_match_screen: Callable[[AuctionBoxes], bool]
    is_world_screen: Callable[[], bool]
    dismiss_notice: Callable[..., bool]
    next_frame: Callable[[], object]
    sleep: Callable[[float], None]
    wait_operate_click: Callable[..., bool]
    wait_ocr: Callable[..., list]
    wait_until: Callable[..., object]
    operate_click: Callable[..., object]
    remaining_timeout: Callable[..., float]
    resume_after_world_drop: Callable[[AuctionBoxes, float], AuctionState]
    poll_interval: float
    log_info: Callable[[str], None]
    log_debug: Callable[[str], None]
    log_warning: Callable[[str], None]


def run_match(actions: MatchActions, boxes: AuctionBoxes, deadline: float) -> AuctionState:
    """匹配阶段: 等待进入确认或出价状态。

    仅在检测到"开始匹配"按钮时才尝试点击, 避免界面过渡期无谓的阻塞。

    deadline 是整轮拍卖的绝对截止时间, 不会因点击重试而重置; 但本阶段的
    MATCH_TIMEOUT 是局部预算, 每次进入本方法都重新计一次 —— 确认失败后会
    再调一次本方法, 所以匹配阶段累计上限是 2 x MATCH_TIMEOUT, 只由整轮
    deadline 兜底。两个循环退出条件都保留 (互补关系见 auction-notes 3)。

    掉线是否还能回场由轮次配额决定, 配额挂轮次不走参数 (原因见 auction-notes 5.4)。
    """
    actions.log_info("匹配阶段开始, 等待确认或出价界面")
    stage_deadline = min(deadline, time.monotonic() + MATCH_TIMEOUT)
    loop_count = 0

    while loop_count < MATCH_MAX_LOOPS and time.monotonic() < stage_deadline:
        loop_count += 1
        # 显式取一帧: 下面四次 ocr 共用同一帧, 避免依赖 sleep 清空缓存的副作用.
        actions.next_frame()

        if actions.is_bid_screen(boxes):
            actions.log_info("检测到已在出价界面")
            return AuctionState.BID
        if actions.is_confirm_screen(boxes):
            actions.log_info("检测到已在确认界面")
            return AuctionState.CONFIRM
        if actions.is_skip_screen(boxes):
            actions.log_info("匹配阶段检测到跳过动画, 拍卖已意外结束")
            return AuctionState.SKIP

        if actions.is_match_screen(boxes):
            result = handle_match_click(actions, boxes, stage_deadline)
            if result is AuctionState.WORLD:
                return actions.resume_after_world_drop(boxes, deadline)
            if result is not None:
                return result

        # 四态全不命中的空闲分支才探弹窗, 正常推进零开销; 首个空闲轮询即探,
        # 弹窗在进入匹配阶段前就已弹出时不用再等一个探测间隔。
        if loop_count % POPUP_PROBE_INTERVAL == 1:
            actions.dismiss_notice(
                boxes, stage_deadline, "匹配阶段", timeout=POPUP_PROBE_TIMEOUT
            )
        actions.sleep(actions.poll_interval)

    # 空转到超时前判一次大世界: 网络抖动掉线时四种界面状态全不命中, 直接抛超时会让
    # 本轮白等两分钟。命中大世界就回场后重跑本阶段, 仍在整轮 deadline 内。
    if actions.is_world_screen():
        return actions.resume_after_world_drop(boxes, deadline)

    raise WaitFailedException("匹配阶段超时, 未进入确认或出价界面")


def handle_match_click(
    actions: MatchActions, boxes: AuctionBoxes, stage_deadline: float
) -> AuctionState | None:
    """点击开始匹配, 并等待后续界面状态变化。

    使用统一循环同时检测确认, 出价和跳过界面, 覆盖匹配对局与加载动画。
    点击后 MATCH_PROBE_TIMEOUT 秒内界面毫无变化时判定点击未生效并立刻返回,
    由调用方重新点击; 界面确实在切换(处于加载动画)时最多等待 MATCH_CLICK_TIMEOUT 秒。

    stage_deadline 是匹配阶段的局部截止时间(见 run_match), 不是整轮 deadline;
    所以这里的超时异常要带上阶段名, 不能沿用「单轮拍卖超时」。
    """
    clicked = actions.wait_operate_click(
        boxes.match,
        RE_MATCH,
        actions.remaining_timeout(stage_deadline, 5, "匹配阶段超时, 未进入确认或出价界面"),
    )
    if not clicked:
        return None
    actions.log_debug("已点击开始匹配, 等待状态变化")

    click_deadline = min(stage_deadline, time.monotonic() + MATCH_CLICK_TIMEOUT)
    probe_deadline = min(click_deadline, time.monotonic() + MATCH_PROBE_TIMEOUT)
    loop_count = 0
    while time.monotonic() < click_deadline:
        loop_count += 1
        actions.next_frame()
        if actions.is_confirm_screen(boxes):
            actions.log_info("匹配成功, 进入确认阶段")
            return AuctionState.CONFIRM
        if actions.is_bid_screen(boxes):
            actions.log_info("匹配成功, 进入出价阶段")
            return AuctionState.BID
        if actions.is_skip_screen(boxes):
            actions.log_info("匹配阶段检测到跳过动画")
            return AuctionState.SKIP

        # 界面切换时按钮会消失, 这种情况继续等加载动画;
        # 按钮还在原地说明点击没生效, 交回上层立刻重新点击.
        if time.monotonic() >= probe_deadline:
            if actions.is_match_screen(boxes):
                actions.log_warning(
                    f"点击开始匹配后 {MATCH_PROBE_TIMEOUT} 秒界面无变化, "
                    f"判定点击未生效, 立即重新点击"
                )
                return None
            # 按钮已消失却没进后续界面: 正常是加载动画, 也可能是掉线被踢回大世界。
            # 大世界判定比 OCR 贵, 按 WORLD_PROBE_INTERVAL 节流探测。
            if loop_count % WORLD_PROBE_INTERVAL == 0 and actions.is_world_screen():
                actions.log_warning("匹配阶段界面长时间无变化且检测到大世界, 判定为掉线")
                return AuctionState.WORLD

        actions.sleep(actions.poll_interval)

    actions.log_warning(
        f"点击匹配后 {MATCH_CLICK_TIMEOUT} 秒内未检测到后续界面, 将重新检查匹配按钮"
    )
    return None


def run_confirm(actions: MatchActions, boxes: AuctionBoxes, deadline: float) -> bool:
    """确认阶段: 等待并点击确认按钮, 所有等待受整轮 deadline 约束。"""
    actions.log_info("确认阶段开始, 等待确认按钮")
    found = actions.wait_ocr(
        box=boxes.popup_ok,
        match=RE_CONFIRM,
        time_out=actions.remaining_timeout(deadline, 15),
        raise_if_not_found=False,
        settle_time=0.5,
    )
    if not found:
        actions.log_warning("确认按钮在阶段等待时间内未出现")
        return False

    actions.operate_click(boxes.popup_ok, after_sleep=0.5)
    confirmed = actions.wait_until(
        lambda: not actions.is_confirm_screen(boxes),
        time_out=actions.remaining_timeout(deadline, 5),
        settle_time=0.5,
        raise_if_not_found=False,
    )
    if not confirmed:
        actions.log_warning("点击确认按钮后按钮仍存在, 本次确认失败")
        return False

    actions.log_info("确认完成, 等待进入出价界面")
    return True


def wait_bid_screen(actions: MatchActions, boxes: AuctionBoxes, deadline: float) -> None:
    """等待确认后的出价界面加载完成。"""
    actions.log_info("等待进入出价界面")
    ready = actions.wait_until(
        lambda: actions.is_bid_screen(boxes),
        time_out=actions.remaining_timeout(deadline, BID_SCREEN_TIMEOUT),
        settle_time=0.5,
        post_action=lambda: actions.sleep(0.5),
        raise_if_not_found=False,
    )
    if not ready:
        raise WaitFailedException("等待出价界面超时")
