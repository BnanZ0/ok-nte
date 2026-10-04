"""拍卖 OCR 数值读取: 全量文本读取, 按标签定位取数, 资产解析, 稳定读取。

模块函数的第一个参数 task 是 AutoBidAuctionTask 实例: OCR/日志/换帧等框架
API 经它访问; 允许访问面由 contracts.AuctionReadingOps 窄协议声明。读取
节奏常量由本模块定义, 不再挂回任务类 (POLL_INTERVAL 除外: 它是匹配/出价/
结算共用的轮询节奏, 所有者仍是任务类)。稳定读取经 task._read_estimate_value
取数, 这是测试的实例级 mock 锚点; 其余模块内部自调直接调用本模块函数,
不绕道任务转发。
本模块只承载「怎么读」: 标签定位, 残缺与贴边防线, 数字滚动的稳定判定;
「读什么、读出来怎么用」(出价计算, 结算记录) 仍由任务层编排。
"""

import time
from collections.abc import Iterable

from ok import Box

from src.tasks.auction.contracts import AuctionReadingOps
from src.tasks.auction.layout import RE_NUMBER
from src.tasks.auction.price import (
    has_inconsistent_grouping,
    is_partial_number_text,
    parse_asset_value,
)

# 资产读取的单次 OCR 等待上限: 出价路径每次出价都要读一遍资产, 给太长会拖慢单轮。
ASSET_OCR_TIMEOUT = 15
# 出价面板的当前估价在界面刚出现时会跳动几次, 第一次识别到的不是最终值;
# 连续读到相同值才采用, 最多等 ESTIMATE_STABLE_TIMEOUT 秒.
ESTIMATE_STABLE_READS = 3
# 数字滚动是逐位就位的, 低位先出现、高位后到, 中间值可以稳定停留数秒,
# 所以从第一次读到有效数值起, 至少观察这么久才允许采用 (实测见 auction-notes 3)。
ESTIMATE_MIN_OBSERVE_SECONDS = 4.0
# 单次稳定读取的总预算: 稳定读取是出价热路径的前置, 超时不稳定就按最后读数兜底。
ESTIMATE_STABLE_TIMEOUT = 10
# 估价数字右端距裁框右边界, 小于「屏幕宽度的这个比例」时认为末位可能已被裁掉。
# 裁框宽度不是安全保证, 运行时必须盯住它 (故障形态与取值依据见 auction-notes 2.3)。
ESTIMATE_EDGE_MARGIN_RATIO = 8 / 1920


def read_estimate_texts(task: AuctionReadingOps, box: Box, timeout: float) -> list:
    """读区域内**全部**文本, 不做 match 过滤。

    必须用 `task.ocr(match=None)` 而不是 `wait_ocr(match=RE_NUMBER)`: 框架的
    `wait_ocr` 会按 match 把返回值过滤成只含命中的框, 标签等非命中文本会丢失,
    导致按标签定位取数永远失败 (框架行为与线上案例见 auction-notes 1.1)。
    取一帧即可: 调用方的稳定判定循环本来就会反复重读。
    """
    deadline = time.monotonic() + timeout
    while True:
        boxes = task.ocr(box=box, match=None, log=False)
        texts = [b for b in boxes if b.name] if boxes else []
        if texts or time.monotonic() >= deadline:
            return texts
        task.sleep(0.2)


