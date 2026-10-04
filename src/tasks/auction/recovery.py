"""拍卖掉线回场与弹窗恢复: 大世界探测, 回场路径, 入口确认, 阻塞弹窗兜底。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: OCR/输入/导航等框架
API 经它访问; 允许访问面由 contracts.AuctionRecoveryOps 窄协议声明, 编排
私有方法不在协议内: 回场成功后的匹配阶段续跑经 recover_from_world 的
resume_match 参数由任务侧转发器注入, 模块不回读任务编排。本模块不读写
任务状态 —— 回场配额 (_recover_quota) 的检查与扣减由任务编排层
(_resume_after_world_drop) 负责, 配额所有者是任务实例 (挂轮次不走参数的
原因见 auction-notes 5.4)。回场预算等行为常量由本模块定义, 不再挂回任务类。
回场路径等内部自调用仍走 task._<方法名>, 让测试的实例级 mock 与任务侧的
统一入口保持生效; 大世界判定没有实例级 seam 依赖, ensure_auction_entry
直调本模块 is_world_screen。阻塞弹窗兜底会把 task 转手给
auction_welfare / auction_interaction 的公开函数, 转手后的访问面由对方
模块自己的契约约束。

预算规则: 回场路径的每一步都按「剩余预算」取超时 (见 return_to_auction 的
注释), 回场成功后重跑匹配阶段用的是调用方传入的同一份轮次 deadline, 不另开
预算。
"""

import time
from collections.abc import Callable

from ok import TaskDisabledException, WaitFailedException

from src.tasks.auction import interaction as auction_interaction
from src.tasks.auction import welfare as auction_welfare
from src.tasks.auction.contracts import AuctionRecoveryOps
from src.tasks.auction.layout import (
    BOX_CITY_FUN_CARDS,
    BOX_CITY_FUN_TITLE,
    BOX_CURRENT_VENUE,
    POS_CITY_FUN_SCROLL,
    RE_CITY_FUN,
    RE_CURRENT_VENUE,
    RE_MAIN_TITLE,
    RE_VENUE_NAME,
    AuctionBoxes,
    AuctionState,
)

# --- 掉线回场 (秒/次) ---
# 网络不稳时匹配阶段会被踢回大世界, 界面状态全不命中, 只能空转到 MATCH_TIMEOUT。
# 回场是一次性的异常路径: 失败就按本轮失败处理, 交给下一轮重试。
RECOVER_TIMEOUT = 90  # 单次回场总预算
RECOVER_STEP_TIMEOUT = 12  # 回场各步骤的等待上限
RECOVER_SCROLL_STEPS = 4  # 「都市闲趣」面板最多滚动几次去找「即刻落槌」
RECOVER_SCROLL_WHEEL = -8  # 每次滚动的滚轮格数
# 单轮回场次数上限, 由 _exec_auction_round 写进 self._recover_quota 并扣减;
# 挂在轮次而不是调用参数上的原因见 auction-notes 5.4。
RECOVER_MAX_PER_ROUND = 1
# 启动时的入口回场 (见 ensure_auction_entry): 探测主界面标题的等待上限,
# 以及一次性回场预算。预算与 RECOVER_TIMEOUT 一致, 两者走的是同一条路径。
ENTRY_PROBE_TIMEOUT = 3
ENTRY_RECOVER_TIMEOUT = 90


def handle_blocking_popup(task: AuctionRecoveryOps, boxes: AuctionBoxes | None = None) -> None:
    """整轮失败后的兜底: 按界面特征处理卡住的弹窗。

    月卡弹窗的时间窗盲区与低保金弹窗残留会让后续每轮识别不到拍卖界面,
    这里只在已经失败的情况下兜一次, 正常路径没有额外开销 (见 auction-notes 5.5)。
    """
    try:
        if task.find_monthly_card() is not None:
            task.log_info("本轮失败且检测到月卡弹窗, 关闭弹窗后重试")
            task.handle_monthly_card()
            return

        if boxes is not None and auction_welfare.is_dialog_open(task, boxes):
            task.log_warning("本轮失败且检测到低保金弹窗未关闭, 尝试关闭")
            auction_welfare.close_dialog(task, boxes, None)
            return

        if boxes is not None:
            task._dismiss_notice_popup(boxes, None, "本轮失败后")
    except TaskDisabledException:
        raise
    except WaitFailedException as e:
        # 月卡 20 秒清不掉等环境性等待失败: 整轮已失败后的兜底只告警,
        # 不进「疑似代码缺陷」计数, 也不把异常抛出去中止整个任务
        # (见 auction-notes 5.4)。
        task.log_warning(f"弹窗兜底处理中断: {type(e).__name__}: {e}")
    except Exception as e:
        task._log_aux_error("弹窗兜底处理失败", e)


