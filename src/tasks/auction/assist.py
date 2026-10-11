"""拍卖出价面板上的可选辅助: 永恒之心检测, 竞拍仪器使用与仪器组装备。

坐标与颜色特征均换算自参考实现 (按键精灵 1080p 脚本), 仪器组相关区域为实机
截图标定 (来源见 auction-notes 3); 两项辅助都默认关闭(「辅助功能」多选框),
每个动作失败时只告警并跳过, 不中断出价主流程。
模块函数的第一个参数 task 是 AutoBidAuctionTask 实例, 允许访问面由
contracts.AuctionAssistOps 窄协议声明; 辅助状态由任务实例持有: 心检测按
「每场」复位 (_heart_checked / _heart_present), 仪器按「每口」消费
(_instrument_served_bid 记录已服务的出价序号, 随轮复位, 见 auction-notes 7)。
仪器组装备会把 task 转手给 auction_interaction.scroll_screen, 转手后的访问面
由对方模块自己的契约约束。
"""

import re
import time

import numpy as np
from ok import Box, TaskDisabledException

from src.tasks.auction import interaction as auction_interaction
from src.tasks.auction.contracts import AuctionAssistOps
from src.tasks.auction.layout import (
    BOX_INSTRUMENT_LIST_ROWS,
    POS_INSTRUMENT_COMBO_SCROLL,
    POS_INSTRUMENT_GROUP_ENTRY,
    RE_COMBO_PAID_BUTTON,
    RE_CONFIRM,
    RE_INSTRUMENT,
    RE_INSTRUMENT_COMBO,
    AuctionBoxes,
)
from src.utils import image_utils as iu

# --- 永恒之心 ---
# 展柜主色范围: 参考实现主色 290D7B, 按按键精灵 RRGGBB 序解读为 RGB(41,13,123)
# 深紫罗兰, 每通道 ±22 容差; 色序依据(同脚本品质色验证 + 解包源码 L9384)见
# auction-notes 7, 色相不符时也只需改这一处。
HEART_COLOR_RANGE = {
    "r": (19, 63),
    "g": (0, 35),
    "b": (101, 145),
}
# 命中像素占比达到该值判定展柜里有永恒之心; 待实机帧标定 (见 auction-notes 7)。
HEART_RATIO_THRESHOLD = 0.004

# --- 竞拍仪器 (槽位/确认/关闭坐标换算自参考实现 1080p 像素; 入口按钮与
# 「仪器列表」弹窗区域 2026-10-04 用户实测/截图校准, 见 auction-notes 7) ---
# 槽位行区域与行点击中心由 layout.BOX_INSTRUMENT_LIST_ROWS 给出, 行非空
# 校验与行点击共用同一区域 (2026-10-04 前是五个独立槽位盲点)。
# 槽位确认 px(410,968) 与面板关闭 px(691,153)。
INSTRUMENT_CONFIRM = (0.2135, 0.8963)
INSTRUMENT_CLOSE = (0.3599, 0.1417)
# 一次仪器使用的总预算与步骤间等待; 剩余预算不足时中止本场仪器并告警, 不影响出价。
INSTRUMENT_BUDGET = 15.0
INSTRUMENT_STEP_SLEEP = 0.5
# 关闭清理确认弹窗是否还开着的短轮询预算: 弹窗已关时空等这一次, 有界。
INSTRUMENT_CLOSE_CHECK_TIMEOUT = 1.0

