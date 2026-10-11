"""拍卖低保领域: 弹窗领取, 今日次数读数与跨日状态。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: OCR/输入/日志等框架
API 经它访问, 允许访问面由 contracts.AuctionWelfareOps 窄协议声明。当日
领取记录由 WelfareState 承载, 任务实例持有状态对象, 更新只经本模块函数
完成, 模块内部互调直接调用本模块函数, 不再绕道任务私有方法。
低保域的行为常量由本模块定义。领取流程与其状态规则集中在这里, 结算后的
多领域观测编排放在 auction.post_round, 避免低保模块同时承担任务流程调度。

出售流程不再依赖低保阶段(双品质清单已合并, 见 auction-notes 4); 本模块提供
次数状态(观测留痕)与领取能力, 不调用出售流程。
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ok import TaskDisabledException, WaitFailedException

from src.tasks.auction.contracts import AuctionWelfareOps
from src.tasks.auction.layout import (
    FULLWIDTH_NUMERIC,
    RE_CANCEL,
    RE_CLAIM,
    RE_WELFARE,
    RE_WELFARE_COUNTER,
    AuctionBoxes,
)

# 资产低于该值时领取低保金.
WELFARE_ASSET_THRESHOLD = 100000

# 低保金弹窗关闭重试次数, 每日次数用尽时弹窗没有领取按钮, 只能靠取消关闭.
WELFARE_CLOSE_RETRIES = 3

# 低保金每日刷新时刻(游戏每日 5 点重置, 与 src/config.py 的「Monthly Card Time」默认值一致)。
# 只用于跨天清空当日领取记录; 具体次数与上限一律以弹窗读数「今日已领取次数：N/5」为准,
# 所以这里不写死「每日 5 次」—— 游戏改上限时不需要跟着改代码。
WELFARE_RESET_HOUR = 5
# 弹窗次数读数最多读几帧、换帧间隔多少秒: 只读一帧会被弹窗淡入的空白帧落空,
# 而这次读数落空后当天可能再也读不到 (静默失效场景见 auction-notes 5.3)。
WELFARE_COUNTER_READS = 2
WELFARE_COUNTER_RETRY_GAP = 0.3

# 主界面资产观测的单次超时。观测每轮都要做, 给太长会拖累单轮总预算。
ASSET_OBSERVE_TIMEOUT = 5


@dataclass
class WelfareState:
    """当日低保领取记录, 弹窗读数是权威来源。

    读数对本地值是**整体覆盖**而不是取较大者: 覆盖能让「本地多记了一次」在
    下次读数时自愈。任务实例持有本对象, 跨日清零与读数刷新只经本模块函数
    更新 (整体覆盖的依据见 auction-notes 5.3)。
    """

    day: date | None = None
    claims_today: int = 0
    daily_limit: int | None = None


def rollover_welfare_day(state: WelfareState) -> None:
    """跨过游戏每日刷新时刻时清空当日低保领取记录。

    按 5 点切分而不是自然日午夜, 与游戏刷新时刻一致 (切分依据见 auction-notes 5.3)。
    """
    day = (datetime.now() - timedelta(hours=WELFARE_RESET_HOUR)).date()
    if state.day == day:
        return

    state.day = day
    state.claims_today = 0
    state.daily_limit = None


def read_counter(
    task: AuctionWelfareOps, state: WelfareState, boxes: AuctionBoxes, deadline: float | None = None
) -> None:
    """读弹窗正文的「今日已领取次数：N/5」, 刷新当日已领次数与上限(观测留痕)。

    领取流程里会读两次: 打开弹窗后读一次, 点击领取后再读一次(刷新为领取后的
    权威读数, 同时纠正点击落空造成的虚计)。读数只用于日志留痕, 不再驱动出售
    清单决策(双清单已合并, 见 auction-notes 4)。

    用 `task.ocr(match=None)` 拿区域内全部文本拼回整行, 不赌「标签 + 数值」的
    识别粒度 (框架过滤行为见 auction-notes 1.1); 换帧重读的依据见 auction-notes 5.3。
    """
    found = None
    for attempt in range(1, WELFARE_COUNTER_READS + 1):
        texts = [box.name for box in task.ocr(box=boxes.welfare_counter, match=None)]
        found = RE_WELFARE_COUNTER.search("".join(texts).translate(FULLWIDTH_NUMERIC))
        if found is not None:
            break
        if attempt < WELFARE_COUNTER_READS:
            task.next_frame()
            task._bounded_sleep(deadline, WELFARE_COUNTER_RETRY_GAP)

    if found is None:
        task.log_debug("低保金领取次数读数失败, 保持上次记录")
        return

    state.claims_today = int(found.group(1))
    state.daily_limit = int(found.group(2))
    task.log_info(f"今日已领取低保 {state.claims_today}/{state.daily_limit} 次")


def is_dialog_open(task: AuctionWelfareOps, boxes: AuctionBoxes) -> bool:
    """检测低保金弹窗是否仍留在界面上。

    标题与取消按钮任一命中即认为弹窗存在, 避免只有其一被识别时误判为已关闭。
    """
    if task.ocr(box=boxes.welfare_dialog, match=RE_WELFARE):
        return True
    return bool(task.ocr(box=boxes.cancel, match=RE_CANCEL))


def close_dialog(task: AuctionWelfareOps, boxes: AuctionBoxes, deadline: float | None) -> bool:
    """关闭低保金弹窗, 领取成功与否都必须执行。

    每日次数用尽时弹窗没有领取按钮, 只点领取的旧逻辑会把弹窗留在界面上,
    后续所有阶段的识别都会被挡住。这里以界面特征判定弹窗是否还在, 反复点击取消,
    直到弹窗消失或重试次数用尽。
    """
    # 循环走完(break 未发生)即重试用尽, 只有两个 break 点算关闭成功, 成功日志只记一次.
    for attempt in range(1, WELFARE_CLOSE_RETRIES + 1):
        if not is_dialog_open(task, boxes):
            break

        task._wait_click_optional(boxes.cancel, RE_CANCEL, deadline, 3, "取消按钮")
        task._bounded_sleep(deadline, 0.5)

        if not is_dialog_open(task, boxes):
            break

        task.log_warning(f"第 {attempt}/{WELFARE_CLOSE_RETRIES} 次点击取消后低保金弹窗仍未关闭")
    else:
        task.log_warning("低保金弹窗多次尝试后仍未关闭")
        return False

    task.log_info("低保金弹窗已关闭")
    return True


def try_claim(
    task: AuctionWelfareOps, state: WelfareState, boxes: AuctionBoxes, deadline: float | None = None
) -> bool:
    """尝试领取每日低保金, deadline 为空时保持原有独立超时行为。

    低保金是可选的附加流程, 按钮未出现(如当日已领取)或弹窗异常时只跳过本次领取;
    只有单轮超时才向上传播, 避免拖垮已经成功的拍卖轮次。

    每日次数用尽(如 5/5)时界面仍会打开弹窗但没有领取按钮, 此时必须继续关闭弹窗,
    否则弹窗会一直盖住拍卖界面, 让后续所有阶段都识别不到。
    """
    try:
        if not task._wait_click_optional(boxes.welfare_btn, RE_WELFARE, deadline, 5, "低保金按钮"):
            return False
        task._bounded_sleep(deadline, 0.5)

        # 弹窗已经打开了, 顺手把「今日已领取次数：N/5」读回来留痕;
        # 点击领取后再读一次, 纠正点击落空造成的虚计。
        read_counter(task, state, boxes, deadline)

        if task._wait_click_optional(boxes.popup_ok, RE_CLAIM, deadline, 5, "领取按钮"):
            task._bounded_sleep(deadline, 0.5)
            task.log_debug("已点击领取按钮")
            # 点击已发出不代表领取已生效, 重读权威读数而不是盲目 +1;
            # 读不到就保持领取前的值, 代价不对称的分析见 auction-notes 5.3。
            read_counter(task, state, boxes, deadline)
        else:
            task.log_info("未检测到领取按钮(今日次数可能已用尽), 直接关闭低保金弹窗")

        if not close_dialog(task, boxes, deadline):
            task.log_warning("低保金弹窗未关闭, 跳过本次领取的后续确认")
            return False

        task.log_info("低保金领取完成")
        return True
    except TaskDisabledException:
        raise
    except WaitFailedException:
        raise
    except Exception as e:
        task._log_aux_error("低保金领取失败", e)
        return False


def claim_welfare_if_needed(
    task: AuctionWelfareOps,
    state: WelfareState,
    boxes: AuctionBoxes,
    deadline: float,
    asset_value: int | None,
) -> bool:
    """主界面资产低于阈值时领取低保金, 返回弹窗流程是否走完。

    True 不区分「领到了」与「未检出领取按钮」(后者按当日已领完处理, 直接关
    弹窗); 当前调用方不消费返回值, 语义为流程结论留档。

    asset_value 由调用方通过 observe_main_asset 读出后传入: 资产观测与低保金领取
    拆开后, 两者的可用时间互相独立, 一次 OCR 的读数也只采信一次。
    """
    if asset_value is None:
        task.log_warning("资产值识别失败, 跳过本次低保金领取")
        return False

    if asset_value >= WELFARE_ASSET_THRESHOLD:
        task.log_info(
            f"资产 {asset_value} 达到低保阈值 {WELFARE_ASSET_THRESHOLD}, 跳过低保金领取"
        )
        return False

    task.log_info(
        f"资产 {asset_value} 低于低保阈值 {WELFARE_ASSET_THRESHOLD}, 执行低保金领取"
    )
    return try_claim(task, state, boxes, deadline)


def observe_main_asset(task: AuctionWelfareOps, boxes: AuctionBoxes, deadline: float) -> int | None:
    """读取主界面资产值, 记录到本地历史, 返回数值(未读出时 None)。

    独立于低保金领取: 资产历史记录是用户要的长期观测, 不依赖「辅助功能」里
    是否勾选低保金 (合并导致的静默停摆见 auction-notes 5.3)。

    资产读取属于可选的观测步骤, 和满仓检测一样用 _optional_timeout:
    单轮时间用尽时只表示这次没测到(返回 None), 不该抛 WaitFailedException ——
    那会把已经成功结算的轮次判成失败, 而且调用方写回观测结果的那一步会被跳过,
    连出售也一并丢失。
    """
    timeout = task._optional_timeout(deadline, ASSET_OBSERVE_TIMEOUT)
    if timeout is None:
        task.log_debug("资产观测没有可用时间, 跳过本次读取")
        return None

    # 使用数字 match, 避免漏识别单字符数值 0.
    ops = task._reading_ops()
    asset_value = ops.asset_once(ops, boxes.main_asset, timeout)
    if asset_value is None:
        task.log_warning("资产值识别失败, 跳过本次观测")
        return None

    task.log_info(f"当前资产: {asset_value}")
    return asset_value
