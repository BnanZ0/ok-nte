import math
import re
import time
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

from ok import Box, Config, TaskDisabledException, WaitFailedException
from ok.util.file import get_relative_path, read_json_file

from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask

# --- 拍卖界面 OCR 正则 ---
# 集中定义, 避免各阶段重复编译同一规则。
RE_MATCH = re.compile(r"开始匹配|开始|匹配")
RE_CONFIRM = re.compile(r"确\s*认")
RE_BID = re.compile(r"出\s*价")
# 数字键盘弹出后的出价界面判定。
# 键盘弹窗会盖住 BOX_BID 所在区域(实测该框 all_boxes 全空), 此时界面上只剩弹窗自己的
# 元素 —— 右下角的「出价」按钮和「放弃」都在弹窗外或不可见, 用 RE_BID 判定会永远为假,
# 于是空转到 60 秒报「等待出价界面超时」(2026-09-23 18:44 实测一次)。这里收弹窗上的
# 固定文案作为补充特征; 这些字串只出现在出价键盘/面板上, 放宽不会误命中其他界面。
RE_BID_PANEL = re.compile(r"出\s*价|请输入你愿意出的价格|推荐出价参考|上轮出价|清空")
RE_SKIP = re.compile(r"跳\s*过")
RE_EXIT = re.compile(r"退\s*出")
RE_BID_CONFIRM = re.compile(r"确认出价")
RE_BID_PANEL_READY = re.compile(r"确认出价|[0-9]")
RE_NUMBER = re.compile(r"[0-9\uff10-\uff19,]+")
# 价格输入区未输入时显示 "可输入范围0~<资产>" 提示, 同样能被 RE_NUMBER 命中, 不能当作价格.
RE_PRICE_HINT = re.compile(r"[~\uff5e\u4e00-\u9fff]")
RE_MAIN_TITLE = re.compile(r"即刻落槌")
RE_COLLECTION_INSUFFICIENT = re.compile(r"少于200格")
RE_WELFARE = re.compile(r"低保金")
RE_CLAIM = re.compile(r"领取")
RE_CANCEL = re.compile(r"取消")
RE_WAREHOUSE = re.compile(r"藏品仓库")
RE_SELL_LABEL = re.compile(r"出售\s*价值")
# 结算界面右下角游戏自带的「一键出售」圆钮(图标 + 文字), 拍卖成功后才出现.
# 首字「一」是单笔画, 检测模型在裁剪偏紧时会直接丢掉它(实测只读出「键出售」),
# 所以两种写法都收 —— 这个区域里只有这一个按钮, 放宽不会误命中别的东西.
RE_ONE_CLICK_SELL = re.compile(r"一键出售|键出售")
# 一键出售后弹出的「获得物品」提示条, 底部写着这句, 点提示条以外的空白区域即可关闭.
# 只匹配前半句: 整句较长, 尾部被 OCR 认坏时仍要能命中.
RE_POPUP_CLOSE_HINT = re.compile(r"点击空白")
# 掉线回场路径上的两个标志: 「都市闲趣」MENU 面板标题, 以及拍卖入口卡片「即刻落槌」。
RE_CITY_FUN = re.compile(r"都市闲趣")
# 拍卖主界面右侧的会场文字, 形如「当前：海贝场」。回场后用它核对会场有没有被重置。
RE_CURRENT_VENUE = re.compile(r"当前")

# 全角数字转半角, 用于统一资产与价格的 OCR 文本。
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 数字键盘上一次点击即可输入的快捷键, 需优先于逐位输入。
PAD_SHORTCUTS = ("0000", "00")


class AuctionState(Enum):
    """单轮拍卖过程中可检测到的界面状态。"""

    CONFIRM = "confirm"
    BID = "bid"
    SKIP = "skip"
    # 掉线被踢回大世界: 界面既不在拍卖流程里, 也不在主界面上, 需要先回场再继续。
    WORLD = "world"


@dataclass(frozen=True)
class AuctionBoxes:
    """单轮拍卖使用的 UI 区域, 在轮次开始时按相对比例一次性构建。"""

    match: Box
    confirm: Box
    bid: Box
    bid_keypad: Box
    skip_area: Box
    exit: Box
    bid_confirm: Box
    abandon: Box
    abandon_confirm: Box
    asset_value: Box
    estimate: Box
    last_bid: Box
    clear: Box
    price_result: Box
    price_result_keypad: Box
    exception_area: Box
    main_title: Box
    main_asset: Box
    insufficient: Box
    welfare_btn: Box
    welfare_dialog: Box
    claim: Box
    cancel: Box
    warehouse_btn: Box
    warehouse_title: Box
    sell: Box
    confirm_sell: Box
    sell_label: Box
    sell_value: Box
    blank: Box
    close: Box
    one_click_sell: Box
    popup_close_hint: Box
    popup_blank: Box


@dataclass(frozen=True)
class PostRoundState:
    """结算后回到主界面时的观测结果, 供轮次末尾的出售决策复用。

    inventory_full 为 None 表示本轮未检测满仓状态, 需要时由调用方补测。
    observed 表示本轮是否真的执行过结算后观测: _finish_auction 在主界面标题没
    识别到时会提前返回, 此时画面状态未知, 轮次末尾不能再按「已回到主界面」去
    点仓库入口, 否则只会在错误的界面上白等超时。
    """

    inventory_full: bool | None = None
    welfare_claimed: bool = False
    observed: bool = False


def _inst_line(text: str, color: str = "", *, bold: bool = False, indent: int = 0):
    content = f"{'&nbsp;' * (indent * 4)}{text}"
    if bold:
        content = f"<strong>{content}</strong>"
    return f'<span style="color:{color};">{content}</span>'


# 任务卡上的「说明」按钮内容: ok-script 在 task.instructions 非空时显示该按钮,
# 点击后用富文本弹窗渲染, 支持 <strong> / <span style> / <a href>, 换行由框架转 <br>。
# 本任务只支持 zh_CN(supported_languages), 因此不准备英文版本。
# 正文里带「」的串与面板上的配置标签同名, 便于用户按标签在面板上对号入座。
#
# ⚠️ 行数是硬约束, 加内容前先看这里: qfluentwidgets 的 Dialog 用
# QVBoxLayout.SetMinimumSize, 既没有滚动区也没有高度钳制, 而框架的
# show_instructions 会 hide() 掉「取消」, 只留内容下方的「确定」按钮。
# 上限由 MaskDialogBase.setGeometry(0, 0, parent.width(), parent.height()) 决定 ——
# 遮罩跟着**父窗口**(src/config.py 的 window_size = 1200x800)而不是屏幕走, 弹窗高于
# 父窗口时居中溢出, 上下两端一起被裁, 底部的按钮行随之消失, 用户直接关不掉弹窗。
# 实测: 每行约 16px + 185px 固定开销(标题/边距/按钮行), 旧版 50 行 = 985px,
# 在 800px 父窗口下溢出 185px; 硬上限约 38 行。因此这里也不插空行(_inst_gap)——
# 空行同样吃高度, 分段靠标题本身。上限由测试的长度用例守住。
# ruff: disable[E501]
INST = "<br>".join(
    [
        _inst_line("📍 使用前提", "#FF5555", bold=True),
        _inst_line("在大世界或拍卖主界面启动均可, 会自动进入; 「循环次数」填 0 = 一直运行", indent=1),
        _inst_line("掉线被踢回大世界时会自动回场(F5 都市大亨 → 都市闲趣 → 即刻落槌)", indent=1),
        _inst_line("💰 「出价模式」三选一", "#FF5555", bold=True),
        _inst_line(
            "按系统估价: 估价 x「估价倍率」, 读不出时回退「基础价」", "#FE821D", bold=True, indent=1
        ),
        _inst_line(
            "自定义价格: 以「基础价」为基准, 开「启用自动加价」后按「加价方式 / 加价数值 /",
            "#FE821D",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "加价回合数」逐次提价, 也可用「启用指定回合单独出价」走「指定回合价格」", indent=2
        ),
        _inst_line(
            "每轮指定价格: 「第1次出价价格」~「第6次出价价格」, 填 0 沿用上次",
            "#FE821D",
            bold=True,
            indent=1,
        ),
        _inst_line("第 6 次必须高于第 5 次, 否则会被系统拒绝", "#FF5555", bold=True, indent=2),
        _inst_line("📦 「出售藏品模式」四选一", "#FF5555", bold=True),
        _inst_line(
            "不出售 / 满仓时清理 / 按间隔出售 / 拍卖成功一键出售", "#FE821D", bold=True, indent=1
        ),
        _inst_line(
            "满仓后无法继续出价, 建议至少选「满仓时清理」; 「出售藏品间隔次数」填 0 = 只按满仓清理",
            indent=2,
        ),
        _inst_line(
            "「保留藏品品质」勾选的不卖, 一个都不勾 = 全卖; 「满仓或领低保后追加出售品质」",
            indent=2,
        ),
        _inst_line("会在满仓或领低保时连保留品质一起卖", indent=2),
        _inst_line(
            "✨ 「启用辅助功能」: 表情包 = 出价后发表情; 低保金 = 资产低于 10 万时领取",
            "#FF5555",
            bold=True,
        ),
        _inst_line("🔄 升级后必看", "#FF5555", bold=True),
        _inst_line(
            "旧版「启用自动清理藏品 / 出售藏品间隔次数 / 启用表情包 / 启用低保金」已合并进",
            "#FE821D",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "「出售藏品模式」与「启用辅助功能」, 首次启动自动换算, 旧键消失属正常", indent=2
        ),
        _inst_line("「加价方式」旧值「倍数」已改名「倍率」, 自动改写", indent=2),
        _inst_line("「按系统估价」读估价会多等约 1 秒(防误读成 1/10 价格), 不是卡住", indent=2),
        _inst_line(
            "想让本机配置回到这套默认: 面板点「重置配置」, 它会清掉你填过的值(含价格),",
            "#FF5555",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "「循环次数」回到 0(一直运行), 「出售藏品模式」回到「不出售」",
            "#FF5555",
            bold=True,
            indent=2,
        ),
        _inst_line(
            "⚠️ 出价失败会自动重试, 单轮失败不影响后续轮次; 仓库卖不掉时会放宽保留品质", indent=1
        ),
    ]
)
# ruff: enable[E501]


