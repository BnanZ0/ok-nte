"""拍卖任务领域包。

只承载拍卖域的纯声明与领域能力 (配置声明, 界面契约, 价格, OCR 读数,
出价价格决策, 单次出价执行, 键盘输入, 单轮控制, 匹配与确认, 结算阶段,
结算后协调, 弹窗关闭, 出售, 低保, 回场, 出价面板辅助), 依赖方向: 本包只依赖
stdlib、ok 框架与 src/utils 工具层, 不反向 import 任务模块;
`AutoBidAuctionTask` 管理任务和轮次生命周期, 直接从这里导入常量与纯函数。
模块协作约定: `round.py` 持有单轮状态机, `match.py`/`settle.py`/
`post_round.py`/`keypad.py` 分别承载匹配确认、结算收尾、结算后观测与
键盘输入 —— 五者都经冻结的窄动作接口 (AuctionRoundActions / MatchActions /
SettleActions / KeypadActions / PostRoundActions) 拿到所需动作, 不持有任务
对象; 直持任务实例的 8 个能力模块 (`bid.py`/`bid_price.py`/`recovery.py`/
`sell.py`/`reading.py`/`welfare.py`/`assist.py`/`popup.py`) 已各自
迁到窄协议 (contracts.AuctionBidOps / AuctionBidPriceOps / AuctionRecoveryOps /
AuctionSellOps / AuctionReadingOps / AuctionWelfareOps / AuctionAssistOps /
AuctionPopupOps, 模块只访问协议声明的成员) —— 新增跨模块
调用必须先在对应协议加一行, 并受 TestAuctionContracts 反射与 AST 扫描
用例守卫;
`bid_price.py` 内含出价模式的注册表: 模式声明、入口校验与运行时算价
共用同一张表, 新增模式只需在注册表加一项; `interaction.py` 收口原子点击
序列 (挂起 sleep_check 钩子)与滚动坐标换算。跨领域调用直接调用对方模块的公开函数
(如 recovery 兜底弹窗直调 auction_welfare), 低保的当日领取记录经
WelfareState 状态对象传递, 出售模块经三态返回值报告满仓结论, 跨轮标记由
任务侧落账。
出售 / 低保 / 回场 / 读取 / 弹窗 / 辅助的行为常量定义在各自模块里。
"""