def is_world_screen(task: AuctionRecoveryOps) -> bool:
    """是否被踢回大世界, 复用基类的 in_team_and_world()。

    不能只用 in_world() 判: 旋转模板匹配没有「场景饱和」惩罚, 亮色面板会得满分
    与真箭头分不开, 误判会白白耗掉本轮唯一的回场配额 (实测数据与推导见
    auction-notes 1.7)。加 is_in_team() 的血条判定能分开, 且与 return_to_auction
    第一步 ensure_main(in_world=True) 的要求一致。
    """
    try:
        return bool(task.in_team_and_world())
    except TaskDisabledException:
        raise
    except Exception as e:
        task.log_debug(f"大世界判定失败: {type(e).__name__}: {e}")
        return False


def recover_from_world(
    task: AuctionRecoveryOps,
    boxes: AuctionBoxes,
    deadline: float,
    *,
    resume_match: Callable[[AuctionBoxes, float], AuctionState],
) -> AuctionState:
    """掉线回场: 大世界 → 拍卖主界面, 成功后重新进入匹配阶段。

    掉线时拍卖界面的四种状态判定全不命中, 不回场的话本轮只能空转到
    MATCH_TIMEOUT(120 秒) 再按失败处理, 每轮白等两分钟。回场成功后经
    resume_match 重跑匹配阶段(由任务侧转发器注入, 编排私有方法不进本模块
    的协议), 让本轮接着走完; 只回场 RECOVER_MAX_PER_ROUND 次, 配额用完
    再掉线就按本轮失败结束 —— 继续重试只会把整轮 deadline 耗光。
    """
    task.log_warning("检测到被踢回大世界, 尝试自动回到拍卖界面")
    task.info_set("当前阶段", "回场中")
    recover_deadline = min(deadline, time.monotonic() + RECOVER_TIMEOUT)
    if not task._return_to_auction(boxes, recover_deadline):
        raise WaitFailedException("被踢回大世界后未能回到拍卖界面")

    venue = task._read_current_venue()
    task.log_info(f"已回到拍卖主界面, 当前会场: {venue or '未识别'}")
    # 回场落点不保证就是期望会场, 回场后顺手复核一次(只告警不停止)。
    task._check_expected_venue(boxes, venue)
    task.info_set("当前阶段", "匹配中")
    return resume_match(boxes, deadline)