# --- 仪器组装备 (主界面一次性动作, 独立预算不吃轮次 deadline) ---
# 内容区校验统一为非空白即放行: 各组仪器名无统一关键词, 按「至尊鉴定仪器」
# 分档的严格校验已删 (2026-10-04 事故: 严格档分档键错套默认组, 见
# auction-notes 7); 点选落空由流程末尾的卡片复读按组名拦截。
RE_ANY_TEXT = re.compile(r"\S")
# 打开「仪器组合」+ 滚动找组 + 装备/补全 + 复核的总预算; 每轮都进弹窗
# (实机流程: 已用完的仪器要靠底部「一键补全」补回, 见 auction-notes 7)。
INSTRUMENT_GROUP_BUDGET = 45.0
# 组列表最多滚动几次找目标组(列表共 8 组, 至尊组在末尾); 实际终止通常靠下面的
# 「到底判定」, 上限只是安全网。
INSTRUMENT_GROUP_SCROLL_STEPS = 10
INSTRUMENT_GROUP_SCROLL_WHEEL = -8
# 组列表识别节奏: 滚动后先等沉降间隔再确认画面静止, 静止后对组名窄条做
# 短轮询识别(「都市闲趣」回场路径同款 wait_ocr, 单帧读坏由轮询兜底),
# 滚动动画中不识别 (2026-10-03/04 用户实测反馈, 见 auction-notes 7)。
INSTRUMENT_SCROLL_SETTLE = 0.8
INSTRUMENT_LIST_STATIC_CHECKS = 4
INSTRUMENT_LIST_STATIC_INTERVAL = 0.5
# 组名窄条单页轮询预算: 每页给 wait_ocr 的等待上限, 与回场路径每页 3 秒同思路。
INSTRUMENT_LIST_READ_TIMEOUT = 2.0
# 主界面装备卡片的状态读数预算: 装备判定以此为唯一依据, 瞬态读空会误判
# 「未装备」触发重装备并把队列重置成满列表(队列漂移, 见 auction-notes 7),
# 用与组列表同量级的短轮询兜住单帧坏帧。
INSTRUMENT_CARD_READ_TIMEOUT = 2.0
# 点卡片后等「仪器组合」弹窗打开; 入口点击可能未生效, 未打开按上限重试
# (见 auction-notes 7)。
INSTRUMENT_DIALOG_TIMEOUT = 5.0
INSTRUMENT_DIALOG_OPEN_ATTEMPTS = 3
# 弹窗内单步(列表扫描 / 内容列表校验)与拍卖内「仪器列表」渲染的独立短等待;
# 语义与「找卡片 / 读价格文本」的白名单 3 秒互不相同, 不混用。
INSTRUMENT_STEP_TIMEOUT = 3.0
# 未配置「仪器槽位序列」时的默认消费顺序, 与拆分前的 1→5 轮换逐场一致;
# 也是仪器队列的满列表状态(装备/补全后恢复)。
DEFAULT_SLOT_CYCLE = tuple(range(1, len(BOX_INSTRUMENT_LIST_ROWS) + 1))

# ensure_instrument_group 的结论, 任务层据此落账仪器队列 (_instrument_remaining):
# FULL = 仪器列表已满(执行了装备/补全, 或确认已装备且无需补全), 队列重置为满列表;
# INTACT = 已装备但组列表读数失败, 状态未确认, 队列保持; FAILED = 失败, 本轮不用仪器。
INSTRUMENT_GROUP_FULL = "full"
INSTRUMENT_GROUP_INTACT = "intact"
INSTRUMENT_GROUP_FAILED = "failed"


def instrument_row_for_slot(slot: int, remaining: list[int]) -> int | None:
    """仪器队列换算: 满列表槽位号 -> 当前要点击的行号。

    实机机制: 每使用一个仪器后, 其下方仪器上移补位, 列表尾部变空 —— 序列
    数字指「满列表时的槽位号」(仪器身份, 如紫=3), 不能直接当坐标点。remaining
    是仍在列表中的槽位号(按当前视觉顺序), 目标仪器的当前行 = 它在 remaining
    中的位置 +1; 已用掉(不在 remaining)返回 None, 由调用方跳过该口。
    """
    if slot not in remaining:
        return None
    return remaining.index(slot) + 1