def read_estimate_value(
    task: AuctionReadingOps,
    box: Box,
    timeout: float,
    label: str = "当前估价",
    label_keywords: Iterable[str] = ("估价",),
) -> tuple[int | None, bool]:
    """读估价数字: 先按标签定位, 再取标签右侧的数字。

    必须按标签过滤而不能只靠裁框宽度: 区域内混进第二个数字栏时, 直接
    `"".join()` 会把两串数字粘成一个(表现为「估价少了一位」, 见 auction-notes 3);
    `BOX_ESTIMATE` 的右边界已把「我的资产」排除在外, 但标签过滤仍是最后一道
    防线: 界面过渡帧里数字位置会偏移, 也可能混进别的小字。

    label_keywords 默认只认「估价」; 结算面板的成交价值与估价共用同一段屏幕
    位置但标签文案不同, 由调用方传入放宽的关键词集合(见任务层 _read_result_value)。

    Returns:
        (值, 是否贴边). 贴边表示数字右端距裁框右边界不足 ESTIMATE_EDGE_MARGIN_RATIO
        对应的像素数, 该读数可能已被裁掉末位, 调用方应按不可信处理。
    """
    all_boxes = read_estimate_texts(task, box, timeout)
    if not all_boxes:
        return None, False

    label_right = max(
        (
            b.x + b.width
            for b in all_boxes
            if any(keyword in b.name.replace("：", "") for keyword in label_keywords)
        ),
        default=None,
    )
    if label_right is None:
        # 标签没读出来: 不猜, 交给调用方重读一帧.
        task.log_debug(f"{label} 标签未读到, 本帧数字不可靠")
        return None, False

    candidates = [b for b in all_boxes if b.x >= label_right and RE_NUMBER.search(b.name)]
    if not candidates:
        return None, False

    digit_boxes = sorted(candidates, key=lambda b: b.x)
    raw_text = "".join(b.name for b in digit_boxes)
    if is_partial_number_text(raw_text):
        task.log_debug(f"{label} OCR: '{raw_text}', 千位分隔符前缺数字, 视为残缺读数")
        return None, False
    if has_inconsistent_grouping(raw_text):
        task.log_debug(f"{label} OCR: '{raw_text}', 千位分组不自洽, 视为残缺读数")
        return None, False

    value = parse_asset_value(raw_text)
    right_edge = max(b.x + b.width for b in digit_boxes)
    # 阈值随分辨率等比放大, 至少 1px: 高 DPI 下框宽不变(本项目 resize_image 为默认 0,
    # 截图不重采样)时小于 1px 的判定没有意义.
    margin = max(1, round(task.width * ESTIMATE_EDGE_MARGIN_RATIO))
    tight = (box.x + box.width - right_edge) < margin
    task.log_debug(f"{label} OCR: '{raw_text}', 解析值: {value}, 贴边: {tight}")
    return value, tight


def read_asset_value(
    task: AuctionReadingOps,
    box: Box,
    timeout: float,
    label: str = "资产",
    *,
    reject_partial: bool = False,
) -> int | None:
    """对指定区域做 OCR 并解析资产数值, 未识别或解析失败时返回 None。

    reject_partial 目前只有测试直调启用, 生产调用方都走默认 False; 估价区域的
    同类防线(会跳动、首位可能被漏读)内建在 read_estimate_value, 不经过本参数。
    传入 True 时, 千位分隔符前面空着的残缺读数按未读出处理, 交给调用方重读。
    """
    boxes = task.wait_ocr(
        box=box,
        match=RE_NUMBER,
        time_out=timeout,
        raise_if_not_found=False,
        settle_time=0.5,
    )
    if not boxes:
        return None

    raw_text = "".join(text_box.name for text_box in boxes)
    if reject_partial and is_partial_number_text(raw_text):
        task.log_debug(f"{label} OCR: '{raw_text}', 千位分隔符前缺数字, 视为残缺读数")
        return None

    value = parse_asset_value(raw_text)
    task.log_debug(f"{label} OCR: '{raw_text}', 解析值: {value}")
    return value


