"""出价价格的键盘输入: 数字按键序列, 上轮出价快捷键, 输入结果校验, 确认出价。

模块函数的第一个参数 actions 是 KeypadActions: 冻结的窄动作接口, 点击/OCR/
日志等框架 API 与价格计算经它访问, 本模块不持有任务对象。上轮出价价格由
调用方显式传入, 确认成功后经返回值交还任务侧落账。按键序列切分与数值解析
的纯规则见 auction_price。输入失败抛 WaitFailedException / ValueError,
由出价循环决定重试。
"""

from collections.abc import Callable
from dataclasses import dataclass

from ok import Box, WaitFailedException

from src.tasks.auction import price as auction_price
from src.tasks.auction.layout import (
    PAD_MAP,
    RE_BID_CONFIRM,
    RE_NUMBER,
    RE_PRICE_HINT,
    AuctionBoxes,
)

# 每次出价都要查一遍异常确认框, 预算调小: 漏掉一次弹窗要赔上整轮, 但不该固定白等 2 秒.
BID_NOTICE_POPUP_TIMEOUT = 1.0


@dataclass(frozen=True)
class KeypadActions:
    """键盘输入所需的界面动作与输入决策依据, 由任务侧在每次出价时装配。

    auto_raise_enabled 与 bid_notice_popup_timeout 在装配时读取: 配置与类常量
    在单次输入流程内不会变化, 显式传入让本模块不反查任务状态。
    """

    calculate_price: Callable[[AuctionBoxes | None, float | None], int]
    remaining_timeout: Callable[[float | None, float], float]
    wait_operate_click: Callable[..., bool]
    dismiss_notice_popup: Callable[..., bool]
    operate_click: Callable[..., object]
    box_of_screen: Callable[..., Box]
    wait_ocr: Callable[..., list]
    # 放大裁剪后的区域全量 OCR (match=None): 孤立单位数字在原尺寸稀疏裁剪里
    # 检测不到, 原尺寸读不到价格时的补拍通道 (实测见 auction-notes 5.1)。
    ocr_upscaled: Callable[[Box], list]
    auto_raise_enabled: bool
    bid_notice_popup_timeout: float
    log_info: Callable[[str], None]
    log_debug: Callable[[str], None]
    log_warning: Callable[[str], None]


def input_price(
    actions: KeypadActions,
    boxes: AuctionBoxes,
    price: int | None = None,
    deadline: float | None = None,
    last_bid_price: int | None = None,
) -> int:
    """使用游戏内数字键盘输入价格, 支持上轮出价、00 和 0000 快捷按钮。

    输入失败时抛出异常, 由调用方重试本次出价。返回确认输入的价格, 由调用方
    把它记为新的上轮出价; 确认失败时不会返回, 上轮出价保持原值。
    """
    if price is None:
        price = actions.calculate_price(boxes, deadline)

    price_str = str(price)
    if price <= 0:
        raise ValueError(f"非法价格 '{price}'")
    if deadline is not None:
        actions.remaining_timeout(deadline, 0.1)

    if can_reuse_last_bid(actions.auto_raise_enabled, last_bid_price, price):
        actions.operate_click(boxes.last_bid, after_sleep=0.2)
        actions.log_info(f"使用上轮出价快捷输入价格 {price}")
    else:
        actions.operate_click(boxes.clear, after_sleep=0.3)
        press_price_digits(actions, price_str, deadline)

    verify_input_price(actions, boxes, price, deadline)
    confirm_bid_price(actions, boxes, deadline)

    actions.log_info(f"输入价格 {price}")
    return price


def can_reuse_last_bid(
    auto_raise_enabled: bool, last_bid_price: int | None, price: int
) -> bool:
    """仅在未启用自动加价时复用上轮出价, 避免自动加价下快捷输入的不确定性。"""
    return (
        not auto_raise_enabled
        and last_bid_price is not None
        and price == last_bid_price
    )


def press_price_digits(
    actions: KeypadActions, price_str: str, deadline: float | None
) -> None:
    """按数字键盘逐键点击, 优先使用 0000 / 00 快捷键。"""
    for key in auction_price.price_key_sequence(price_str):
        if deadline is not None:
            actions.remaining_timeout(deadline, 0.1)
        actions.operate_click(actions.box_of_screen(*PAD_MAP[key]), after_sleep=0.2)


def _is_unusable_price_text(raw_price: str) -> bool:
    """提示文案或残缺读数都不能当作输入结果, 语义见 verify_input_price。"""
    return bool(
        RE_PRICE_HINT.search(raw_price)
        or auction_price.is_partial_number_text(raw_price)
        or auction_price.has_inconsistent_grouping(raw_price)
    )