def parse_instrument_sequence(value: object) -> list[int]:
    """解析「仪器槽位序列」配置, 返回槽位号列表(0 表示该场跳过仪器)。

    空串(含纯空白)返回 [] 表示维持默认 1→5 轮换; 其余只接受 0-5 的数字组成的
    串, 含其它字符或全为 0 时抛 ValueError —— 全 0 序列等于永不使用仪器, 应该
    直接取消勾选「竞拍仪器」辅助, 而不是留一个看似生效的无效序列。
    """
    text = str(value or "").strip()
    if not text:
        return []
    if any(ch not in "012345" for ch in text):
        raise ValueError(f"仪器槽位序列只能由 0-5 的数字组成: {value!r}")
    slots = [int(ch) for ch in text]
    if not any(slots):
        raise ValueError(f"仪器槽位序列不能全为 0(等于不用仪器): {value!r}")
    return slots


def heart_pixel_ratio(frame, box) -> float:
    """展柜区域内命中永恒之心主色系的像素占比 (0~1)。

    frame 是框架的整屏 BGR 帧, box 是已换算的屏幕 Box (boxes.heart_area)。
    纯计算无 IO, 合成帧可直接单测; 裁剪为空(框在帧外)时返回 0.0。
    """
    cropped = frame[box.y : box.y + box.height, box.x : box.x + box.width]
    if cropped.size == 0:
        return 0.0
    mask = iu.create_color_mask(cropped, HEART_COLOR_RANGE, to_bgr=False)
    return float(np.count_nonzero(mask)) / mask.size


def should_abandon_for_heart(ratio: float) -> bool:
    """像素占比低于阈值时判定展柜没有永恒之心, 供「无心放弃本场」使用。"""
    return ratio < HEART_RATIO_THRESHOLD


def _close_instrument_list(task: AuctionAssistOps, boxes: AuctionBoxes) -> None:
    """「仪器列表」弹窗还开着就点右上角关闭; 清理路径, 已关闭时不点击。

    短轮询而非单帧读取: 偶发坏帧不会把还开着的弹窗留在出价界面上; 弹窗已
    关闭时付出一次 INSTRUMENT_CLOSE_CHECK_TIMEOUT 的空等, 有界。
    """
    if task.wait_ocr(
        box=boxes.instrument_list_title,
        match=RE_INSTRUMENT,
        time_out=INSTRUMENT_CLOSE_CHECK_TIMEOUT,
        raise_if_not_found=False,
        settle_time=0.2,
    ):
        task.log_debug("关闭仪器列表弹窗")
        task.operate_click(*INSTRUMENT_CLOSE, after_sleep=INSTRUMENT_STEP_SLEEP)


def _gate_wait_ocr(
    task: AuctionAssistOps, budget_deadline: float, box: Box, match: re.Pattern
) -> list | None:
    """仪器使用的单步门控读数: 步骤预算内短轮询 OCR, 收拢预算检查与等待参数。

    预算耗尽时告警并返回 None —— None 不能直接传给框架的 wait_*(0 会被解释
    为默认 10 秒等待, 见 auction-notes 1.2), 也让调用方区分「预算耗尽」与
    「读数为空」; 未耗尽返回命中框列表, 可为空。
    """
    budget = task._optional_timeout(budget_deadline, INSTRUMENT_STEP_TIMEOUT)
    if budget is None:
        task.log_warning("仪器使用预算耗尽, 中止本场仪器使用")
        return None
    return task.wait_ocr(
        box=box, match=match, time_out=budget, raise_if_not_found=False, settle_time=0.3
    ) or []


