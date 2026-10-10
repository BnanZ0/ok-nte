"""拍卖 OCR 数值读取: 全量文本读取, 按标签定位取数, 资产解析, 稳定读取。

    依赖以冻结数据类 ReadingOps 显式注入 (装配点在任务类 _reading_ops, 口径见
    auction-notes 8): 低层框架面 (OCR/换帧/睡眠/日志/屏宽/轮询节奏/放大补拍)
    由装配器填充; 五个读数原语 (asset_once / estimate_once / input_range_once /
    stable_once / info_once) 缺省填充为本模块同名函数 (未绑定, 调用时首参
    显式传 ops),
内部组合与外部调用方 (bid / bid_price / sell / welfare / routing) 一律经
字段调用 —— 测试注入假读数时只覆盖对应字段, 不再 patch 任务实例方法;
dataclasses.replace 派生时替换框架字段后原语自然使用新 ops, 无旧绑定残留。
本模块不持有任务对象, 无 task.<attr> 访问面。
读取节奏常量由本模块定义, 不再挂回任务类 (POLL_INTERVAL 除外: 它是匹配/
出价/结算共用的轮询节奏, 所有者仍是任务类, 经 ReadingOps.poll_interval 传入)。
本模块只承载「怎么读」: 标签定位, 残缺与贴边防线, 数字滚动的稳定判定;
「读什么、读出来怎么用」(出价计算, 结算记录) 仍由任务层编排。
"""

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ok import Box

from src.tasks.auction.layout import FULLWIDTH_NUMERIC, RE_NUMBER
from src.tasks.auction.price import (
    has_inconsistent_grouping,
    is_partial_number_text,
    parse_asset_value,
    parse_price_hint_cap,
)

# 资产读取的单次 OCR 等待上限: 出价路径每次出价都要读一遍资产, 给太长会拖慢单轮。
ASSET_OCR_TIMEOUT = 15
# 键盘「可输入范围0~N」提示是面板就绪即在场的静态文案, 预算给小: 读不到时
# (区域为空/已是输入回显)尽快返回 None, 交回钳制路径落到资产框复核。
INPUT_RANGE_CAP_OCR_TIMEOUT = 5
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
# 情报窗口单次读取预算: 每口出价前快照不全时补读一次, 不在轮询热路径,
# 读不到尽快返回, 由调用方按缺字段降级。
INFO_OCR_TIMEOUT = 5

# LotInfo 的字段名清单: complete / empty / merged 按这份清单遍历。
_LOT_INFO_FIELDS = ("purple_count", "purple_avg", "gold_count", "gold_avg", "total_count")
# 情报条目的颜色词: 「紫色品质藏品」「金色品质藏品」。
RE_INFO_COLOR = re.compile(r"([紫金])色品质")
# 情报条目文案 → LotInfo 字段: 「总数量」/「平均价值」; 「所占格数」期望价值
# 模式不消费, 不解析。
_INFO_FIELD_MAP = {
    ("紫", "总数量"): "purple_count",
    ("紫", "平均价值"): "purple_avg",
    ("金", "总数量"): "gold_count",
    ("金", "平均价值"): "gold_avg",
}


def _fill(current: int | None, incoming: int | None) -> int | None:
    """合并快照时字段只补缺: 已有读数优先, 不被后续读数覆盖(信息只升不降)。"""
    return current if current is not None else incoming


@dataclass(frozen=True)
class LotInfo:
    """单件拍品的情报窗口快照, 期望价值模式的输入。

    字段含义来自游戏情报文案: 紫/金品质藏品的总数量与平均价值, 以及紫金红
    三色藏品总件数。None 表示尚未读到: 情报条目在窗口里逐条滚动展示, 单次
    读取经常只看到部分条目, 由调用方跨口用 merged 只补缺不降级, 读全
    (complete)即停。
    """

    purple_count: int | None = None
    purple_avg: int | None = None
    gold_count: int | None = None
    gold_avg: int | None = None
    total_count: int | None = None

    def _values(self) -> Iterable[int | None]:
        return (getattr(self, name) for name in _LOT_INFO_FIELDS)

    @property
    def complete(self) -> bool:
        """五个字段是否都已读到。"""
        return all(value is not None for value in self._values())

    @property
    def empty(self) -> bool:
        """是否一个字段都没读到(整窗未识别到情报条目)。"""
        return all(value is None for value in self._values())

    def merged(self, other: "LotInfo") -> "LotInfo":
        """用其他快照补本快照的缺字段, 返回新快照(本类型不可变)。"""
        return LotInfo(
            **{
                name: _fill(getattr(self, name), getattr(other, name))
                for name in _LOT_INFO_FIELDS
            }
        )