def verify_input_price(
    actions: KeypadActions, boxes: AuctionBoxes, price: int, deadline: float | None
) -> None:
    """校验数字面板显示的价格与目标价格一致, 不一致时抛出异常。

    价格区未输入时显示 "可输入范围0~<资产>" 提示文本, 视为未识别处理;
    残缺读数(首位漏读/千位分组不自洽, 防线见 auction-notes 2.1)位数不可信,
    比对通过也不能证明读数正确, 同样视为未识别。丢逗号型残缺读数(逗号不
    改变数字个数)解析值可能仍等于目标价, 防线对它的代价是转第二区域复核。

    先读常规价格区, 读不出或命中提示文案时再读键盘态输入框: 键盘态下
    BOX_PRICE_RESULT 会落在提示文案上而永远读不到数字 (背景见 auction-notes 5.1),
    两个位置都没得到可用数字才算未输入。
    """
    raw_price = read_price_text(actions, boxes.price_result, deadline)
    if not raw_price or _is_unusable_price_text(raw_price):
        keypad_text = read_price_text(actions, boxes.price_result_keypad, deadline)
        if keypad_text and not _is_unusable_price_text(keypad_text):
            raw_price = keypad_text

    if not raw_price:
        actions.log_warning("输入价格结果未识别, 取消确认并重试当前出价")
        raise WaitFailedException("输入价格结果未识别")
    if _is_unusable_price_text(raw_price):
        actions.log_warning("价格区仍是提示文案或残缺读数, 视为未输入, 取消确认并重试当前出价")
        raise WaitFailedException("输入价格结果未识别")

    input_price_value = auction_price.parse_asset_value(raw_price)
    actions.log_debug(f"输入价格结果 OCR: '{raw_price}', 解析值: {input_price_value}")
    if input_price_value != price:
        actions.log_warning(
            f"输入价格校验失败, 目标价格: {price}, 实际价格: {input_price_value}, "
            "取消确认并重试当前出价"
        )
        raise WaitFailedException("输入价格校验失败")


def read_price_text(actions: KeypadActions, box: Box, deadline: float | None) -> str:
    """读取价格区文本, 未识别时返回空串。

    原尺寸裁剪对多位数字可靠 (标定帧与实机数据见 auction-notes 3); 孤立单位
    数字(一位价格)在这个宽稀疏裁剪里检测器整帧漏检 (2026-10-05 实测: 大字 6
    在 BOX_PRICE_RESULT 内, 原尺寸 0 命中, 放大 3 倍后置信度 0.99+, 见
    auction-notes 5.1), 原尺寸读不到时用放大裁剪补拍一拍。
    """
    price_boxes = actions.wait_ocr(
        box=box,
        match=RE_NUMBER,
        time_out=3 if deadline is None else actions.remaining_timeout(deadline, 3),
        settle_time=0.5,
        raise_if_not_found=False,
    )
    if price_boxes:
        return "".join(text_box.name for text_box in price_boxes)
    return _read_upscaled_price_text(actions, box)


def _read_upscaled_price_text(actions: KeypadActions, box: Box) -> str:
    """放大裁剪补拍价格区: 返回数字拼接文本, 仍读不到时留痕检测器全量读数。

    全量读数取自 match=None 的放大补拍, 是排查「输入价格结果未识别」时能拿到的
    最接近检测器原文的证据 (诊断惯例见 auction-notes 1.1)。
    """
    boxes = actions.ocr_upscaled(box)
    texts = [b.name for b in boxes if b.name] if boxes else []
    digits = "".join(text for text in texts if RE_NUMBER.search(text))
    if not digits:
        actions.log_warning(f"价格区放大回读仍无数字, 检测器全量读数: {texts}")
    return digits


def confirm_bid_price(
    actions: KeypadActions, boxes: AuctionBoxes, deadline: float | None
) -> None:
    """点击确认出价, 并处理可能出现的异常确认框。"""
    confirmed = actions.wait_operate_click(
        boxes.bid_confirm,
        RE_BID_CONFIRM,
        5 if deadline is None else actions.remaining_timeout(deadline, 5),
        after_sleep=0.2,
    )
    if not confirmed:
        actions.log_warning("确认出价点击超时, 准备重试当前出价")
        raise WaitFailedException("确认出价失败")

    # 检测是否出现异常确认框, 带短超时轮询防单帧漏检 (背景见 auction-notes 5.1).
    actions.dismiss_notice_popup(
        boxes, deadline, "确认出价后", timeout=actions.bid_notice_popup_timeout
    )