def use_instrument(
    task: AuctionAssistOps, boxes: AuctionBoxes, deadline: float | None, row: int
) -> bool:
    """出价前使用一个竞拍仪器: 入口 → 弹窗确认 → 行校验 → 槽位 → 确认 → 关闭。

    row 从 1 开始, 是点击的「仪器列表」行号, 由调用方按队列模型从槽位号换算。
    入口按钮与弹窗标题按 OCR 门控: 入口识别不到时不点击任何后续步骤(盲点入口
    会落空, 见 auction-notes 7); 入口点击后弹窗未打开时重试入口点击(上限
    INSTRUMENT_DIALOG_OPEN_ATTEMPTS, 每次重试前重过入口门控 —— 入口区域与弹窗
    「使用」按钮重叠, 弹窗已开而标题漏读时盲点重击会误耗默认高亮的第一项,
    见 auction-notes 7), 重试用尽才整口放弃。目标行读不出内容(空槽或队列记录
    与实机列表漂移)时放弃本口, 不按默认高亮误用第一项。槽位与确认两步仍按
    标定区域盲点, 契约沿革见 auction-notes 7。比例元组点击必须解包成 x, y
    (见 auction-notes 7)。
    弹窗开过后的关闭统一收敛到 finally 清理点: 还开着才点关闭, 已自动关闭时
    不在出价界面上盲点, 中断路径同样清场(见 auction-notes 7)。
    本场仪器使用有独立的倒计时预算 (INSTRUMENT_BUDGET, 从进入时起算, 与
    INSTRUMENT_GROUP_BUDGET 的独立预算语义一致): 每个动作步骤前检查剩余预算,
    耗尽即中止本场仪器并告警。任何异常都吞掉并告警 —— 仪器使用失败不该赔上
    本次出价。返回是否走完全部步骤。
    """
    rows = boxes.instrument_list_rows
    if not 1 <= row <= len(rows):
        task.log_warning(f"仪器行号非法: {row}")
        return False

    # 预算必须从本函数进入时新建: 对整轮 deadline 取 min(limit, remaining) 不
    # 随流程推进扣减, 检查会恒通过, 起不到步骤预算的作用 (见 auction-notes 7)。
    now = time.monotonic()
    budget_deadline = now + INSTRUMENT_BUDGET
    if deadline is not None:
        budget_deadline = min(deadline, budget_deadline)

    list_opened = False
    try:
        # 入口点击可能被游戏端吃掉 (2026-10-04 用户实测, 见 auction-notes 7),
        # 未打开按上限重试; 每次重试前重过入口门控 —— 入口区域与弹窗「使用」
        # 按钮重叠, 弹窗已开而标题漏读时盲点重击会误耗默认高亮的第一项, 入口
        # 不可见即停 (与装备侧「重击落在压暗区无害」的前提相反)。
        opened: list | None = []
        budget_gone = False
        clicked = False
        for _ in range(INSTRUMENT_DIALOG_OPEN_ATTEMPTS):
            entry = _gate_wait_ocr(
                task, budget_deadline, boxes.instrument_entry, RE_INSTRUMENT
            )
            if entry is None:
                budget_gone = True
                break
            if not entry:
                if not clicked:
                    task.log_warning("未识别到「仪器」按钮, 跳过本回合仪器使用")
                    return False
                task.log_warning("重试时未识别到「仪器」按钮, 跳过本回合仪器使用")
                _close_instrument_list(task, boxes)
                return False
            task.operate_click(entry[0], after_sleep=INSTRUMENT_STEP_SLEEP)
            clicked = True

            opened = _gate_wait_ocr(
                task, budget_deadline, boxes.instrument_list_title, RE_INSTRUMENT
            )
            if opened is None:
                budget_gone = True
                break
            if opened:
                break
            task.log_debug("仪器列表弹窗未打开, 重试仪器入口点击")

        if not opened:
            if clicked:
                # 末次点击可能在预算边缘才生效: 门控收一下弹窗再放弃, 不让
                # 迟开的弹窗挡住后续键盘输价 (对齐装备侧收尾, 见 auction-notes 7)。
                _close_instrument_list(task, boxes)
            if not budget_gone:
                task.log_warning("仪器列表弹窗未打开, 跳过本回合仪器使用")
            return False
        list_opened = True

        row_hits = _gate_wait_ocr(task, budget_deadline, rows[row - 1], RE_ANY_TEXT)
        if row_hits is None:
            return False
        if not row_hits:
            # 空槽说明队列记录与实机列表已经对不上, 宁可放弃本口也不按
            # 默认高亮误用第一项 (见 auction-notes 7); 关闭由 finally 统一执行。
            task.log_warning(f"仪器列表第 {row} 行没有内容, 跳过本回合仪器使用")
            return False

        for step in (rows[row - 1], INSTRUMENT_CONFIRM):
            if task._optional_timeout(budget_deadline, INSTRUMENT_STEP_TIMEOUT) is None:
                task.log_warning("仪器使用预算耗尽, 中止本场仪器使用")
                return False
            if isinstance(step, tuple):
                task.operate_click(*step, after_sleep=INSTRUMENT_STEP_SLEEP)
            else:
                task.operate_click(step, after_sleep=INSTRUMENT_STEP_SLEEP)
    except TaskDisabledException:
        # 停用时不做清理动作, 直接中止。
        list_opened = False
        raise
    except Exception as e:
        task.log_warning(
            f"仪器使用中断, 本次出价继续: {type(e).__name__}: {e}"
        )
        return False
    finally:
        if list_opened:
            try:
                _close_instrument_list(task, boxes)
            except TaskDisabledException:
                raise
            except Exception as e:
                task.log_warning(f"仪器列表关闭清理失败: {type(e).__name__}: {e}")
    return True