def _int_after_wei(text: str) -> int | None:
    """取最后一个「为」之后的数字组, 没有数字或超过 7 位返回 None。

    情报条目形如「...平均价值为45,500。」「...总件数为14件。」, 数字组紧跟
    「为」字; 全角数字与全角逗号已由调用方归一化。7 位上限防止 OCR 噪声把
    相邻字符串接成天文数字 (口径移植自达芙 ocr_reader._int_after_wei)。
    """
    index = text.rfind("为")
    if index < 0:
        return None
    match = re.match(r"[0-9,.、]+", text[index + 1 :])
    if match is None:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    if not digits or len(digits) > 7:
        return None
    return int(digits)


def parse_info_fields(texts: Iterable[str]) -> LotInfo:
    """情报窗口 OCR 文本行 → 拍品情报快照, 一条都解析不出返回空快照。

    只认情报条目(含「本局内」或「品质藏品」), 每条取最后一个「为」后的数字
    组, 同一字段一行内多次出现取先读到的。文案匹配移植自达芙计算器
    ocr_reader.parse_info (2026-10-07): 「紫」被 OCR 误读成别的字时该条按
    未识别处理, 不猜。
    """
    values: dict[str, int] = {}
    for raw in texts:
        text = re.sub(r"\s+", "", str(raw or "")).translate(FULLWIDTH_NUMERIC)
        if "本局内" not in text and "品质藏品" not in text:
            continue
        value = _int_after_wei(text)
        if value is None:
            continue
        if "总件数" in text:
            values.setdefault("total_count", value)
            continue
        color_match = RE_INFO_COLOR.search(text)
        if color_match is None:
            continue
        keyword = "总数量" if "总数量" in text else ("平均价值" if "平均价值" in text else None)
        field = _INFO_FIELD_MAP.get((color_match.group(1), keyword))
        if field is not None:
            values.setdefault(field, value)
    return LotInfo(**values)


@dataclass(frozen=True)
class ReadingOps:
    """reading 模块的显式依赖束。

    低层框架面字段由任务侧装配器填充; 读数原语字段缺省 None, __post_init__
    填充为本模块同名函数 —— 未绑定, 调用时首参显式传 ops, 因此
    dataclasses.replace 派生 (替换框架字段或覆盖原语) 后不存在指向旧 ops
    的残留绑定。覆盖字段即注入假读数; 调用面与生产路径完全一致。
    """

    # --- 低层框架面 ---
    ocr: Callable
    wait_ocr: Callable
    next_frame: Callable
    sleep: Callable
    # 残缺/无逗号读数的 3 倍放大补拍通道 (任务侧 _ocr_upscaled)
    upscaled_ocr: Callable[[Box], list]
    log_info: Callable
    log_warning: Callable
    log_debug: Callable
    width: int
    poll_interval: float

    # --- 读数原语 (None = 填充本模块同名函数, 未绑定) ---
    asset_once: Callable | None = None
    estimate_once: Callable | None = None
    input_range_once: Callable | None = None
    stable_once: Callable | None = None
    info_once: Callable | None = None

    def __post_init__(self) -> None:
        if self.asset_once is None:
            object.__setattr__(self, "asset_once", read_asset_value)
        if self.estimate_once is None:
            object.__setattr__(self, "estimate_once", read_estimate_value)
        if self.input_range_once is None:
            object.__setattr__(self, "input_range_once", read_input_range_cap)
        if self.stable_once is None:
            object.__setattr__(self, "stable_once", read_stable_asset_value)
        if self.info_once is None:
            object.__setattr__(self, "info_once", read_info_once)