def return_to_auction(task: AuctionRecoveryOps, boxes: AuctionBoxes, deadline: float) -> bool:
    """按「大世界 → F5 都市大亨 → 都市闲趣 → 即刻落槌」的顺序打开拍卖界面。

    整条路径重试一次: 网络抖动时 F5 或入口点击都可能落空, 重试一次比直接判失败
    划算, 但不再多试 —— 每次失败都要重新走一遍面板动画, 会把整轮 deadline 耗光。

    掉线时不会有「网络异常」之类的提示弹窗(已确认), 不在这里兜弹窗;
    真要是有弹窗, 由整轮失败后的 handle_blocking_popup 兜底。

    每一步都按「剩余预算」而不是各处写死的常量取超时: ensure_main 在登录态丢失时
    会把 time_out 抬到 600 秒 (见 auction-notes 1.5), 剩余时间耗尽时直接判失败,
    交给下一轮重来。
    """

    def action():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            task.log_warning("回场预算已耗尽, 放弃本次回场")
            return False
        try:
            task.ensure_main(in_world=True, time_out=remaining)
            task.openF5panel()
        except TaskDisabledException:
            raise
        except WaitFailedException:
            # 预算耗尽/月卡超时属环境性等待失败: 按本轮失败向上传播, 不进辅助
            # 异常计数 (见 auction-notes 5.4)。
            raise
        except Exception as e:
            task._log_aux_error("打开都市大亨面板失败", e)
            return False

        task.operate_click(*task.pos.panels.f5.hobbies)
        # 步骤预算可能被前面的等待耗尽: 耗尽时必须早退, 0/None 预算进 wait_ocr
        # 会被框架按默认 10 秒等待或报错 (见 auction-notes 1.2)。
        panel_budget = task._optional_timeout(deadline, RECOVER_STEP_TIMEOUT)
        if panel_budget is None or not task.wait_ocr(
            box=task.box_of_screen(*BOX_CITY_FUN_TITLE),
            match=RE_CITY_FUN,
            time_out=panel_budget,
            raise_if_not_found=False,
            settle_time=0.5,
        ):
            task.log_warning("未检测到「都市闲趣」面板")
            return False
        if not task._click_instant_lot(deadline):
            return False
        final_budget = task._optional_timeout(deadline, RECOVER_STEP_TIMEOUT)
        return bool(
            final_budget is not None
            and task.wait_ocr(
                box=boxes.main_title,
                match=RE_MAIN_TITLE,
                time_out=final_budget,
                raise_if_not_found=False,
                settle_time=0.5,
            )
        )

    def reset():
        """重试之间的状态复位, 同样受剩余预算约束 (默认 time_out 的漏洞见
        auction-notes 1.5)。

        复位是 best-effort: 失败只告警并放行重试, action 重跑时会重新校验预算
        与界面; 异常穿出会让 retry_on_action 直接终止, 第二次 action 永远不会
        执行, 「整条路径重试一次」落空 (见 auction-notes 5.4)。
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            task.ensure_main(in_world=True, time_out=remaining)
        except TaskDisabledException:
            raise
        except Exception as e:
            task.log_warning(f"回场复位失败, 直接重试主路径: {type(e).__name__}: {e}")

    try:
        return bool(task.retry_on_action(action, reset, attempt=1))
    except TaskDisabledException:
        raise
    except WaitFailedException:
        # 环境性等待失败按本轮失败向上传播, 不进辅助异常计数 (见 auction-notes 5.4)。
        raise
    except Exception as e:
        task._log_aux_error("回场流程异常", e)
        return False


def click_instant_lot(task: AuctionRecoveryOps, deadline: float) -> bool:
    """在「都市闲趣」面板里找「即刻落槌」卡片并点击。

    「即刻落槌」在面板最后一页, 刚打开时看不到, 所以边滚边找。
    命中后直接点 OCR 框中心 —— 卡片是「上图下标题」, 标题本身就在卡片的点击热区内。
    复用 RE_MAIN_TITLE 是因为卡片名与拍卖主界面标题是同一个词「即刻落槌」。
    """
    cards = task.box_of_screen(*BOX_CITY_FUN_CARDS)
    for _ in range(RECOVER_SCROLL_STEPS):
        # 同 return_to_auction: 0/None 预算进 wait_ocr 会被框架按默认 10 秒等待.
        budget = task._optional_timeout(deadline, 3)
        if budget is None:
            task.log_warning("回场预算已耗尽, 放弃寻找「即刻落槌」入口")
            return False
        if task._wait_operate_click(
            cards,
            RE_MAIN_TITLE,
            budget,
            after_sleep=1,
        ):
            return True
        auction_interaction.scroll_screen(task, *POS_CITY_FUN_SCROLL, RECOVER_SCROLL_WHEEL)
        task.sleep(0.5)
    task.log_warning("都市闲趣面板里未找到「即刻落槌」入口")
    return False


def read_current_venue(task: AuctionRecoveryOps) -> str:
    """读拍卖主界面右侧的「当前：XXX场」。

    优先用 RE_VENUE_NAME 提取会场名(如「海贝场」), 供期望会场校验留痕;
    场名读不出但区域里有「当前」字样时回落原文留痕(旧行为), 都没有返回空串。
    用 match=None 拿全量文本再正则提取: 过滤式 match 会把不含「当前」的会场名
    框丢掉, 整行被 OCR 拆成两个框时尤其如此。
    """
    try:
        results = task.ocr(box=task.box_of_screen(*BOX_CURRENT_VENUE), match=None)
    except TaskDisabledException:
        raise
    except Exception as e:
        task.log_debug(f"会场文字读取失败: {type(e).__name__}: {e}")
        return ""
    text = "".join(box.name for box in results or []).strip()
    found = RE_VENUE_NAME.search(text)
    if found:
        return found.group(1)
    return text if RE_CURRENT_VENUE.search(text) else ""


def ensure_auction_entry(task: AuctionRecoveryOps, boxes: AuctionBoxes) -> None:
    """每轮开始时确认人已站在拍卖主界面, 不在就按「大世界 → 即刻落槌」补上入口。

    复用掉线回场的同一条路径(return_to_auction), 不新增识别或导航代码,
    避免用户从大世界启动或上一轮异常退出后, 在匹配阶段里空转到
    MATCH_TIMEOUT(120 秒) 才触发回场 (为什么挂在每轮见 auction-notes 5.4);
    已在拍卖界面时只多花一次标题 OCR。

    判定顺序: 先读拍卖主界面标题, 命中即已在拍卖界面, 直接开跑;
    未命中再判大世界 —— 不在大世界就不接管, 交给原有流程按界面异常报错,
    避免在登录页/加载页等未知界面上误触发 F5 流程. 执行位置在 begin_round 之后、
    _run_single_round 之前, 不消耗轮次内的 `_recover_quota`。
    """
    if task.wait_ocr(
        box=boxes.main_title,
        match=RE_MAIN_TITLE,
        time_out=ENTRY_PROBE_TIMEOUT,
        raise_if_not_found=False,
    ):
        return
    if not is_world_screen(task):
        return
    task.log_info("启动时检测到大世界, 自动进入「即刻落槌」")
    task.info_set("当前阶段", "入场中")
    if not task._return_to_auction(boxes, time.monotonic() + ENTRY_RECOVER_TIMEOUT):
        task.log_warning("启动回场未成功, 交给第一轮按界面异常处理")