def _read_box_text(task: AuctionAssistOps, box) -> str:
    """读区域内全部文本并拼接, 读不出返回空串。"""
    results = task.ocr(box=box, match=None, log=False) or []
    return "".join(b.name for b in results if b.name).strip()


def _read_card_text_stable(
    task: AuctionAssistOps, boxes: AuctionBoxes, deadline: float
) -> str:
    """主界面装备卡片的稳定读数: 带 settle 的短轮询, 瞬态读空不翻转装备判定。

    装备判定只看这一次读数: 单帧读空会把已装备组误判成未装备, 重装备后
    队列被重置成满列表而实机仍有空位 (队列漂移, 见 auction-notes 7)。
    """
    budget = task._optional_timeout(deadline, INSTRUMENT_CARD_READ_TIMEOUT)
    if budget is None:
        return ""
    results = task.wait_ocr(
        box=boxes.instrument_card,
        match=RE_ANY_TEXT,
        time_out=budget,
        raise_if_not_found=False,
        settle_time=0.5,
    ) or []
    return "".join(b.name for b in results if b.name).strip()


def _first_row_is_group(results, target_re: re.Pattern) -> bool:
    """组列表首行是否已是目标组: 已装备的组需要补全时会被游戏置顶到首位
    (实机观察, 见 auction-notes 7); 未置顶 = 仪器还在, 无需动作。

    行内多条文本(组名/剩余)与下一行的间距远大于行内行高差, 用命中框自身
    高度作同行容差, 兼容 1080p/2160p; 结果无有效坐标时按「不在首位」处理,
    走关闭路径不误动作。
    """
    named = [b for b in results if getattr(b, "name", None)]
    positioned = [b for b in named if isinstance(getattr(b, "y", None), (int, float))]
    if not positioned:
        return False
    top_y = min(b.y for b in positioned)
    return any(
        isinstance(getattr(b, "y", None), (int, float))
        and target_re.search(b.name)
        and b.y - top_y <= 1.5 * (b.height or 0)
        for b in named
    )


def _wait_list_static(task: AuctionAssistOps, boxes: AuctionBoxes, deadline: float) -> bool:
    """等「仪器组合」组列表画面静止, 静止返回 True, 迟迟未静止返回 False。

    用画面比对而非读数签名判稳: 对整块列表区截帧快照, 一个观察窗后模板匹配
    旧快照, 匹配不到 = 画面变过(动画未结束), 继续等; 滚动动画中不做 OCR。
    """
    for _ in range(INSTRUMENT_LIST_STATIC_CHECKS):
        if task._optional_timeout(deadline, INSTRUMENT_STEP_TIMEOUT) is None:
            return False
        if not task.run_and_check_changed(
            lambda: None,
            snap_box=boxes.instrument_combo_list,
            after_sleep=INSTRUMENT_LIST_STATIC_INTERVAL,
        ):
            return True
    return False