def read_estimate_texts(ops: ReadingOps, box: Box, timeout: float) -> list:
    """读区域内**全部**文本, 不做 match 过滤。

    必须用 `ops.ocr(match=None)` 而不是 `wait_ocr(match=RE_NUMBER)`: 框架的
    `wait_ocr` 会按 match 把返回值过滤成只含命中的框, 标签等非命中文本会丢失,
    导致按标签定位取数永远失败 (框架行为与线上案例见 auction-notes 1.1)。
    取一帧即可: 调用方的稳定判定循环本来就会反复重读。
    """
    deadline = time.monotonic() + timeout
    while True:
        boxes = ops.ocr(box=box, match=None, log=False)
        texts = [b for b in boxes if b.name] if boxes else []
        if texts or time.monotonic() >= deadline:
            return texts
        ops.sleep(0.2)


def read_estimate_value(
    ops: ReadingOps,
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
    all_boxes = read_estimate_texts(ops, box, timeout)
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
        ops.log_debug(f"{label} 标签未读到, 本帧数字不可靠")
        return None, False

    candidates = [b for b in all_boxes if b.x >= label_right and RE_NUMBER.search(b.name)]
    if not candidates:
        return None, False

    digit_boxes = sorted(candidates, key=lambda b: b.x)
    raw_text = "".join(b.name for b in digit_boxes)
    if is_partial_number_text(raw_text):
        ops.log_debug(f"{label} OCR: '{raw_text}', 千位分隔符前缺数字, 视为残缺读数")
        return None, False
    if has_inconsistent_grouping(raw_text):
        ops.log_debug(f"{label} OCR: '{raw_text}', 千位分组不自洽, 视为残缺读数")
        return None, False

    value = parse_asset_value(raw_text)
    right_edge = max(b.x + b.width for b in digit_boxes)
    # 阈值随分辨率等比放大, 至少 1px: 高 DPI 下框宽不变(本项目 resize_image 为默认 0,
    # 截图不重采样)时小于 1px 的判定没有意义.
    margin = max(1, round(ops.width * ESTIMATE_EDGE_MARGIN_RATIO))
    tight = (box.x + box.width - right_edge) < margin
    ops.log_debug(f"{label} OCR: '{raw_text}', 解析值: {value}, 贴边: {tight}")
    return value, tight


def read_asset_value(
    ops: ReadingOps,
    box: Box,
    timeout: float,
    label: str = "资产",
) -> int | None:
    """对指定区域做 OCR 并解析资产数值, 未识别或解析失败时返回 None。

    残缺读数(逗号前空, 如 7,284 漏读首位成 ,284)与无逗号读数(首位连逗号
    一起丢, 或真实值 < 1000)先做一次 3 倍放大补读: 补读位数严格更多才采信
    (截断只丢前导位不会增值, 采信依据与出价钳制的三源取最大一致, 见
    auction-notes 5.1); 残缺读数补读仍救不回时按未读出返回 None, 不把残缺
    文本洗成错值 —— 无逗号且非残缺的读数可能是真实的小值, 补读无改善时
    保持原读数。
    残余: 带逗号的整组前导丢失(16,155,238 洗成 155,238)从文本上不可检测,
    不触发补读; 出价钳制路径由键盘「可输入范围0~N」上限兜住(见 5.1)。
    """
    boxes = ops.wait_ocr(
        box=box,
        match=RE_NUMBER,
        time_out=timeout,
        raise_if_not_found=False,
        settle_time=0.5,
    )
    if not boxes:
        return None

    raw_text = "".join(text_box.name for text_box in boxes)
    value = parse_asset_value(raw_text)
    partial_flag = is_partial_number_text(raw_text)
    commaless = "," not in raw_text.translate(FULLWIDTH_NUMERIC)
    if partial_flag or commaless:
        value = _recover_truncated_value(ops, box, label, raw_text, value, partial_flag)
    ops.log_debug(f"{label} OCR: '{raw_text}', 解析值: {value}")
    return value


def _recover_truncated_value(
    ops: ReadingOps,
    box: Box,
    label: str,
    raw_text: str,
    value: int | None,
    partial: bool,
) -> int | None:
    """残缺/无逗号读数的放大补读: 位数严格更多才采信, 残缺读数救不回按未读出。

    采信走 log_info: 这是金额相关读数被纠正的时刻, 要在日志里可观测。
    value 为 None 仅见于纯逗号残缺文本(残缺防线必拦的形态), 此时补读出数字
    即按救回采信; 非 None 基线必须位数严格更多, 不给同位数读数翻案空间。
    """
    up_boxes = ops.upscaled_ocr(box)
    up_text = "".join(b.name for b in up_boxes if RE_NUMBER.search(b.name)) if up_boxes else ""
    up_value = parse_asset_value(up_text) if up_text else None
    rescued = up_value is not None and (
        partial if value is None else len(str(up_value)) > len(str(value))
    )
    if rescued:
        ops.log_info(
            f"{label} 原尺寸读数 '{raw_text}' 残缺, 放大补读 '{up_text}', 采信 {up_value}"
        )
        return up_value
    if partial:
        ops.log_warning(f"{label} OCR: '{raw_text}', 残缺读数放大补读未救回, 按未读出处理")
        return None
    ops.log_debug(f"{label} 无逗号读数放大补读无改善, 保持原读数 {value}")
    return value


def read_input_range_cap(ops: ReadingOps, box: Box, timeout: float) -> int | None:
    """读「可输入范围0~N」提示里的输入上限 N, 读不到返回 None。

    必须全量读文本再解析: 走 wait_ocr(match=RE_NUMBER) 会把 ~ 连同中文过滤掉,
    上限与前导 0 的分界无从定位 (match 过滤丢非命中文本的框架行为见
    auction-notes 1.1)。拼出整行后由 parse_price_hint_cap 取最后一个 ~ 后的
    数字组; 无 ~ 时(输入框已有回显的重试帧)按提示不可用返回 None, 不猜。
    """
    texts = read_estimate_texts(ops, box, timeout)
    raw_text = "".join(text_box.name for text_box in texts)
    cap = parse_price_hint_cap(raw_text)
    ops.log_debug(f"输入范围提示 OCR: '{raw_text}', 上限: {cap}")
    return cap


def read_info_once(ops: ReadingOps, box: Box, timeout: float) -> LotInfo:
    """读一次情报窗口并解析成拍品情报快照, 没读到任何条目返回空快照。

    必须全量读文本(match=None): 情报条目是整句中文, 按 RE_NUMBER 过滤会把
    句子连同数字一起丢掉 (match 过滤丢非命中文本的框架行为见 auction-notes 1.1)。
    返回 LotInfo 由调用方跨口合并, 本函数不做合并。
    """
    texts = read_estimate_texts(ops, box, timeout)
    info = parse_info_fields(text_box.name for text_box in texts)
    ops.log_debug(f"情报窗口 OCR: {info}")
    return info


def read_stable_asset_value(
    ops: ReadingOps,
    box: Box,
    timeout: float,
    label: str,
    *,
    skip_zero: bool = False,
) -> int | None:
    """连续读到相同数值才认为读数稳定, 避免取到跳动中的中间值。

    出价面板的当前估价在界面刚出现时会跳动几次, 第一次识别到的往往不是最终值,
    所以按 poll_interval 换帧重读, 连续 ESTIMATE_STABLE_READS 次没有出现更完整的
    读数才采用。
    skip_zero 用于把 0 当作「面板还没滚出数值」的占位读数: 估价面板在数字滚动前会先
    显示 0, 把它当结果会算出 0 元出价, 所以这类读数不计入稳定判定, 继续等真值。

    判定规则(防线结构与推导过程见 auction-notes 5.6):
    - 读数走 `ops.estimate_once`, 即按标签右边沿取数字, 挡住末位被裁与
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
        value, tight = ops.estimate_once(
            ops,
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
            ops.log_info(f"{label}读数稳定: {last} (连续 {same} 次)")
            return last

        # 必须换一帧再读, 否则两次读取会落在同一帧上, 读到同样的中间值.
        ops.next_frame()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            ops.sleep(min(ops.poll_interval, remaining))

    if last is not None:
        ops.log_warning(f"{label}在 {timeout} 秒内未稳定, 使用最后一次读数 {last}")
        if tight_seen:
            # 观测期间出现过贴边帧, 这个值可能不完整, 只作参考.
            ops.log_warning(
                f"{label}观测期间出现过贴边读数, 该值可能不完整; "
                "若与实际不符, 请检查估价识别区域的右边界是否被裁切"
            )
    elif tight_seen:
        ops.log_warning(
            f"{label}读数贴住识别区域边界(或其后读数不可信), 末位可能被裁掉, "
            "视为未读出; 请检查估价识别区域的右边界"
        )
    elif zero_seen:
        ops.log_warning(f"{label}在 {timeout} 秒内只读到 0, 视为未读出")
    return last
