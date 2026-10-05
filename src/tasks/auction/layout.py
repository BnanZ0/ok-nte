"""拍卖界面契约: OCR 正则, 界面状态类型与 UI 区域常量。

本模块只承载纯声明, 不依赖任务类。坐标一律是相对屏幕比例, 区域经
AuctionBoxes 一次性构造后传给流程方法 (见 AutoBidAuctionTask._build_boxes),
不要将 OCR 裁框混入 src/scene/PanelPosition.py 或通用 PositionMap。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ok import Box

# --- 拍卖界面 OCR 正则 ---
RE_MATCH = re.compile(r"开始匹配|开始|匹配")
RE_CONFIRM = re.compile(r"确\s*认")
RE_BID = re.compile(r"出\s*价")
# 数字键盘弹出后的出价界面判定: 键盘弹窗会盖住 BOX_BID 所在框, 只靠 RE_BID 会
# 空转到超时, 所以收弹窗上的固定文案作为补充特征 (背景见 auction-notes 2.7)。
RE_BID_PANEL = re.compile(r"出\s*价|请输入你愿意出的价格|推荐出价参考|上轮出价|清空")
RE_SKIP = re.compile(r"跳\s*过")
RE_EXIT = re.compile(r"退\s*出")
RE_BID_CONFIRM = re.compile(r"确认出价")
RE_BID_PANEL_READY = re.compile(r"确认出价|[0-9]")
RE_NUMBER = re.compile(r"[0-9\uff10-\uff19,]+")
# 结算面板成交价值的标签关键词: 标签文案未实测, 放宽匹配, 读不出标签的帧按
# 未读出处理 (见 auction-notes 2.8)。
RESULT_VALUE_LABELS = ("估价", "价值", "成交")
# 价格输入区未输入时显示 "可输入范围0~<资产>" 提示, 同样能被 RE_NUMBER 命中, 不能当作价格.
RE_PRICE_HINT = re.compile(r"[~\uff5e\u4e00-\u9fff]")
# 从提示文本提取输入上限 N: 取 ~(含全角)后的数字组。N 是游戏实时给出的可输入
# 上限(即当前资产), 与资产框读数互为独立读数源, 供出价钳制交叉复核 (见
# auction-notes 5.1); 提示本身仍按 RE_PRICE_HINT 排除在价格读数之外。
# 数字组必须连全角逗号一起捕: 截断在 ~ 后第一段就断了, parse_asset_value 的
# 全角归一化没有机会处理捕不到的字符 (同 2.4 全角逗号洗掉防线的教训)。
RE_INPUT_RANGE = re.compile(r"[~\uff5e]\s*([0-9\uff10-\uff19,\uff0c]+)")
RE_MAIN_TITLE = re.compile(r"即刻落槌")
RE_COLLECTION_INSUFFICIENT = re.compile(r"少于200格")
RE_WELFARE = re.compile(r"低保金")
# 弹窗正文「今日已领取次数：N/5」。这是「今日低保是否领完」的权威读数, 直接决定
# 能不能放开出售, 因此不再依赖「弹窗里还有没有领取按钮」这类间接信号。
RE_WELFARE_COUNTER = re.compile(r"次数\s*[：:]\s*([0-9\uff10-\uff19]+)\s*/\s*([0-9\uff10-\uff19]+)")
RE_CLAIM = re.compile(r"领取")
RE_CANCEL = re.compile(r"取消")
RE_WAREHOUSE = re.compile(r"藏品仓库")
RE_SELL_LABEL = re.compile(r"出售\s*价值")
# 结算界面右下角游戏自带的「一键出售」圆钮(图标 + 文字), 拍卖成功后才出现.
# 首字「一」单笔画易被检测丢字, 两种写法都收 (见 auction-notes 2.5)。
RE_ONE_CLICK_SELL = re.compile(r"一键出售|键出售")
# 一键出售后弹出的「获得物品」提示条, 底部写着这句, 点提示条以外的空白区域即可关闭.
# 只匹配前半句: 整句较长, 尾部被 OCR 认坏时仍要能命中.
RE_POPUP_CLOSE_HINT = re.compile(r"点击空白")
# 掉线回场路径上的两个标志: 「都市闲趣」MENU 面板标题, 以及拍卖入口卡片「即刻落槌」。
RE_CITY_FUN = re.compile(r"都市闲趣")
# 拍卖主界面右侧的会场文字, 形如「当前：海贝场」。回场后用它核对会场有没有被重置。
RE_CURRENT_VENUE = re.compile(r"当前")
# 从「当前：XXX场」整行里提取会场名(如「海贝场」「珊瑚场」), 供期望会场校验与
# 切换会场后的留痕使用。读不出场名时回落 RE_CURRENT_VENUE 保留旧行为(原文留痕)。
RE_VENUE_NAME = re.compile(r"当前\s*[：:]\s*([\u4e00-\u9fff]{1,6}场)")

# --- 仪器组 ---
# 「仪器组合」弹窗标题与内容区关键词; 装备的目标组名来自「仪器组」配置, 在
# assist 里按 re.escape 动态编译, 不是固定正则。底部按钮三态(实机 85/86.png):
# 免费「使用」; 付费「购买并使用」(无库存)与「一键补全」(已用完), 点击后都会
# 弹「费用无法退回」确认框, 由 assist 点「确认」; 判定先查付费再查使用
# (「购买并使用」包含「使用」二字)。
RE_INSTRUMENT_COMBO = re.compile(r"仪器组合")
RE_COMBO_PAID_BUTTON = re.compile(r"购买并使用|一键补全")
# 出价面板「仪器」入口按钮与拍卖内「仪器列表」弹窗标题共用: 两处文案都含
# 「仪器」, 区域互不重叠, 宽匹配容忍 OCR 丢字 (见 auction-notes 7)。
RE_INSTRUMENT = re.compile(r"仪\s*器")

# 全角数字与全角逗号统一转半角, 用于统一资产与价格的 OCR 文本。
# 逗号必须一起转, 否则残缺读数的两条防线同时失效 (见 auction-notes 2.4)。
FULLWIDTH_NUMERIC = str.maketrans("０１２３４５６７８９\uff0c", "0123456789,")

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
    bid: Box
    bid_keypad: Box
    skip_area: Box
    exit: Box
    bid_confirm: Box
    abandon: Box
    asset_value: Box
    estimate: Box
    last_bid: Box
    clear: Box
    price_result: Box
    price_result_keypad: Box
    main_title: Box
    main_asset: Box
    insufficient: Box
    welfare_btn: Box
    welfare_dialog: Box
    welfare_counter: Box
    cancel: Box
    # 阻塞弹窗右按钮(确认/领取/放弃确认)统一区。
    popup_ok: Box
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
    # 结算面板成交价值区; 永恒之心展柜区。
    result_value: Box
    heart_area: Box
    # 主界面装备仪器组卡片; 「仪器组合」弹窗各区域.
    instrument_card: Box
    instrument_combo_title: Box
    instrument_combo_list: Box
    instrument_combo_names: Box
    instrument_combo_content: Box
    instrument_combo_use: Box
    instrument_combo_close: Box
    # 出价面板「仪器」入口按钮; 拍卖内「仪器列表」弹窗的标题与五个槽位行.
    instrument_entry: Box
    instrument_list_title: Box
    instrument_list_rows: tuple[Box, ...]


@dataclass(frozen=True)
class PostRoundState:
    """结算后回到主界面时的观测结果, 供轮次末尾的出售决策复用。

    inventory_full 为 None 表示本轮未检测满仓状态, 需要时由调用方补测。
    result_value 为 None 表示本轮未观测到成交价值(结算面板读数失败, 或走了
    「返回匹配界面」这条没有结算面板的路径); 明确的 0 表示读到了「未成交」,
    两者不能混同 —— 未成交计数只采信明确的 0。未识别到「一键出售」的读数
    也按原始值写回(不计成交, 见 auction-notes 4): 本字段只是结算面板读数
    留档, 非零不再能单独证明成交。
    observed 表示本轮是否真的执行过结算后观测: _finish_auction 在主界面标题没
    识别到时会提前返回, 此时画面状态未知, 轮次末尾不能再按「已回到主界面」去
    点仓库入口, 否则只会在错误的界面上白等超时。
    """

    inventory_full: bool | None = None
    observed: bool = False
    result_value: int | None = None


# --- UI 坐标 (相对比例) ---
# 主界面按钮.
BOX_MATCH = (0.7427, 0.8972, 0.8360, 0.9472)  # 开始匹配
BOX_BID = (0.882, 0.913, 0.930, 0.953)  # 出价按钮
# 数字键盘弹窗的文字区。只取弹窗右侧的文字部分(「请输入你愿意出的价格」等),
# 避开数字键, 用于键盘态下的界面判定 (盖住 BOX_BID 的背景见 auction-notes 2.7)。
BOX_BID_KEYPAD = (0.578, 0.560, 0.800, 0.720)  # 键盘弹窗文字区
BOX_SKIP_AREA = (0.703, 0.902, 0.807, 0.953)  # 跳过区域
BOX_EXIT = (0.853, 0.900, 0.961, 0.949)  # 退出拍卖
BOX_BID_CONFIRM = (0.649, 0.868, 0.726, 0.911)  # 确认出价

# 出价面板.
BOX_ABANDON = (0.7276, 0.9083, 0.7833, 0.9583)  # 放弃按钮
BOX_ASSET_VALUE = (0.8583, 0.0426, 0.9870, 0.0806)  # 出价面板资产
# 出价面板当前估价。
# 右边界 0.9520 的取舍依据(千万级 10 字符读数右端 ~0.943 不贴边 / 不圈进右端
# 0.9594 的「我的资产」/ 亿级 11 字符放不下是已知上限)见 auction-notes 3;
# 数字的实际筛选以「估价」标签右边沿为准。
BOX_ESTIMATE = (0.7780, 0.1330, 0.9520, 0.1820)  # 出价面板当前估价, 不覆盖我的资产

BOX_LAST_BID = (0.473, 0.733, 0.546, 0.807)  # 上轮出价
BOX_CLEAR = (0.488, 0.859, 0.533, 0.917)  # 清除按钮
# 键盘未弹出时的「可输入范围0~N」提示区, 用于判断价格是否尚未输入.
BOX_PRICE_RESULT = (0.588, 0.685, 0.783, 0.747)  # 输入价格结果
# 键盘弹出后价格输入框移到键盘右侧, 校验优先用这个区域, 否则会一直读到提示文案
# 而报「未输入」(见 auction-notes 5.1)。
BOX_PRICE_RESULT_KEYPAD = (0.588, 0.665, 0.790, 0.700)  # 键盘弹出后的输入价格结果

# 主界面 / 结算.
# 主界面左上角标题「即刻落槌」, 与藏品仓库标题同一个槽位(界面切换后文字才变),
# 因此坐标与 BOX_WAREHOUSE_TITLE 一致.
BOX_MAIN_TITLE = (0.058, 0.032, 0.130, 0.081)  # 主界面标题: 即刻落槌
BOX_MAIN_ASSET = (0.670, 0.025, 0.830, 0.095)  # 主界面资产数值
BOX_INSUFFICIENT = (0.240, 0.467, 0.747, 0.536)  # 库存不足提示

# 结算面板的成交价值区, 与出价面板估价区是同一段屏幕位置; 坐标跟随 BOX_ESTIMATE
# (含 0.9520 右边界放宽, 成交价值同样可能超百万), 布局如有出入按实机日志微调
# (参考实现来源见 auction-notes 3)。
BOX_RESULT_VALUE = (0.7780, 0.1330, 0.9520, 0.1820)

# --- 永恒之心 ---
# 出价面板展柜区域, 换算自参考实现 px(1302,196)-(1891,787): 心形道具出现时
# 该区域内会有一块深紫红色高亮, 用颜色像素占比判定(阈值见 auction/assist.py)。
BOX_HEART_AREA = (0.678, 0.181, 0.985, 0.729)

# --- 仪器组 (实机截图标定, 来源见 auction-notes 3) ---
# 主界面右下「当前装备仪器组」卡片的标题行: 至尊仪器组/超级估值仪器组等组名
# 在两种实测状态下都完整落框。
BOX_INSTRUMENT_CARD = (0.636, 0.708, 0.745, 0.755)
# 「仪器组合」弹窗: 标题 / 左侧组列表 / 组名窄条 / 右侧选中组的内容列表 /
# 底部「使用(购买并使用)」按钮 / 右上角关闭按钮。
BOX_INSTRUMENT_COMBO_TITLE = (0.039, 0.125, 0.133, 0.178)
BOX_INSTRUMENT_COMBO_LIST = (0.036, 0.215, 0.234, 0.824)
# 组列表里只圈组名文字的窄条, 供找组 OCR 用: 组名是卡片左上角的大字, 止于
# ~0.133, 右侧「剩余: N」起于 ~0.167 (1080p 实测 ok_templates/56.png, 见
# auction-notes 3); 收窄后读数只剩组名与描述行开头, 不含「剩余」读数。
BOX_INSTRUMENT_COMBO_NAMES = (0.036, 0.215, 0.150, 0.824)
BOX_INSTRUMENT_COMBO_CONTENT = (0.266, 0.231, 0.565, 0.824)
BOX_INSTRUMENT_COMBO_USE = (0.305, 0.859, 0.461, 0.924)
BOX_INSTRUMENT_COMBO_CLOSE = (0.549, 0.116, 0.591, 0.181)
# 组列表滚动落点 (列表中线), 与回场路径同款滚轮参数见 auction/assist.py。
POS_INSTRUMENT_COMBO_SCROLL = (0.135, 0.519)

# --- 拍卖内「仪器列表」(出价面板仪器入口与弹窗, 见 auction-notes 7) ---
# 出价面板「仪器」入口按钮 (2026-10-04 用户实测 box), 使用侧先 OCR 后点击
# 命中框 (见 auction-notes 3/7)。
BOX_INSTRUMENT_ENTRY = (0.155, 0.908, 0.263, 0.956)
# 「仪器列表」弹窗标题: 按用户 2026-10-04 实机截图估算, 未经实机标定,
# 偏差按日志微调。右缘止于 0.200, 不圈进右上角关闭按钮 (0.36 附近)。
BOX_INSTRUMENT_LIST_TITLE = (0.040, 0.125, 0.200, 0.185)
# 五个槽位行的点击 y 中心 (1080p px 300/440/570/700/830, 换算自参考实现,
# 按五个独立槽位取值, 参考脚本槽位 2 的复制粘贴缺陷见 auction-notes 6)。
INSTRUMENT_SLOT_YS = (0.2778, 0.4074, 0.5278, 0.6481, 0.7685)
# 各槽位行整行区域: 行非空校验与行点击共用, y 以点击中心上下各扩半行
# (相邻行中心间距 ~0.12, 不重叠), x 覆盖行卡片文字、中心 ≈ 原槽位点 px(400, y)。
BOX_INSTRUMENT_LIST_ROWS = tuple(
    (0.040, y - 0.045, 0.375, y + 0.045) for y in INSTRUMENT_SLOT_YS
)
# 主界面「请选择仪器组合」入口点击点: 未装备时装备卡片标题行没有文字,
# 装备流程必须无条件点此入口打开「仪器组合」弹窗 (2026-10-04 用户实测)。
POS_INSTRUMENT_GROUP_ENTRY = (0.737, 0.779)

# --- 掉线回场 (大世界 → 拍卖主界面) ---
# 「即刻落槌」是「都市闲趣」里的一个玩法卡片, 入口路径固定:
# 大世界按 F5 打开「都市大亨」面板 → 点「都市闲趣」→ 在 MENU 面板里找「即刻落槌」卡片。
# 「都市闲趣」入口坐标走位置表 self.pos.panels.f5.hobbies (见 PanelPosition);
# 下面是「都市闲趣」MENU 子面板内部的区域, 属任务私有, 不进位置表。
POS_CITY_FUN_SCROLL = (0.500, 0.550)  # 子面板内容区中部, 滚动落点
# 子面板标题「MENU 都市闲趣」。它和都市大亨面板上的「都市闲趣」入口同名, 但位置不重叠
# (入口文字在 0.48~0.56/0.39~0.47), 用区域就能区分两个界面。
BOX_CITY_FUN_TITLE = (0.10, 0.09, 0.42, 0.20)
BOX_CITY_FUN_CARDS = (0.08, 0.22, 0.87, 0.88)  # 卡片区, 「即刻落槌」在最后一页
BOX_CURRENT_VENUE = (0.630, 0.550, 0.800, 0.598)  # 主界面「当前：XXX场」

# 阻塞弹窗右按钮(确认/领取/放弃确认)统一区: 旧四常量同落一条按钮带, 已合并;
# 区域中心点击与 OCR 命中框点击都可用 (合并依据见 auction-notes 3)。
BOX_POPUP_OK = (0.5677, 0.6380, 0.6411, 0.6843)

# 低保金.
BOX_WELFARE_BTN = (0.8266, 0.0398, 0.8984, 0.0778)
BOX_WELFARE_DIALOG = (0.4400, 0.3050, 0.5650, 0.3620)  # 弹窗标题
# 「今日已领取次数：N/5」整行(标签 + 数值), 孤立小号数字读不出; 上下余量小,
# 改前复验方法见 auction-notes 3。
BOX_WELFARE_COUNTER = (0.4100, 0.5150, 0.5950, 0.5820)
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
# 实测像素、「框必须比文字宽松」的丢字依据见 auction-notes 2.5 与 3。
BOX_ONE_CLICK_SELL = (0.86198, 0.75463, 0.92708, 0.86574)
# 结算界面右下品质圆点行的红圈中心: 勾选「拍卖成功出售红」后在点一键出售前
# 先点这里选中红色品质, 一键出售只卖红品质藏品。用户 2026-10-03 实测坐标,
# 与 ok_templates/90-91.png 吻合; 注意与藏品仓库出售模式的 QUALITY_BOXES(红)
# 不是同一段 UI, 位置不同, 不可混用。
POS_SETTLE_QUALITY_RED = (0.959, 0.746)
# 「获得物品」弹窗底部的「点击空白区域关闭」, 只用来确认弹窗出现了.
# 弹窗家族与复用关系见 auction-notes 3。
BOX_POPUP_CLOSE_HINT = (0.44010, 0.79630, 0.56250, 0.91204)
# 真正要点的「空白区域」: 不能点 OCR 命中的提示文字, 游戏要求点空白区域
# (实测坐标见 auction-notes 3)。
BOX_POPUP_BLANK = (0.47396, 0.69444, 0.52604, 0.76852)

# 品质圆点区域 (白, 绿, 蓝, 紫, 橙, 红), 与 options.QUALITY_KEYS 一一对应.
QUALITY_BOXES = (
    (0.682, 0.799, 0.687, 0.819),
    (0.730, 0.799, 0.735, 0.813),
    (0.779, 0.800, 0.788, 0.816),
    (0.829, 0.801, 0.838, 0.818),
    (0.877, 0.799, 0.886, 0.816),
    (0.927, 0.799, 0.936, 0.819),
)

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


def build_boxes(screen: Callable[..., Box]) -> AuctionBoxes:
    """按相对比例一次性构建单轮拍卖使用的全部 UI 区域。

    screen 是任务侧的 `box_of_screen`: 接受相对比例元组, 返回屏幕实际 Box。
    区域数值全部来自本模块上方的 BOX_* 常量, 任务类不再保留副本。
    """
    return AuctionBoxes(
        match=screen(*BOX_MATCH),
        bid=screen(*BOX_BID),
        bid_keypad=screen(*BOX_BID_KEYPAD),
        skip_area=screen(*BOX_SKIP_AREA),
        exit=screen(*BOX_EXIT),
        bid_confirm=screen(*BOX_BID_CONFIRM),
        abandon=screen(*BOX_ABANDON),
        asset_value=screen(*BOX_ASSET_VALUE),
        estimate=screen(*BOX_ESTIMATE),
        last_bid=screen(*BOX_LAST_BID),
        clear=screen(*BOX_CLEAR),
        price_result=screen(*BOX_PRICE_RESULT),
        price_result_keypad=screen(*BOX_PRICE_RESULT_KEYPAD),
        main_title=screen(*BOX_MAIN_TITLE),
        main_asset=screen(*BOX_MAIN_ASSET),
        insufficient=screen(*BOX_INSUFFICIENT),
        welfare_btn=screen(*BOX_WELFARE_BTN),
        welfare_dialog=screen(*BOX_WELFARE_DIALOG),
        welfare_counter=screen(*BOX_WELFARE_COUNTER),
        cancel=screen(*BOX_CANCEL),
        popup_ok=screen(*BOX_POPUP_OK),
        warehouse_btn=screen(*BOX_WAREHOUSE_BTN),
        warehouse_title=screen(*BOX_WAREHOUSE_TITLE),
        sell=screen(*BOX_SELL),
        confirm_sell=screen(*BOX_CONFIRM_SELL),
        sell_label=screen(*BOX_SELL_LABEL),
        sell_value=screen(*BOX_SELL_VALUE),
        blank=screen(*BOX_BLANK),
        close=screen(*BOX_CLOSE),
        one_click_sell=screen(*BOX_ONE_CLICK_SELL),
        popup_close_hint=screen(*BOX_POPUP_CLOSE_HINT),
        popup_blank=screen(*BOX_POPUP_BLANK),
        result_value=screen(*BOX_RESULT_VALUE),
        heart_area=screen(*BOX_HEART_AREA),
        instrument_card=screen(*BOX_INSTRUMENT_CARD),
        instrument_combo_title=screen(*BOX_INSTRUMENT_COMBO_TITLE),
        instrument_combo_list=screen(*BOX_INSTRUMENT_COMBO_LIST),
        instrument_combo_names=screen(*BOX_INSTRUMENT_COMBO_NAMES),
        instrument_combo_content=screen(*BOX_INSTRUMENT_COMBO_CONTENT),
        instrument_combo_use=screen(*BOX_INSTRUMENT_COMBO_USE),
        instrument_combo_close=screen(*BOX_INSTRUMENT_COMBO_CLOSE),
        instrument_entry=screen(*BOX_INSTRUMENT_ENTRY),
        instrument_list_title=screen(*BOX_INSTRUMENT_LIST_TITLE),
        instrument_list_rows=tuple(screen(*row) for row in BOX_INSTRUMENT_LIST_ROWS),
    )