def _read_settled_list(
    task: AuctionAssistOps, boxes: AuctionBoxes, deadline: float, match: re.Pattern
) -> list:
    """组列表静止后对组名窄条做短轮询识别, 返回命中框列表。

    节奏对齐「都市闲趣」回场路径 (click_instant_lot): 静止确认后不再单次
    OCR, 改为 wait_ocr 在短预算内逐帧重试 —— 单次坏帧不会漏页, settle 要求
    命中持续, 瞬态误读被过滤; 找组按目标组名 match 只拿命中框, 首行判定按
    RE_ANY_TEXT 拿全部命名行。静止确认失败(迟迟不安稳)或预算耗尽返回空
    列表, 由调用方走保守分支, 不拿动画中的坐标去点击。
    """
    if not _wait_list_static(task, boxes, deadline):
        return []
    budget = task._optional_timeout(deadline, INSTRUMENT_LIST_READ_TIMEOUT)
    if budget is None:
        return []
    return task.wait_ocr(
        box=boxes.instrument_combo_names,
        match=match,
        time_out=budget,
        raise_if_not_found=False,
        settle_time=0.5,
    ) or []


def _scroll_list_step(task: AuctionAssistOps, boxes: AuctionBoxes) -> bool:
    """组列表下滚一步, 返回本次滚动是否产生画面位移。

    滚动前列表已静止(调用方保证), 以它为快照, 滚动 + 沉降间隔后模板匹配
    旧快照: 匹配不到 = 列表动过; 仍匹配 = 无位移(到底或滚轮事件丢失)。
    落点坐标经 auction_interaction.scroll_screen 按 box 口径换算成绝对像素,
    裸 task.scroll 的 float 夹逼换算在超宽屏下会偏 (见 auction-notes 5.8)。
    """
    return bool(
        task.run_and_check_changed(
            lambda: auction_interaction.scroll_screen(
                task, *POS_INSTRUMENT_COMBO_SCROLL, INSTRUMENT_GROUP_SCROLL_WHEEL
            ),
            snap_box=boxes.instrument_combo_list,
            after_sleep=INSTRUMENT_SCROLL_SETTLE,
        )
    )


def _close_combo_dialog(task: AuctionAssistOps, boxes: AuctionBoxes) -> None:
    """「仪器组合」弹窗还开着就点右上角关闭; 清理路径, 已关闭时不点击。"""
    if task.ocr(box=boxes.instrument_combo_title, match=RE_INSTRUMENT_COMBO, log=False):
        task.log_debug("关闭仪器组合弹窗")
        task.operate_click(boxes.instrument_combo_close, after_sleep=0.5)


