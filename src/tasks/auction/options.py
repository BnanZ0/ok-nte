"""拍卖任务配置声明: 配置键, 取值, 默认配置, 控件类型与说明文本。

本模块只承载纯声明, 不依赖任务类。`AutoBidAuctionTask` 直接按名导入这些常量,
配置面板在任务 `__init__` 里由 default_config / config_type /
config_description 三个构建器装配, 键与值必须与迁移前逐一致。
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
# 「出售品质」是「勾选即出售」: 勾了才卖, 没勾的一律保留。
# 低保金的领取前提是资产低于 10 万, 卖藏品会抬高资产 —— 想领低保就别多勾
# 高价值品质 (双清单合并历史见 auction-notes 4)。
CONF_SELL_QUALITIES = "出售品质"

# 自动加价配置.
CONF_AUTO_RAISE = "启用自动加价"
CONF_RAISE_MODE = "加价方式"
# 加价方式的取值既是下拉框标签, 又是持久化的配置值, 还被 _raise_mode 当判定串用,
# 改取值前先看 auction-notes 4 的兼容约束。
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

# 期望价值模式的红池单价: 红藏品单件估价, 来自用户自己的成交统计(如达芙计算器
# 记忆池汇总)。红价不显示在情报窗口, 只能外部供给; 0 = 红不计入 EV, 保守。
CONF_RED_POOL_PRICE = "红池均价"

# 出价模式.
CONF_BID_MODE = "出价模式"
BID_MODE_CUSTOM = "自定义价格"
BID_MODE_LIST = "每轮指定价格"
BID_MODE_ESTIMATE = "按系统估价"
# 逐口追踪(内部叫法): 独立的整场策略, 不基于按键精灵脚本; 按每口估价增量跟踪
# 判定物品潜力, 前后口的估价差与固定底价表决定出价, 只规划前 5 口; 数值未经实机
# 验证。只推荐高级场, 中级场可选, 低级场不推荐(口径修订见 auction-notes 9)。
BID_MODE_SMART = "巨物小吱4123"
# 期望价值回合系数: 读拍卖情报窗口的紫金数量与均价现场算期望价值(EV),
# 出价 = EV ÷ 回合系数, 逐口收紧 (第 1 口出期望的一半, 第 4 口出九成),
# 只出前 4 口, 第 5 口起放弃本场。系数表移植自达芙计算器「回合参考价」
# (2026-10-07), 数值未经实机验证; 翻倍/闪耀不建模, 翻倍场会低估 (见
# auction-notes 7)。
BID_MODE_EV = "期望价值回合系数"
BID_MODES = (BID_MODE_CUSTOM, BID_MODE_LIST, BID_MODE_ESTIMATE, BID_MODE_SMART, BID_MODE_EV)

# 估价分档百分比: 「按系统估价」模式下按估价区间取不同百分比出价,
# 出价 = 估价×值/100(对齐参考实现的 InputBox/100×估价, 填 109 即 1.09 倍)。
# 区间语义与参考实现一致: 取第一个 估价 < 上限 的档, 上限填 0 表示无上限
# (只允许最后一档)。开关关闭或未命中任何档时回退到单一「估价倍率」。
# 取值一律按百分比; 旧键「估价分档倍率 / 分档N倍率 / 分档方式」不再迁移
# (删除「分档方式」的背景见 auction-notes 4)。
CONF_TIERED_RATIO = "估价分档百分比"
TIER_COUNT = 6
CONF_TIER_BOUNDS = tuple(f"分档{index}估价上限" for index in range(1, TIER_COUNT + 1))
CONF_TIER_RATIOS = tuple(f"分档{index}百分比" for index in range(1, TIER_COUNT + 1))
DEFAULT_TIER_BOUNDS = (1_000_000, 2_000_000, 5_000_000, 10_000_000, 20_000_000, 0)
# 默认取值即参考攻略的按估价分档: 低价值敢抢, 高价值收紧, 不会低于半价。
DEFAULT_TIER_PERCENTS = ("109", "103", "90", "80", "70", "50")

# 每轮指定价格: 一轮拍卖最多 6 回合出价, 每次出价各自一个价格.
MAX_BID_ROUNDS = 6
CONF_BID_PRICES = tuple(f"第{index}次出价价格" for index in range(1, MAX_BID_ROUNDS + 1))

# 拍卖辅助功能: 多选框, 勾选即启用.
CONF_ASSIST_FEATURES = "辅助功能"
# 表情包: 出价确认成功后发送表情菜单第一个表情, 2026-10-02 移除后于 2026-10-06
# 恢复 (沿革见 auction-notes 4); 盲点击无视觉确认, 默认不勾选。
ASSIST_EMOTE = "表情包"
ASSIST_WELFARE = "低保金"
# 永恒之心/竞拍仪器换算自参考实现的坐标与颜色特征, 未经实机验证.
ASSIST_HEART = "永恒之心"
ASSIST_INSTRUMENT = "竞拍仪器"
# 拍卖成功出售红: 结算界面先点品质红再点一键出售, 只卖红色品质藏品,
# 坐标为用户 2026-10-03 实测 (见 layout.POS_SETTLE_QUALITY_RED)。
ASSIST_SELL_RED = "拍卖成功出售红"
# 出售红的子配置: 本件拍品成交价值(当前估价)低于该值才卖红, 不低于则保留;
# 0 = 不限总是卖 (比较对象与保守方向见 auction-notes 7)。
CONF_SELL_RED_MAX = "低于此价才卖红"
ASSIST_FEATURES = (ASSIST_EMOTE, ASSIST_WELFARE, ASSIST_HEART, ASSIST_INSTRUMENT, ASSIST_SELL_RED)

# 永恒之心辅助的子配置: 检出心时在算出的价格上追加固定加价; 展柜未检出心时
# 可选择放弃本场(等下一场)。两项都只在勾选「永恒之心」辅助后生效。
CONF_HEART_INCREMENT = "永恒之心加价"
CONF_HEART_ABANDON = "无心放弃本场"

# 竞拍仪器辅助的子配置: 每轮开始自动校验并装备选定的仪器组; 仪器用完(组被置顶)
# 时自动购买补全并确认, 真实扣资产 (购买决策沿革见 auction-notes 7)。
# 要装备的组走下拉, 候选来自实机截图(ok_templates/53-59.png)的「仪器组合」组列表;
# 第 5 个组(紫色, 描述「特供大型藏品...」)的名字在截图里被裁切, 暂不进候选
# (见 auction-notes 7)。
CONF_INSTRUMENT_GROUP = "仪器组"
# 组默认值常量(2026-10-04 起指向超级品鉴仪器组, 改名沿革见 auction-notes 4),
# 至尊仪器组保留在候选末位; 老配置存过的组名不受默认值变化影响。
INSTRUMENT_GROUP_SUPREME = "超级品鉴仪器组"
INSTRUMENT_GROUPS = (
    "初级泛用仪器组",
    "初级数据仪器组",
    "中级泛用仪器组",
    "中级价值仪器组",
    INSTRUMENT_GROUP_SUPREME,
    "超级估值仪器组",
    "至尊仪器组",
)
# 每口出价前使用仪器的槽位序列(2026-10-04 按用户要求从「每件拍品一次」改为
# 「每口一次」): 数字 1-5 为槽位号, 0 表示该口跳过, 逐口消费后循环; 留空维持
# 默认 1→5 轮换。仪器每口消耗一个, 耗尽后等下一件拍品开始时的自动补全。
CONF_INSTRUMENT_SEQUENCE = "仪器槽位序列"

# --- 会场 ---
# 期望会场名: 启动与回场后读「当前：XXX场」做包含匹配, 不匹配只告警不停止.
CONF_EXPECTED_VENUE = "期望会场名"

# 结算成交价值达到该值时自动截图留档, 0 表示不截图. 截图存框架的 screenshots 目录
# (按进程 cwd 解析: 正式版由 pyappify chdir 到 <安装目录>\data\apps\ok-nte\working,
# 开发版为项目根目录; 正式版每次启动只删 7 天前旧文件, 目录超 300MB 才整体清,
# 调试模式每次启动清空, 实测见 auction-notes 9)。
# 成交判据是结算界面「一键出售」按钮 OCR 命中: 未识别到时不计成交, 也不截图
# (见 auction-notes 4).
CONF_RESULT_SCREENSHOT_MIN = "成交价值截图下限"

# 品质按钮 (白, 绿, 蓝, 紫, 橙, 红), 与 layout.QUALITY_BOXES 一一对应.
QUALITY_KEYS = ["品质白", "品质绿", "品质蓝", "品质紫", "品质橙", "品质红"]

# 指定回合下拉框候选项.
SPECIAL_ROUND_OPTIONS = [str(index) for index in range(1, 7)]


def default_config() -> dict:
    """任务默认配置, 键为上面的配置键常量, 顺序与面板控件顺序一致。"""
    return {
        # 出售藏品相关配置集中放在最前面, 由模式下拉框统一控制可见性,
        # 避免「出售间隔 / 出售品质 / 自动清理」散落在面板各处.
        CONF_SELL_MODE: SELL_MODE_ONE_CLICK,
        CONF_SELL_INTERVAL: 0,
        # 「出售品质」默认只勾低价值品质: 低保金的领取前提是资产低于 10 万,
        # 而卖藏品会抬高资产, 卖多了当天剩下的低保就领不到.
        CONF_SELL_QUALITIES: ["品质白", "品质绿", "品质蓝"],
        CONF_AUTO_RAISE: False,
        CONF_FIXED_PRICE: 1,
        CONF_BID_MODE: BID_MODE_ESTIMATE,
        # 估价分档百分比: 默认档位与默认取值都来自参考攻略, 开关默认关闭时 12 个子键全部隐藏.
        CONF_TIERED_RATIO: False,
        # 取值排在上限前面: 上限有现成默认值基本不用改, 取值才是每种玩法必改的
        # (default_config 的键序即面板控件顺序), 避免切了方式却漏改取值.
        **dict(zip(CONF_TIER_RATIOS, DEFAULT_TIER_PERCENTS)),
        **dict(zip(CONF_TIER_BOUNDS, DEFAULT_TIER_BOUNDS)),
        # 每轮指定价格: 6 次出价各自一个价格, 0 表示沿用上一次的价格.
        **dict.fromkeys(CONF_BID_PRICES, 0),
        CONF_RAISE_MODE: RAISE_MODE_MULTIPLE,
        CONF_RAISE_VALUE: "1.6",
        CONF_RAISE_ROUND: 2,
        CONF_SPECIAL_ROUND: False,
        CONF_SPECIAL_ROUNDS: ["5"],
        CONF_SPECIAL_ROUND_PRICE: "66666",
        # 期望价值模式的红池单价: 0 = 红不计入, 只按紫金出价(保守默认)。
        CONF_RED_POOL_PRICE: 0,
        # 期望会场校验默认关闭(留空): 回场过渡帧会读不到, 只告警不停止.
        CONF_EXPECTED_VENUE: "",
        # 结算成交价值截图: 0 = 只记日志不截图.
        CONF_RESULT_SCREENSHOT_MIN: 0,
        # 默认全部不勾选: 低保金领取会改变资产行为, 由用户显式开启; 实验两项
        # 的关闭依据见 auction-notes 4.
        CONF_ASSIST_FEATURES: [],
        # 永恒之心辅助的子配置, 需在辅助功能里勾选「永恒之心」才生效.
        CONF_HEART_INCREMENT: 0,
        CONF_HEART_ABANDON: False,
        # 竞拍仪器辅助的子配置, 需勾选「竞拍仪器」才生效; 组默认 INSTRUMENT_GROUP_SUPREME,
        # 老配置缺该键时回落到这里; 槽位序列留空维持 1→5 轮换。
        CONF_INSTRUMENT_GROUP: INSTRUMENT_GROUP_SUPREME,
        CONF_INSTRUMENT_SEQUENCE: "",
        # 出售红辅助的子配置, 需勾选「拍卖成功出售红」才生效; 0 = 不限总是卖.
        CONF_SELL_RED_MAX: 0,
    }


def config_type() -> dict:
    """下拉框选项与条件子配置(控件类型)定义。"""
    return {
        # 按出价模式只展示该模式真正会用到的价格配置.
        CONF_BID_MODE: {
            "options": list(BID_MODES),
            "sub_configs": {
                # 「加价方式 / 加价回合数」不在这里列出: 它们挂在「启用自动加价」名下,
                # 可见性沿嵌套逐级取 AND(自定义价格 -> 自动加价开 -> 才显示), 语义与
                # 直接并列相同. 不要把同一键挂到两个可同时激活的父条件下
                # (框架重排缺陷见 auction-notes 1.3)。
                BID_MODE_CUSTOM: [
                    CONF_FIXED_PRICE,
                    CONF_AUTO_RAISE,
                    CONF_SPECIAL_ROUND,
                ],
                BID_MODE_LIST: list(CONF_BID_PRICES),
                # 「基础价」是该模式估价读不出时的回退出价, 用户必须能在面板上改,
                # 使用指南本就要求按场次填起步价 (auction-notes 4); 排在分档开关
                # 上方: 每场次该改的是起步价, 分档默认即参考攻略取值基本不用动。
                BID_MODE_ESTIMATE: [CONF_FIXED_PRICE, CONF_TIERED_RATIO],
                # 「基础价」同时挂在 CUSTOM / ESTIMATE / SMART 三个分支下: 出价模式
                # 是单选下拉框, 各父条件不会同时激活, 不违反上面的重排约束。
                BID_MODE_SMART: [CONF_FIXED_PRICE],
                # 期望价值模式: 红池均价是独立输入; 「基础价」只在缺情报降级按
                # 估价口径出价时兜底, 同样要能在面板上改。
                BID_MODE_EV: [CONF_RED_POOL_PRICE, CONF_FIXED_PRICE],
            },
        },
        # 分档百分比只在开关打开时展开 12 个子键; 开关本身只在「按系统估价」下显示.
        # 取值排在上限前面: 上限有现成默认值基本不用改, 而取值是每种玩法必改的.
        CONF_TIERED_RATIO: {
            "sub_configs": {
                True: [*CONF_TIER_RATIOS, *CONF_TIER_BOUNDS],
            }
        },
        # 仅在「启用自动加价」打开时显示加价方式 / 加价回合数, 加价数值经「加价方式」
        # 链式隐藏; 「启用自动加价」又只在「出价模式=自定义价格」下显示, 因此关掉
        # 自动加价或切到其他出价模式时, 面板不会留着一排不参与算价的控件.
        CONF_AUTO_RAISE: {
            "sub_configs": {
                True: [CONF_RAISE_MODE, CONF_RAISE_ROUND],
            }
        },
        CONF_RAISE_MODE: {
            "options": list(RAISE_MODES),
            "sub_configs": {
                RAISE_MODE_MULTIPLE: [CONF_RAISE_VALUE],
                RAISE_MODE_CUSTOM: [CONF_RAISE_VALUE],
                RAISE_MODE_PERCENT: [CONF_RAISE_VALUE],
            },
        },
        CONF_SELL_QUALITIES: {
            "type": "multi_selection",
            "options": list(QUALITY_KEYS),
        },
        # 仪器组下拉: 候选即 INSTRUMENT_GROUPS, 默认至尊仪器组兼容旧配置.
        CONF_INSTRUMENT_GROUP: {
            "options": list(INSTRUMENT_GROUPS),
        },
        CONF_ASSIST_FEATURES: {
            "type": "multi_selection",
            "options": list(ASSIST_FEATURES),
            # 勾选「永恒之心」才展开其两项专属配置, 勾选「竞拍仪器」展开仪器组
            # 与槽位序列, 勾选「拍卖成功出售红」展开其价值阈值; 默认面板少五行常驻项.
            "sub_configs": {
                ASSIST_HEART: [CONF_HEART_INCREMENT, CONF_HEART_ABANDON],
                ASSIST_INSTRUMENT: [CONF_INSTRUMENT_GROUP, CONF_INSTRUMENT_SEQUENCE],
                ASSIST_SELL_RED: [CONF_SELL_RED_MAX],
            },
        },
        # 出售模式把「出售间隔 / 出售品质」收在同一处:
        # 选「不出售」时这些子项全部隐藏, 面板只剩一个下拉框.
        CONF_SELL_MODE: {
            "options": list(SELL_MODES),
            "sub_configs": {
                SELL_MODE_OFF: [],
                SELL_MODE_FULL: [CONF_SELL_QUALITIES],
                SELL_MODE_INTERVAL: [
                    CONF_SELL_INTERVAL,
                    CONF_SELL_QUALITIES,
                ],
                # 一键出售用游戏自带的整包出售, 没有品质勾选也没有间隔, 因此没有子项.
                SELL_MODE_ONE_CLICK: [],
            },
        },
        # 仅在开关启用时显示指定回合配置.
        CONF_SPECIAL_ROUND: {
            "sub_configs": {
                True: [CONF_SPECIAL_ROUNDS, CONF_SPECIAL_ROUND_PRICE],
            }
        },
        CONF_SPECIAL_ROUNDS: {
            "type": "multi_selection",
            "options": list(SPECIAL_ROUND_OPTIONS),
        },
    }


def config_description() -> dict:
    """配置说明, 按 default_config 的顺序排列, 与面板上的控件顺序一致。

    每条只写「标签本身看不出来的信息」: 做什么, 硬约束, 以及读不到时的回退行为。
    """
    descriptions = {
        # --- 藏品出售 ---
        CONF_SELL_MODE: "包满了就不能再出价, 所以多少得卖点。不出售=仓库碰都不碰; "
        "满仓时清理=弹出库存不足提示才去卖; 按间隔出售=每 N 轮卖一次, 快满会提前卖; "
        "拍卖成功一键出售=结算时把这场拍到的全卖了, 不看仓库也不挑品质",
        CONF_SELL_INTERVAL: "每 N 轮卖一次, 快满会提前卖; 填 0 就是不按间隔, 只在包满时清",
        CONF_SELL_QUALITIES: "勾了才卖, 没勾的都留着, 都不勾就一件不卖; "
        "低保金要资产低于 10 万才领得到, 卖东西会让资产涨上去, 想领低保就别勾贵的",
        # --- 出价 ---
        CONF_AUTO_RAISE: "打开后每回合出价按「加价方式」一回合比一回合高",
        CONF_FIXED_PRICE: "自定义价格模式: 每回合都出这个价; 按系统估价模式: 算出来的价"
        f"比它低就按它出, 估价读不到也按它出; {BID_MODE_SMART}模式: 估价读不到时按它出, "
        f"填个能接受的起步价; {BID_MODE_EV}模式: 情报读不全降级按估价口径出价时, "
        "估价也读不到才按它出。要正整数, 自定义模式填错直接不让启动, 其他模式那一次"
        "出价会因价格不对而失败",
        CONF_BID_MODE: "决定每回合出多少钱, 五选一。自定义价格=每回合出「基础价」, "
        "可开自动加价往上加; 每轮指定价格=6 次出价各填各的价; 按系统估价=跟着面板"
        "估价出, 可开「估价分档百分比」按价格段定倍数, 算出来低于「基础价」就按"
        f"「基础价」出, 估价读不到也按它出; {BID_MODE_SMART}=独立的整场策略, "
        "看估价涨得快不快决定追不追, 只出前 5 回合(数值没实测过), "
        "只推荐高级场, 中级场可选, 低级场不推荐; "
        f"{BID_MODE_EV}=读拍卖情报窗口的紫金数量与均价现场算期望价值, 按回合系数"
        "逐口收紧(第 1 口出期望的一半, 第 4 口出九成), 只出前 4 回合, 之后放弃本场; "
        "「红池均价」填红色藏品的单件估价, 0=红不计入; 情报读不全时按「按系统估价」"
        "口径出价(用「基础价」兜底); 翻倍场次会低估, 数值未实测; "
        f"{BID_MODE_SMART}和{BID_MODE_EV}模式下资产跌破 100 万时, 本次运行自动按"
        f"「{BID_MODE_ESTIMATE}」出价, 配置不变, 资产回升也不切回",
        CONF_RED_POOL_PRICE: "红藏品按这里的单件价折算进期望价值, 数值来自你自己的"
        "红色藏品成交统计(比如达芙计算器的记忆池汇总); 填 0 = 红不计入, 只按紫金"
        "出价, 最保守; 要非负整数, 填错任务不启动",
        CONF_TIERED_RATIO: "开了就按估价所在的价格段套「分档N百分比」, 分档参考按键精灵"
        "攻略移植: 出价=估价×值/100, 填 109 就是 1.09 倍; 哪一档都没套上就按估价原价出",
        # 生成 12 条: 上限档说明区分末档的 0=无上限, 倍率档统一说明.
        **{
            key: (
                f"估价低于这个数就套第 {index} 档的百分比, 正好等于时算下一档; "
                + ("只有最后一档能填 0(不设上限)" if index == TIER_COUNT else "得比上一档大")
            )
            for index, key in enumerate(CONF_TIER_BOUNDS, start=1)
        },
        **{
            key: f"估价落在第 {index} 档时用的百分比, 出价=估价×值/100, "
            "填 109 就是 1.09 倍, 得是正数"
            for index, key in enumerate(CONF_TIER_RATIOS, start=1)
        },
        CONF_RAISE_MODE: "倍率=每回合在上一回合上再乘数值, 越加越快; 百分比=每回合比"
        "上一回合多加一份基础价的百分之数值, 匀速爬; 自定义=每回合固定多加数值",
        CONF_RAISE_VALUE: "三种方式共用这个数, 含义跟着方式变(乘的倍数 / 加的百分比 / "
        "每回合加的金额), 可以填小数",
        CONF_RAISE_ROUND: "从第几回合开始按加价算; 0=第一回合就加, 填 N=前面几回合先出"
        "基础价, 到第 N 回合起才加",
        CONF_SPECIAL_ROUND: "勾了的回合直接出「指定回合价格」, 不走基础价和加价那套算法",
        CONF_SPECIAL_ROUNDS: "这些回合单独定价, 序号从 1 数起; 勾多个的话共用同一个价格, "
        "注意第 5、6 回合价格一样会被游戏拒掉(第 6 回合必须比第 5 回合高); "
        "开着开关却一个没勾或勾了 1~6 之外的值, 启动直接拦下",
        CONF_SPECIAL_ROUND_PRICE: "指定回合出的价, 要正整数; 开着指定回合时填错直接不让启动",
        # --- 辅助功能 ---
        CONF_ASSIST_FEATURES: "勾了才启用。表情包=出价成功后发送表情菜单里的第一个表情; "
        "低保金=资产低于 10 万时自动领; "
        "永恒之心(实验)=每场看展柜有没有永恒之心, 有就加价、没有可以放弃这场; "
        "竞拍仪器(实验)=每回合出价前用一个仪器, 每轮自动检查并装上「仪器组」选的组, "
        "用完了下一件拍品开始时自动买(真扣钱); 拍卖成功出售红=结算先点品质红再一键出售, "
        "只卖红色藏品(配「拍卖成功一键出售」模式用)",
        CONF_HEART_INCREMENT: "检测到永恒之心时, 在算出的价上多加一笔固定的钱; "
        f"对自定义价格、按系统估价、{BID_MODE_SMART}生效, "
        "「指定回合价格」和「每轮指定价格」不加; 0=不加",
        CONF_HEART_ABANDON: "展柜里没检测到永恒之心就放弃这场, 等下一场; 默认关",
        CONF_INSTRUMENT_GROUP: "每轮开始自动检查: 没装就装上选的组; 装了但仪器用完"
        "(组被顶到最上面)就自动「一键补全/购买并使用」并确认, 真扣钱; 都正常就直接继续。"
        "每组仪器数量不一样(初级泛用组 3 个, 至尊组 5 个), 「仪器槽位序列」的槽位号"
        "按选的组从上往下数",
        CONF_INSTRUMENT_SEQUENCE: "每回合出价前按这里填的顺序用一个槽位的仪器, 用到头"
        "再从头来; 数字是补全后满列表从上往下数的槽位号, 仪器用掉后下面的会上移, "
        "任务自己会算该点哪一行; 0=这一回合不用仪器; 留空=按 1 到 5 轮着用, "
        "即第一回合用 1 号、第二回合用 2 号…每回合消耗一个, 用完后这场剩下的回合都跳过。"
        "低级场建议不使用仪器, 不勾「竞拍仪器」就行; 填了 0-5 以外的字符或"
        "全是 0 时不让启动",
        CONF_SELL_RED_MAX: "填正整数才生效: 这件的成交价(当前估价)低于这个数才卖红, "
        "不低于它、或者价读不出来, 都留着不卖; 0=不限, 红的一律卖",
        # --- 会场 ---
        CONF_EXPECTED_VENUE: "启动和掉线回场后核对会场, 识别出来的会场名不含这里填的"
        "字就告警(比如填「海贝」); 留空=不核对",
        # --- 结算观测 ---
        CONF_RESULT_SCREENSHOT_MIN: "填正整数开启: 识别到「一键出售」(成交)且成交价 ≥ 这个"
        "数的场次自动截图存档, 0=关闭只记日志; 截图在安装目录的 data\\apps\\ok-nte\\working"
        "\\screenshots (开发版跑 main_debug.py 则在项目目录的 screenshots), 正式版不会"
        "重启清空, 每次启动自动删 7 天前的旧图, 整个文件夹超 300MB 才会整体清掉, "
        "调试模式才是每次启动清空; 想长期留就自己拷走",
    }

    # 每轮指定价格: 6 个价格依次对应第 1~6 次出价.
    for index, key in enumerate(CONF_BID_PRICES, start=1):
        if index == 1:
            description = "第 1 次出价的价格, 必须大于 0, 不然任务不启动"
        elif index == MAX_BID_ROUNDS:
            description = (
                f"第 {index} 次出价的价格, 必须自己填而且要比第 {index - 1} 次高, 不然游戏不认"
            )
        else:
            description = f"第 {index} 次出价的价格; 填 0 就照上一回合的价出"
        descriptions[key] = description
    return descriptions


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
# ⚠️ 行数是硬约束: 弹窗高度无钳制, 超高会被父窗口裁掉底部按钮, 用户关不掉弹窗,
# 实测上限约 38 行 —— 不插空行, 分段靠标题, 依据见 auction-notes 1.4。
# ruff: disable[E501]
INST = "<br>".join(
    [
        _inst_line("📍 开始前", "#FF5555", bold=True),
        _inst_line(
            "大世界或拍卖界面里点启动就行, 不在里面会自己进; 「循环次数」填 0 就一直跑", indent=1
        ),
        _inst_line(
            "半路掉线被踢回大世界也不怕, 会自己走 F5 都市大亨 → 都市闲趣 → 即刻落槌 回来", indent=1
        ),
        _inst_line("💰 「出价模式」五选一, 决定每回合出多少钱", "#FF5555", bold=True),
        _inst_line(
            "按系统估价: 跟着面板估价出, 算出来比「基础价」低就按「基础价」出; "
            "「估价分档百分比」按价格段定倍数(填 109 就是 1.09 倍), 估价读不到就按「基础价」出; "
            "期望价值回合系数: 读拍卖情报窗口的紫金数量与均价现场算期望价, 第 1 口出期望的一半、"
            "第 4 口出九成, 只出前 4 口, 「红池均价」按红色藏品单件估价填(0=红不计入), "
            "情报读不全按「按系统估价」口径出",
            "#FE821D",
            bold=True,
            indent=1,
        ),
        _inst_line(
            f"{BID_MODE_SMART}: 独立的整场策略, 看估价涨得快不快决定追不追, 只出前 5 回合; "
            "只推荐高级场, 中级场可选",
            "#FE821D",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "自定义价格: 每回合都出「基础价」; 开「启用自动加价」后按「加价方式 / 加价数值 /",
            "#FE821D",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "加价回合数」一回合比一回合高, 也能用「启用指定回合单独出价」给某几回合单独走「指定回合价格」",
            indent=2,
        ),
        _inst_line(
            "每轮指定价格: 「第1次出价价格」到「第6次出价价格」一回合一个价",
            "#FE821D",
            bold=True,
            indent=1,
        ),
        _inst_line("第 6 次必须比第 5 次高, 一样高游戏不认", "#FF5555", bold=True, indent=2),
        _inst_line("📦 「出售藏品模式」四选一", "#FF5555", bold=True),
        _inst_line(
            "不出售 / 满仓时清理 / 按间隔出售 / 拍卖成功一键出售", "#FE821D", bold=True, indent=1
        ),
        _inst_line(
            "包满了就没法再出价, 至少选「满仓时清理」; 「出售藏品间隔次数」填 0 就是只在包满时清",
            indent=2,
        ),
        _inst_line(
            "「出售品质」勾了才卖, 都不勾就一件不卖任何藏品; 低保金要资产低于 10 万才领得到,"
            "想领低保就别勾贵的, 卖多了当天剩下的低保就领不到",
            indent=2,
        ),
        _inst_line(
            "✨ 「辅助功能」: 表情包 = 出价成功后发送表情菜单第一个表情; "
            "低保金 = 资产低于 10 万时自动领",
            "#FF5555",
            bold=True,
        ),
        _inst_line(
            "「永恒之心」(有心加价、没心弃局)和「竞拍仪器」(每回合用一个仪器, 「仪器槽位序列」定用哪个,"
            " 组在「仪器组」里选, 用完了下件拍品自动买、真扣钱; 低级场建议不用)是实验功能, 默认关",
            indent=1,
        ),
        _inst_line(
            "「拍卖成功出售红」= 结算先点品质红再一键出售, 只卖红; "
            "「低于此价才卖红」= 成交价低于它才卖, 不低于或读不到就留着, 0=不限",
            indent=2,
        ),
        _inst_line("🔄 升级后必看", "#FF5555", bold=True),
        _inst_line(
            "「启用辅助功能」改名「辅助功能」, 勾选状态不保留; 低保金默认改成不勾",
            "#FF5555",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "「启用自动清理藏品 / 启用低保金 / 保留藏品品质 / 满仓或领低保后追加出售品质」"
            "这四个老设置不用了, 也不会自动换算成新设置;",
            "#FF5555",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "「未领完/已领完低保时出售品质」并进了「出售品质」, 第一次启动按默认值重新填好;",
            "#FF5555",
            bold=True,
            indent=2,
        ),
        _inst_line(
            f"「至尊组用尽自动购买」和{BID_MODE_SMART}的两个道具开关已移除",
            "#FF5555",
            bold=True,
            indent=2,
        ),
        _inst_line(
            f"出价模式「逐口追踪」改名「{BID_MODE_SMART}」, 旧名启动会报错; "
            "「加价方式」存了无效值的按默认「倍率」算, 下拉框空白就重选一次",
            "#FF5555",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "「估价分档倍率 / 分档N倍率 / 分档方式」并成了「估价分档百分比 / 分档N百分比」:",
            "#FF5555",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "填的数一律按百分比算(109 = 1.09 倍), 旧数值不作数, 默认按攻略的 109/103/90/80/70/50 重新填好",
            indent=2,
        ),
        _inst_line(
            "「估价倍率」删掉了: 不开分档就按估价原价出, 估价读不到还是按「基础价」出", indent=2
        ),
        _inst_line(
            "「按系统估价」读估价会多等 4 秒左右, 是在等数字停止滚动, 免得读到一半的数, 不是卡住",
            indent=2,
        ),
        _inst_line(
            "想把配置恢复成默认: 点「重置配置」, 会清掉你填过的所有值(含价格),",
            "#FF5555",
            bold=True,
            indent=1,
        ),
        _inst_line(
            "「循环次数」回到 0(一直跑), 「出售藏品模式」回到「拍卖成功一键出售」",
            "#FF5555",
            bold=True,
            indent=2,
        ),
        _inst_line(
            "⚠️ 出价失败会自动重试, 一轮砸了不影响后面; 包满清理失败会先跳过出价, "
            "确认包是真满再接着清",
            indent=1,
        ),
    ]
)
# ruff: enable[E501]