def read_stable_asset_value(
    task: AuctionReadingOps,
    box: Box,
    timeout: float,
    label: str,
    *,
    skip_zero: bool = False,
) -> int | None:
    """连续读到相同数值才认为读数稳定, 避免取到跳动中的中间值。

    出价面板的当前估价在界面刚出现时会跳动几次, 第一次识别到的往往不是最终值,
    所以按 POLL_INTERVAL 换帧重读, 连续 ESTIMATE_STABLE_READS 次没有出现更完整的
    读数才采用。
    skip_zero 用于把 0 当作「面板还没滚出数值」的占位读数: 估价面板在数字滚动前会先
    显示 0, 把它当结果会算出 0 元出价, 所以这类读数不计入稳定判定, 继续等真值。

    判定规则(防线结构与推导过程见 auction-notes 5.6):
    - 读数走 `task._read_estimate_value`, 即按标签右边沿取数字, 挡住末位被裁与
      相邻数字栏的污染;
    - 原有的「位数不减少」判定保留, 对「数字滚动中途读到更短的值」仍然有效;
    - 贴边读数按「可能被裁掉末位」处理 —— 不采信, 并**作废**已攒的连续计数
      (last 与 valid_reads 一起清), 因为末位丢失后位数可能不变, 只有贴边这个
      几何信号能发现它;
    - `same` 只能确认「画面不再变化」, 另加 `valid_reads >= required` 门槛确认
      「读到的是完整数值」;
    - 「连续相同」之上还要叠加 ESTIMATE_MIN_OBSERVE_SECONDS 的最短观察窗口,
      否则会在滚动结束前采信中间值。
    超时仍未稳定时返回最后一次有效读数并告警, 让调用方拿到比回退值更接近真实的值。
    """
    required = max(ESTIMATE_STABLE_READS, 1)
    observe = ESTIMATE_MIN_OBSERVE_SECONDS
    deadline = time.monotonic() + timeout
    last: int | None = None
    same = 0
    first_seen: float | None = None
    zero_seen = False
    valid_reads = 0
    tight_seen = False

    while time.monotonic() < deadline:
        value, tight = task._read_estimate_value(
            box,
            min(deadline - time.monotonic(), ASSET_OCR_TIMEOUT),
            label,
        )
        now = time.monotonic()
        if tight:
            # 贴边帧: 末位可能已被切掉且位数不变, 不能按「未读出」累积 same,
            # last / valid_reads / first_seen 必须一起作废, 否则旧值会被攒成
            # 「稳定值」返回 (推导见 auction-notes 5.6)。
            tight_seen = True
            same = 0
            first_seen = None
            last = None
            valid_reads = 0
        elif value is None:
            # 未读出或残缺读数, 没有带来更完整的信息. 画面算「没变化」(same 继续累积),
            # 但它证明不了 last 是终值, 所以「连续有效读数」的计数到此为止.
            same += 1
            valid_reads = 0
        elif skip_zero and value == 0:
            # 面板加载中的占位读数, 不计入稳定判定.
            zero_seen = True
            valid_reads = 0
        elif last is None or (len(str(value)) >= len(str(last)) and value != last):
            # 位数变多或数值更新: 之前攒的连续次数作废, 以这次为准.
            if first_seen is None:
                first_seen = now
            last = value
            same = 1
            valid_reads = 1
        else:
            # 与当前读数相同, 或位数更少(数字滚动中途读到更短的值).
            same += 1
            if value == last:
                # 同一个完整数值被再次读到, 才是真正意义上的「有效读数」.
                valid_reads += 1
            else:
                # 位数更少的残缺值: 同样是一次「没读全」, 连续有效读数的计数归零.
                valid_reads = 0

        if (
            last is not None
            and same >= required
            and valid_reads >= required
            and first_seen is not None
            and now - first_seen >= observe
        ):
            task.log_info(f"{label}读数稳定: {last} (连续 {same} 次)")
            return last

        # 必须换一帧再读, 否则两次读取会落在同一帧上, 读到同样的中间值.
        task.next_frame()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            task.sleep(min(task.POLL_INTERVAL, remaining))

    if last is not None:
        task.log_warning(f"{label}在 {timeout} 秒内未稳定, 使用最后一次读数 {last}")
        if tight_seen:
            # 观测期间出现过贴边帧, 这个值可能不完整, 只作参考.
            task.log_warning(
                f"{label}观测期间出现过贴边读数, 该值可能不完整; "
                "若与实际不符, 请检查估价识别区域的右边界是否被裁切"
            )
    elif tight_seen:
        task.log_warning(
            f"{label}读数贴住识别区域边界(或其后读数不可信), 末位可能被裁掉, "
            "视为未读出; 请检查估价识别区域的右边界"
        )
    elif zero_seen:
        task.log_warning(f"{label}在 {timeout} 秒内只读到 0, 视为未读出")
    return last