def ensure_instrument_group(
    task: AuctionAssistOps, boxes: AuctionBoxes, deadline: float, group_name: str
) -> str:
    """主界面校验并装备「仪器组」选定的组, 返回结论字符串供任务层落账。

    返回 INSTRUMENT_GROUP_FULL(仪器列表已满: 执行了装备/补全, 或确认已装备且
    无需补全, 任务层据此重置仪器队列) / INSTRUMENT_GROUP_INTACT(组列表读数
    失败, 状态未确认, 队列保持) / INSTRUMENT_GROUP_FAILED(失败, 本轮不用仪器)。

    每轮无条件点主界面「请选择仪器组合」入口(POS_INSTRUMENT_GROUP_ENTRY 用户
    实测)打开「仪器组合」弹窗核实补全状态, 按三种实机状态分支 (见
    auction-notes 7): 未装备目标组 -> 组列表滚动找组并点选, 底部「使用」直点
    (免费)/「购买并使用」付费点确认; 已装备且组被置顶到列表首位 -> 仪器用完
    需要补全, 走同一套按钮分派; 已装备且未置顶 -> 仪器还在, 直接点右上角关闭
    继续拍卖。入口不能按装备卡片读数门控(未装备时卡片标题行无文字, 见
    auction-notes 7), 主界面身份由调用方的标题探针保证。组列表滚动稳定后才
    识别, 未找到继续下滚, 滚到列表末尾仍没找到才放弃。付费按钮都会真实扣资产
    并弹「费用无法退回」确认框, 点「确认」才生效 (购买决策沿革见 auction-notes 7)。

    只有装备了选定组, 拍卖内「仪器列表」的槽位坐标对应的才是该组的仪器; 其它组
    内容不同, 盲点槽位会点空 —— 组身份由本流程的主界面卡片读数(稳定读卡, 单帧
    读空不翻转判定, 见 _read_card_text_stable)与复读保证, 任务层的就绪门也只认
    卡片组名 (见 _gate_instrument_ready 与 auction-notes 7)。
    选中与否: 右侧「内含下列仪器」内容区非空白即认为点选生效 (各组仪器名无
    统一关键词, 原至尊组严格档已删, 见 auction-notes 7); 点选落空时最后的
    卡片复读仍按组名拦住。任何一步失败只告警返回 FAILED, 不阻塞拍卖。
    """
    target_re = re.compile(re.escape(group_name))
    try:
        equipped = _read_card_text_stable(task, boxes, deadline)
        was_equipped = bool(target_re.search(equipped))
        if equipped:
            task.log_info(f"当前装备仪器组: {equipped}, 校验并装备 {group_name}")
        else:
            # 未装备时卡片标题行没有文字(「请选择仪器组合」在框外), 入口不能
            # 按卡片读数门控 (见 auction-notes 7); 主界面身份已由调用方的
            # 标题探针确认。
            task.log_info("主界面仪器组卡片无读数, 按未装备处理")

        opened = False
        budget_exhausted = False
        for _ in range(INSTRUMENT_DIALOG_OPEN_ATTEMPTS):
            dialog_budget = task._optional_timeout(deadline, INSTRUMENT_DIALOG_TIMEOUT)
            if dialog_budget is None:
                budget_exhausted = True
                break
            task.operate_click(*POS_INSTRUMENT_GROUP_ENTRY, after_sleep=1.0)
            # 入口点在弹窗外侧压暗区, 弹窗已开时重试点击无害 (见 auction-notes 7)。
            if task.wait_ocr(
                box=boxes.instrument_combo_title,
                match=RE_INSTRUMENT_COMBO,
                time_out=dialog_budget,
                raise_if_not_found=False,
                settle_time=0.5,
            ):
                opened = True
                break
            task.log_debug("仪器组合弹窗未打开, 重试装备入口点击")
        if not opened:
            # 末次点击可能在超时后才生效: OCR 门控收一下弹窗, 不滞留挡出价。
            _close_combo_dialog(task, boxes)
            if budget_exhausted:
                task.log_warning("仪器组校验预算耗尽, 本轮不用仪器")
            else:
                task.log_warning("仪器组合弹窗未打开, 放弃仪器组装备")
            return INSTRUMENT_GROUP_FAILED

        if was_equipped:
            first_row = _read_settled_list(task, boxes, deadline, RE_ANY_TEXT)
            if not first_row:
                # 读数失败(迟迟不安稳或持续不可读)说明拿不到可信的首行判定;
                # 不动作只关闭, 下轮校验重试, 不拿可疑读数去点付费按钮。状态
                # 未确认, 返回 INTACT 让任务层保持队列不动(不据此重置)。
                task.log_warning("组列表读数失败, 本轮不动作, 关闭仪器组合弹窗")
                _close_combo_dialog(task, boxes)
                return INSTRUMENT_GROUP_INTACT
            if not _first_row_is_group(first_row, target_re):
                # 已装备的组只有在需要补全时才会被置顶到列表首位; 未置顶说明仪器
                # 还在(满), 不点卡片, 直接关闭继续拍卖 (实机流程见 auction-notes 7)。
                # 返回 FULL 而非「队列保持」: 「未置顶 = 仪器满」是实机事实, 任务层
                # 据此把队列重置为满列表 —— 队列可能因补全在别处生效(用户手动补全,
                # 或上轮补全动作成功但被判定失败)而残留已用掉的槽位, 不重置会让该
                # 槽位被永久跳过 (2026-10-06 事故, 见 auction-notes 7)。
                task.log_info("仪器组已装备且无需补全, 关闭仪器组合弹窗")
                _close_combo_dialog(task, boxes)
                return INSTRUMENT_GROUP_FULL
            task.log_info("已装备的仪器组被置顶, 需要补全, 走补全流程")

        entry = None
        still_scrolls = 0
        for _ in range(INSTRUMENT_GROUP_SCROLL_STEPS):
            if task._optional_timeout(deadline, INSTRUMENT_STEP_TIMEOUT) is None:
                break
            # _read_settled_list 已按 target_re 过滤, 命中框即目标组行。
            matched = _read_settled_list(task, boxes, deadline, target_re)
            entry = matched[0] if matched else None
            if entry is not None:
                break
            # 滚动一步后按画面位移判到底: 连续两次滚动都无位移(排除单次滚轮
            # 事件丢失)说明列表已到末尾, 没找到目标组, 放弃。识别只发生在
            # 静止确认之后, 与滚动之间隔着沉降间隔, 见 _read_settled_list /
            # _scroll_list_step 与 auction-notes 7。
            if _scroll_list_step(task, boxes):
                still_scrolls = 0
            else:
                still_scrolls += 1
                if still_scrolls >= 2:
                    break
        if entry is None:
            task.log_warning(f"仪器组合列表未找到 {group_name}, 放弃装备")
            _close_combo_dialog(task, boxes)
            return INSTRUMENT_GROUP_FAILED
        task.operate_click(entry, after_sleep=0.5)

        content_budget = task._optional_timeout(deadline, INSTRUMENT_STEP_TIMEOUT)
        if content_budget is None or not task.wait_ocr(
            box=boxes.instrument_combo_content,
            match=RE_ANY_TEXT,
            time_out=content_budget,
            raise_if_not_found=False,
            settle_time=0.3,
        ):
            task.log_warning(f"{group_name}点选未生效, 放弃装备")
            _close_combo_dialog(task, boxes)
            return INSTRUMENT_GROUP_FAILED

        button_text = _read_box_text(task, boxes.instrument_combo_use)
        if RE_COMBO_PAID_BUTTON.search(button_text):
            # 付费按钮(购买并使用/一键补全)会真实扣资产, 点击后弹「费用无法退回」
            # 确认框, 点「确认」才生效 (见 auction-notes 7)。
            task.log_info(f"{group_name} 底部按钮为 {button_text!r}, 购买补全并确认")
            task.operate_click(boxes.instrument_combo_use, after_sleep=1.0)
            confirm_budget = task._optional_timeout(deadline, INSTRUMENT_STEP_TIMEOUT)
            if confirm_budget is None or not task.wait_ocr(
                box=boxes.popup_ok,
                match=RE_CONFIRM,
                time_out=confirm_budget,
                raise_if_not_found=False,
                settle_time=0.3,
            ):
                task.log_warning("购买确认弹窗未出现, 放弃仪器组装备")
                _close_combo_dialog(task, boxes)
                return INSTRUMENT_GROUP_FAILED
            task.operate_click(boxes.popup_ok, after_sleep=1.0)
        elif "使用" in button_text:
            task.operate_click(boxes.instrument_combo_use, after_sleep=1.0)
        else:
            task.log_warning(f"仪器组合底部按钮未识别({button_text!r}), 放弃装备")
            _close_combo_dialog(task, boxes)
            return INSTRUMENT_GROUP_FAILED

        # 弹窗一般随装备自动关闭; 还开着就点右上角关闭.
        _close_combo_dialog(task, boxes)
        # 装备后的复读保持单帧: 读空只会记 FAILED 下轮重试, 无队列漂移风险.
        equipped = _read_box_text(task, boxes.instrument_card)
        if target_re.search(equipped):
            task.log_info(f"{group_name}已装备")
            return INSTRUMENT_GROUP_FULL
        task.log_warning(f"装备动作已执行但仪器组卡片仍未读到 {group_name}, 本轮不用仪器")
        return INSTRUMENT_GROUP_FAILED
    except TaskDisabledException:
        raise
    except Exception as e:
        task.log_warning(f"仪器组校验异常, 本轮不用仪器: {type(e).__name__}: {e}")
        return INSTRUMENT_GROUP_FAILED