class AutoBidAuctionTask(NTEOneTimeTask, BaseNTETask):
    """自动完成游戏内拍卖流程。

    功能包括: 匹配, 确认, 出价, 出价重试, 结算, 低保金领取, 表情包发送, 藏品出售。
    需要在拍卖主界面选择低级会场后开始执行。
    """

    # --- 拍卖配置 ---
    CONF_FIXED_PRICE = "基础价"

    # 藏品出售: 用一个模式下拉框统一控制, 相关子配置集中显示在它下面。
    # 旧版的「启用自动清理藏品」开关与「出售藏品间隔次数」是互斥的两档, 现在合并进模式:
    #   不出售           -> 完全不碰仓库
    #   满仓时清理       -> 只在主界面出现「库存不足」提示时出售
    #   按间隔出售       -> 每 N 轮出售一次, 满仓时提前触发
    #   拍卖成功一键出售 -> 用游戏自带的一键出售, 在结算界面直接卖掉本局藏品, 不碰仓库
    # 前三种走同一套「仓库流程」(满仓检测 + 品质勾选 + 确认出售), 第四种是另一条独立路径,
    # 见 _uses_collection_sell / _sell_on_settlement_screen。
    CONF_SELL_MODE = "出售藏品模式"
    SELL_MODE_OFF = "不出售"
    SELL_MODE_FULL = "满仓时清理"
    SELL_MODE_ONE_CLICK = "拍卖成功一键出售"
    SELL_MODE_INTERVAL = "按间隔出售"
    SELL_MODES = (
        SELL_MODE_OFF,
        SELL_MODE_FULL,
        SELL_MODE_INTERVAL,
        SELL_MODE_ONE_CLICK,
    )

    CONF_SELL_INTERVAL = "出售藏品间隔次数"
    CONF_KEEP_QUALITIES = "保留藏品品质"

    # 自动加价配置.
    CONF_AUTO_RAISE = "启用自动加价"
    CONF_RAISE_MODE = "加价方式"
    # 加价方式的取值既是下拉框标签, 又是持久化的配置值, 还被 _raise_price 当判定串用。
    # 改这几个取值必须配套迁移(见 _migrate_legacy_raise_mode), 否则老用户的下拉框会显示
    # 空白, 而且判定串失配后会静默落到「自定义」分支, 算出完全不同的价格。
    RAISE_MODE_MULTIPLE = "倍率"
    RAISE_MODE_CUSTOM = "自定义"
    RAISE_MODE_PERCENT = "百分比"
    RAISE_MODES = (RAISE_MODE_MULTIPLE, RAISE_MODE_CUSTOM, RAISE_MODE_PERCENT)
    CONF_RAISE_VALUE = "加价数值"
    CONF_RAISE_ROUND = "加价回合数"

    # 指定回合出价配置.
    CONF_SPECIAL_ROUND = "启用指定回合单独出价"
    CONF_SPECIAL_ROUNDS = "指定回合(可多选)"
    CONF_SPECIAL_ROUND_PRICE = "指定回合价格"

    # 出价模式.
    CONF_BID_MODE = "出价模式"
    BID_MODE_CUSTOM = "自定义价格"
    BID_MODE_LIST = "每轮指定价格"
    BID_MODE_ESTIMATE = "按系统估价"
    CONF_ESTIMATE_RATIO = "估价倍率"

    # 每轮指定价格: 一轮拍卖最多 6 回合出价, 每次出价各自一个价格.
    MAX_BID_ROUNDS = 6
    CONF_BID_PRICES = tuple(f"第{index}次出价价格" for index in range(1, MAX_BID_ROUNDS + 1))

    CONF_EXTRA_SELL_QUALITIES = "满仓或领低保后追加出售品质"

    # 拍卖辅助功能: 多选框, 勾选即启用.
    CONF_ASSIST_FEATURES = "启用辅助功能"
    ASSIST_EMOTE = "表情包"
    ASSIST_WELFARE = "低保金"
    ASSIST_FEATURES = (ASSIST_EMOTE, ASSIST_WELFARE)

    # 旧版配置键, 已合并进 CONF_SELL_MODE, 只在迁移时读取, 不再注册到 GUI.
    LEGACY_CONF_AUTO_CLEAR = "启用自动清理藏品"
    # 旧版辅助功能开关, 已合并进 CONF_ASSIST_FEATURES 的勾选项, 只在迁移时读取.
    LEGACY_CONF_USE_EMOTE = "启用表情包"
    LEGACY_CONF_USE_WELFARE = "启用低保金"
    # 旧版加价方式取值, 已改名为「倍率」, 只在迁移与读取兜底时使用.
    LEGACY_RAISE_MODE_MULTIPLE = "倍数"

    # --- UI 坐标 (相对比例) ---
    # 主界面按钮.
    BOX_MATCH = (0.7427, 0.8972, 0.8360, 0.9472)  # 开始匹配
    BOX_CONFIRM = (0.535, 0.633, 0.666, 0.681)  # 确认按钮
    BOX_BID = (0.882, 0.913, 0.930, 0.953)  # 出价按钮
    # 数字键盘弹窗的文字区。键盘弹窗整体覆盖约 0.30~0.90 / 0.44~0.80, 会盖住 BOX_BID,
    # 导致「是否已进入出价界面」判定永远为假。这个区域只取弹窗右侧的文字部分
    # (「请输入你愿意出的价格」等), 避开数字键, 用于键盘态下的界面判定。
    BOX_BID_KEYPAD = (0.578, 0.560, 0.800, 0.720)  # 键盘弹窗文字区
    BOX_SKIP_AREA = (0.703, 0.902, 0.807, 0.953)  # 跳过区域
    BOX_EXIT = (0.853, 0.900, 0.961, 0.949)  # 退出拍卖
    BOX_BID_CONFIRM = (0.649, 0.868, 0.726, 0.911)  # 确认出价

    # 出价面板.
    BOX_ABANDON = (0.7276, 0.9083, 0.7833, 0.9583)  # 放弃按钮
    BOX_ABANDON_CONFIRM = (0.5474, 0.6389, 0.6714, 0.6861)  # 放弃确认
    BOX_ASSET_VALUE = (0.8583, 0.0426, 0.9870, 0.0806)  # 出价面板资产
    # 出价面板当前估价.
    # 实测数字右端最靠右的一局是 0.9052 (price_result.png 的 "22,684"), 另一局 13,875 在
    # 0.9010; 再往右的「我的资产」数值右端在 0.9594。右边界放在 0.9200, 即在估价数字右端
    # 外留出约 0.015 (1080p 约 28px, 2160p 约 57px) 的余量, 同时与资产数值左端保持距离.
    #   - 不能 < 0.9052: 会把估价末位数字裁掉 (2026-09-23 的 "2,643 读成 ,643").
    #   - 不能 > 0.9594: 会把「我的资产」数值一起圈进来.
    # 缩到 0.9200 的意义: 即使「估价」标签这一帧没读出来, 区域里也只剩估价一个数字,
    # `_read_estimate_value` 按标签过滤失败时不会退化成「拼接整段数字」.
    # 数字的实际筛选仍以「估价」标签右边沿为准 (见 _read_estimate_value).
    BOX_ESTIMATE = (0.7780, 0.1330, 0.9200, 0.1820)  # 出价面板当前估价, 不覆盖我的资产

    BOX_LAST_BID = (0.473, 0.733, 0.546, 0.807)  # 上轮出价
    BOX_CLEAR = (0.488, 0.859, 0.533, 0.917)  # 清除按钮
    # 键盘未弹出时的「可输入范围0~N」提示区, 用于判断价格是否尚未输入.
    BOX_PRICE_RESULT = (0.588, 0.685, 0.783, 0.747)  # 输入价格结果
    # 键盘弹出后价格输入框移到键盘右侧, 上面那个矩形会落在提示文案「可输入范围0~N」上,
    # 导致校验永远读到提示文案而报「未输入」。这个区域是键盘态的输入框本体, 校验优先用它。
    BOX_PRICE_RESULT_KEYPAD = (0.588, 0.665, 0.790, 0.700)  # 键盘弹出后的输入价格结果
    BOX_EXCEPTION_AREA = (0.579, 0.641, 0.634, 0.681)  # 异常确认框

    # 主界面 / 结算.
    # 主界面左上角标题「即刻落槌」, 与藏品仓库标题同一个槽位(界面切换后文字才变),
    # 因此坐标与 BOX_WAREHOUSE_TITLE 一致.
    BOX_MAIN_TITLE = (0.058, 0.032, 0.130, 0.081)  # 主界面标题: 即刻落槌
    BOX_MAIN_ASSET = (0.670, 0.025, 0.830, 0.095)  # 主界面资产数值
    BOX_INSUFFICIENT = (0.240, 0.467, 0.747, 0.536)  # 库存不足提示

    # --- 掉线回场 (大世界 → 拍卖主界面) ---
    # 「即刻落槌」是「都市闲趣」里的一个玩法卡片, 入口路径固定:
    # 大世界按 F5 打开「都市大亨」面板 → 点「都市闲趣」→ 在 MENU 面板里找「即刻落槌」卡片。
    # 「都市闲趣」入口坐标走位置表 self.pos.panels.f5.hobbies (光环中心, 见 PanelPosition);
    # 下面是「都市闲趣」MENU 子面板内部的区域, 属任务私有, 不进位置表。
    POS_CITY_FUN_SCROLL = (0.500, 0.550)  # 子面板内容区中部, 滚动落点
    # 子面板标题「MENU 都市闲趣」。它和都市大亨面板上的「都市闲趣」入口同名, 但位置不重叠
    # (入口文字在 0.48~0.56/0.39~0.47), 用区域就能区分两个界面。
    BOX_CITY_FUN_TITLE = (0.10, 0.09, 0.42, 0.20)
    BOX_CITY_FUN_CARDS = (0.08, 0.22, 0.87, 0.88)  # 卡片区, 「即刻落槌」在最后一页
    BOX_CURRENT_VENUE = (0.630, 0.550, 0.800, 0.598)  # 主界面「当前：XXX场」

    # 低保金.
    BOX_WELFARE_BTN = (0.8266, 0.0398, 0.8984, 0.0778)
    BOX_WELFARE_DIALOG = (0.4400, 0.3050, 0.5650, 0.3620)  # 弹窗标题
    BOX_CLAIM = (0.576, 0.636, 0.632, 0.685)
    BOX_CANCEL = (0.370, 0.637, 0.421, 0.684)

    # 藏品仓库.
    BOX_WAREHOUSE_BTN = (0.2109, 0.8583, 0.2740, 0.9713)
    BOX_WAREHOUSE_TITLE = (0.058, 0.032, 0.130, 0.081)
    BOX_SELL = (0.931, 0.860, 0.949, 0.900)
    BOX_CONFIRM_SELL = (0.862, 0.863, 0.886, 0.917)
    BOX_BLANK = (0.442, 0.851, 0.564, 0.917)
    BOX_CLOSE = (0.950, 0.045, 0.963, 0.073)

    # 出售模式 (点击出售圆钮后才出现): 用「出售价值」条区分初始视图与出售模式,
    # 初始视图的同一位置是空网格, OCR 不会命中.
    BOX_SELL_LABEL = (0.673, 0.862, 0.722, 0.895)  # 「出售价值」标签
    # 出售价值条必须整条一起识别: 检测模型看不到孤立的小号数字, 只框数值区域读不到 "0",
    # 而 "0" 恰好是「一个品质都没勾上」的判据. 右边界停在圆钮之前, 避免图标被误读成数字.
    BOX_SELL_VALUE = (0.650, 0.830, 0.840, 0.950)  # 「出售价值」整条 (含标签)

    # 结算界面: 拍卖成功后的游戏自带「一键出售」圆钮, 以及它触发的「获得物品」提示条.
    # 实测(1920x1080, 用户 09-19 截图): 按钮组 px(1678,843)-(1758,920) ——
    # 圆钮 px(1682,843)-(1755,905), 文字「一键出售」px(1670,901)-(1758,929).
    # ⚠️ 框必须比文字本身宽松: 贴着文字裁时, 首字「一」是单笔画, 检测模型会直接丢掉它,
    # 只读出「键出售」, 正则随之失配. 实测同一张图放大 2 倍能读出、3/4 倍读不出 ——
    # 这是检测的抖动, 不能靠「试到一次成功」就收工, 必须留够余量.
    # 框中心 px(1717,875) 落在圆钮内(圆钮中心 px(1718,874)): 点圆钮比点文字稳.
    BOX_ONE_CLICK_SELL = (0.86198, 0.75463, 0.92708, 0.86574)
    # 「获得物品」提示条底部的「点击空白区域关闭」, 只用来确认提示条出现了.
    # 提示条本体实测 px(353,383)-(1574,685), 这句提示在 px(853,937)-(1079,977).
    BOX_POPUP_CLOSE_HINT = (0.44010, 0.79630, 0.56250, 0.91204)
    # 真正要点的「空白区域」: 提示条下沿 px685 与提示文字上沿 px937 之间都是纯背景,
    # 取 px(960,790). 不能直接点 OCR 命中的提示文字 —— 用户要求点的是空白区域,
    # 而且 _wait_operate_click 点的正是 OCR 命中的那个文字框.
    BOX_POPUP_BLANK = (0.47396, 0.69444, 0.52604, 0.76852)

    # 品质按钮 (白, 绿, 蓝, 紫, 橙, 红), 与 QUALITY_KEYS 一一对应.
    QUALITY_KEYS = ["品质白", "品质绿", "品质蓝", "品质紫", "品质橙", "品质红"]
    QUALITY_BOXES = (
        (0.682, 0.799, 0.687, 0.819),
        (0.730, 0.799, 0.735, 0.813),
        (0.779, 0.800, 0.788, 0.816),
        (0.829, 0.801, 0.838, 0.818),
        (0.877, 0.799, 0.886, 0.816),
        (0.927, 0.799, 0.936, 0.819),
    )

    # 品质圆点每点击一次界面会重绘, 间隔太短时后续点击会落空;
    # 勾选后读出售价值校验, 读到 0 或读不出时换帧重读, 最多尝试 SELL_SELECT_RETRIES 次.
    # 不能靠重新勾选来重试: 勾选是无条件点击, 再点一次会把刚勾上的品质全部点掉.
    SELL_QUALITY_GAP = 0.5
    SELL_SELECT_RETRIES = 2

    # 「出售价值」在品质圆点刚点完时会短暂变成空白(界面重绘), 只给 1 秒经常读空;
    # 读不出时返回 None, 调用方必须按「未确认」处理, 不能当成出售成功.
    SELL_VALUE_TIMEOUT = 3

    # 提示类弹窗(入场费确认 / 异常出价 / 满仓提示)共用一套模板: 标题「提示」在
    # 屏幕中部, 确认与取消按钮在底部同一组坐标. 因此一个区域就能兜住这一类弹窗.
    # 单帧 ocr 会漏掉刚出现的弹窗, 必须带短超时轮询.
    NOTICE_POPUP_TIMEOUT = 2
    # 每次出价都要查一遍弹窗, 预算调小: 漏掉一次弹窗要赔上整轮, 但不该固定白等 2 秒.
    BID_NOTICE_POPUP_TIMEOUT = 1.0

    # 库存不足提示条出现时机不定, 给 1 秒容易漏掉(漏掉就不会提前清理, 满仓会卡住).
    INVENTORY_FULL_TIMEOUT = 3

    # 出售连续失败到这个次数后放宽「保留品质」再试一次: 满仓卖不掉会让后续出价全部失败,
    # 这时候把仓库腾空的优先级高于保留指定品质.
    SELL_FAILURE_ESCALATE_AFTER = 2

    # 轮次末尾出售流程的总预算。出售是收尾动作, 不该像拍卖阶段那样吃掉整轮 600 秒:
    # 逐分支等待下限约 35 秒, 连续失败放宽再走一趟约 71 秒, 留一倍余量。
    SELL_TIMEOUT = 90

    # 关闭藏品仓库的重试次数。批量关闭失败会把「出售模式 + 已勾选品质」留给下一轮,
    # 下次进来会无条件再点一遍同一批品质(全部取反), 必须确认真的关掉了。
    WAREHOUSE_CLOSE_RETRIES = 3
    # 藏品仓库入口与界面标题的等待上限。两者是同一段 UI 就绪过程(点入口 → 界面加载),
    # 用同一个上限, 免得调一处漏一处。
    WAREHOUSE_LOAD_TIMEOUT = 10

    # 结算界面的「一键出售」: 跳过动画刚点完, 按钮本来就该在, 给短超时即可.
    ONE_CLICK_SELL_TIMEOUT = 3
    # 点完一键出售要等服务端返回才弹出「获得物品」提示条, 给足时间.
    POPUP_CLOSE_TIMEOUT = 5

    # 数字键盘映射.
    PAD_MAP = {
        "0": (0.223, 0.862, 0.256, 0.924),
        "1": (0.224, 0.510, 0.256, 0.571),
        "2": (0.308, 0.505, 0.344, 0.573),
        "3": (0.394, 0.506, 0.433, 0.568),
        "4": (0.232, 0.629, 0.254, 0.683),
        "5": (0.310, 0.629, 0.343, 0.687),
        "6": (0.399, 0.626, 0.432, 0.690),
        "7": (0.226, 0.744, 0.252, 0.812),
        "8": (0.313, 0.743, 0.346, 0.812),
        "9": (0.401, 0.747, 0.434, 0.805),
        "00": (0.304, 0.853, 0.356, 0.935),
        "0000": (0.383, 0.855, 0.449, 0.932),
    }

    # 表情包点击坐标 (相对坐标, 非 Box).
    EMOTE_BTN = (0.036, 0.910)
    EMOTE_FIRST = (0.164, 0.516)

    # 指定回合下拉框候选项.
    SPECIAL_ROUND_OPTIONS = [str(index) for index in range(1, 7)]

    # --- 阶段超时 (秒) ---
    # 单轮拍卖的硬上限, 防止各阶段局部超时叠加后长期卡住任务.
    ROUND_TIMEOUT = 600
    MATCH_TIMEOUT = 120
    MATCH_CLICK_TIMEOUT = 30
    # 点击成功后界面切换只要 0.6 秒左右(见历史运行日志), 所以点击后短时间内按钮仍在原位
    # 就说明这次点击没有生效, 立刻重试比白等 MATCH_CLICK_TIMEOUT 划算得多.
    MATCH_PROBE_TIMEOUT = 3
    # 等待「确认出价」后的出价界面加载完成。与 BID_RESULT_TIMEOUT 数值相同但语义不同:
    # 那个是等「一次出价的结果」(见 _stage_bid_loop), 这个是等界面渲染出来, 不要合并。
    BID_SCREEN_TIMEOUT = 60
    # 等一次出价的结果: 在这段时间内看界面是变成「已结束」还是「被加价」。
    BID_RESULT_TIMEOUT = 60
    # 结算阶段实测最长 3.9 秒(207 次样本), 这个上限从未触发过, 保留用于界面卡死时兜底;
    # 注意 RESULT_MAX_LOOPS(180) x POLL_INTERVAL(0.5) 恰好也是 90 秒, 两个条件同时到点.
    RESULT_TIMEOUT = 90
    # 结算画面存在跳过动画已出现而退出按钮尚未渲染的中间态, 等待不能太短.
    EXIT_BUTTON_TIMEOUT = 10
    ASSET_OCR_TIMEOUT = 15
    # 主界面资产观测的单次超时。观测每轮都要做, 给太长会拖累单轮总预算。
    ASSET_OBSERVE_TIMEOUT = 5
    # 出价面板的当前估价在界面刚出现时会跳动几次, 第一次识别到的不是最终值;
    # 连续读到相同值才采用, 最多等 ESTIMATE_STABLE_TIMEOUT 秒.
    ESTIMATE_STABLE_READS = 3
    # 数字滚动是逐位就位的, 低位先出现、高位后到, 所以「读到相同值」只说明这一帧没变化,
    # 不代表滚动结束。线上日志: 19:16 那局的中间值 35,365 一直持续到面板打开后 2.63 秒
    # 才变成真值 37,979; 19:18 那局恰好在 2.63 秒采信了 197(同局真实估价数万)。
    # 因此从第一次读到有效数值起, 至少观察这么久才允许采用。
    ESTIMATE_MIN_OBSERVE_SECONDS = 4.0
    ESTIMATE_STABLE_TIMEOUT = 10
    # 估价数字右端距裁框右边界, 小于「屏幕宽度的这个比例」时认为末位可能已被裁掉。
    # 2026-09-23 的故障就是这个形态: 末位 "3" 只剩 4px 宽的一条竖边, OCR 直接丢弃,
    # 2,643 读成 ,643 / 1,912 读成 912。裁框宽度不是安全保证, 所以要在运行时盯住它。
    # 取 8/1920: 该故障在 1080p 下实测的临界宽度, 按分辨率等比换算, 避免 1440p/2160p
    # 下 UI 与文字同步放大而阈值不变导致的漏判 (AGENTS.md: 坐标用相对比例, 不硬编码像素)。
    ESTIMATE_EDGE_MARGIN_RATIO = 8 / 1920

    # --- 轮询与重试 ---
    POLL_INTERVAL = 0.5
    # 基类睡眠钩子的触发间隔, 用于在长流程中处理月卡弹窗等外部打断.
    SLEEP_CHECK_INTERVAL = 0.5
    # 循环次数上限与同名阶段的 *_TIMEOUT 是互补的两重保险, 不是重复:
    # 循环体每轮至少 sleep(POLL_INTERVAL), 但命中「开始匹配」时还要走一次点击探测
    # (额外 3~30 秒), 单轮耗时并不固定 —— 所以「次数先到」和「时间先到」都会发生.
    # 实测空转到超时的样本(09-14/09-19/09-20, n=261): 62~66 秒(次数先到) 与
    # 120.4/120.7 秒(时间先到) 两种都存在, 两个上限各自生效过, 不要删任何一个.
    MATCH_MAX_LOOPS = 120
    RESULT_MAX_LOOPS = 180
    BID_MAX_RETRIES = 3

    # 资产低于该值时领取低保金.
    WELFARE_ASSET_THRESHOLD = 100000

    # 低保金弹窗关闭重试次数, 每日次数用尽时弹窗没有领取按钮, 只能靠取消关闭.
    WELFARE_CLOSE_RETRIES = 3

    # --- 掉线回场 (秒/次) ---
    # 网络不稳时匹配阶段会被踢回大世界, 界面状态全不命中, 只能空转到 MATCH_TIMEOUT。
    # 回场是一次性的异常路径: 失败就按本轮失败处理, 交给下一轮重试。
    RECOVER_TIMEOUT = 90  # 单次回场总预算
    RECOVER_STEP_TIMEOUT = 12  # 回场各步骤的等待上限
    RECOVER_SCROLL_STEPS = 4  # 「都市闲趣」面板最多滚动几次去找「即刻落槌」
    RECOVER_SCROLL_WHEEL = -8  # 每次滚动的滚轮格数
    # 单轮回场次数上限, 由 _exec_auction_round 写进 self._recover_quota 并扣减。
    # 挂在轮次而不是调用参数上的原因见 _exec_auction_round: 参数会在「确认失败后重跑
    # _stage_match」的路径上被默认值恢复, 使同一轮可以反复回场, 每次都重走一遍面板动画
    # 把整轮 deadline 耗光, 并且让「本轮只回场一次」这个约定形同虚设。
    RECOVER_MAX_PER_ROUND = 1
    # 启动时的入口回场(见 _ensure_auction_entry): 探测主界面标题的等待上限,
    # 以及一次性回场预算。预算与 RECOVER_TIMEOUT 一致, 两者走的是同一条路径。
    ENTRY_PROBE_TIMEOUT = 3
    ENTRY_RECOVER_TIMEOUT = 90
    # 匹配阶段每 N 次轮询探一次大世界(约 2 秒): 判定要跑一次旋转模板匹配 + 一次血条模板
    # 匹配, 比 OCR 贵, 不能每 0.5 秒调一次; 探测只在点击「开始匹配」之后、界面迟迟不变化时
    # 才开始.
    WORLD_PROBE_INTERVAL = 4

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.supported_languages = ["zh_CN"]
        self.name = "自动拍卖(目前仅支持简中)"
        self.description = "可从大世界或拍卖主界面直接启动, 会自动进入拍卖界面; 请先阅读「说明」"
        self.group_name = "都市闲趣"
        # 任务卡上的「说明」按钮只在 instructions 非空时出现, 内容是富文本 HTML.
        self.instructions = INST
        self.add_rounds_config()
        # 本轮回场配额, 由 _exec_auction_round 每轮重置。这里先给初值, 使任务实例在任何
        # 入口(包括测试直接调 _stage_match)下都有确定行为 —— 缺省视为「本轮回场机会已用完」,
        # 不会因为没有轮次上下文而无限回场。
        self._recover_quota = 0

        self.default_config.update(
            {
                # 出售藏品相关配置集中放在最前面, 由模式下拉框统一控制可见性,
                # 避免「出售间隔 / 保留品质 / 自动清理」散落在面板各处.
                self.CONF_SELL_MODE: self.SELL_MODE_ONE_CLICK,
                self.CONF_SELL_INTERVAL: 0,
                self.CONF_KEEP_QUALITIES: ["品质红"],
                self.CONF_EXTRA_SELL_QUALITIES: [],
                self.CONF_AUTO_RAISE: False,
                self.CONF_FIXED_PRICE: 1,
                self.CONF_BID_MODE: self.BID_MODE_ESTIMATE,
                self.CONF_ESTIMATE_RATIO: "1",
                # 每轮指定价格: 6 次出价各自一个价格, 0 表示沿用上一次的价格.
                **dict.fromkeys(self.CONF_BID_PRICES, 0),
                self.CONF_RAISE_MODE: self.RAISE_MODE_MULTIPLE,
                self.CONF_RAISE_VALUE: "1.6",
                self.CONF_RAISE_ROUND: 2,
                self.CONF_SPECIAL_ROUND: False,
                self.CONF_SPECIAL_ROUNDS: ["5"],
                self.CONF_SPECIAL_ROUND_PRICE: "66666",
                self.CONF_ASSIST_FEATURES: [self.ASSIST_WELFARE],
            }
        )

        # 定义下拉框和条件子配置的控件类型.
        self.config_type = {
            # 按出价模式只展示该模式真正会用到的价格配置.
            self.CONF_BID_MODE: {
                "options": [self.BID_MODE_CUSTOM, self.BID_MODE_LIST, self.BID_MODE_ESTIMATE],
                "sub_configs": {
                    self.BID_MODE_CUSTOM: [
                        self.CONF_FIXED_PRICE,
                        self.CONF_AUTO_RAISE,
                        self.CONF_RAISE_MODE,
                        self.CONF_RAISE_ROUND,
                        self.CONF_SPECIAL_ROUND,
                    ],
                    self.BID_MODE_LIST: list(self.CONF_BID_PRICES),
                    self.BID_MODE_ESTIMATE: [self.CONF_ESTIMATE_RATIO],
                },
            },
            self.CONF_RAISE_MODE: {
                "options": list(self.RAISE_MODES),
                "sub_configs": {
                    self.RAISE_MODE_MULTIPLE: [self.CONF_RAISE_VALUE],
                    self.RAISE_MODE_CUSTOM: [self.CONF_RAISE_VALUE],
                    self.RAISE_MODE_PERCENT: [self.CONF_RAISE_VALUE],
                },
            },
            self.CONF_KEEP_QUALITIES: {
                "type": "multi_selection",
                "options": list(self.QUALITY_KEYS),
            },
            self.CONF_EXTRA_SELL_QUALITIES: {
                "type": "multi_selection",
                "options": list(self.QUALITY_KEYS),
            },
            self.CONF_ASSIST_FEATURES: {
                "type": "multi_selection",
                "options": list(self.ASSIST_FEATURES),
            },
            # 出售模式把「出售间隔 / 保留品质 / 追加出售品质」收在同一处:
            # 选「不出售」时这些子项全部隐藏, 面板只剩一个下拉框.
            self.CONF_SELL_MODE: {
                "options": list(self.SELL_MODES),
                "sub_configs": {
                    self.SELL_MODE_OFF: [],
                    self.SELL_MODE_FULL: [
                        self.CONF_KEEP_QUALITIES,
                        self.CONF_EXTRA_SELL_QUALITIES,
                    ],
                    self.SELL_MODE_INTERVAL: [
                        self.CONF_SELL_INTERVAL,
                        self.CONF_KEEP_QUALITIES,
                        self.CONF_EXTRA_SELL_QUALITIES,
                    ],
                    # 一键出售用游戏自带的整包出售, 没有品质勾选也没有间隔, 因此没有子项.
                    self.SELL_MODE_ONE_CLICK: [],
                },
            },
            # 仅在开关启用时显示指定回合配置.
            self.CONF_SPECIAL_ROUND: {
                "sub_configs": {
                    True: [self.CONF_SPECIAL_ROUNDS, self.CONF_SPECIAL_ROUND_PRICE],
                }
            },
            self.CONF_SPECIAL_ROUNDS: {
                "type": "multi_selection",
                "options": list(self.SPECIAL_ROUND_OPTIONS),
            },
        }

        # 描述按 default_config 的顺序排列, 与面板上的控件顺序一致, 便于对照维护.
        # 每条只写「标签本身看不出来的信息」: 做什么, 硬约束, 以及读不到时的回退行为.
        self.config_description.update(
            {
                # --- 藏品出售 ---
                self.CONF_SELL_MODE: "满仓后无法继续出价, 建议至少选「满仓时清理」; 选「不出售」"
                "则完全不碰仓库; 选「拍卖成功一键出售」用游戏自带的一键出售在结算界面直接"
                "卖掉本局藏品, 不做满仓检测也不筛选品质",
                self.CONF_SELL_INTERVAL: "每 N 轮出售一次, 满仓时提前触发; 填 0 会退化成只按"
                "满仓清理",
                self.CONF_KEEP_QUALITIES: "勾选的品质不出售, 其余品质全部出售; 一个都不勾会卖掉"
                "全部藏品",
                self.CONF_EXTRA_SELL_QUALITIES: "满仓或成功领取低保金时, 这些品质会覆盖"
                "「保留藏品品质」一并出售, 用于腾出仓位; 只对保留列表里勾选的品质有效, "
                "勾其他品质不会改变行为",
                # --- 出价 ---
                self.CONF_AUTO_RAISE: "在基础价之上按「加价方式」逐次提高出价",
                self.CONF_FIXED_PRICE: "自定义价格的基准价; 必须为正整数, 否则任务不会启动",
                self.CONF_BID_MODE: "自定义价格按基础价与加价方式定价, 每轮指定价格按出价序号"
                "逐个取值, 按系统估价读界面右上角估价并乘倍率",
                self.CONF_ESTIMATE_RATIO: "出价 = 当前估价 x 倍率, 需为正数; 估价读不出时回退到"
                "「基础价」",
                self.CONF_RAISE_MODE: "倍率: 基础价x倍率^次数; 百分比: 基础价x(1+百分比/100x次数); "
                "自定义: 基础价+数值x次数",
                self.CONF_RAISE_VALUE: "三种加价方式共用, 含义随方式变化(倍率 / 百分比 / 每次"
                "增加额), 支持小数",
                self.CONF_RAISE_ROUND: "第几次出价开始加价; 0 表示第 1 次就按加价算, N 表示第 N 次"
                "起才开始加价",
                self.CONF_SPECIAL_ROUND: "勾选的回合直接使用「指定回合价格」, 跳过基础价与加价计算",
                self.CONF_SPECIAL_ROUNDS: "这些序号的出价使用单独价格, 序号从 1 开始",
                self.CONF_SPECIAL_ROUND_PRICE: "指定回合使用的价格, 需为正整数; 留空或填 0 时该"
                "功能不生效",
                # --- 辅助功能 ---
                self.CONF_ASSIST_FEATURES: "勾选即启用; 表情包: 每次出价成功后发送表情菜单里的"
                "第一个表情; 低保金: 主界面资产低于 10 万时自动领取",
            }
        )

        # 每轮指定价格: 6 个价格依次对应第 1~6 次出价.
        for index, key in enumerate(self.CONF_BID_PRICES, start=1):
            if index == 1:
                description = "第 1 次出价的价格, 必须大于 0, 否则任务不会启动"
            elif index == self.MAX_BID_ROUNDS:
                description = (
                    f"第 {index} 次出价的价格, 必须显式填写且高于第 {index - 1} 次,"
                    " 否则会被系统拒绝"
                )
            else:
                description = f"第 {index} 次出价的价格; 留 0 则沿用上一次的价格"
            self.config_description[key] = description

        self.last_bid_price = None
        self.current_bid_count = 0
        self._post_round_state = PostRoundState()
        # 藏品出售连续失败计数: 满仓卖不掉时后续出价必然失败, 需要升级处理而不是每轮重试.
        self._sell_failures = 0
        self._inventory_stuck = False
        # 启用基类的睡眠钩子, 拍卖流程跨过每日 5 点时靠它处理月卡弹窗.
        self.sleep_check_interval = self.SLEEP_CHECK_INTERVAL
        self.add_exit_after_config()

    # --- 配置迁移 ---
    @staticmethod
    def _migrate_sell_mode(raw: dict) -> str:
        """把旧版「启用自动清理藏品 / 出售藏品间隔次数」换算成新的出售模式。

        旧版两档是互斥的: 自动清理开启时忽略间隔, 只按满仓触发; 关闭时按间隔触发,
        满仓会提前触发。因此 自动清理=True -> 满仓时清理, 间隔>0 -> 按间隔出售。
        """
        if not isinstance(raw, dict):
            return AutoBidAuctionTask.SELL_MODE_OFF
        if raw.get(AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR) is True:
            return AutoBidAuctionTask.SELL_MODE_FULL
        interval = raw.get(AutoBidAuctionTask.CONF_SELL_INTERVAL)
        # bool 是 int 的子类, True 不该被当成间隔 1.
        if isinstance(interval, int) and not isinstance(interval, bool) and interval > 0:
            return AutoBidAuctionTask.SELL_MODE_INTERVAL
        return AutoBidAuctionTask.SELL_MODE_OFF

    def load_config(self):
        """加载配置前先迁移旧版取值, 否则老用户升级后会静默改变行为。

        出售模式迁移必须发生在 super().load_config() 之前: Config 会按 default_config
        补齐缺失的键并落盘, 写进 default_config 的值就是最终写回用户文件的值。
        辅助功能迁移同理 —— 新键是列表型多选框, 旧键会被同一次 verify 清掉。
        加价方式迁移相反 —— 键还在、只是取值变了, Config 不会覆盖已存在的键, 所以只能
        在 self.config 建好之后再改写, 靠 Config.__setitem__ 自己落盘。
        """
        raw = self._read_raw_config()
        self._migrate_legacy_sell_config(raw)
        self._migrate_legacy_assist_config(raw)
        super().load_config()
        self._migrate_legacy_raise_mode(raw)

    def _read_raw_config(self) -> dict:
        """读取落盘的用户配置原文, 文件不存在或格式不对时返回空字典。"""
        config_file = get_relative_path(Config.config_folder, f"{type(self).__name__}.json")
        raw = read_json_file(config_file)
        return raw if isinstance(raw, dict) else {}

    def _migrate_legacy_sell_config(self, raw: dict) -> None:
        """配置里还没有新模式键时, 用旧版的两个键推导出模式并写进默认值。

        写进 default_config 后 verify_config 会用该值补齐配置并落盘, 所以迁移只发生
        一次; 旧键不在 default_config 里, 会在同一次 verify 中被清掉。
        """
        if self.CONF_SELL_MODE not in self.default_config:
            return
        if self.CONF_SELL_MODE in raw:
            return
        if self.CONF_SELL_INTERVAL not in raw and self.LEGACY_CONF_AUTO_CLEAR not in raw:
            # 全新用户, 没有需要迁移的旧值.
            return
        mode = self._migrate_sell_mode(raw)
        self.default_config[self.CONF_SELL_MODE] = mode
        self.log_info(f"已按旧版出售开关迁移「{self.CONF_SELL_MODE}」为: {mode}")

    @staticmethod
    def _migrate_assist_features(raw: dict) -> list:
        """把旧版两个辅助开关换算成多选框的勾选项列表。

        只有显式 True 才算勾选: 旧键缺失、False, 以及用户手工填的字符串都按未勾选处理。
        """
        if not isinstance(raw, dict):
            return []
        pairs = (
            (AutoBidAuctionTask.ASSIST_EMOTE, AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE),
            (AutoBidAuctionTask.ASSIST_WELFARE, AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE),
        )
        return [feature for feature, key in pairs if raw.get(key) is True]

    def _migrate_legacy_assist_config(self, raw: dict) -> None:
        """配置里还没有多选框键时, 用旧版两个开关推导出勾选项并写进默认值。

        旧键「启用表情包 / 启用低保金」不在 default_config 里, 会在同一次 verify 中
        被清掉, 所以迁移只发生一次。
        """
        if self.CONF_ASSIST_FEATURES not in self.default_config:
            return
        if self.CONF_ASSIST_FEATURES in raw:
            return
        if self.LEGACY_CONF_USE_EMOTE not in raw and self.LEGACY_CONF_USE_WELFARE not in raw:
            # 全新用户, 没有需要迁移的旧值.
            return
        features = self._migrate_assist_features(raw)
        self.default_config[self.CONF_ASSIST_FEATURES] = features
        self.log_info(
            f"已按旧版辅助开关迁移「{self.CONF_ASSIST_FEATURES}」为: "
            f"{', '.join(features) if features else '未勾选'}"
        )

    def _migrate_legacy_raise_mode(self, raw: dict) -> None:
        """把「加价方式」的旧取值「倍数」改写为「倍率」。

        取值同时是下拉框选项和价格计算的判定串: 不改写会让下拉框显示空白, 而且
        _raise_price 里的判定失配后会静默落到「自定义」分支, 价格从指数增长变成线性
        增长, 不报错也不告警。迁移后旧取值不再出现, 重复执行是无操作。
        """
        if self.CONF_RAISE_MODE not in self.default_config:
            return
        if raw.get(self.CONF_RAISE_MODE) != self.LEGACY_RAISE_MODE_MULTIPLE:
            return
        # __setitem__ 在取值真的变化时会自己 save_file().
        self.config[self.CONF_RAISE_MODE] = self.RAISE_MODE_MULTIPLE
        self.log_info(
            f"已迁移「{self.CONF_RAISE_MODE}」的旧值 {self.LEGACY_RAISE_MODE_MULTIPLE}"
            f" 为 {self.RAISE_MODE_MULTIPLE}"
        )

    # --- 任务入口 ---
    def run(self):
        """任务入口, 确保游戏窗口捕获和连接已就绪。"""
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("自动拍卖任务执行异常", e)
            raise

    def do_run(self):
        """主执行逻辑, 使用基类的轮次管理框架。"""
        self.start_rounds()
        try:
            # 入场校验放在 try 内: 配置非法时也要走到 finally 的 finish_rounds,
            # 否则任务抛错退出后连轮次汇总日志和结束通知都不会发出。
            self._validate_price_config()
            self._warn_if_no_sellable_quality()
            self._warn_if_extra_sell_is_redundant()
            boxes = self._build_boxes()
            while self.has_remaining_rounds():
                if not self.begin_round():
                    break
                try:
                    # 每轮都重新确认一次入口, 与 AutoHeistTask._run_loop「每轮先
                    # ensure_main + 入口判断」保持一致: 上一轮掉线或异常退出时人可能已经
                    # 不在拍卖界面, 只在循环外确认一次的话, 后续每轮都要在 _stage_match
                    # 里空转到 MATCH_TIMEOUT(120 秒) 才由末尾的大世界兜底触发回场。
                    self._ensure_auction_entry(boxes)
                    self._run_single_round(boxes)
                    if self._is_warehouse_open(boxes):
                        # 关窗失败时 _close_warehouse 只告警, 整轮照样「正常返回」。此时界面
                        # 还压在藏品仓库上, 下一轮所有拍卖控件的等待只能空转, 每轮都把
                        # ROUND_TIMEOUT 烧光, 日志里看起来却只是「每轮都失败」。这里直接收尾:
                        # 关不掉的仓库不需要另做状态标记 —— _try_sell_collections 只记录出售
                        # 结果, 被中断的「满仓清理」不会把它算成失败, 于是
                        # _inventory_stuck 仍停在 False, 下次启动会照常走完整流程重试,
                        # 不会因为这次中断而跳过出价。
                        self.log_error("藏品仓库未关闭且无法自动收起, 停止后续轮次")
                        break
                except TaskDisabledException:
                    raise
                except Exception as e:
                    self.add_failed("拍卖执行异常")
                    self.log_error(f"本轮拍卖失败: {type(e).__name__}: {e}")
                    # 界面被弹窗挡住才会整轮什么都识别不到, 失败后按弹窗特征兜底一次.
                    self._recover_blocking_popup(boxes)
                    self.sleep(2)
        finally:
            self.finish_rounds()

    # --- 外部打断 ---
    def sleep_check(self):
        """睡眠钩子: 处理月卡弹窗等外部打断, 与其它任务保持同一套机制。

        拍卖流程可能跨过每日 5 点的刷新时刻, 此时月卡弹窗会盖住拍卖界面,
        所有阶段状态判定都会失败, 轮询只能一直空转到单轮超时。
        基类在每次 sleep 时按 sleep_check_interval 调用本方法(调用前会刷新帧),
        因此轮询循环、出价中间步骤和结算后处理都被覆盖。
        check_monthly_card 只在 5 点前后 2 分钟的时间窗内生效, 窗口外只是一次时间比较。
        """
        super().sleep_check()
        if self.check_monthly_card():
            self.log_info("检测到月卡弹窗, 关闭后继续当前轮次")
            self.handle_monthly_card()

    def _recover_blocking_popup(self, boxes: AuctionBoxes | None = None) -> None:
        """整轮失败后的兜底: 按界面特征处理卡住的弹窗。

        `check_monthly_card` 只在 5 点前后 2 分钟的时间窗内生效, 任务在窗口之后
        才启动(或本机时钟与游戏刷新时刻不一致)时, 弹窗不会被时间窗命中, 每轮都会
        空转到超时。这里只在已经失败的情况下多花一次模板匹配, 正常路径没有额外开销。

        低保金弹窗同理: 领取流程异常时弹窗会留在界面上, 之后每轮都会识别不到拍卖界面。
        入场费确认/异常出价/满仓提示等弹窗共用一套模板, 也在这里兜一次 —— 整轮失败后
        才执行, 正常路径没有额外开销。
        """
        try:
            if self.find_monthly_card() is not None:
                self.log_info("本轮失败且检测到月卡弹窗, 关闭弹窗后重试")
                self.handle_monthly_card()
                return

            if boxes is not None and self._is_welfare_dialog_open(boxes):
                self.log_warning("本轮失败且检测到低保金弹窗未关闭, 尝试关闭")
                self._close_welfare_dialog(boxes, None)
                return

            if boxes is not None:
                self._dismiss_notice_popup(boxes, None, "本轮失败后")
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_warning(f"弹窗兜底处理失败: {type(e).__name__}: {e}")

    # --- 掉线回场 (大世界 → 拍卖主界面) ---
    def _is_world_screen(self) -> bool:
        """是否被踢回大世界, 复用基类的 in_team_and_world()。

        不能只用 in_world() 判: 小地图箭头走的是 chamfer 打分
        (`0.7*coverage + 0.3*(1 - avg_distance/max_distance)`), 没有「场景饱和」惩罚 ——
        搜索区整片偏亮时每个模板像素的最近亮像素距离都是 0, coverage 与 distance_score
        双双为 1, 纯白画面直接得满分。实测「都市大亨」面板得 1.000、「仪器组合」面板得
        0.841, 都越过 0.75 的阈值, 与真箭头(0.997)分不开; 匹配中的亮色加载帧同理。
        误判的代价很实在: 匹配阶段会被当成掉线, 白白耗掉本轮唯一的回场配额。

        加上 is_in_team() 的组队血条判定就能分开: 真大世界命中(0.939), 都市大亨/仪器组合/
        竞拍结束/黑屏全不命中。这也正是框架自己的定义(见 is_main(in_world=True)), 而
        _return_to_auction 第一步的 ensure_main(in_world=True) 本来就要求 is_in_team() ——
        两边保持一致, 才不会出现「判定在大世界, 但 ensure_main 认为不在」。
        """
        try:
            return bool(self.in_team_and_world())
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_debug(f"大世界判定失败: {type(e).__name__}: {e}")
            return False

    def _resume_after_world_drop(self, boxes: AuctionBoxes, deadline: float) -> AuctionState:
        """掉线后的统一出口: 本轮回场配额还有就回场并重跑匹配阶段, 否则按本轮失败结束。

        配额由 `_exec_auction_round` 创建(见 `_recover_quota`), 不通过参数逐层传递 ——
        参数会在「确认失败后重新调 `_stage_match`」这条路径上被默认值重置, 使同一轮能反复
        回场。这里扣减配额, 扣完就抛「本轮放弃」。
        """
        if self._recover_quota <= 0:
            raise WaitFailedException("被踢回大世界后再次掉线, 本轮放弃")
        self._recover_quota -= 1
        return self._recover_from_world(boxes, deadline)

    def _recover_from_world(self, boxes: AuctionBoxes, deadline: float) -> AuctionState:
        """掉线回场: 大世界 → 拍卖主界面, 成功后重新进入匹配阶段。

        网络不稳时点「开始匹配」后会被踢回大世界, 此时拍卖界面的四种状态判定全不命中,
        原逻辑只能空转到 MATCH_TIMEOUT(120 秒) 再按本轮失败处理, 每轮白等两分钟,
        轮次很快就被耗尽。回场成功后重跑 _stage_match, 让本轮接着走完。

        只回场 `RECOVER_MAX_PER_ROUND` 次: 配额在 `_resume_after_world_drop` 里扣减,
        配额用完再掉线就按本轮失败结束 —— 继续重试只会把整轮 deadline 耗光, 留给下一轮
        更合适(轮次之间本来就有间隔)。
        """
        self.log_warning("检测到被踢回大世界, 尝试自动回到拍卖界面")
        self.info_set("当前阶段", "回场中")
        recover_deadline = min(deadline, time.monotonic() + self.RECOVER_TIMEOUT)
        if not self._return_to_auction(boxes, recover_deadline):
            raise WaitFailedException("被踢回大世界后未能回到拍卖界面")

        venue = self._read_current_venue()
        self.log_info(f"已回到拍卖主界面, 当前会场: {venue or '未识别'}")
        self.info_set("当前阶段", "匹配中")
        return self._stage_match(boxes, deadline)

    def _return_to_auction(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """按「大世界 → F5 都市大亨 → 都市闲趣 → 即刻落槌」的顺序打开拍卖界面。

        整条路径重试一次: 网络抖动时 F5 或入口点击都可能落空, 重试一次比直接判失败划算,
        但不再多试 —— 每次失败都要重新走一遍面板动画, 会把整轮 deadline 耗光。
        注意 retry_on_action 的 attempt 是「额外重试次数」, 实际执行 attempt + 1 次,
        所以这里传 1 才是「总共两次」。

        掉线时不会有「网络异常」之类的提示弹窗(已确认), 所以不在这里兜弹窗;
        真要是有弹窗, ensure_main 里的月卡/登录处理也覆盖不到, 由整轮失败后的
        _recover_blocking_popup 兜底。

        每一步都按「剩余预算」而不是各处写死的常量取超时: ensure_main 在登录态丢失时
        会把 time_out 抬到 600 秒(见 BaseNTETask.ensure_main), 而 RECOVER_TIMEOUT 只有
        90 秒 —— 不把剩余时间传进去, 这一步就能把整个回场预算连同本轮 deadline 一起耗光,
        后面的 F5/入口/落槌根本轮不到执行. 剩余时间耗尽时直接判失败, 交给下一轮重来.
        """

        def action():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.log_warning("回场预算已耗尽, 放弃本次回场")
                return False
            try:
                self.ensure_main(in_world=True, time_out=remaining)
                self.openF5panel()
            except TaskDisabledException:
                raise
            except Exception as e:
                self.log_warning(f"打开都市大亨面板失败: {type(e).__name__}: {e}")
                return False

            self.operate_click(*self.pos.panels.f5.hobbies)
            if not self.wait_ocr(
                box=self.box_of_screen(*self.BOX_CITY_FUN_TITLE),
                match=RE_CITY_FUN,
                time_out=self._timeout_or_zero(deadline, self.RECOVER_STEP_TIMEOUT),
                raise_if_not_found=False,
                settle_time=0.5,
            ):
                self.log_warning("未检测到「都市闲趣」面板")
                return False
            if not self._click_instant_lot(deadline):
                return False
            return bool(
                self.wait_ocr(
                    box=boxes.main_title,
                    match=RE_MAIN_TITLE,
                    time_out=self._timeout_or_zero(deadline, self.RECOVER_STEP_TIMEOUT),
                    raise_if_not_found=False,
                    settle_time=0.5,
                )
            )

        def reset():
            """重试之间的状态复位, 同样受剩余预算约束。

            原来是直接传 self.ensure_main, 即用默认 time_out(登录态丢失时被抬到 600 秒),
            与 action 是同一个漏洞 —— 第二次重试前的复位就能把预算吃干净.
            """
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.ensure_main(in_world=True, time_out=remaining)

        try:
            return bool(self.retry_on_action(action, reset, attempt=1))
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_warning(f"回场流程异常: {type(e).__name__}: {e}")
            return False

    def _click_instant_lot(self, deadline: float) -> bool:
        """在「都市闲趣」面板里找「即刻落槌」卡片并点击。

        「即刻落槌」在面板最后一页, 刚打开时看不到, 所以边滚边找。
        命中后直接点 OCR 框中心 —— 卡片是「上图下标题」, 标题本身就在卡片的点击热区内。
        复用 RE_MAIN_TITLE 是因为卡片名与拍卖主界面标题是同一个词「即刻落槌」。
        """
        cards = self.box_of_screen(*self.BOX_CITY_FUN_CARDS)
        for _ in range(self.RECOVER_SCROLL_STEPS):
            if self._wait_operate_click(
                cards,
                RE_MAIN_TITLE,
                self._timeout_or_zero(deadline, 3),
                after_sleep=1,
            ):
                return True
            self.scroll(*self.POS_CITY_FUN_SCROLL, self.RECOVER_SCROLL_WHEEL)
            self.sleep(0.5)
        self.log_warning("都市闲趣面板里未找到「即刻落槌」入口")
        return False

    def _read_current_venue(self) -> str:
        """读拍卖主界面右侧的「当前：XXX场」, 只用于在日志里留痕, 读不出返回空串。"""
        try:
            results = self.ocr(
                box=self.box_of_screen(*self.BOX_CURRENT_VENUE), match=RE_CURRENT_VENUE
            )
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_debug(f"会场文字读取失败: {type(e).__name__}: {e}")
            return ""
        return "".join(box.name for box in results or []).strip()

    def _build_boxes(self) -> AuctionBoxes:
        """按相对比例一次性构建本轮拍卖使用的全部 UI 区域。"""
        screen = self.box_of_screen
        return AuctionBoxes(
            match=screen(*self.BOX_MATCH),
            confirm=screen(*self.BOX_CONFIRM),
            bid=screen(*self.BOX_BID),
            bid_keypad=screen(*self.BOX_BID_KEYPAD),
            skip_area=screen(*self.BOX_SKIP_AREA),
            exit=screen(*self.BOX_EXIT),
            bid_confirm=screen(*self.BOX_BID_CONFIRM),
            abandon=screen(*self.BOX_ABANDON),
            abandon_confirm=screen(*self.BOX_ABANDON_CONFIRM),
            asset_value=screen(*self.BOX_ASSET_VALUE),
            estimate=screen(*self.BOX_ESTIMATE),
            last_bid=screen(*self.BOX_LAST_BID),
            clear=screen(*self.BOX_CLEAR),
            price_result=screen(*self.BOX_PRICE_RESULT),
            price_result_keypad=screen(*self.BOX_PRICE_RESULT_KEYPAD),
            exception_area=screen(*self.BOX_EXCEPTION_AREA),
            main_title=screen(*self.BOX_MAIN_TITLE),
            main_asset=screen(*self.BOX_MAIN_ASSET),
            insufficient=screen(*self.BOX_INSUFFICIENT),
            welfare_btn=screen(*self.BOX_WELFARE_BTN),
            welfare_dialog=screen(*self.BOX_WELFARE_DIALOG),
            claim=screen(*self.BOX_CLAIM),
            cancel=screen(*self.BOX_CANCEL),
            warehouse_btn=screen(*self.BOX_WAREHOUSE_BTN),
            warehouse_title=screen(*self.BOX_WAREHOUSE_TITLE),
            sell=screen(*self.BOX_SELL),
            confirm_sell=screen(*self.BOX_CONFIRM_SELL),
            sell_label=screen(*self.BOX_SELL_LABEL),
            sell_value=screen(*self.BOX_SELL_VALUE),
            blank=screen(*self.BOX_BLANK),
            close=screen(*self.BOX_CLOSE),
            one_click_sell=screen(*self.BOX_ONE_CLICK_SELL),
            popup_close_hint=screen(*self.BOX_POPUP_CLOSE_HINT),
            popup_blank=screen(*self.BOX_POPUP_BLANK),
        )

    # --- 任务入口回场 (大世界 → 拍卖主界面) ---
    def _ensure_auction_entry(self, boxes: AuctionBoxes) -> None:
        """每轮开始时确认人已站在拍卖主界面, 不在就按「大世界 → 即刻落槌」补上入口。

        复用掉线回场的同一条路径(_return_to_auction), 不新增识别或导航代码:
        用户从大世界直接启动时, 原来的第一轮只能在 _stage_match 里空转到
        MATCH_TIMEOUT(120 秒) 由末尾的大世界兜底触发回场, 首轮白等约一分钟.

        判定顺序: 先读拍卖主界面标题, 命中即已在拍卖界面, 直接开跑;
        未命中再判大世界 —— 不在大世界就不接管, 交给原有流程按界面异常报错,
        避免在登录页/加载页等未知界面上误触发 F5 流程.

        挂在每轮而不是只在启动时调一次, 与 AutoHeistTask._run_loop 的
        「每轮先 ensure_main + 入口判断」一致: 上一轮掉线或异常退出时人可能已经不在
        拍卖界面, 那时后续每轮都要白等 MATCH_TIMEOUT 才由 _stage_match 末尾兜底.
        已在拍卖界面时本方法只多花一次标题 OCR(命中即返回), 正常路径开销可忽略.

        执行位置在 begin_round 之后、_run_single_round 之前, 不消耗轮次内的
        `_recover_quota`(该配额每轮由 _exec_auction_round 重置), 因此不影响
        「每轮掉线可回场一次」的既有约定.
        """
        if self.wait_ocr(
            box=boxes.main_title,
            match=RE_MAIN_TITLE,
            time_out=self.ENTRY_PROBE_TIMEOUT,
            raise_if_not_found=False,
        ):
            return
        if not self._is_world_screen():
            return
        self.log_info("启动时检测到大世界, 自动进入「即刻落槌」")
        self.info_set("当前阶段", "入场中")
        if not self._return_to_auction(boxes, time.monotonic() + self.ENTRY_RECOVER_TIMEOUT):
            self.log_warning("启动回场未成功, 交给第一轮按界面异常处理")

    def _run_single_round(self, boxes: AuctionBoxes) -> None:
        """执行一轮拍卖, 记录结果并仅在确认回到主界面后触发出售。"""
        # 结算后处理会把本轮的满仓与低保金结果写回, 每轮开始前先清空上一轮的观测.
        self._post_round_state = PostRoundState()

        if self._inventory_stuck:
            # 上一轮满仓且出售未成功: 仓库腾不出空间时这一轮出价必然失败,
            # 与其把整轮 deadline 空转掉, 不如先重试一次清理藏品.
            # 满仓是上一轮已经判定过的结论, 这里直接沿用, 不再要求重新 OCR 命中:
            # 满仓提示会被弹窗遮住, 重新检测失败就什么都不做, 变成每轮空跳的死循环.
            self.log_warning("满仓且上次出售未成功, 跳过本轮拍卖, 先重试清理藏品")
            self.add_failed("满仓未清理")
            self._try_sell_collections(PostRoundState(inventory_full=True), boxes)
            return

        finished = self._exec_auction_round(boxes)
        if finished:
            self.add_success()
        else:
            self.add_failed("结果阶段进入下一轮出价")

        self.log_info(f"本轮拍卖完成 ({self.current_round}/{self._round_state.total_text})")
        if finished and self._post_round_state.observed:
            # 两个前置条件都不能少:
            # - finished 为 False 时画面仍在拍卖出价界面, 出售只会在仓库入口白等超时;
            # - observed 为 False 说明 _finish_auction 没识别到主界面标题就返回了,
            #   画面状态未知, 同样不能去点仓库入口.
            self._try_sell_collections(self._post_round_state, boxes)

    def _try_sell_collections(self, state: PostRoundState, boxes: AuctionBoxes) -> None:
        """轮次末尾的出售入口: 给出售流程一个独立的、有界的预算。

        原本两处调用都不传 deadline, 于是 _bounded_timeout(deadline=None, ...) 一律
        原样返回 limit、_timeout_or_zero 也永不返回 0 —— 整条出售流程实际上没有任何
        上级预算。逐分支累加的等待下限约 35 秒(连续失败放宽时约 71 秒), 而它不消耗
        ROUND_TIMEOUT(600): 「满仓时清理」每轮都要走一遍, 仓库入口读不到时就变成
        「任务在跑但几乎不出价」, 且日志里看不出时间被谁吃掉。

        出售失败不该影响本轮已经记下的成功/失败结论, 所以这里兜住 WaitFailedException ——
        补上 deadline 后 _sell_collections 收尾的 _bounded_sleep 会在预算耗尽时抛它。
        """
        sell_deadline = time.monotonic() + self.SELL_TIMEOUT
        try:
            self._sell_collections_on_interval(boxes, sell_deadline, state=state)
        except TaskDisabledException:
            raise
        except WaitFailedException as e:
            self.log_warning(f"藏品出售超出 {self.SELL_TIMEOUT} 秒预算, 本轮放弃出售: {e}")

    # --- 单轮流程编排 ---
    def _exec_auction_round(self, boxes: AuctionBoxes) -> bool:
        """执行单轮拍卖, 按序调度各阶段。

        Returns:
            bool: 拍卖是否顺利进入结算(进入下一轮出价时返回 False)。
        """
        deadline = time.monotonic() + self.ROUND_TIMEOUT
        # 本轮回场配额, 由整轮创建、所有匹配重跑共享。原来把这个状态放在
        # `allow_recover` 参数上逐层传递, 但 `_ensure_confirm_stage` 的确认失败分支会重新
        # 调 `_stage_match(boxes, deadline)`, 默认值 `True` 把配额悄悄恢复 —— 于是一轮里可以
        # 回场多次, 每次都要重走一遍「F5 → 都市闲趣 → 即刻落槌」的动画, 把整轮 deadline
        # 耗光。配额属于「这一轮」而不是「这一次匹配调用」, 所以只能挂在轮次上。
        self._recover_quota = self.RECOVER_MAX_PER_ROUND
        self.info_set("当前阶段", "匹配中")
        self.log_info(f"拍卖开始, 单轮最长运行 {self.ROUND_TIMEOUT} 秒")
        self.sleep(0.5)

        state = self._stage_match(boxes, deadline)
        if state is AuctionState.SKIP:
            return self._stage_result(boxes, deadline)

        state = self._ensure_confirm_stage(boxes, state, deadline)
        if state is AuctionState.SKIP:
            return self._stage_result(boxes, deadline)

        self.info_set("当前阶段", "出价中")
        if state is AuctionState.CONFIRM:
            self._wait_bid_screen(boxes, deadline)

        self._stage_bid_loop(boxes, deadline)

        self.info_set("当前阶段", "结算中")
        return self._stage_result(boxes, deadline)

    def _ensure_confirm_stage(
        self, boxes: AuctionBoxes, state: AuctionState, deadline: float
    ) -> AuctionState:
        """确保确认阶段完成, 失败时回到匹配阶段重试, 仍受同一 deadline 约束。"""
        self.info_set("当前阶段", "确认中" if state is AuctionState.CONFIRM else "出价中")
        if state is not AuctionState.CONFIRM:
            self.log_info("当前已在出价界面, 跳过确认阶段")
            return state

        if self._stage_confirm(boxes, deadline):
            return state

        self.log_warning("确认阶段未完成, 回到匹配阶段重试, 不重置单轮超时")
        # 整轮 deadline 确实不重置, 但 _stage_match 内部的 MATCH_TIMEOUT 是局部预算,
        # 这一次重试会重新计一次(累计上限 2 x MATCH_TIMEOUT), 由整轮 deadline 兜底。
        state = self._stage_match(boxes, deadline)
        if state is AuctionState.SKIP:
            return state
        if state is AuctionState.CONFIRM and not self._stage_confirm(boxes, deadline):
            raise WaitFailedException("确认阶段连续失败")
        return state

    def _wait_bid_screen(self, boxes: AuctionBoxes, deadline: float) -> None:
        """等待确认后的出价界面加载完成。"""
        self.log_info("等待进入出价界面")
        ready = self.wait_until(
            lambda: self._is_bid_screen(boxes),
            time_out=self._remaining_timeout(deadline, self.BID_SCREEN_TIMEOUT),
            settle_time=0.5,
            post_action=lambda: self.sleep(0.5),
            raise_if_not_found=False,
        )
        if not ready:
            raise WaitFailedException("等待出价界面超时")

    # --- 各阶段实现 ---
    def _stage_match(self, boxes: AuctionBoxes, deadline: float) -> AuctionState:
        """匹配阶段: 等待进入确认或出价状态。

        仅在检测到"开始匹配"按钮时才尝试点击, 避免界面过渡期无谓的阻塞。

        deadline 是整轮拍卖的绝对截止时间, 不会因点击重试而重置; 但本阶段的
        MATCH_TIMEOUT 是局部预算, 每次进入本方法都重新计一次 —— _ensure_confirm_stage
        失败后会再调一次本方法, 所以匹配阶段累计上限是 2 x MATCH_TIMEOUT, 只由整轮
        deadline 兜底。另外循环退出条件有两个(MATCH_MAX_LOOPS 与 stage_deadline),
        循环体耗时不等于 POLL_INTERVAL 时两者谁先生效不确定, 都保留。

        掉线是否还能回场由轮次配额 `_recover_quota` 决定, 不再用参数传递: 参数会在
        「确认失败后重跑本方法」这条路径上被默认值恢复, 让同一轮反复回场。
        """
        self.log_info("匹配阶段开始, 等待确认或出价界面")
        stage_deadline = min(deadline, time.monotonic() + self.MATCH_TIMEOUT)
        loop_count = 0

        while loop_count < self.MATCH_MAX_LOOPS and time.monotonic() < stage_deadline:
            loop_count += 1
            # 显式取一帧: 下面四次 ocr 共用同一帧, 避免依赖 self.sleep 清空缓存的副作用.
            self.next_frame()

            # 优先快速检测当前界面状态.
            if self._is_bid_screen(boxes):
                self.log_info("检测到已在出价界面")
                return AuctionState.BID
            if self._is_confirm_screen(boxes):
                self.log_info("检测到已在确认界面")
                return AuctionState.CONFIRM
            if self._is_skip_screen(boxes):
                self.log_info("匹配阶段检测到跳过动画, 拍卖已意外结束")
                return AuctionState.SKIP

            # 仅在"开始匹配"按钮确实存在时才尝试点击.
            # 点击后界面未变化时返回 None 继续轮询; 单轮 deadline 到期由内部抛出超时.
            if self._is_match_screen(boxes):
                result = self._handle_match_click(boxes, stage_deadline)
                if result is AuctionState.WORLD:
                    return self._resume_after_world_drop(boxes, deadline)
                if result is not None:
                    return result

            self.sleep(self.POLL_INTERVAL)

        # 空转到超时前判一次大世界: 网络抖动掉线时四种界面状态全不命中, 直接抛超时会让
        # 本轮白等两分钟。命中大世界就回场后重跑本阶段, 仍在整轮 deadline 内。
        if self._is_world_screen():
            return self._resume_after_world_drop(boxes, deadline)

        raise WaitFailedException("匹配阶段超时, 未进入确认或出价界面")

    def _handle_match_click(
        self, boxes: AuctionBoxes, stage_deadline: float
    ) -> AuctionState | None:
        """点击开始匹配, 并等待后续界面状态变化。

        使用统一循环同时检测确认, 出价和跳过界面, 覆盖匹配对局与加载动画。
        点击后 MATCH_PROBE_TIMEOUT 秒内界面毫无变化时判定点击未生效并立刻返回,
        由调用方重新点击; 界面确实在切换(处于加载动画)时最多等待 MATCH_CLICK_TIMEOUT 秒。

        stage_deadline 是匹配阶段的局部截止时间(见 _stage_match), 不是整轮 deadline;
        所以这里的超时异常要带上阶段名, 不能沿用「单轮拍卖超时」。
        """
        clicked = self._wait_operate_click(
            boxes.match,
            RE_MATCH,
            self._remaining_timeout(stage_deadline, 5, "匹配阶段超时, 未进入确认或出价界面"),
        )
        if not clicked:
            return None
        self.log_info("已点击开始匹配, 等待状态变化")

        click_deadline = min(stage_deadline, time.monotonic() + self.MATCH_CLICK_TIMEOUT)
        probe_deadline = min(click_deadline, time.monotonic() + self.MATCH_PROBE_TIMEOUT)
        loop_count = 0
        while time.monotonic() < click_deadline:
            loop_count += 1
            self.next_frame()
            if self._is_confirm_screen(boxes):
                self.log_info("匹配成功, 进入确认阶段")
                return AuctionState.CONFIRM
            if self._is_bid_screen(boxes):
                self.log_info("匹配成功, 进入出价阶段")
                return AuctionState.BID
            if self._is_skip_screen(boxes):
                self.log_info("匹配阶段检测到跳过动画")
                return AuctionState.SKIP

            # 界面切换时按钮会消失, 这种情况继续等加载动画;
            # 按钮还在原地说明点击没生效, 交回上层立刻重新点击.
            if time.monotonic() >= probe_deadline:
                if self._is_match_screen(boxes):
                    self.log_warning(
                        f"点击开始匹配后 {self.MATCH_PROBE_TIMEOUT} 秒界面无变化, "
                        f"判定点击未生效, 立即重新点击"
                    )
                    return None
                # 按钮已消失却没进后续界面: 正常是加载动画, 也可能是掉线被踢回大世界。
                # 大世界判定比 OCR 贵, 按 WORLD_PROBE_INTERVAL 节流探测。
                if loop_count % self.WORLD_PROBE_INTERVAL == 0 and self._is_world_screen():
                    self.log_warning("匹配阶段界面长时间无变化且检测到大世界, 判定为掉线")
                    return AuctionState.WORLD

            self.sleep(self.POLL_INTERVAL)

        self.log_warning(
            f"点击匹配后 {self.MATCH_CLICK_TIMEOUT} 秒内未检测到后续界面, 将重新检查匹配按钮"
        )
        return None

    def _stage_confirm(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """确认阶段: 等待并点击确认按钮, 所有等待受整轮 deadline 约束。"""
        self.log_info("确认阶段开始, 等待确认按钮")
        found = self.wait_ocr(
            box=boxes.confirm,
            match=RE_CONFIRM,
            time_out=self._remaining_timeout(deadline, 15),
            raise_if_not_found=False,
            settle_time=0.5,
        )
        if not found:
            self.log_warning("确认按钮在阶段等待时间内未出现")
            return False

        self.operate_click(boxes.confirm, after_sleep=0.5)
        confirmed = self.wait_until(
            lambda: not self._is_confirm_screen(boxes),
            time_out=self._remaining_timeout(deadline, 5),
            settle_time=0.5,
            raise_if_not_found=False,
        )
        if not confirmed:
            self.log_warning("点击确认按钮后按钮仍存在, 本次确认失败")
            return False

        self.log_info("确认完成, 等待进入出价界面")
        return True

    def _stage_bid_loop(self, boxes: AuctionBoxes, deadline: float) -> None:
        """出价阶段: 循环出价直到拍卖结束, 支持多轮竞拍。

        每次出价结果最多等待 BID_RESULT_TIMEOUT 秒, 整个阶段仍受单轮 deadline 约束。
        资产为 0 时放弃本次出价并等待拍卖结束, 放弃不计入出价序号。
        """
        # 每轮拍卖开始前重置出价计数和上次价格.
        self.current_bid_count = 0
        self.last_bid_price = None

        retry = 0
        # 循环只通过 return(拍卖结束) 或 raise(连续失败/超时) 退出, 所以用 while True.
        # 出价失败一律抛异常, 连续失败达到 BID_MAX_RETRIES 时在 except 分支抛出.
        while True:
            self._remaining_timeout(deadline, 0.1)
            try:
                bid_placed = self._attempt_bid(boxes, deadline)
            except TaskDisabledException:
                raise
            except Exception as e:
                retry += 1
                self.log_warning(
                    f"出价异常 ({retry}/{self.BID_MAX_RETRIES}): {type(e).__name__}: {e}"
                )
                if retry >= self.BID_MAX_RETRIES:
                    raise
                self.sleep(1)
                continue

            # 尝试完成(无论是否真正出价)后重置失败重试计数, 用于下一次尝试.
            retry = 0
            if bid_placed:
                self.current_bid_count += 1
                self.log_info(f"第 {self.current_bid_count} 次出价成功, 等待拍卖结果或加价")
            else:
                self.log_info("本次出价已放弃, 等待拍卖结果")

            if self._wait_bid_outcome(boxes, deadline):
                return

    def _wait_bid_outcome(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """等待本次出价的结果。

        Returns:
            bool: True 表示拍卖已结束; False 表示有人加价, 需要继续下一次出价。
        """
        wait_deadline = min(deadline, time.monotonic() + self.BID_RESULT_TIMEOUT)
        while time.monotonic() < wait_deadline:
            self.next_frame()
            if self._is_skip_screen(boxes):
                self.log_info("检测到跳过动画, 拍卖结束")
                return True
            if self._is_match_screen(boxes):
                self.log_info("返回匹配界面, 拍卖结束")
                return True
            if self._is_bid_screen(boxes):
                self.log_info("检测到有人加价, 准备再次出价")
                return False
            self.sleep(self.POLL_INTERVAL)

        if time.monotonic() >= deadline:
            raise WaitFailedException("单轮拍卖超时")
        self.log_info(f"本次出价结果等待 {self.BID_RESULT_TIMEOUT} 秒未变化, 按拍卖结束处理")
        return True

    def _attempt_bid(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """单次出价尝试: 包含资产识别, 出价面板确认和可选表情包动作。

        失败时抛出 WaitFailedException, 由调用方决定是否重试。
        Returns:
            bool: True 表示已提交出价; False 表示资产为 0 已放弃本次出价。
        """
        # 等待确认后的加载动画完成, 再判断资产值.
        asset_value = self._read_asset_value(
            boxes.asset_value, self._remaining_timeout(deadline, self.ASSET_OCR_TIMEOUT)
        )
        if asset_value is None:
            self.log_warning("资产值识别失败, 准备重试本次出价")
            raise WaitFailedException("资产值未识别")

        # 资产为 0 时放弃本轮出价; 单次误读就放弃整场拍卖代价过高, 放弃前需二次确认.
        if asset_value == 0:
            confirm_value = self._read_asset_value(
                boxes.asset_value, self._remaining_timeout(deadline, self.ASSET_OCR_TIMEOUT)
            )
            if confirm_value is None:
                self.log_warning("资产值二次识别失败, 准备重试本次出价")
                raise WaitFailedException("资产值未识别")
            if confirm_value != 0:
                self.log_warning(f"资产二次识别为 {confirm_value}, 首次读数 0 判定为误读, 继续出价")
                asset_value = confirm_value
            else:
                self.log_info("当前资产值两次识别均为 0, 放弃本轮出价")
                self.operate_click(boxes.abandon, after_sleep=0.5)
                self.sleep(0.5)
                self.operate_click(boxes.abandon_confirm, after_sleep=0.5)
                abandoned = self.wait_until(
                    lambda: not self._is_bid_screen(boxes),
                    time_out=self._remaining_timeout(deadline, 5),
                    settle_time=0.5,
                    raise_if_not_found=False,
                )
                if not abandoned:
                    self.log_warning("点击放弃后仍在出价界面, 准备重试本次尝试")
                    raise WaitFailedException("放弃出价失败")
                return False

        self.log_debug(f"当前资产值为 {asset_value}, 继续执行出价")

        # 数字键盘已经弹出时 BOX_BID 被弹窗盖住(实测该框 all_boxes 全空), 在 boxes.bid 上
        # 等 RE_BID 只会等到超时, 于是 _input_fixed_price 永远走不到. 面板已就绪时直接跳过
        # 出价按钮, 否则保持原有等待与点击路径.
        keypad_open = bool(self.ocr(box=boxes.bid_keypad, match=RE_BID_PANEL))
        if keypad_open:
            self.log_info("数字面板已打开, 跳过出价按钮")
            found = True
        else:
            self.log_info("等待出价按钮")
            found = self._wait_operate_click(
                boxes.bid,
                RE_BID,
                self._remaining_timeout(deadline, 10),
            )
        if not found:
            self.log_warning("出价按钮未出现, 准备重试本次出价")
            raise WaitFailedException("出价按钮未出现")

        self.log_info("点击出价")
        panel_ready = self.wait_ocr(
            box=boxes.bid_confirm,
            match=RE_BID_PANEL_READY,
            time_out=self._remaining_timeout(deadline, 5),
            raise_if_not_found=False,
            settle_time=0.5,
        )
        if not panel_ready:
            self.log_warning("数字面板未出现, 准备重试本次出价")
            raise WaitFailedException("数字面板未出现")

        self.log_info("数字面板加载完成")
        self._input_fixed_price(boxes, deadline=deadline)

        bid_confirmed = self.wait_until(
            lambda: not self._is_bid_screen(boxes),
            time_out=self._remaining_timeout(deadline, 5),
            settle_time=0.5,
            raise_if_not_found=False,
        )
        if not bid_confirmed:
            raise WaitFailedException("出价确认失败: 出价按钮仍存在")

        if self._assist_enabled(self.ASSIST_EMOTE):
            self._send_emote()

        return True

    def _stage_result(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """结果阶段: 等待结算, 处理跳过动画或返回匹配界面。

        只有「等待结算」受 RESULT_TIMEOUT 约束; 检测到结束状态之后的收尾动作
        (跳过动画 / 一键出售 / 退出拍卖 / 结算后观测) 一律用整轮 deadline。
        这两者不能共用一个截止时间: RESULT_TIMEOUT(90 秒) 只是阶段预算, 把它当成
        收尾预算后, 结算阶段空转掉 80 秒就只剩 10 秒可用来退出和观测, 空转满 90 秒
        时收尾动作会在整轮还有几百秒的情况下抛「单轮拍卖超时」, 把一个已经拍成的
        轮次记成失败; 「返回匹配界面」分支更静默 —— _observe_post_round_on_main_screen
        拿不到时间就直接返回, _post_round_state.observed 保持 False, 轮次末尾不出售,
        满仓时后续每轮都会卡在「开始匹配」上。

        主界面判定必须排在「跳过动画」之前: 两个区域都落在主界面底部按钮带
        (skip_area 0.703~0.807 / 0.902~0.953 与 BOX_MATCH 0.7427~0.8360 / 0.8972~0.9472
        大面积重叠), 主界面上误读到「跳过」时, 若先走跳过分支就会点一个不存在的
        「跳过动画」、再等一个不存在的「退出拍卖」按钮, 失败时抛异常跳过整个
        _run_post_round_actions —— 而 _post_round_state.observed 保持 False 的后果
        正是上面那段描述的「满仓时清理完全失效」。
        """
        self.log_info("结算阶段开始, 等待拍卖结果")
        result_deadline = min(deadline, time.monotonic() + self.RESULT_TIMEOUT)
        loop_count = 0

        while loop_count < self.RESULT_MAX_LOOPS and time.monotonic() < result_deadline:
            loop_count += 1
            self.next_frame()

            if self._is_match_screen(boxes):
                self.log_info("返回匹配界面")
                self._observe_post_round_on_main_screen(boxes, deadline)
                return True

            skip_results = self.ocr(box=boxes.skip_area, match=RE_SKIP)
            if skip_results:
                self._finish_auction(boxes, skip_results, deadline)
                return True

            if self._is_bid_screen(boxes):
                self.log_info("进入下一轮出价")
                return False

            self.sleep(self.POLL_INTERVAL)

        raise WaitFailedException("结算阶段超时, 未检测到结束状态")

    def _finish_auction(
        self, boxes: AuctionBoxes, skip_results: list[Box], deadline: float
    ) -> None:
        """拍卖结束处理: 跳过动画, 一键出售, 退出拍卖, 并在主界面执行结算后的辅助操作。"""
        self.log_info("检测到跳过动画")
        self.operate_click(skip_results, after_sleep=0.5)

        # 「拍卖成功一键出售」必须在退出拍卖之前完成: 一键出售按钮只在结算界面存在.
        if self._sell_mode() == self.SELL_MODE_ONE_CLICK:
            self._sell_on_settlement_screen(boxes, deadline)

        exit_button = self._wait_operate_click(
            boxes.exit,
            RE_EXIT,
            self._remaining_timeout(deadline, self.EXIT_BUTTON_TIMEOUT),
            after_sleep=0.5,
        )
        if not exit_button:
            raise WaitFailedException("退出拍卖按钮未出现")
        self.log_info("退出拍卖")

        self.log_info("等待主界面稳定")
        # 「我的资产」在结算等界面也会出现, 改用只在拍卖主界面出现的「即刻落槌」判定.
        main_title = self.wait_ocr(
            box=boxes.main_title,
            match=RE_MAIN_TITLE,
            time_out=self._remaining_timeout(deadline, 15),
            raise_if_not_found=False,
            settle_time=0.5,
            post_action=lambda: self.sleep(0.5),
        )
        if not main_title:
            self.log_warning("主界面「即刻落槌」标题未识别, 跳过本轮结算后处理")
            return

        self.log_info("主界面加载完成")
        self._run_post_round_actions(boxes, deadline)

    def _observe_post_round_on_main_screen(self, boxes: AuctionBoxes, deadline: float) -> None:
        """已经回到主界面时的结算后观测。

        _stage_result 的「返回匹配界面」分支不会走 _finish_auction —— 那里要点「跳过
        动画」和「退出拍卖」, 而这两个按钮在已经回到主界面的情况下并不存在。但结算后
        观测必须照做: 少了它, _post_round_state.observed 保持 False, 轮次末尾就不出售,
        于是满仓时后续每轮都卡在「开始匹配」上(点一次弹一次「库存不足」), 而
        _inventory_stuck 永远不会置位 —— 「满仓时清理」在这条路径上完全失效。

        仍以主界面标题为准再动手: 只有确认画面是拍卖主界面才做观测, 否则会去点不存在
        的仓库入口白等超时。
        """
        title_timeout = self._timeout_or_zero(deadline, 5)
        if title_timeout <= 0:
            return
        if not self.wait_ocr(
            box=boxes.main_title,
            match=RE_MAIN_TITLE,
            time_out=title_timeout,
            raise_if_not_found=False,
            settle_time=0.5,
        ):
            self.log_warning("主界面「即刻落槌」标题未识别, 跳过本轮结算后处理")
            return

        self.log_info("主界面加载完成")
        self._run_post_round_actions(boxes, deadline)

    def _sell_on_settlement_screen(self, boxes: AuctionBoxes, deadline: float) -> None:
        """结算界面「一键出售」: 点游戏自带的按钮卖掉本局藏品, 再关掉「获得物品」提示。

        按钮只在拍卖成功(拍到东西)后才出现, 找不到就跳过 —— 流拍时本来就没有可卖的
        东西, 不该让整轮失败。

        这条路径不碰藏品仓库, 也不做满仓检测: 一键出售是游戏自己的整包出售, 没有品质
        勾选, 也读不到「出售价值」, 因此不复用 _sell_collections。

        关提示条分两步: 先用「点击空白区域关闭」确认提示条出现了, 再点提示条以外的
        空白区域 —— 游戏要求点的是空白区域, 不是那句提示文字本身。

        单轮 deadline 用尽时按「没时间」跳过, 而不是抛异常: 结算已经完成, 不该因为
        时间不够把整轮判成失败。
        """
        sell_timeout = self._timeout_or_zero(deadline, self.ONE_CLICK_SELL_TIMEOUT)
        if sell_timeout <= 0:
            self.log_warning("单轮时间已用尽, 跳过结算界面的「一键出售」")
            return
        if not self._wait_operate_click(
            boxes.one_click_sell, RE_ONE_CLICK_SELL, sell_timeout, after_sleep=1
        ):
            self.log_info("结算界面未出现「一键出售」, 跳过(流拍时没有可出售的藏品)")
            return
        self.log_info("已点击一键出售")

        popup_timeout = self._timeout_or_zero(deadline, self.POPUP_CLOSE_TIMEOUT)
        if popup_timeout <= 0:
            self.log_warning("单轮时间已用尽, 「获得物品」提示未处理")
            return
        # 只检测不点击: 要关掉提示条, 得点提示条以外的空白区域, 而不是提示文字本身.
        hint = self.wait_ocr(
            box=boxes.popup_close_hint,
            match=RE_POPUP_CLOSE_HINT,
            time_out=popup_timeout,
            raise_if_not_found=False,
            settle_time=0.5,
        )
        if not hint:
            self.log_warning("「获得物品」提示未出现, 退出拍卖前请留意残留弹窗")
            return
        self.operate_click(boxes.popup_blank, after_sleep=0.5)
        self.log_info("已点击空白区域关闭「获得物品」提示")

    def _run_post_round_actions(self, boxes: AuctionBoxes, deadline: float) -> None:
        """结算后回到主界面时的辅助操作: 观测满仓状态并领取低保金。

        库存不足提示位于屏幕中部, 会被低保金弹窗遮挡, 因此必须在打开弹窗之前检测。

        这里只观测并把结果写入 _post_round_state, 是否出售由轮次末尾的
        _sell_collections_on_interval 统一决定。两处都动手会让同一轮卖两次: 第二次
        面对已被卖空的仓库读到「出售价值 0」, 白白累计失败次数, 最终触发「放宽保留
        品质」把用户明确要保留的藏品一起卖掉。
        """
        # 满仓等提示弹窗会盖住库存不足提示条, 先兜掉再观测, 否则满仓永远检测不到.
        self._dismiss_notice_popup(boxes, deadline, "结算后主界面")

        # 未检测到一律保持 None(而不是 False): None 表示「本轮未测出结论」, 轮次末尾的
        # _sell_collections_on_interval 会在那里(deadline 为空, 有完整超时预算)补测一次。
        # 写成 False 等于宣称「确定没满仓」, 会把满仓静默漏掉。
        inventory_full: bool | None = None
        if self._uses_collection_sell():
            # 观测步骤没有可用时间时返回 None, 不抛异常.
            inventory_full = self._detect_inventory_full(
                boxes, self._timeout_or_zero(deadline, self.INVENTORY_FULL_TIMEOUT)
            )

        # 资产观测无条件执行, 与低保金开关无关: 这是独立的长期记录功能。
        # 观测失败(未读出)只返回 None, 不影响后续低保金与出售流程。
        asset_value = self._observe_main_asset(boxes, deadline)

        welfare_claimed = False
        if self._assist_enabled(self.ASSIST_WELFARE):
            try:
                welfare_claimed = self._claim_welfare_if_needed(boxes, deadline, asset_value)
            except TaskDisabledException:
                raise
            except WaitFailedException as e:
                # 低保金领取是可选的收尾动作, 单轮时间用尽时只跳过本次领取.
                # 让它传播出去会把已经结算成功的轮次判成失败, 而且本方法写回观测结果
                # 的那一行会被跳过 —— _post_round_state.observed 保持 False, 轮次末尾
                # 连带跳过出售.
                self.log_warning(f"低保金领取超时, 跳过本次领取: {e}")

        self._post_round_state = PostRoundState(
            inventory_full=inventory_full,
            welfare_claimed=welfare_claimed,
            observed=True,
        )

    def _sell_mode(self) -> str:
        """读取出售模式, 未知值一律按「不出售」处理, 避免脏配置意外清空仓库。"""
        mode = self.config.get(self.CONF_SELL_MODE, self.SELL_MODE_OFF)
        return mode if mode in self.SELL_MODES else self.SELL_MODE_OFF

    def _assist_enabled(self, feature: str) -> bool:
        """判断「启用辅助功能」多选框里是否勾选了某个功能。

        多选框存的是勾选项列表, 未勾选时为空列表。值不是列表(用户手工改成字符串或
        迁移未落盘的旧 bool)时一律按未勾选处理: 少发一个表情、少领一次低保金都是可
        恢复的, 不该因为脏配置去点不存在的按钮。
        """
        selected = self.config.get(self.CONF_ASSIST_FEATURES, ())
        if not isinstance(selected, list):
            return False
        return feature in selected

    def _uses_collection_sell(self) -> bool:
        """本轮是否需要走「藏品仓库」出售流程: 满仓检测 + 品质勾选 + 确认出售。

        「拍卖成功一键出售」用游戏自带的按钮在结算界面直接卖, 不碰仓库, 也不做满仓检测,
        因此不算这条流程 —— 见 _sell_on_settlement_screen。
        """
        return self._sell_mode() not in (self.SELL_MODE_OFF, self.SELL_MODE_ONE_CLICK)

    def _detect_inventory_full(self, boxes: AuctionBoxes, timeout: float) -> bool | None:
        """检测主界面的库存不足提示, 命中表示满仓无法继续拍卖。

        这是可选的观测步骤: 没有可用时间时按「未检测」处理(返回 None), 不抛异常,
        否则单轮 deadline 用尽会让整个结算后处理崩掉。

        注意不能返回 False: False 的语义是「确定没满仓」, 写进 PostRoundState 后
        轮次末尾的 _sell_collections_on_interval 会因为「不是 None」而不再补测,
        满仓会被静默漏掉 —— 之后每轮出价都失败, 却永远不触发清理。返回 None 时
        调用方(那里 deadline 为空, 有完整的 INVENTORY_FULL_TIMEOUT 可用)会重新检测。
        """
        if timeout <= 0:
            self.log_debug("满仓检测没有可用时间, 跳过本次检测")
            return None

        found = self.wait_ocr(
            box=boxes.insufficient,
            match=RE_COLLECTION_INSUFFICIENT,
            time_out=timeout,
            settle_time=0.5,
            raise_if_not_found=False,
        )
        if found:
            self.log_info("检测到库存不足提示, 当前处于满仓状态")
        return bool(found)

    def _observe_main_asset(self, boxes: AuctionBoxes, deadline: float) -> int | None:
        """读取主界面资产值, 记录到本地历史, 返回数值(未读出时 None)。

        独立于低保金领取: 资产历史记录是用户要的长期观测, 不依赖「启用辅助功能」里
        是否勾选低保金。两者合在一个方法里时, 用户取消勾选低保金会让整个资产记录
        静默停摆 —— 任务运行完全正常, 只是数据一条都不写, 极难发现。

        资产读取属于可选的观测步骤, 和 _detect_inventory_full 一样用 _timeout_or_zero:
        单轮时间用尽时只表示这次没测到, 不该抛 WaitFailedException —— 那会把已经成功
        结算的轮次判成失败, 而且调用方写回观测结果的那一步会被跳过, 连出售也一并丢失。
        """
        timeout = self._timeout_or_zero(deadline, self.ASSET_OBSERVE_TIMEOUT)
        if timeout <= 0:
            self.log_debug("资产观测没有可用时间, 跳过本次读取")
            return None

        # 使用数字 match, 避免漏识别单字符数值 0.
        asset_value = self._read_asset_value(boxes.main_asset, timeout)
        if asset_value is None:
            self.log_warning("资产值识别失败, 跳过本次观测")
            return None

        self.log_info(f"当前资产: {asset_value}")
        return asset_value

    def _claim_welfare_if_needed(
        self, boxes: AuctionBoxes, deadline: float, asset_value: int | None
    ) -> bool:
        """主界面资产低于阈值时领取低保金, 返回是否成功领取。

        asset_value 由调用方通过 _observe_main_asset 读出后传入: 资产观测与低保金领取
        拆开后, 两者的可用时间互相独立, 一次 OCR 的读数也只采信一次。
        """
        if asset_value is None:
            self.log_warning("资产值识别失败, 跳过本次低保金领取")
            return False

        if asset_value >= self.WELFARE_ASSET_THRESHOLD:
            self.log_info(f"资产达到{self.WELFARE_ASSET_THRESHOLD}, 跳过低保金领取")
            return False

        self.log_info(f"资产低于{self.WELFARE_ASSET_THRESHOLD}, 执行低保金领取")
        return self._try_claim_welfare(boxes, deadline)

    # --- 界面状态判定 ---
    def _is_match_screen(self, boxes: AuctionBoxes) -> bool:
        return bool(self.ocr(box=boxes.match, match=RE_MATCH))

    def _is_confirm_screen(self, boxes: AuctionBoxes) -> bool:
        return bool(self.ocr(box=boxes.confirm, match=RE_CONFIRM))

    def _is_bid_screen(self, boxes: AuctionBoxes) -> bool:
        """判断是否已在出价界面(含数字键盘已弹出的状态)。

        键盘弹窗会盖住 BOX_BID, 那时只能靠弹窗上的文案认出界面, 否则会空等到超时。
        键盘态优先判定: 它是更具体的形态, 命中就不必再读 BOX_BID。
        """
        if self.ocr(box=boxes.bid_keypad, match=RE_BID_PANEL):
            return True
        return bool(self.ocr(box=boxes.bid, match=RE_BID))

    def _is_skip_screen(self, boxes: AuctionBoxes) -> bool:
        return bool(self.ocr(box=boxes.skip_area, match=RE_SKIP))

    def _dismiss_notice_popup(
        self,
        boxes: AuctionBoxes,
        deadline: float | None,
        reason: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        """点掉挡在流程前面的「提示」类弹窗, 命中返回 True。

        入场费确认、异常出价、满仓提示等弹窗共用一套模板, 确认按钮都在同一组坐标上,
        因此复用 exception_area 即可, 不需要为每个弹窗单独量框。
        单帧 ocr 会漏掉刚出现的弹窗(之后整条流程卡在弹窗上), 所以带短超时轮询;
        每次出价都要走一遍的热路径用 timeout 调小预算, 避免固定白等。
        """
        budget = self.NOTICE_POPUP_TIMEOUT if timeout is None else timeout
        budget = self._timeout_or_zero(deadline, budget)
        if budget <= 0:
            return False
        if not self.wait_ocr(
            box=boxes.exception_area,
            match=RE_CONFIRM,
            time_out=budget,
            raise_if_not_found=False,
            settle_time=0.3,
        ):
            return False

        self.log_info(f"检测到提示弹窗({reason}), 点击确认")
        self.operate_click(boxes.exception_area, after_sleep=0.3)
        return True

    # --- 超时辅助 ---
    @staticmethod
    def _remaining_timeout(deadline: float, limit: float, message: str = "单轮拍卖超时") -> float:
        """返回受 deadline 限制的等待时间, deadline 到期时立即失败。

        message 供「传进来的不是整轮 deadline 而是阶段 deadline」的调用点区分日志:
        匹配阶段的局部预算(MATCH_TIMEOUT)用尽时整轮往往还剩几百秒, 沿用「单轮拍卖
        超时」会让排查的人以为 600 秒跑满了。
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaitFailedException(message)
        return min(limit, remaining)

    def _bounded_timeout(self, deadline: float | None, limit: float) -> float:
        """在兼容无 deadline 调用的同时, 限制有 deadline 调用的等待时间。"""
        return limit if deadline is None else self._remaining_timeout(deadline, limit)

    @staticmethod
    def _timeout_or_zero(deadline: float | None, limit: float) -> float:
        """和 _bounded_timeout 类似, 但 deadline 用尽时返回 0 而不是抛异常。

        用于可选的观测步骤(满仓检测、弹窗兜底): deadline 用尽只意味着这次没测到,
        不应该让整个结算后处理崩掉。
        """
        if deadline is None:
            return limit
        return max(min(limit, deadline - time.monotonic()), 0.0)

    def _bounded_sleep(self, deadline: float | None, delay: float) -> None:
        """执行受 deadline 限制的短暂操作等待。"""
        self.sleep(self._bounded_timeout(deadline, delay))

    # --- 配置读取辅助 ---
    def _config_int(self, key: str, default: int = 0, *, warn: str | None = None) -> int:
        """读取整数配置, 非法时回退到默认值并可选输出告警。"""
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            if warn:
                self.log_warning(warn)
            return default

    def _config_float(self, key: str, default: float = 0.0) -> float:
        """读取浮点配置, 非法时回退到默认值。"""
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _config_decimal(self, key: str, default: str = "0") -> Decimal:
        """读取十进制配置, 非法时回退到默认值。

        用于参与 Decimal 运算的配置(加价数值): 配置原值本身就是文本框里的字符串,
        直接解析能保留用户填的全部精度, 而先经 float 中转再 str() 只剩 17 位有效数字。
        非法值一律回退默认值, 与 _config_float 一样不抛异常 —— 调用方另有兜底。
        """
        try:
            return Decimal(str(self.config.get(key, default)))
        except (ArithmeticError, ValueError):
            return Decimal(default)

    def _config_int_list(self, key: str) -> list[int]:
        """读取整数列表配置, 任一项非法时返回空列表。"""
        try:
            return [int(item) for item in self.config.get(key, [])]
        except (TypeError, ValueError):
            return []

    def _quality_selection(self) -> tuple[list[str], list[str]]:
        """读取「保留藏品品质」与「追加出售品质」, 非列表值一律按空列表处理。

        两个键都是多选框(配置值是列表), 但用户可能手工改成字符串或留下旧版脏值。
        清洗集中在这里: 调用方一律拿到列表, 不必各自判断类型。
        """
        keep = self.config.get(self.CONF_KEEP_QUALITIES, [])
        keep = list(keep) if isinstance(keep, (list, tuple)) else []
        extra = self.config.get(self.CONF_EXTRA_SELL_QUALITIES, [])
        extra = list(extra) if isinstance(extra, (list, tuple)) else []
        return keep, extra

    def _validate_price_config(self) -> None:
        """任务开始前校验价格相关配置, 非法时直接终止任务。

        出价面板打开后才发现非法配置, 会以每轮 3 次重试的方式空转, 必须在入口拦截。
        校验范围随出价模式变化, 未使用的价格配置不参与校验。
        """
        mode = self.config.get(self.CONF_BID_MODE, self.BID_MODE_CUSTOM)

        if mode == self.BID_MODE_LIST:
            prices = self._resolve_bid_prices()
            if not prices:
                raise ValueError(f"每轮指定价格未配置: {self.CONF_BID_PRICES[0]} 必须大于 0")
            self._validate_last_bid_is_higher(prices)
            return

        if mode == self.BID_MODE_ESTIMATE:
            ratio = self._config_float(self.CONF_ESTIMATE_RATIO, 0.0)
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError(
                    f"估价倍率必须为正数, 当前: {self.config.get(self.CONF_ESTIMATE_RATIO)!r}"
                )
            return

        base_raw = self.config.get(self.CONF_FIXED_PRICE)
        try:
            base_price = int(base_raw)
        except (TypeError, ValueError):
            raise ValueError(f"基础价配置非法: {base_raw!r}") from None
        if base_price <= 0:
            raise ValueError(f"基础价必须为正整数, 当前: {base_raw!r}")

        if self.config.get(self.CONF_AUTO_RAISE, False):
            raise_value = self._config_float(self.CONF_RAISE_VALUE, 0.0)
            if not math.isfinite(raise_value):
                raise ValueError(f"加价数值配置非法: {self.config.get(self.CONF_RAISE_VALUE)!r}")

    def _warn_if_no_sellable_quality(self) -> None:
        """保留品质覆盖全部品质时给出告警: 开了出售模式却没有可出售的品质。

        配置本身不非法(用户可以随时改), 所以只告警不拦截。但满仓时这种配置会让
        出售一直报「成功」却清不出空间, 之后每轮出价都失败, 提前说清楚更好排查。
        """
        if not self._uses_collection_sell():
            return

        keep, extra = self._quality_selection()

        # 追加出售的品质会覆盖保留列表, 所以只要有一个品质「该卖」就不算空配置.
        if any(name in extra or name not in keep for name in self.QUALITY_KEYS):
            return
        self.log_warning(
            f"「{self.CONF_KEEP_QUALITIES}」保留了全部品质且没有追加出售品质, "
            "本次运行不会清掉任何藏品"
        )

    def _warn_if_extra_sell_is_redundant(self) -> None:
        """「追加出售品质」里勾了不在「保留品质」里的品质时给出告警。

        追加只在「该品质本来要保留」时才改变行为: 不在保留列表里的品质本来就会出售,
        勾进追加列表等于没勾。而这个组合又很自然(看到「追加出售」就把低价值品质勾上),
        所以提前说清楚, 免得用户以为配置生效了。

        配置本身不非法(用户可能有意预留), 因此只告警不拦截。
        """
        if not self._uses_collection_sell():
            return

        keep, extra = self._quality_selection()
        keep = set(keep)

        # 只关心品质枚举里的项: 脏配置里的未知名称既不会生效, 也不该被拿出来说.
        redundant = [n for n in extra if n in self.QUALITY_KEYS and n not in keep]
        if not redundant:
            return
        self.log_warning(
            f"「{self.CONF_EXTRA_SELL_QUALITIES}」里的 "
            + ", ".join(redundant)
            + f" 不在「{self.CONF_KEEP_QUALITIES}」中, 本来就会出售, 追加设置对它们无效"
        )

    # --- 资产解析 ---
    @staticmethod
    def _parse_asset_value(raw_text: str) -> int | None:
        """统一解析资产 OCR 文本, 返回整数或 None。

        处理流程:
        1. 全角数字转半角数字.
        2. 修正常见 OCR 错误 (O -> 0, l/I -> 1).
        3. 提取数字.
        4. 转换为 int, 失败时返回 None.
        """
        normalized = raw_text.translate(FULLWIDTH_DIGITS)
        corrected = normalized.replace("l", "1").replace("I", "1").replace("O", "0")
        digits = re.sub(r"[^\d]", "", corrected)
        if not digits:
            return None

        try:
            return int(digits)
        except ValueError:
            return None

    @staticmethod
    def _is_partial_number_text(raw_text: str) -> bool:
        """判断 OCR 文本是否为「首位数字被漏读」的残缺读数。

        估价按千位分隔显示, 逗号前面必须有数字。首位数字在区域最左侧, OCR 对它的识别
        不稳定, 漏读时会剩下 ",544" 这种逗号前空着的文本 (线上日志 18:56 那局连着 9 次),
        它对应的真实值至少是 "x,544"。把它当结果会按低一个数量级的价格出价。
        """
        normalized = raw_text.translate(FULLWIDTH_DIGITS)
        digits_and_commas = re.sub(r"[^\d,]", "", normalized)
        return digits_and_commas.startswith(",")

    @staticmethod
    def _has_inconsistent_grouping(raw_text: str) -> bool:
        """判断带千位分隔符的读数是否「位数与逗号不自洽」。

        千位分隔的合法形式只有 `1,234` / `12,345` / `123,456` 这几种: 去掉逗号后
        长度必须满足 (len - 1) % 3 == 0 且首位分组不为空。`1,23` / `12,3,456` 这类
        不合法, 说明 OCR 丢了或多了字符。

        注意这条拦不住 `643`(无逗号, 天然自洽), 所以它只是辅助防线; 末位丢失主要靠
        `_read_estimate_value` 的「数字右端贴裁框边界」告警来发现。
        """
        normalized = raw_text.translate(FULLWIDTH_DIGITS)
        digits_and_commas = re.sub(r"[^\d,]", "", normalized)
        if "," not in digits_and_commas:
            return False
        groups = digits_and_commas.split(",")
        if groups[0] == "" or len(groups[0]) > 3:
            return False  # 首位分组缺失或超长, 由 _is_partial_number_text 或调用方处理
        return any(len(group) != 3 for group in groups[1:])

    def _read_estimate_texts(self, box: Box, timeout: float) -> list:
        """读区域内**全部**文本, 不做 match 过滤。

        必须用 `self.ocr(match=None)` 而不是 `wait_ocr(match=RE_NUMBER)`:
        框架 `OCR.wait_ocr` 的 `match` 不只是「找到了没有」的判定条件 —— `onnx_ocr` 里
        `detected_boxes = find_boxes_by_name(detected_boxes, match)` 会把返回值**过滤成
        只含命中的框**, 非命中文本(如「当前估价：」标签)在返回列表里根本不存在。

        2026-09-23 21:49 线上的故障正是这个: 日志里 `all_boxes: [当前估价：_0.99,
        20,930_1.00]` 说明区域内两个框都在, 但 `wait_ocr(match=RE_NUMBER)` 只返回
        `[20,930]`, 标签被过滤掉 -> `label_right is None` -> 每一帧都判「标签未读到」,
        估价永远读不出, 出价一直回退基础价 1。

        这里改用 `ocr()`(不是 wait)并传 `match=None` 拿全量结果, 标签和数字都在里面。
        取一帧即可: 调用方的稳定判定循环本来就会反复重读。
        """
        deadline = time.monotonic() + timeout
        while True:
            boxes = self.ocr(box=box, match=None, log=False)
            texts = [b for b in boxes if b.name] if boxes else []
            if texts or time.monotonic() >= deadline:
                return texts
            self.sleep(0.2)

    def _read_estimate_value(
        self, box: Box, timeout: float, label: str = "当前估价"
    ) -> tuple[int | None, bool]:
        """读估价数字: 先按「估价」标签定位, 再取标签右侧的数字。

        必须按标签过滤而不能只靠裁框宽度。拿到的框里有多个文本时, 只把「估价」标签
        右侧的接起来 —— 区域内混进第二个数字栏(如「我的资产」数值)时, 直接 `"".join()`
        会把两串数字粘成一个, 表现为「估价少了一位」(2026-09-23 截图: 2,643 读成 ,643、
        1,912 读成 912)。`BOX_ESTIMATE` 的右边界现在收到 0.9200, 已经把「我的资产」数值
        排除在区域外(见该常量注释), 但标签过滤仍是最后一道防线: 界面过渡帧里数字位置会
        偏移, 也可能混进别的小字。

        实测「估价」标签右边沿在 0.8438~0.8464, 数字在 0.8484~0.9052 (另一局 13,875 在
        0.8542~0.9010), 两者之间有明显空隙, 所以按 `x0 >= label_right` 过滤是稳定的。

        Returns:
            (值, 是否贴边). 贴边表示数字右端距裁框右边界不足 ESTIMATE_EDGE_MARGIN_RATIO
            对应的像素数, 该读数可能已被裁掉末位, 调用方应按不可信处理。
        """
        all_boxes = self._read_estimate_texts(box, timeout)
        if not all_boxes:
            return None, False

        label_right = max(
            (b.x + b.width for b in all_boxes if "估价" in b.name.replace("：", "")),
            default=None,
        )
        if label_right is None:
            # 标签没读出来: 不猜, 交给调用方重读一帧.
            self.log_debug(f"{label} 标签未读到, 本帧数字不可靠")
            return None, False

        candidates = [b for b in all_boxes if b.x >= label_right and RE_NUMBER.search(b.name)]
        if not candidates:
            return None, False

        digit_boxes = sorted(candidates, key=lambda b: b.x)
        raw_text = "".join(b.name for b in digit_boxes)
        if self._is_partial_number_text(raw_text):
            self.log_debug(f"{label} OCR: '{raw_text}', 千位分隔符前缺数字, 视为残缺读数")
            return None, False
        if self._has_inconsistent_grouping(raw_text):
            self.log_debug(f"{label} OCR: '{raw_text}', 千位分组不自洽, 视为残缺读数")
            return None, False

        value = self._parse_asset_value(raw_text)
        right_edge = max(b.x + b.width for b in digit_boxes)
        # 阈值随分辨率等比放大, 至少 1px: 高 DPI 下框宽不变(本项目 resize_image 为默认 0,
        # 截图不重采样)时小于 1px 的判定没有意义.
        margin = max(1, round(self.width * self.ESTIMATE_EDGE_MARGIN_RATIO))
        tight = (box.x + box.width - right_edge) < margin
        self.log_debug(f"{label} OCR: '{raw_text}', 解析值: {value}, 贴边: {tight}")
        return value, tight

    def _read_asset_value(
        self, box: Box, timeout: float, label: str = "资产", *, reject_partial: bool = False
    ) -> int | None:
        """对指定区域做 OCR 并解析资产数值, 未识别或解析失败时返回 None。

        reject_partial 用于估价这类会跳动、首位可能被漏读的区域: 千位分隔符前面空着的
        残缺读数按未读出处理, 交给调用方重读; 资产、出售价值这类稳定的数字保持原行为。
        """
        boxes = self.wait_ocr(
            box=box,
            match=RE_NUMBER,
            time_out=timeout,
            raise_if_not_found=False,
            settle_time=0.5,
        )
        if not boxes:
            return None

        raw_text = "".join(text_box.name for text_box in boxes)
        if reject_partial and self._is_partial_number_text(raw_text):
            self.log_debug(f"{label} OCR: '{raw_text}', 千位分隔符前缺数字, 视为残缺读数")
            return None

        value = self._parse_asset_value(raw_text)
        self.log_debug(f"{label} OCR: '{raw_text}', 解析值: {value}")
        return value

    # --- 自动加价计算 ---
    def _calculate_auction_price(
        self, boxes: AuctionBoxes | None = None, deadline: float | None = None
    ) -> int:
        """计算当前出价应该输入的价格。

        价格来源由出价模式决定:
        - 自定义价格: 基准价格为自定义价格, 启用自动加价时按模式计算加价结果;
          启用指定回合单独出价且当前序号在勾选列表中时直接使用该价格。
        - 每轮指定价格: 按出价序号从配置列表依次取用。
        - 按系统估价: 读取出价面板上的当前估价并乘以倍率。

        出价序号从 1 开始计数, 基于成功出价次数 + 1。
        """
        bid_count = self.current_bid_count + 1
        mode = self.config.get(self.CONF_BID_MODE, self.BID_MODE_CUSTOM)

        if mode == self.BID_MODE_LIST:
            return self._listed_bid_price(bid_count)
        if mode == self.BID_MODE_ESTIMATE:
            return self._estimate_bid_price(boxes, deadline, bid_count)

        base_price = self._config_int(self.CONF_FIXED_PRICE, 1)

        special_price = self._special_round_price(bid_count)
        if special_price is not None:
            return special_price

        if not self.config.get(self.CONF_AUTO_RAISE, False):
            return base_price

        return self._raise_price(base_price, bid_count)

    def _validate_last_bid_is_higher(self, prices: list[int]) -> None:
        """校验最后一次出价高于前一次。

        游戏中第 6 次出价必须高于第 5 次, 否则这一次出价会被系统拒绝;
        留 0 沿用上一次价格会导致两者相等, 所以第 6 次必须显式填写。
        """
        if len(prices) < 2:
            return

        last_key = self.CONF_BID_PRICES[len(prices) - 1]
        previous_key = self.CONF_BID_PRICES[len(prices) - 2]
        last, previous = prices[-1], prices[-2]
        if last > previous:
            return

        # 区分「没填」和「填小了」, 两种情况用户要做的修改不一样。
        if self._config_int(last_key, 0) <= 0:
            raise ValueError(
                f"{last_key} 未设置: 沿用上一次的价格 {previous} 不会高于 "
                f"{previous_key}, 请显式填写一个更大的值"
            )
        raise ValueError(f"{last_key} ({last}) 必须大于 {previous_key} ({previous})")

    def _resolve_bid_prices(self) -> list[int]:
        """把 6 个每轮指定价格解析成可按出价序号直接取用的列表。

        每个价格对应一次出价: 第 1 次用「第1次出价价格」, 第 2 次用「第2次出价价格」, 依此类推。
        未设置(0)的回合沿用上一次已设置的价格, 因此只填前几次也能正常工作。
        第 1 次出价必须有价格, 否则返回空列表, 由调用方按配置错误处理。
        """
        resolved: list[int] = []
        current = 0
        for key in self.CONF_BID_PRICES:
            price = self._config_int(key, 0)
            if price > 0:
                current = price
            resolved.append(current)

        if not resolved or resolved[0] <= 0:
            return []
        return resolved

    def _listed_bid_price(self, bid_count: int) -> int:
        """按出价序号取每轮指定价格, 出价次数超出配置项时沿用最后一次的价格。"""
        prices = self._resolve_bid_prices()
        if not prices:
            raise ValueError(f"每轮指定价格未配置: {self.CONF_BID_PRICES[0]} 必须大于 0")

        index = min(max(bid_count, 1), len(prices)) - 1
        price = prices[index]
        if bid_count > len(prices):
            # 游戏里一轮最多 6 回合出价, 走到这里说明出价次数与预期不符, 值得告警。
            self.log_warning(
                f"出价序号 {bid_count} 超出每轮指定价格的 {len(prices)} 次出价, "
                f"沿用最后一次价格 {price}"
            )
        else:
            self.log_info(f"第 {bid_count} 次出价使用指定价格 {price}")
        return price

    def _read_stable_asset_value(
        self,
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

        读数走 `_read_estimate_value`, 即「按估价标签右边沿取数字」。这解决的是本函数
        原来修不掉的那类残缺: 末位数字被裁框切掉时位数不变 (`2,643` 读成 `,643`,
        连逗号一起丢就成 `643`), 「位数不减少」这条防线对它完全无效。附带地, 按标签
        过滤也杜绝了「我的资产」数值被 `RE_NUMBER` 拼进来的污染。

        原有的「位数不减少」判定保留: 它对「数字滚动中途读到更短的值」仍然有效。
        贴边的读数按「可能被裁掉末位」处理 —— 不采信, 并**重置**已攒的连续计数,
        因为末位丢失后位数可能不变, 只有贴边这个几何信号能发现它; 连续观察到的贴边
        不能反过来抬高更早那次读数的可信度。

        `same` 统计的是「连续几帧没有带来新信息」, 其中可能**一帧有效读数都没有**(画面
        静止时 OCR 一直读不出), 所以它只能用来确认「画面不再变化」, 不能确认「读到的是
        完整数值」。返回值另加 `valid_reads >= required` 一道门槛: `valid_reads` 是
        「连续读到同一个完整数值」的最长连续帧数, 任何一个 `value is None` 的缺失帧
        都会把它归零。少了这道门槛, 单次有效读数后再来两帧 OCR 失败就能凑满 `same >= 3`,
        在 10 秒超时之前把那次未必是终值的读数当「稳定值」采信, 并直接拿去算出价。

        数字滚动本身也要时间: 实测中间值可以稳定停留到面板打开后 2.6 秒 (19:16 那局),
        所以「连续相同」还要叠加 ESTIMATE_MIN_OBSERVE_SECONDS 的最短观察窗口,
        否则会在滚动结束前就采信 (19:18 那局 2.63 秒返回, 拿到了残缺的 197)。
        超时仍未稳定时返回最后一次有效读数并告警, 让调用方拿到比回退值更接近真实的值。
        """
        required = max(self.ESTIMATE_STABLE_READS, 1)
        observe = self.ESTIMATE_MIN_OBSERVE_SECONDS
        deadline = time.monotonic() + timeout
        last: int | None = None
        same = 0
        first_seen: float | None = None
        zero_seen = False
        valid_reads = 0
        tight_seen = False

        while time.monotonic() < deadline:
            value, tight = self._read_estimate_value(
                box,
                min(deadline - time.monotonic(), self.ASSET_OCR_TIMEOUT),
                label,
            )
            now = time.monotonic()
            if tight:
                # 数字右端贴住裁框边界: 末位可能已被切掉且位数不变, 无法与正常读数区分,
                # 只能整帧判为不可信. 记下来, 超时时用它解释为什么没读到.
                #
                # 这类帧**不能**按「未读出」处理: 未读出只是没拿到新信息, 之前那次读数
                # 仍然成立, 所以可以继续累积 same; 而贴边是「当前帧的裁框已经不够用」的
                # 证据, 之前那个值是在旧帧上读的, 它的可信度不会因为又观察到几次贴边而上升.
                # 若按未读出累积 same, 连续贴边反倒会把旧值攒成「稳定值」返回 —— 正是本
                # 次要防的末位被裁故障, 而且 tight_seen 的告警分支(只在 last is None 时
                # 才走)永远不会触发, 贴边信号被静默吞掉.
                tight_seen = True
                same = 0
                first_seen = None
                # last / valid_reads 必须一起作废. 只清 same 和 first_seen 会留下两个漏洞:
                # last 仍在 -> 上面的提前稳判条件里 `last is not None` 恒为真, 而
                # `first_seen is not None` 因为被清空而恒为假, 于是提前稳判**永远不会**触发;
                # 循环只能走到超时兜底, 把贴边**之前**在旧帧上读到的值当稳定值返回.
                # 更糟的是超时分支的 tight_seen 告警要求 `last is not None` 之外的路径,
                # 一旦 last 非空就只报「未稳定, 使用最后一次读数」, 贴边这个关键信号被吞掉,
                # 排查时看不出这个价格其实来自一帧已被裁掉末位的旧读数.
                # valid_reads 同理: 它统计的是 last 那次读数的有效性, last 作废后计数也必须归零.
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
                self.log_info(f"{label}读数稳定: {last} (连续 {same} 次)")
                return last

            # 必须换一帧再读, 否则两次读取会落在同一帧上, 读到同样的中间值.
            self.next_frame()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                self.sleep(min(self.POLL_INTERVAL, remaining))

        if last is not None:
            self.log_warning(f"{label}在 {timeout} 秒内未稳定, 使用最后一次读数 {last}")
            if tight_seen:
                # 中途出现过贴边帧, 说明这段读数是在「有帧被裁掉末位」的干扰下得到的:
                # 贴边帧本身已被丢弃, 但同一屏的其它帧也可能同样不完整, 这个值只作参考.
                self.log_warning(
                    f"{label}观测期间出现过贴边读数, 该值可能不完整; "
                    f"若与实际不符, 请检查 "
                    f"{self.__class__.__name__}.BOX_ESTIMATE 右边界"
                )
        elif tight_seen:
            # last 为空只有两种成因: 从头到尾没读到, 或读到之后又被贴边帧作废.
            # 后者才是要提示用户去调裁框的情形, 文案要能同时覆盖.
            self.log_warning(
                f"{label}读数贴住识别区域边界(或其后读数不可信), 末位可能被裁掉, "
                f"视为未读出; 请检查 {self.__class__.__name__}.BOX_ESTIMATE 右边界"
            )
        elif zero_seen:
            self.log_warning(f"{label}在 {timeout} 秒内只读到 0, 视为未读出")
        return last

    def _estimate_bid_price(
        self, boxes: AuctionBoxes | None, deadline: float | None, bid_count: int
    ) -> int:
        """按出价面板上的当前估价乘倍率出价, 估价读不出时回退到基础价。

        估价区域被弹窗或动画遮挡时不应中断整场拍卖, 因此只告警并回退。
        估价数字会先跳动几次才稳定, 所以走稳定读取而不是单次 OCR。
        面板在滚出数字前会先显示 0, 所以用 skip_zero 把 0 当占位读数继续等,
        否则 0 会被 `is None` 之外的假值判断当成「识别失败」, 直接回退到基础价。
        """
        base_price = self._config_int(self.CONF_FIXED_PRICE, 1)
        estimate = None
        if boxes is not None:
            estimate = self._read_stable_asset_value(
                boxes.estimate,
                self._bounded_timeout(deadline, self.ESTIMATE_STABLE_TIMEOUT),
                "当前估价",
                skip_zero=True,
            )
        if estimate is None:
            self.log_warning(f"当前估价识别失败, 第 {bid_count} 次出价回退到基础价 {base_price}")
            return base_price

        ratio = self._config_float(self.CONF_ESTIMATE_RATIO, 1.0)
        result = Decimal(str(estimate)) * Decimal(str(ratio))
        final_price = int(result.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if final_price <= 0:
            self.log_warning(
                f"按估价 {estimate} 与倍率 {ratio} 计算出的价格 {final_price} 无效, "
                f"回退到基础价 {base_price}"
            )
            return base_price

        self.log_info(f"按系统估价出价: 当前估价 {estimate}, 倍率 {ratio}, 出价 {final_price}")
        return final_price

    def _special_round_price(self, bid_count: int) -> int | None:
        """启用指定回合单独出价时返回该回合价格, 否则返回 None。"""
        if not self.config.get(self.CONF_SPECIAL_ROUND, False):
            return None

        special_rounds = self._config_int_list(self.CONF_SPECIAL_ROUNDS)
        special_price = self._config_int(self.CONF_SPECIAL_ROUND_PRICE, 0)
        if special_price > 0 and bid_count in special_rounds:
            self.log_info(f"指定回合 {bid_count} 使用单独价格 {special_price}")
            return special_price
        return None

    def _raise_mode(self) -> str:
        """读取加价方式, 把旧版取值「倍数」归一到「倍率」。

        迁移会在加载时改写配置, 这里再兜一次: 配置文件读不到或用户手改回旧取值时,
        不能让判定串失配 —— 失配会静默落到「自定义」分支, 算出完全不同的价格。
        未知取值保持原有的兜底语义(按自定义处理), 不在这里改行为。
        """
        mode = self.config.get(self.CONF_RAISE_MODE, self.RAISE_MODE_MULTIPLE)
        if mode == self.LEGACY_RAISE_MODE_MULTIPLE:
            return self.RAISE_MODE_MULTIPLE
        return mode

    def _raise_price(self, base_price: int, bid_count: int) -> int:
        """按配置的加价方式计算第 bid_count 次出价的价格。

        全程用 Decimal 计算: 倍率模式是 `base * value ** offset`, 用原始 float 时
        「加价数值」填得稍大就会在 `value ** offset` 上抛 OverflowError(实测
        `100000 * 10.0 ** 400`)。异常会被 _stage_bid_loop 吞成「出价异常」重试,
        价格永远算不出来, 却看不到真正的原因。
        """
        mode = self._raise_mode()
        value = self._config_decimal(self.CONF_RAISE_VALUE, "0")
        raise_round = self._config_int(self.CONF_RAISE_ROUND, 0)

        # 在达到配置的加价回合前使用基础价.
        if raise_round > 0 and bid_count < raise_round:
            return base_price

        # 计算加价偏移次数, 从 1 开始.
        offset = bid_count if raise_round == 0 else bid_count - raise_round + 1

        # 根据所选方式计算价格.
        try:
            base = Decimal(str(base_price))
            if mode == self.RAISE_MODE_MULTIPLE:
                # 指数增长: 基础价 * (倍率 ^ offset).
                result = base * (value**offset)
            elif mode == self.RAISE_MODE_PERCENT:
                # 线性增长: 基础价 * (1 + 百分比 / 100 * offset).
                result = base * (Decimal(1) + value / 100 * offset)
            else:  # 自定义
                # 线性增长: 基础价 + 自定义值 * offset.
                result = base + value * offset
            # 量化必须留在 try 内: Decimal 的指数范围极大, `10 ** 400` 仍是有限值,
            # is_finite() 拦不住; 但它有 405 位有效数字, 超过默认上下文精度 28,
            # 到这一步 quantize 才抛 InvalidOperation。放在 try 外等于把
            # OverflowError 换成同样会漏出的 InvalidOperation。
            # 位数粗筛(整数部分 30 位以上)提前挡掉, 避免真的把大数交给 quantize。
            rounded = (
                result.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if result.adjusted() < 30
                else None
            )
            if rounded is None:
                result = None
            else:
                result = rounded
        except (ArithmeticError, InvalidOperation, ValueError):
            result = None

        # 溢出/精度异常时 result 为 None, 或退化成非有限值(NaN / Infinity).
        if result is None or not result.is_finite():
            self.log_warning(
                f"加价计算结果超出可表示范围, 回退到基础价 {base_price} "
                f"(模式 {mode}, 数值 {value}, 加价偏移 {offset})"
            )
            return base_price

        # 已在 try 内完成量化, 这里直接取整.
        final_price = int(result)

        # 确保计算结果为正整数.
        if final_price <= 0:
            self.log_warning(f"计算出的价格 {final_price} 无效, 回退到基础价 {base_price}")
            final_price = base_price

        self.log_info(
            f"自动加价计算: 基础价 {base_price}, 模式 {mode}, 数值 {value}, "
            f"出价序号 {bid_count}, 加价偏移 {offset}, 出价 {final_price}"
        )
        return final_price

    # --- 价格输入 ---
    def _input_fixed_price(
        self, boxes: AuctionBoxes, price: int | None = None, deadline: float | None = None
    ) -> None:
        """使用游戏内数字键盘输入价格, 支持上轮出价、00 和 0000 快捷按钮。

        输入失败时抛出异常, 由调用方重试本次出价。
        """
        if price is None:
            price = self._calculate_auction_price(boxes, deadline)

        price_str = str(price)
        if not price_str.isdigit() or price <= 0:
            raise ValueError(f"非法价格 '{price}'")
        if deadline is not None:
            self._remaining_timeout(deadline, 0.1)

        if self._can_reuse_last_bid(price):
            self.operate_click(boxes.last_bid, after_sleep=0.2)
            self.log_info(f"使用上轮出价快捷输入价格 {price}")
        else:
            self.operate_click(boxes.clear, after_sleep=0.3)
            self._press_price_digits(price_str, deadline)

        self._verify_input_price(boxes, price, deadline)
        self._confirm_bid_price(boxes, deadline)

        self.log_info(f"输入价格 {price}")
        self.last_bid_price = price

    def _can_reuse_last_bid(self, price: int) -> bool:
        """仅在未启用自动加价时复用上轮出价, 避免自动加价下快捷输入的不确定性。"""
        return (
            not self.config.get(self.CONF_AUTO_RAISE, False)
            and self.last_bid_price is not None
            and price == self.last_bid_price
        )

    def _press_price_digits(self, price_str: str, deadline: float | None) -> None:
        """按数字键盘逐键点击, 优先使用 0000 / 00 快捷键。"""
        for key in self._price_key_sequence(price_str):
            if deadline is not None:
                self._remaining_timeout(deadline, 0.1)
            self.operate_click(self.box_of_screen(*self.PAD_MAP[key]), after_sleep=0.2)

    @staticmethod
    def _price_key_sequence(price_str: str) -> list[str]:
        """把价格字符串切分为按键序列, 可一次输入的 0000 / 00 优先整体输入。

        按**最长优先**做前缀匹配, 而不是只在「剩余整串恰好等于快捷键」时才用:
        后者会把 `1000000` 切成 `1 0 0 0000`(4 键), 前缀匹配切成 `1 0000 00`(3 键),
        而每次点击都带 after_sleep —— 少按一键就少一次 0.2 秒的等待。
        """
        shortcuts = sorted(PAD_SHORTCUTS, key=len, reverse=True)
        keys: list[str] = []
        index = 0
        while index < len(price_str):
            for shortcut in shortcuts:
                if price_str.startswith(shortcut, index):
                    keys.append(shortcut)
                    index += len(shortcut)
                    break
            else:
                keys.append(price_str[index])
                index += 1
        return keys

    def _verify_input_price(self, boxes: AuctionBoxes, price: int, deadline: float | None) -> None:
        """校验数字面板显示的价格与目标价格一致, 不一致时抛出异常。

        价格区未输入时显示 "可输入范围0~<资产>" 提示文本, 视为未识别处理。

        键盘弹出后价格输入框移到了键盘右侧, BOX_PRICE_RESULT 那个矩形会落在提示文案
        「可输入范围0~<资产>」上, 于是键盘态下**永远**读到提示文案、永远判「未输入」
        (2026-09-23 实测)。所以两个区域都读: 命中提示文案或读不出时, 换用键盘态的
        输入框区域再试一次, 两个位置都没得到数字才算未输入。
        """
        raw_price = self._read_price_text(boxes.price_result, deadline)
        if not raw_price or RE_PRICE_HINT.search(raw_price):
            # 只有一种情况需要换区域重读: 读到的是提示文案。此时键盘态的实际输入框
            # 在别处。若两个区域都读到提示文案, 说明价格确实还没输入。
            keypad_text = self._read_price_text(boxes.price_result_keypad, deadline)
            if keypad_text and not RE_PRICE_HINT.search(keypad_text):
                raw_price = keypad_text

        if not raw_price:
            self.log_warning("输入价格结果未识别, 取消确认并重试当前出价")
            raise WaitFailedException("输入价格结果未识别")
        if RE_PRICE_HINT.search(raw_price):
            self.log_warning("价格区仍显示可输入范围提示, 视为未输入, 取消确认并重试当前出价")
            raise WaitFailedException("输入价格结果未识别")

        input_price = self._parse_asset_value(raw_price)
        self.log_debug(f"输入价格结果 OCR: '{raw_price}', 解析值: {input_price}")
        if input_price != price:
            self.log_warning(
                f"输入价格校验失败, 目标价格: {price}, 实际价格: {input_price}, "
                "取消确认并重试当前出价"
            )
            raise WaitFailedException("输入价格校验失败")

    def _read_price_text(self, box: Box, deadline: float | None) -> str:
        """读取价格区文本, 未识别时返回空串。"""
        price_boxes = self.wait_ocr(
            box=box,
            match=RE_NUMBER,
            time_out=3 if deadline is None else self._remaining_timeout(deadline, 3),
            settle_time=0.5,
            raise_if_not_found=False,
        )
        return "".join(text_box.name for text_box in price_boxes) if price_boxes else ""

    def _confirm_bid_price(self, boxes: AuctionBoxes, deadline: float | None) -> None:
        """点击确认出价, 并处理可能出现的异常确认框。"""
        confirmed = self._wait_operate_click(
            boxes.bid_confirm,
            RE_BID_CONFIRM,
            5 if deadline is None else self._remaining_timeout(deadline, 5),
            after_sleep=0.2,
        )
        if not confirmed:
            self.log_warning("确认出价点击超时, 准备重试当前出价")
            raise WaitFailedException("确认出价失败")

        # 检测是否出现异常确认框. 原来用单帧 ocr, 弹窗晚出现一点就漏掉, 之后整条流程卡住;
        # 改成带短超时轮询, 预算见 BID_NOTICE_POPUP_TIMEOUT.
        self._dismiss_notice_popup(
            boxes, deadline, "确认出价后", timeout=self.BID_NOTICE_POPUP_TIMEOUT
        )

    # --- 低保金 ---
    def _try_claim_welfare(self, boxes: AuctionBoxes, deadline: float | None = None) -> bool:
        """尝试领取每日低保金, deadline 为空时保持原有独立超时行为。

        低保金是可选的附加流程, 按钮未出现(如当日已领取)或弹窗异常时只跳过本次领取;
        只有单轮超时才向上传播, 避免拖垮已经成功的拍卖轮次。

        每日次数用尽(如 5/5)时界面仍会打开弹窗但没有领取按钮, 此时必须继续关闭弹窗,
        否则弹窗会一直盖住拍卖界面, 让后续所有阶段都识别不到。
        """
        try:
            self.log_info("执行低保金领取流程")
            if not self._wait_click_optional(
                boxes.welfare_btn, RE_WELFARE, deadline, 5, "低保金按钮"
            ):
                return False
            self._bounded_sleep(deadline, 0.5)

            if self._wait_click_optional(boxes.claim, RE_CLAIM, deadline, 5, "领取按钮"):
                self._bounded_sleep(deadline, 0.5)
                self.log_info("已点击领取按钮")
            else:
                self.log_info("未检测到领取按钮(今日次数可能已用尽), 直接关闭低保金弹窗")

            if not self._close_welfare_dialog(boxes, deadline):
                self.log_warning("低保金弹窗未关闭, 跳过本次领取的后续确认")
                return False

            self.log_info("低保金领取完成")
            return True
        except TaskDisabledException:
            raise
        except WaitFailedException:
            raise
        except Exception as e:
            self.log_warning(f"低保金领取失败: {type(e).__name__}: {e}")
            return False

    def _is_welfare_dialog_open(self, boxes: AuctionBoxes) -> bool:
        """检测低保金弹窗是否仍留在界面上。

        标题与取消按钮任一命中即认为弹窗存在, 避免只有其一被识别时误判为已关闭。
        """
        if self.ocr(box=boxes.welfare_dialog, match=RE_WELFARE):
            return True
        return bool(self.ocr(box=boxes.cancel, match=RE_CANCEL))

    def _close_welfare_dialog(self, boxes: AuctionBoxes, deadline: float | None) -> bool:
        """关闭低保金弹窗, 领取成功与否都必须执行。

        每日次数用尽时弹窗没有领取按钮, 只点领取的旧逻辑会把弹窗留在界面上,
        后续所有阶段的识别都会被挡住。这里以界面特征判定弹窗是否还在, 反复点击取消,
        直到弹窗消失或重试次数用尽。
        """
        for attempt in range(1, self.WELFARE_CLOSE_RETRIES + 1):
            if not self._is_welfare_dialog_open(boxes):
                self.log_info("低保金弹窗已关闭")
                return True

            self._wait_click_optional(boxes.cancel, RE_CANCEL, deadline, 3, "取消按钮")
            self._bounded_sleep(deadline, 0.5)

            if not self._is_welfare_dialog_open(boxes):
                self.log_info("低保金弹窗已关闭")
                return True

            self.log_warning(
                f"第 {attempt}/{self.WELFARE_CLOSE_RETRIES} 次点击取消后低保金弹窗仍未关闭"
            )

        self.log_warning("低保金弹窗多次尝试后仍未关闭")
        return False

    def _wait_click_optional(
        self,
        box: Box,
        match: re.Pattern,
        deadline: float | None,
        timeout: float,
        desc: str,
    ) -> bool:
        """等待并点击目标控件, 超时未出现时返回 False; deadline 到期仍会抛出单轮超时。"""
        clicked = self._wait_operate_click(box, match, self._bounded_timeout(deadline, timeout))
        if not clicked:
            self.log_warning(f"{desc}未出现, 跳过本次操作")
        return clicked

    def _wait_operate_click(
        self,
        box: Box,
        match: re.Pattern,
        timeout: float,
        *,
        after_sleep: float = 0,
        settle_time: float = 0.5,
    ) -> bool:
        """等待目标控件出现后点击, 并使用带光标还原的点击路径。

        框架的 wait_click_ocr 直接走 click_box, 不会保存和还原鼠标位置;
        后台执行时会把用户的鼠标留在游戏窗口内, 因此统一改用 operate_click。
        """
        found = self.wait_ocr(
            box=box,
            match=match,
            time_out=timeout,
            raise_if_not_found=False,
            settle_time=settle_time,
        )
        if not found:
            return False
        self.operate_click(found, after_sleep=after_sleep)
        return True

    # --- 藏品出售 ---
    def _extra_sell_qualities(self, state: PostRoundState) -> list[str]:
        """满仓或成功领取低保金时, 追加出售配置的品质。

        这些品质会覆盖「保留藏品品质」, 用于在满仓或领完低保后清掉占用仓位的藏品。
        """
        if not (state.inventory_full or state.welfare_claimed):
            return []

        _, qualities = self._quality_selection()
        selected = [name for name in qualities if name in self.QUALITY_KEYS]
        if selected:
            self.log_info("满足出售条件, 追加出售品质: " + ", ".join(selected))
        return selected

    def _sell_collections_on_interval(
        self,
        boxes: AuctionBoxes,
        deadline: float | None = None,
        state: PostRoundState | None = None,
    ) -> None:
        """按出售模式决定本轮是否出售藏品。

        「满仓时清理」只在主界面出现库存不足提示时出售; 「按间隔出售」每 N 轮出售一次,
        且间隔没到就满仓时提前出售(库存不足无法继续出价, 等间隔会卡住拍卖)。
        「不出售」与「拍卖成功一键出售」不走这里: 后者在结算界面就卖完了。

        仅应在拍卖结束回到主界面后调用, 否则仓库入口 OCR 无法命中。
        """
        if not self._uses_collection_sell():
            # 「不出售」不碰仓库; 「一键出售」在结算界面就卖完了(见 _sell_on_settlement_screen),
            # 与轮次末尾无关.
            return

        mode = self._sell_mode()
        sell_interval = 0
        if mode == self.SELL_MODE_INTERVAL:
            sell_interval = self._config_int(
                self.CONF_SELL_INTERVAL, 0, warn="出售间隔次数配置无效, 按满仓清理处理"
            )
            if sell_interval <= 0:
                # 间隔无效时退化成「满仓时清理」, 而不是直接不出售.
                self.log_warning("出售间隔次数未设置, 本次按满仓清理处理")

        state = state or PostRoundState()
        inventory_full = state.inventory_full
        if inventory_full is None:
            # 结算后观测没测出满仓结论(含当时 deadline 用尽)时在这里补测:
            # 本方法在轮次末尾调用, deadline 为空, 有完整的 INVENTORY_FULL_TIMEOUT 可用。
            inventory_full = self._detect_inventory_full(
                boxes, self._timeout_or_zero(deadline, self.INVENTORY_FULL_TIMEOUT)
            )

        reached_interval = sell_interval > 0 and self.current_round % sell_interval == 0
        if not (reached_interval or inventory_full):
            return

        if reached_interval:
            self.log_info(f"第 {self.current_round} 轮到达出售间隔 {sell_interval}, 执行定期出售")
        else:
            self.log_info("检测到满仓提示, 提前执行藏品出售")

        self._sell_collections_with_escalation(
            boxes,
            deadline,
            self._extra_sell_qualities(state),
            inventory_full=bool(inventory_full),
        )

    def _sell_collections(
        self,
        boxes: AuctionBoxes,
        deadline: float | None = None,
        extra_sell: list[str] | tuple[str, ...] = (),
        *,
        require_sale: bool = False,
    ) -> bool:
        """尝试出售藏品, deadline 为空时保持定期清理分支的原有行为。

        require_sale 表示本次出售必须真的清掉藏品(满仓时无法继续出价)。此时「一个品质
        都没勾上」不能再算成功 —— 那会把满仓标记清掉, 之后每轮出价都失败却不再重试清理。
        """
        self.log_info("开始执行藏品出售流程")
        try:
            # 满仓等提示弹窗会盖住仓库入口, 先兜掉再找入口.
            self._dismiss_notice_popup(boxes, deadline, "出售流程开始前")

            warehouse_button = self._wait_operate_click(
                boxes.warehouse_btn,
                RE_WAREHOUSE,
                self._bounded_timeout(deadline, self.WAREHOUSE_LOAD_TIMEOUT),
            )
            if not warehouse_button:
                self.log_warning("藏品仓库入口未出现, 取消出售流程")
                return False
            self._bounded_sleep(deadline, 1)
            self.log_info("藏品仓库入口已点击")

            if not self.wait_ocr(
                box=boxes.warehouse_title,
                match=RE_WAREHOUSE,
                time_out=self._bounded_timeout(deadline, self.WAREHOUSE_LOAD_TIMEOUT),
                raise_if_not_found=False,
                settle_time=0.5,
            ):
                self.log_warning("藏品仓库界面加载失败, 取消出售流程")
                return False
            self.log_info("藏品仓库界面加载完成")

            # 上一次出售中途失败会把仓库留在出售模式, 此时「出售」圆钮的位置是「取消」,
            # 再点一次会退出出售模式, 后续品质勾选与确认出售全部落空却不报错.
            if self._is_sell_mode(boxes, self._bounded_timeout(deadline, 1)):
                self.log_warning("藏品仓库已处于出售模式(上次出售未走完), 跳过点击出售")
            else:
                self.operate_click(boxes.sell, after_sleep=0)
                self._bounded_sleep(deadline, 1)

            selected = self._select_quality_filters(deadline, extra_sell)
            # 残留勾选会让这一遍无条件点击全部取反, _ensure_sell_value 读到 0 时会重勾一次兜住.
            sell_value = self._ensure_sell_value(boxes, deadline, selected, extra_sell)
            # 只有读到正数才算勾选生效: 读到 0 或读不出(界面重绘中的空白态)都不能算成功,
            # 否则会在毫无证据的情况下打印「藏品出售完成」, 掩盖「一个品质都没勾上」.
            selection_ok = self._is_selection_confirmed(
                selected, sell_value, require_sale=require_sale
            )

            self.operate_click(boxes.confirm_sell, after_sleep=0)
            self._bounded_sleep(deadline, 1.5)
            self.log_info("已点击确认出售")

            self.operate_click(boxes.blank, after_sleep=0)
            self._bounded_sleep(deadline, 0.5)
            self._close_warehouse(boxes)
            self._bounded_sleep(deadline, 1)

            if selection_ok is None:
                self.log_warning("出售价值未读出, 本次出售是否清掉藏品无法确认")
                return False
            if not selection_ok:
                if selected <= 0:
                    self.log_warning("没有勾选任何品质, 本次出售没有清掉任何藏品")
                else:
                    self.log_warning("品质勾选未生效, 本次出售没有清掉任何藏品")
                return False
            if selected <= 0:
                self.log_info("没有需要出售的品质, 本次出售未清掉藏品")
                return True
            self.log_info("藏品出售完成")
            return True
        except TaskDisabledException:
            raise
        except WaitFailedException:
            # 单轮超时要向上传播, 但界面得收拾干净再走: 把仓库连同「出售模式 + 已勾选
            # 的品质」留给下一轮, 下次进来会检测到「已在出售模式」而跳过点「出售」,
            # 然后无条件再点一遍同一批品质 —— 全部取反成未勾选, 满仓放宽时还会连带
            # 卖掉用户明确要保留的品质。关掉仓库是唯一不需要勾选态素材就能复位的动作.
            self._close_warehouse(boxes)
            raise
        except Exception as e:
            self.log_warning(f"藏品出售失败: {type(e).__name__}: {e}")
            self._close_warehouse(boxes)
            return False

    def _close_warehouse(self, boxes: AuctionBoxes) -> None:
        """关掉藏品仓库界面, 让出售模式和里面的勾选状态一起复位。

        出售流程无论成功还是异常退出都要走到这一步。关闭按钮是无文字图标(OCR 在所有
        截图上都读到空), 只能按调用点确认含义; 在主界面点它是空操作, 所以异常发生在
        「还没打开仓库」的阶段时也安全。

        点完要确认仓库真的关了(标题消失)再重试一次: 仓库连同「出售模式 + 已勾选的品质」
        留给下一轮时, 下次进来会检测到「已在出售模式」而跳过点「出售」, 然后无条件再点
        一遍同一批品质 —— 全部取反成未勾选, 满仓放宽时还会连带卖掉用户明确保留的品质。
        关不掉时只告警不抛: 这里是异常收尾路径, 再抛异常会盖掉真正的失败原因。
        """
        for attempt in range(1, self.WAREHOUSE_CLOSE_RETRIES + 1):
            self.operate_click(boxes.close, after_sleep=0.5)
            if not self._is_warehouse_open(boxes):
                return
            self.log_warning(
                f"第 {attempt}/{self.WAREHOUSE_CLOSE_RETRIES} 次点击关闭后藏品仓库仍未收起"
            )
        self.log_warning("藏品仓库界面多次尝试后仍未关闭, 下一轮可能受残留勾选影响")

    def _is_warehouse_open(self, boxes: AuctionBoxes) -> bool:
        """检测藏品仓库界面是否还在, 复用标题区域的 OCR。"""
        return bool(
            self.ocr(box=boxes.warehouse_title, match=RE_WAREHOUSE, log=False)
        )

    @staticmethod
    def _is_selection_confirmed(
        selected: int, sell_value: int | None, *, require_sale: bool = False
    ) -> bool | None:
        """判断品质勾选是否被出售价值证实。

        Args:
            selected: 实际点击勾选的品质数量.
            sell_value: 读到的出售价值, None 表示读不出.
            require_sale: 本次出售是否必须真的清掉藏品(满仓时无法继续出价).

        Returns:
            True: 读到正数, 勾选确实生效; 或本来就没有要出售的品质且不要求出售.
            False: 读到了 0; 或要求出售却一个品质都没勾上.
            None: 读不出(界面重绘中的空白态), 无法判断 —— 调用方必须按「未确认」处理.
        """
        if selected <= 0:
            # 没有勾选任何品质: 本来就不该清掉藏品, 但要求出售时不能算成功 ——
            # 否则会把满仓标记清掉, 仓库一件没腾却报告成功, 之后每轮出价都失败.
            return not require_sale
        if sell_value is None:
            return None
        return sell_value > 0

    def _sell_collections_with_escalation(
        self,
        boxes: AuctionBoxes,
        deadline: float | None,
        extra_sell: list[str] | tuple[str, ...],
        *,
        inventory_full: bool,
    ) -> bool:
        """执行藏品出售, 连续失败且满仓时放宽保留品质再试一次。

        满仓卖不掉会让后续出价全部失败, 所以连续失败后优先把仓库腾空,
        不再保留配置里指定的品质。成功一次就清零计数。

        非满仓的失败多半是界面重绘导致的读数抖动, 此时放宽会白白卖掉用户明确要
        保留的品质, 而收益为零 —— 所以只在满仓时放宽, **也只在满仓失败时累积计数**。

        计数必须按「满仓失败」累积, 不能让非满仓失败把它填满: 阈值是「满仓连续失败
        几次后放宽」, 若非满仓的抖动也计入, 阈值会被历史抖动提前填满, 之后满仓的
        第一次失败就立刻放宽, 把用户明确保留的品质一起卖掉(实测: 非满仓失败 5 次后,
        紧接一次满仓失败即触发, escalated 集合是 6 个品质全卖)。

        出售超时(预算耗尽, _sell_collections 抛 WaitFailedException)**不**计入失败:
        超时点无法区分是在「确认出售」之前还是之后 —— 收尾的 _bounded_sleep 在点完
        confirm_sell 之后也会抛, 那次出售可能已经生效。把这种「结果未知」当成满仓失败
        会连累两处: 放宽品质(可能卖掉用户明确保留的品质)被提前触发, 且 _inventory_stuck
        一旦被误置, 下一轮会直接跳过拍卖并记一次失败(见 _run_single_round), 仓库其实
        已空时还会反复触发。所以只有拿到「读数为 0 / 未勾选」这类明确失败证据才累积计数。

        计数达到阈值后以放宽集合开局(use_escalated): 否则第一次调用就超时的话,
        放宽分支永远走不到。
        """
        escalated = sorted(set(extra_sell) | set(self.QUALITY_KEYS))
        # 已经达到放宽阈值时直接用放宽集合开局: 满仓耗尽 SELL_TIMEOUT 会让第一次调用就抛
        # 异常, 永远走不到下面的放宽分支, 计数累到阈值也没有用.
        use_escalated = inventory_full and self._sell_failures >= self.SELL_FAILURE_ESCALATE_AFTER
        if use_escalated:
            self.log_warning(f"藏品出售已连续 {self._sell_failures} 次未完成, 直接放宽保留品质")

        try:
            sold = self._sell_collections(
                boxes,
                deadline,
                escalated if use_escalated else extra_sell,
                require_sale=inventory_full,
            )
        except WaitFailedException as e:
            # 结果未知, 不动计数也不置 _inventory_stuck: 收尾的 _bounded_sleep 在点完
            # confirm_sell 之后也会抛, 那次出售可能已经生效. 误置 _inventory_stuck 会让
            # 下一轮跳过拍卖并记一次失败, 而仓库其实已空时还会反复触发.
            self.log_warning(f"藏品出售超出预算, 结果未知, 不计入放宽计数: {e}")
            raise
        if sold:
            self._sell_failures = 0
            self._inventory_stuck = False
            return True

        if not inventory_full:
            # 非满仓失败只是读数抖动, 不为将来的满仓放宽积攒「信用」.
            self.log_warning("藏品出售未完成 (非满仓, 不计入放宽计数)")
            self._inventory_stuck = False
            return False

        self._sell_failures += 1
        if self._sell_failures < self.SELL_FAILURE_ESCALATE_AFTER or use_escalated:
            # 本次已经是放宽后的尝试, 不再重复放宽一次.
            self.log_warning(f"藏品出售未完成 (满仓连续 {self._sell_failures} 次)")
            self._inventory_stuck = inventory_full
            return False

        self.log_warning(f"藏品出售连续 {self._sell_failures} 次未完成, 放宽保留品质重试一次")
        try:
            escalated_sold = self._sell_collections(
                boxes, deadline, escalated, require_sale=True
            )
        except WaitFailedException as e:
            # 同上一处: 放宽后的这次出售是否生效同样无法确认, 保持计数与 _inventory_stuck
            # 不变, 交给下一轮的实测结论决定.
            self.log_warning(f"放宽保留品质后的出售超出预算, 结果未知: {e}")
            raise
        if escalated_sold:
            self.log_info("放宽保留品质后出售成功")
            self._sell_failures = 0
            self._inventory_stuck = False
            return True

        self.log_warning("放宽保留品质后出售仍未成功, 满仓会导致后续出价失败")
        self._inventory_stuck = inventory_full
        return False

    def _select_quality_filters(
        self,
        deadline: float | None,
        extra_sell: list[str] | tuple[str, ...] = (),
    ) -> int:
        """勾选需要出售的品质按钮, 跳过配置中保留的品质, 返回实际点击次数。

        extra_sell 中的品质即使被配置保留也会出售, 用于满仓或领完低保后的追加清理。

        本方法是「无条件点击」: 对同一个品质调用两次会把刚勾上的状态点掉。所以它只在
        进入出售模式后调用一次, 校验读数时不能靠再调一次来重试(那是双重取反)。
        """
        keep = set(self._quality_selection()[0])
        extra = set(extra_sell)
        clicked = 0

        for quality_name, quality_pos in zip(self.QUALITY_KEYS, self.QUALITY_BOXES):
            # 只有「本来要保留、这次被追加出售」的品质才值得单独说一句. 其余品质本来就会
            # 出售, 在那里报「追加」会让日志与行为不符 —— 用户会以为配置起了作用.
            overridden = quality_name in extra and quality_name in keep
            if quality_name in keep and not overridden:
                self.log_info(f"保留{quality_name}")
                continue
            if overridden:
                self.log_info(f"条件触发, 追加出售{quality_name}")
            else:
                self.log_info(f"选择{quality_name}")

            self.operate_click(self.box_of_screen(*quality_pos), after_sleep=0)
            self._bounded_sleep(deadline, self.SELL_QUALITY_GAP)
            clicked += 1

        return clicked

    def _is_sell_mode(self, boxes: AuctionBoxes, timeout: float) -> bool:
        """检测藏品仓库是否已经处于出售模式。

        出售模式下才会出现「出售价值」条, 用它区分初始视图和出售模式;
        初始视图的同一位置是空网格, OCR 不会命中, 因此读不到就按未进入处理。
        """
        return bool(
            self.wait_ocr(
                box=boxes.sell_label,
                match=RE_SELL_LABEL,
                time_out=timeout,
                raise_if_not_found=False,
                settle_time=0.5,
            )
        )

    def _ensure_sell_value(
        self,
        boxes: AuctionBoxes,
        deadline: float | None,
        selected: int,
        extra_sell: list[str] | tuple[str, ...] = (),
    ) -> int | None:
        """校验品质勾选是否真的生效: 读数没到位时先换帧重读, 仍是 0 才重勾一次。

        品质圆点每点一次界面都会重绘, 间隔太短时后续点击会落空, 表现为只卖掉一种品质,
        而流程依旧会点确认出售并报告成功。这里读「出售价值」来验证: 读到正数说明生效。

        读到 0 和读不出要分开处理, 两者的成因不同:

        - **读不出**(界面重绘期间的空白态, 线上 4 次出售有 3 次是这样)只换帧重读,
          绝不能重勾 —— 重勾会把刚勾上的品质点掉(双重取反), 让「重试」必然失败;
        - **换帧后仍是 0**, 说明这一遍点击之后真的一个品质都没勾上。干净的初始视图点完
          目标集合 T 之后价值必然为正, 所以这种情况多半是仓库里本来就有残留勾选: 上一次
          出售在点「确认出售」之前异常退出, 品质圆点还留在勾选态, 这一遍无条件点击恰好
          把它们全部点掉。此时再点一遍 T 能把状态拉回来 —— 设点击前集合为 S, 则
          「点完 T 后价值为 0」等价于 S 与 T 的对称差里没有一件有藏品的品质, 于是重勾后
          留下的 S 中「有藏品」的部分全部落在 T 内, 只会卖掉本该出售的品质。

        Returns:
            读到正数时返回该数值; 其余情况返回最后一次读数(0 或 None), 由调用方判定。
        """
        if selected <= 0:
            # 没有任何品质需要出售, 不必校验, 也不必花时间读数值.
            return None

        retries = max(self.SELL_SELECT_RETRIES, 1)
        value: int | None = None
        for attempt in range(retries):
            if attempt > 0:
                # 必须换帧: 两次读取落在同一帧上会读到同样的空白值.
                self.next_frame()
                self._bounded_sleep(deadline, self.SELL_QUALITY_GAP)
            value = self._read_sell_value(
                boxes, self._bounded_timeout(deadline, self.SELL_VALUE_TIMEOUT)
            )
            if value is None:
                # 界面重绘中的空白态, 重读一次再放弃.
                self.log_warning("出售价值未读出, 无法确认品质勾选是否生效")
                continue
            if value > 0:
                self.log_info(f"品质勾选生效, 出售价值 {value}")
                return value
            self.log_warning(f"已勾选 {selected} 个品质但出售价值为 {value}, 换帧后重读")

        if value == 0:
            # 换帧重读后仍是 0, 才按「残留勾选被这一遍点掉」处理. 只重勾一次:
            # 若本来就是干净的初始视图(目标品质都没藏品), 重勾得到空集, 与不重勾
            # 的结果一样(都卖不掉), 不会更糟; 无限重试没有意义.
            self.log_warning("换帧重读后出售价值仍为 0, 按残留勾选被点掉处理, 重新勾选一次")
            self._select_quality_filters(deadline, extra_sell)
            value = self._read_sell_value(
                boxes, self._bounded_timeout(deadline, self.SELL_VALUE_TIMEOUT)
            )
            if value is None:
                self.log_warning("重新勾选后出售价值未读出, 本次出售是否生效无法确认")
            elif value <= 0:
                self.log_warning(f"重新勾选后出售价值仍为 {value}, 本次出售不会清掉任何藏品")
            else:
                self.log_info(f"重新勾选后品质勾选生效, 出售价值 {value}")
        return value

    def _read_sell_value(self, boxes: AuctionBoxes, timeout: float) -> int | None:
        """读取出售模式下的「出售价值」数值, 未识别或解析失败时返回 None。"""
        return self._read_asset_value(boxes.sell_value, timeout, "出售价值")

    # --- 表情包 ---
    def _send_emote(self) -> None:
        """发送表情菜单中的第一个表情。"""
        self.log_info("发送表情包")
        self.operate_click(*self.EMOTE_BTN, after_sleep=0.8)
        self.operate_click(*self.EMOTE_FIRST, after_sleep=0.5)
        self.log_info("表情包发送完成")
