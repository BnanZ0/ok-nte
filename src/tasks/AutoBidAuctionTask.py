import re
import time
import traceback
from collections.abc import Iterable
from decimal import Decimal

from ok import Box, TaskDisabledException, WaitFailedException

from src.tasks.auction import assist as auction_assist
from src.tasks.auction import bid as auction_bid
from src.tasks.auction import bid_price as auction_bid_price
from src.tasks.auction import interaction as auction_interaction
from src.tasks.auction import keypad as auction_keypad
from src.tasks.auction import layout as auction_layout
from src.tasks.auction import match as auction_match
from src.tasks.auction import options as auction_options
from src.tasks.auction import popup as auction_popup
from src.tasks.auction import post_round as auction_post_round
from src.tasks.auction import price as auction_price
from src.tasks.auction import reading as auction_reading
from src.tasks.auction import recovery as auction_recovery
from src.tasks.auction import round as auction_round
from src.tasks.auction import sell as auction_sell
from src.tasks.auction import settle as auction_settle
from src.tasks.auction import welfare as auction_welfare
from src.tasks.auction.layout import (
    RE_BID,
    RE_BID_PANEL,
    RE_CONFIRM,
    RE_MAIN_TITLE,
    RE_MATCH,
    RE_SKIP,
    RESULT_VALUE_LABELS,
    AuctionBoxes,
    AuctionState,
    PostRoundState,
)
from src.tasks.auction.options import (
    ASSIST_HEART,
    ASSIST_INSTRUMENT,
    ASSIST_WELFARE,
    BID_MODE_CUSTOM,
    BID_MODE_SMART,
    CONF_ASSIST_FEATURES,
    CONF_AUTO_RAISE,
    CONF_BID_MODE,
    CONF_EXPECTED_VENUE,
    CONF_HEART_ABANDON,
    CONF_INSTRUMENT_GROUP,
    CONF_INSTRUMENT_SEQUENCE,
    CONF_RAISE_MODE,
    CONF_RESULT_SCREENSHOT_MIN,
    CONF_SELL_MODE,
    CONF_SELL_QUALITIES,
    INST,
    INSTRUMENT_GROUP_SUPREME,
    INSTRUMENT_GROUPS,
    QUALITY_KEYS,
    RAISE_MODE_MULTIPLE,
    RAISE_MODES,
    SELL_MODE_OFF,
)
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class AutoBidAuctionTask(NTEOneTimeTask, BaseNTETask):
    """自动完成游戏内拍卖流程。

    功能包括: 匹配, 确认, 出价, 出价重试, 结算, 低保金领取, 藏品出售。
    需要在拍卖主界面选择低级会场后开始执行。

    配置键 / UI 区域 / 纯函数的唯一来源在 `src/tasks/auction/` 子包
    (options / layout / price), 本类直接按名导入使用, 不再保留类级别名;
    出售 / 低保 / 回场的行为常量定义在各自的子包模块里。
    """

    # 轮次末尾出售流程的总预算。出售是收尾动作, 不该像拍卖阶段那样吃掉整轮 600 秒
    # (预算构成见 auction-notes 5.7); 出售域自己的常量见 auction_sell。
    SELL_TIMEOUT = 90

    # --- 阶段超时 (秒) ---
    # 单轮拍卖的硬上限, 防止各阶段局部超时叠加后长期卡住任务.
    ROUND_TIMEOUT = 600
    # 等一次出价的结果: 在这段时间内看界面是变成「已结束」还是「被加价」。
    BID_RESULT_TIMEOUT = 60
    # 结算面板成交价值的读取预算, 预算内重读直到可解析(见 _read_result_value)。
    # 「低于此价才卖红」拿它做放行判定, 读不出该件整包不卖, 预算不宜再压缩。
    RESULT_VALUE_READ_TIMEOUT = 4
    # 仪器组校验前的主界面标题探测预算: 只为「不在主界面就不点装备卡片」的守卫服务,
    # 命中时第一帧即返回 (见 auction-notes 7)。
    INSTRUMENT_GROUP_PROBE_TIMEOUT = 2

    # --- 轮询与重试 ---
    # 匹配/出价/结算共用的轮询节奏; 估价读取的节奏常量见 auction_reading。
    POLL_INTERVAL = 0.5
    # 基类睡眠钩子的触发间隔, 用于在长流程中处理月卡弹窗等外部打断.
    SLEEP_CHECK_INTERVAL = 0.5
    # 出价重试上限。与阶段 TIMEOUT 互补的两重保险关系(「次数先到」和「时间先到」
    # 都实际发生过, 见 auction-notes 3)对匹配阶段同样成立, 匹配侧的
    # MATCH_MAX_LOOPS 见 auction_match。
    BID_MAX_RETRIES = 3
    # 辅助路径单轮累计异常的升级阈值: 单轮内达到即按疑似代码缺陷升级为 error,
    # 避免「告警后继续」把编程错误静默吞掉 (见 auction-notes 5.9)。
    AUX_ERROR_ESCALATE_AFTER = 3
    # 编程错误类型: 设计内降级点(quiet)容忍识别/界面异常, 但不应容忍代码缺陷,
    # 这类异常即使在安静路径也照常计数升级 (判据见 auction-notes 5.9)。
    _AUX_PROGRAMMING_ERRORS = (AttributeError, TypeError, KeyError, NameError)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.supported_languages = ["zh_CN"]
        self.name = "自动拍卖(目前仅支持简中)"
        self.description = "大世界或拍卖界面点启动就行, 进场、出价、结算全程自动; 用法见「说明」"
        self.group_name = "都市闲趣"
        # 任务卡上的「说明」按钮只在 instructions 非空时出现, 内容是富文本 HTML.
        self.instructions = INST
        self.add_rounds_config()

        self.default_config.update(auction_options.default_config())

        self.config_type = auction_options.config_type()

        # 描述按 default_config 的顺序排列, 与面板上的控件顺序一致, 便于对照维护.
        # 每条只写「标签本身看不出来的信息」: 做什么, 硬约束, 以及读不到时的回退行为.
        self.config_description.update(auction_options.config_description())

        # --- 状态字段归属 ---
        # 出价计数(每口在 _stage_bid_loop 重置): last_bid_price / current_bid_count。
        # 跨轮级: _inventory_stuck(满仓且出售未成功, 置位时下一轮先复核满仓再重试清理,
        #   见 _run_single_round 与 auction_sell.run_round_end_sell) / _instrument_slots
        #   / _instrument_group(「仪器组」配置的缓存组名, 由入口校验填充) /
        #   _instrument_remaining(仪器队列: 尚未使用的满列表槽位号, 按当前视觉顺序) /
        #   _instrument_group_ready。
        # 会话级: _session_result_total / _session_result_rounds / _welfare_state。
        # 轮次级: 归 _reset_round_state 唯一入口, 含 _instrument_seq(仪器槽位序列
        #   游标, 每件拍品从序列第 1 项开始; 序列内容变化时也归零, 见
        #   _refresh_instrument_slots); 本轮回场配额 _recover_quota 由
        #   _exec_auction_round 每轮赋值, 检查与扣减只发生在任务编排层
        #   (_resume_after_world_drop), 能力模块不读写。
        self.last_bid_price = None
        self.current_bid_count = 0
        self._inventory_stuck = False
        self._session_result_total = 0
        self._session_result_rounds = 0
        # 「仪器槽位序列」的解析结果, 由 _validate_instrument_sequence 在任务入口
        # 填充, 之后每口消耗前由 _refresh_instrument_slots 重读更新; 空 list 表示
        # 未配置, 沿用 1→5 轮换。
        self._instrument_slots: list[int] = []
        # 上次读到的「仪器槽位序列」配置原文, 供 _refresh_instrument_slots 去重:
        # 原文没变不重解析不刷日志, 解析失败也只对新的原文告警一次。
        self._instrument_seq_raw: str | None = None
        # 「仪器组」配置的组名缓存, 由 _validate_instrument_group 在任务入口填充;
        # 默认取 INSTRUMENT_GROUP_SUPREME, 老配置缺该键时回落到这里。
        self._instrument_group: str = INSTRUMENT_GROUP_SUPREME
        # 仪器队列: 尚未使用的满列表槽位号, 按当前视觉顺序 (用一场出一个, 下方
        # 上移)。跨场持续; 装备/补全动作成功后由 _ensure_instrument_group 重置
        # 为满列表 (见 auction-notes 7 队列模型)。
        self._instrument_remaining: list[int] = list(auction_assist.DEFAULT_SLOT_CYCLE)
        # 本轮仪器组校验结论, 由 do_run 每轮在主界面上重新赋值; 未就绪时
        # _use_instrument_once 直接跳过 (组不对时仪器列表的槽位坐标点的是别的仪器).
        self._instrument_group_ready = False
        # 当日低保领取记录, 由 WelfareState 承载. 次数与上限只从弹窗「今日已领取
        # 次数：N/5」读回(领取前读一次, 领取后再重读一次), 不做本地推算 —— 点击
        # 领取不等于领取生效, 依据见 auction_welfare.read_counter 与 auction-notes 5.3。
        self._welfare_state = auction_welfare.WelfareState()
        # 轮次级状态的初始值与每轮重置共用同一入口; 必须在 _welfare_state 之后调用,
        # 切日逻辑要读它。
        self._reset_round_state()
        # 启用基类的睡眠钩子, 拍卖流程跨过每日 5 点时靠它处理月卡弹窗.
        self.sleep_check_interval = self.SLEEP_CHECK_INTERVAL
        self.add_exit_after_config()

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
            self._validate_instrument_sequence()
            self._validate_instrument_group()
            self._warn_if_no_sellable_quality()
            boxes = self._build_boxes()
            # 启动时的期望会场校验与留痕: 只告警不停止, 交给用户改会场或配置.
            self._check_expected_venue(boxes)
            while self.has_remaining_rounds():
                if not self.begin_round():
                    break
                try:
                    # 残留的藏品仓库会盖住主界面标题与全部拍卖控件, 每轮开头先查:
                    # 开着先收一次, 收不掉才停止 (背景见 auction-notes 5.7)。
                    if self._is_warehouse_open(boxes):
                        self.log_warning("检测到藏品仓库仍开着, 先收起再继续本轮")
                        self._close_warehouse(boxes)
                        if self._is_warehouse_open(boxes):
                            self.log_error("藏品仓库未关闭且无法自动收起, 停止后续轮次")
                            break
                    # 每轮都重新确认一次入口: 上一轮掉线或异常退出时人可能已经
                    # 不在拍卖界面 (见 auction_recovery.ensure_auction_entry)。
                    self._ensure_auction_entry(boxes)
                    self._ensure_instrument_group(boxes)
                    self._run_single_round(boxes)
                    if self._is_warehouse_open(boxes):
                        # 关窗失败时 _close_warehouse 只告警, 界面还压在藏品仓库上,
                        # 继续跑只会每轮空烧超时; 这里直接收尾, 不需要另做状态标记
                        # (下次启动照常走完整流程重试, 见 auction-notes 5.7)。
                        self.log_error("藏品仓库未关闭且无法自动收起, 停止后续轮次")
                        break
                except TaskDisabledException:
                    raise
                except Exception as e:
                    self.add_failed("拍卖执行异常")
                    self.log_error(f"本轮拍卖失败: {type(e).__name__}: {e}")
                    # 界面被弹窗挡住才会整轮什么都识别不到, 失败后按弹窗特征兜底一次.
                    self._handle_blocking_popup(boxes)
                    self.sleep(2)
        finally:
            self.finish_rounds()
            # 轮次汇总之后补一条成交价值累计, 与 finish_rounds 的成功/失败汇总互不干扰.
            if self._session_result_rounds:
                self.log_info(
                    f"本次运行累计 {self._session_result_rounds} 轮有成交, "
                    f"成交价值合计 {self._session_result_total}"
                )

    # --- 外部打断 ---
    def sleep_check(self):
        """睡眠钩子: 处理月卡弹窗等外部打断, 与其它任务保持同一套机制。

        拍卖流程可能跨过每日 5 点的刷新时刻, 此时月卡弹窗会盖住拍卖界面,
        所有阶段状态判定都会失败, 轮询只能一直空转到单轮超时。
        基类在每次 sleep 时按 sleep_check_interval 调用本方法(调用前会刷新帧),
        因此轮询循环、出价中间步骤和结算后处理都被覆盖。
        check_monthly_card 只在 5 点前后 2 分钟的时间窗内生效, 窗口外只是一次时间比较。
        钩子会从任意 self.sleep() 调用点冒泡, 月卡处理失败只降级为告警并跳过本次,
        下一次回调自然重试; TaskDisabledException 照旧传播 (见 auction-notes 5.8)。
        """
        super().sleep_check()
        try:
            if self.check_monthly_card():
                self.log_info("检测到月卡弹窗, 关闭后继续当前轮次")
                self.handle_monthly_card()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_warning(f"月卡弹窗处理失败, 跳过本次: {type(e).__name__}: {e}")

    def _handle_blocking_popup(self, boxes: AuctionBoxes | None = None) -> None:
        """整轮失败后的弹窗兜底, 实现见 auction_recovery.handle_blocking_popup。"""
        auction_recovery.handle_blocking_popup(self, boxes)

    # --- 掉线回场 (大世界 → 拍卖主界面) ---
    def _is_world_screen(self) -> bool:
        """是否被踢回大世界, 判定与误报防线见 auction_recovery.is_world_screen。"""
        return auction_recovery.is_world_screen(self)

    def _resume_after_world_drop(self, boxes: AuctionBoxes, deadline: float) -> AuctionState:
        """掉线后的统一出口: 扣回场配额后走回场路径并重跑匹配阶段。

        配额的检查与扣减留在任务编排层: 配额所有者是任务实例, 能力模块不
        直接改写任务状态, 回场路径本身见 auction_recovery.recover_from_world。
        配额由 _exec_auction_round 每轮赋值, 挂轮次不走参数的原因见
        auction-notes 5.4。
        """
        if self._recover_quota <= 0:
            raise WaitFailedException("被踢回大世界后再次掉线, 本轮放弃")
        self._recover_quota -= 1
        return self._recover_from_world(boxes, deadline)

    def _recover_from_world(self, boxes: AuctionBoxes, deadline: float) -> AuctionState:
        """掉线回场并重跑匹配阶段, 预算规则见 auction_recovery.recover_from_world。"""
        # 匹配阶段续跑由编排方注入, recovery 模块的协议不含编排私有方法。
        return auction_recovery.recover_from_world(
            self, boxes, deadline, resume_match=self._stage_match
        )

    def _return_to_auction(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """「大世界 → F5 → 都市闲趣 → 即刻落槌」回场路径, 实现见 auction_recovery。"""
        return auction_recovery.return_to_auction(self, boxes, deadline)

    def _click_instant_lot(self, deadline: float) -> bool:
        """在「都市闲趣」面板里找「即刻落槌」卡片, 实现见 auction_recovery.click_instant_lot。"""
        return auction_recovery.click_instant_lot(self, deadline)

    def _read_current_venue(self) -> str:
        """读主界面「当前：XXX场」留痕, 实现见 auction_recovery.read_current_venue。"""
        return auction_recovery.read_current_venue(self)

    def _build_boxes(self) -> AuctionBoxes:
        """按相对比例一次性构建本轮拍卖使用的全部 UI 区域, 装配见 auction_layout.build_boxes。"""
        return auction_layout.build_boxes(self.box_of_screen)

    # --- 任务入口回场 (大世界 → 拍卖主界面) ---
    def _ensure_auction_entry(self, boxes: AuctionBoxes) -> None:
        """每轮开始确认在拍卖主界面, 入口回场规则见 auction_recovery.ensure_auction_entry。"""
        auction_recovery.ensure_auction_entry(self, boxes)

    def _reset_round_state(self) -> None:
        """重置所有轮次级状态, 任务初始化与每轮开始共用这唯一入口, 不要另加赋值点。"""
        # 结算后处理会把本轮的满仓与低保金结果写回, 每轮开始前先清空上一轮的观测.
        self._post_round_state = PostRoundState()
        # 逐口追踪的估价序列按局重置, 上一局的增量不能泄进这一局.
        self._smart_state = auction_price.SmartRoundState()
        # 结算成交价值观测: 本轮读数由 welfare.run_post_round_actions 写入 PostRoundState.
        self._current_result_value = None
        # 永恒之心检测与仪器使用都是辅助状态: 心按「每场」复位; 仪器按「每口」
        # 消费, 本场已服务的出价序号随轮复位 (_instrument_served_bid); 序列游标
        # 也随轮归零 —— 队列由每场补全恢复满列表, 每件拍品从序列第 1 项开始
        # (见 auction-notes 7)。
        self._heart_checked = False
        self._heart_present = False
        self._instrument_served_bid = 0
        self._instrument_seq = 0
        # 跨过每日刷新时刻(5 点)时清空当日低保领取记录, 否则昨天领满的记录会让今天
        # 一开局就按「已领完」放开出售.
        self._rollover_welfare_day()
        # 辅助路径异常计数按轮归零: 达到阈值升级 error 的依据是「单轮内累计异常」.
        self._aux_error_count = 0

    def _log_aux_error(self, where: str, e: Exception, *, quiet: bool = False) -> None:
        """辅助路径异常分级: 告警 + 单轮累计计数, 达到阈值升级为 error。

        quiet=True 标记设计内降级点(纯观测, 失败按缺省值继续): 预期内的
        识别/界面异常降为 debug 且不计数, 编程错误仍照常计数升级 —— 设计
        容忍识别失败, 不容忍代码缺陷 (见 auction-notes 5.9)。
        调用点都在 except Exception 分支里; TaskDisabledException 与
        WaitFailedException 由各调用点既有的分支先行处理, 不会走到这里。
        """
        if quiet and not isinstance(e, self._AUX_PROGRAMMING_ERRORS):
            self.log_debug(f"{where}: {type(e).__name__}: {e}")
            return
        self._aux_error_count += 1
        self.log_warning(f"{where}: {type(e).__name__}: {e}")
        self.log_debug(traceback.format_exc())
        if self._aux_error_count >= self.AUX_ERROR_ESCALATE_AFTER:
            self.log_error(
                f"辅助路径单轮内累计 {self._aux_error_count} 次异常, 疑似代码缺陷: {where}"
            )

    def _run_single_round(self, boxes: AuctionBoxes) -> None:
        """执行一轮拍卖, 记录结果并仅在确认回到主界面后触发出售。"""
        self._reset_round_state()

        if self._inventory_stuck:
            # 上一轮满仓且出售未成功: 先重试清理而不是空烧整轮 deadline。
            # 满仓结论不能直接沿用: 出售结果未确认(实际已清空仓库)时, 空仓库的
            # 出售读数永远是 0, 卡死标记永远清不掉, 任务从此不再拍卖 —— 所以每轮
            # 先用库存提示复核, 提示消失就恢复拍卖。这里传的检测预算恒为正
            # (INVENTORY_FULL_TIMEOUT 常量), detect_inventory_full 不会返回 None;
            # 弹窗遮挡表现为「读不到提示」即 False, 会误清标记恢复拍卖 —— 真仍
            # 满仓时本轮拍卖失败, 下轮结算后复测重新置位, 自愈代价是一轮空转
            # (背景见 auction-notes 5.2)。
            detected = self._detect_inventory_full(
                boxes, auction_sell.INVENTORY_FULL_TIMEOUT
            )
            if detected is False:
                self.log_info("库存不足提示未再出现, 满仓结论已失效, 恢复正常拍卖")
                self._inventory_stuck = False
            else:
                self.log_warning("满仓未清理, 跳过本轮拍卖, 先重试清理藏品")
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

    def _check_expected_venue(self, boxes: AuctionBoxes, venue: str | None = None) -> None:
        """配置了期望会场时校验当前会场并留痕, 不匹配只告警不停止。

        包含匹配而不是全等: 会场全集未知, 用户填「海贝」就要能命中「当前：海贝场」。
        会场读不出时不告警 —— 回场过渡帧里经常读不到, 每轮都报是噪声。
        """
        if venue is None:
            venue = self._read_current_venue()
        self.info_set("当前会场", venue or "未识别")

        expected = str(self.config.get(CONF_EXPECTED_VENUE, "") or "").strip()
        if not expected or not venue:
            return
        if expected not in venue:
            self.log_warning(f"当前会场「{venue}」不包含期望的「{expected}」, 请确认会场选择")

    def _try_sell_collections(self, state: PostRoundState, boxes: AuctionBoxes) -> None:
        """轮次末尾的出售入口: 给出售流程一个独立的、有界的预算。

        出售域的逐分支等待没有上级预算约束时, 仓库入口读不到会让任务表现为
        「在跑但几乎不出价」且日志看不出时间被谁吃掉, 所以这里授予 SELL_TIMEOUT
        预算 (构成见 auction-notes 5.7)。出售失败不影响本轮已记下的结论,
        因此兜住 WaitFailedException。
        """
        sell_deadline = time.monotonic() + self.SELL_TIMEOUT
        try:
            self._run_round_end_sell(boxes, sell_deadline, state=state)
        except TaskDisabledException:
            raise
        except WaitFailedException as e:
            self.log_warning(f"藏品出售超出 {self.SELL_TIMEOUT} 秒预算, 本轮放弃出售: {e}")

    # --- 单轮流程编排 ---
    def _exec_auction_round(self, boxes: AuctionBoxes) -> bool:
        """初始化单轮预算并委托单轮状态机。

        Returns:
            bool: 拍卖是否顺利进入结算(进入下一轮出价时返回 False)。
        """
        deadline = time.monotonic() + self.ROUND_TIMEOUT
        # 本轮回场配额由整轮创建, 所有匹配重跑共享。配额属于「这一轮」而不是
        # 「这一次匹配调用」; 确认失败重新匹配时也不能重置, 否则反复回场会耗尽整轮预算。
        self._recover_quota = auction_recovery.RECOVER_MAX_PER_ROUND
        self.info_set("当前阶段", "匹配中")
        self.log_info(f"拍卖开始, 单轮最长运行 {self.ROUND_TIMEOUT} 秒")
        self.sleep(0.5)
        actions = auction_round.AuctionRoundActions(
            match=self._stage_match,
            confirm=self._stage_confirm,
            wait_bid_screen=self._wait_bid_screen,
            bid=self._stage_bid_loop,
            settle=self._stage_result,
            set_phase=lambda phase: self.info_set("当前阶段", phase),
            warn=self.log_warning,
        )
        return auction_round.AuctionRoundRunner(actions).run(boxes, deadline)

    def _wait_bid_screen(self, boxes: AuctionBoxes, deadline: float) -> None:
        """等待确认后的出价界面加载完成, 编排见 auction_match.wait_bid_screen。"""
        auction_match.wait_bid_screen(self._match_actions(), boxes, deadline)

    # --- 各阶段实现 ---
    def _stage_match(self, boxes: AuctionBoxes, deadline: float) -> AuctionState:
        """匹配阶段: 等待进入确认或出价状态, 编排见 auction_match.run_match。"""
        return auction_match.run_match(self._match_actions(), boxes, deadline)

    def _handle_match_click(
        self, boxes: AuctionBoxes, stage_deadline: float
    ) -> AuctionState | None:
        """点击开始匹配并等待界面变化, 编排见 auction_match.handle_match_click。"""
        return auction_match.handle_match_click(self._match_actions(), boxes, stage_deadline)

    def _stage_confirm(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """确认阶段: 等待并点击确认按钮, 编排见 auction_match.run_confirm。"""
        return auction_match.run_confirm(self._match_actions(), boxes, deadline)

    def _match_actions(self) -> auction_match.MatchActions:
        """为匹配与确认阶段装配其所需的界面动作与回场续跑入口。"""
        return auction_match.MatchActions(
            is_bid_screen=self._is_bid_screen,
            is_confirm_screen=self._is_confirm_screen,
            is_skip_screen=self._is_skip_screen,
            is_match_screen=self._is_match_screen,
            is_world_screen=self._is_world_screen,
            dismiss_notice=self._dismiss_notice_popup,
            next_frame=self.next_frame,
            sleep=self.sleep,
            wait_operate_click=self._wait_operate_click,
            wait_ocr=self.wait_ocr,
            wait_until=self.wait_until,
            operate_click=self.operate_click,
            remaining_timeout=self._remaining_timeout,
            resume_after_world_drop=self._resume_after_world_drop,
            poll_interval=self.POLL_INTERVAL,
            log_info=self.log_info,
            log_debug=self.log_debug,
            log_warning=self.log_warning,
        )

    def _stage_bid_loop(self, boxes: AuctionBoxes, deadline: float) -> None:
        """出价阶段: 循环出价直到拍卖结束, 支持多轮竞拍。

        每次出价结果最多等待 BID_RESULT_TIMEOUT 秒, 整个阶段仍受单轮 deadline 约束。
        资产为 0 时放弃本次出价并等待拍卖结束, 放弃不计入出价序号。
        """
        self.current_bid_count = 0
        self.last_bid_price = None

        retry = 0
        # 循环只通过 return(拍卖结束) 或 raise(重试耗尽/超时) 退出, 没有 break 路径.
        # 「逐口追踪只出前 5 口」的不变式由 _wait_bid_outcome 的到顶守卫保证:
        # 第 5 口之后等待结果不可能返回 False, 到顶后即使拍卖仍在进行也只继续
        # 等待结束, 不会提前按「已结束」交给结算 —— 否则下一轮重置口数后会以
        # 第 1 口策略重新出价, 绕过上限 (见 auction-notes 5.7)。
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

        逐口追踪到顶(current_bid_count >= 5)时跳过「出价界面出现即有人加价」的
        判定, 只等跳过动画/匹配界面; 到顶后等待窗口耗尽时出价界面仍在, 说明
        拍卖并未结束, 继续轮询而不是按结束处理 —— 否则 settle 会以「下一轮出价」
        结束本轮, 下一轮重置口数后绕过「只出前 5 口」的上限 (见 auction-notes
        5.7)。守卫必须放在本方法内而不是调用方循环里, 否则会形成忙循环。

        Returns:
            bool: True 表示拍卖已结束(界面状态未知时按结束兜底);
            False 表示有人加价, 需要继续下一次出价。
        """
        cap_active = self._smart_bid_cap_reached()
        if cap_active:
            self.log_info(
                f"已达{BID_MODE_SMART}模式的 {auction_price.SMART_MAX_BIDS} 口上限, 等待拍卖结束"
            )
        while True:
            wait_deadline = min(deadline, time.monotonic() + self.BID_RESULT_TIMEOUT)
            while time.monotonic() < wait_deadline:
                self.next_frame()
                if self._is_skip_screen(boxes):
                    self.log_info("检测到跳过动画, 拍卖结束")
                    return True
                if self._is_match_screen(boxes):
                    self.log_info("返回匹配界面, 拍卖结束")
                    return True
                if not cap_active and self._is_bid_screen(boxes):
                    self.log_info("检测到有人加价, 准备再次出价")
                    return False
                self.sleep(self.POLL_INTERVAL)

            if time.monotonic() >= deadline:
                raise WaitFailedException("单轮拍卖超时")
            if cap_active and self._is_bid_screen(boxes):
                # 到顶后出价界面仍在 = 有人加价, 拍卖未结束; 不能按结束处理返回
                # True, 否则 settle 以「下一轮出价」结束本轮, 下一轮重置口数后
                # 绕过上限 (见 auction-notes 5.7)。继续等到真正结束, 整轮
                # deadline 是唯一出口。
                self.log_info("到顶后仍检测到出价界面, 拍卖未结束, 继续等待")
                continue
            self.log_info(f"本次出价结果等待 {self.BID_RESULT_TIMEOUT} 秒未变化, 按拍卖结束处理")
            return True

    def _smart_bid_cap_reached(self) -> bool:
        """逐口追踪模式是否已到出价口数上限; 其他模式恒为 False。"""
        return (
            self._bid_mode() == BID_MODE_SMART
            and self.current_bid_count >= auction_price.SMART_MAX_BIDS
        )

    def _attempt_bid(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """单次出价尝试: 包含资产识别和出价面板确认, 实现见 auction_bid.attempt_bid。

        Returns:
            bool: True 表示已提交出价; False 表示未出价即放弃本次尝试 ——
            资产两次读 0, 或无心弃局 (弃局与重试的分派见 _stage_bid_loop)。
        """
        return auction_bid.attempt_bid(self, boxes, deadline)

    def _abandon_current_auction(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """放弃本场拍卖: 点「放弃」→ 确认, 返回是否成功离开出价界面。

        资产为 0 的兜底与「无心弃局」共用同一条路径; 放弃失败由调用方决定
        重试或抛异常, 本方法只报告结果。放弃→确认是必须连续的两击, 期间挂起
        月卡钩子; 尾部的离开判定(wait_until)留在序列外, 让钩子继续兜弹窗。
        """
        with auction_interaction.atomic_sequence(self, reason="放弃确认"):
            self.operate_click(boxes.abandon, after_sleep=0.5)
            self.sleep(0.5)
            self.operate_click(boxes.popup_ok, after_sleep=0.5)
        abandoned = self.wait_until(
            lambda: not self._is_bid_screen(boxes),
            time_out=self._remaining_timeout(deadline, 5),
            settle_time=0.5,
            raise_if_not_found=False,
        )
        if not abandoned:
            self.log_warning("点击放弃后仍在出价界面")
        return abandoned

    def _check_heart_once(self, boxes: AuctionBoxes) -> None:
        """每场第一次出价前检测一次永恒之心, 结论供本场加价与弃局判定复用。

        展柜检测是纯观测, 检测失败按「无永恒之心」方向落(读不到帧视为占比 0),
        后续是否弃局由 _should_abandon_without_heart 的配置开关兜底。
        调用时机在数字键盘弹出之前, 展柜区域完整可见。
        """
        if self._heart_checked or not self._assist_enabled(ASSIST_HEART):
            return
        self._heart_checked = True
        try:
            ratio = auction_assist.heart_pixel_ratio(self.frame, boxes.heart_area)
        except TaskDisabledException:
            raise
        except Exception as e:
            self._log_aux_error("永恒之心检测失败, 本场按未检出处理", e, quiet=True)
            ratio = 0.0
        self._heart_present = not auction_assist.should_abandon_for_heart(ratio)
        self.log_info(
            f"永恒之心检测: 占比 {ratio:.4f}, 判定为{'有' if self._heart_present else '无'}永恒之心"
        )

    def _should_abandon_without_heart(self) -> bool:
        """勾选「无心放弃本场」且本场检出无心时返回 True。

        检测必须已完成(_heart_checked): 未启用辅助或还没检测过时不能凭空弃局。
        """
        return (
            self._assist_enabled(ASSIST_HEART)
            and self._heart_checked
            and not self._heart_present
            and bool(self.config.get(CONF_HEART_ABANDON, False))
        )

    def _use_instrument_once(self, boxes: AuctionBoxes, deadline: float) -> None:
        """每口出价前使用一个竞拍仪器, 槽位取「仪器槽位序列」的下一项 (2026-10-04
        按用户要求从「每件拍品首口一次」改为「每口一次」, 沿革见 auction-notes 7)。

        消费单位是出价序号 ordinal = current_bid_count + 1: 同一口的出价重试
        (attempt_bid 抛 WaitFailedException 后由 _stage_bid_loop 重试) 不重复
        消耗; 成功出价后序号推进, 下一口取序列下一项。序列游标随拍品复位
        (_reset_round_state): 队列由每场补全恢复满列表, 每件拍品从序列第 1 项
        开始 (见 auction-notes 7)。序列每口经
        _refresh_instrument_slots 重读, 运行中改动即时生效(沿革见 auction-notes 7);
        序列未配置时按 1→5 轮换;
        序列项为 0 表示该口跳过, 但同样消耗一次并推进游标。前置是本轮的仪器组
        校验结论 (见 _ensure_instrument_group): 组不对时仪器列表里同一批槽位
        坐标对应的是别的仪器, 未就绪直接跳过该口仪器。

        队列模型 (auction-notes 7): 数字是满列表时的槽位号(仪器身份), 使用后
        下方仪器上移, 先按 _instrument_remaining 换算当前行; 仪器已用掉则该口
        跳过(等下一件拍品开始时的补全恢复); 使用成功才把该槽位号出队。
        """
        if not self._assist_enabled(ASSIST_INSTRUMENT):
            return
        ordinal = self.current_bid_count + 1
        if self._instrument_served_bid == ordinal:
            return
        self._refresh_instrument_slots()
        if not self._instrument_group_ready:
            self.log_info("仪器组未就绪, 本口跳过仪器使用")
            self._instrument_served_bid = ordinal
            return
        self._instrument_served_bid = ordinal
        slots = self._instrument_slots or auction_assist.DEFAULT_SLOT_CYCLE
        slot = slots[self._instrument_seq % len(slots)]
        self._instrument_seq += 1
        if slot == 0:
            self.log_info(f"第 {ordinal} 口: 仪器槽位序列当前项为 0, 跳过仪器使用")
            return
        row = auction_assist.instrument_row_for_slot(slot, self._instrument_remaining)
        if row is None:
            self.log_warning(
                f"第 {ordinal} 口: 序列槽位 {slot} 的仪器已用掉, 跳过(等补全后恢复)"
            )
            return
        # 使用侧的入口/弹窗/行校验 OCR 区域都由 boxes 传入, 它同时是出价侧
        # 调用面(协议与测试桩)的一部分 (见 auction-notes 7)。
        if auction_assist.use_instrument(self, boxes, deadline, row):
            self.log_info(f"第 {ordinal} 口出价前使用 {slot} 号槽位的竞拍仪器(点击第 {row} 行)")
            self._instrument_remaining.remove(slot)

    def _ensure_instrument_group(self, boxes: AuctionBoxes) -> None:
        """每轮开始校验并装备「仪器组」选定的组, 就绪结论供 _use_instrument_once 门控。

        装备实现见 auction_assist.ensure_instrument_group: 每轮都进「仪器组合」
        弹窗执行装备/补全(实机流程, 已用完的仪器靠底部按钮补回), 用独立预算,
        失败只影响本轮仪器, 不影响拍卖。就绪与否不看装备动作成败, 只看主界面
        卡片是否读出目标组名 (见 _gate_instrument_ready)。
        """
        self._instrument_group_ready = False
        if not self._assist_enabled(ASSIST_INSTRUMENT):
            return
        # 结算返回 False 的中途续轮可能停在出价界面而非主界面: 组卡片读得到但
        # 不是目标组的文案时装备流程会点击卡片, 在错误界面上就是误点击。先确认
        # 主界面标题; 探针失败(如「获得物品」弹窗遮罩压暗标题, auction-notes 5.5)
        # 时不再整轮放弃 —— 补一次只读的卡片复核, 卡片已是目标组就放行使用仪器。
        if not self.wait_ocr(
            box=boxes.main_title,
            match=RE_MAIN_TITLE,
            time_out=self.INSTRUMENT_GROUP_PROBE_TIMEOUT,
            raise_if_not_found=False,
            settle_time=0.5,
        ):
            self.log_warning("未确认在拍卖主界面, 跳过本轮仪器组装备校验")
            self._gate_instrument_ready(boxes)
            return
        equip_deadline = time.monotonic() + auction_assist.INSTRUMENT_GROUP_BUDGET
        state = auction_assist.ensure_instrument_group(
            self,
            boxes,
            equip_deadline,
            self._instrument_group,
        )
        if state == auction_assist.INSTRUMENT_GROUP_ACTIONED:
            # 装备/补全动作执行过, 仪器列表恢复满, 队列重置 (队列模型见 auction-notes 7)。
            self._instrument_remaining = list(auction_assist.DEFAULT_SLOT_CYCLE)
        self._gate_instrument_ready(boxes)

    def _gate_instrument_ready(self, boxes: AuctionBoxes) -> None:
        """仪器使用就绪判定: 主界面卡片读出「仪器组」目标组名即就绪。

        「仪器列表」槽位坐标点到的仪器内容只由装备的组决定 —— 卡片是目标组,
        坐标点到的就是目标组的仪器; 装备/补全动作本身失败(弹窗/列表/预算)不改变
        这个事实, 不该连坐仪器使用 (2026-10-03 用户实测第二回合起不再用仪器,
        见 auction-notes 7)。卡片读到别的组或读不出时保持未就绪, 拦住盲点。
        """
        card_text = "".join(
            b.name for b in self.ocr(box=boxes.instrument_card, match=None, log=False) or []
        ).strip()
        self._instrument_group_ready = bool(card_text) and self._instrument_group in card_text
        if self._instrument_group_ready:
            self.log_debug(f"仪器组就绪: {card_text}")
        else:
            self.log_warning(f"仪器组卡片未读到 {self._instrument_group}, 本场不用仪器")

    def _stage_result(self, boxes: AuctionBoxes, deadline: float) -> bool:
        """结果阶段: 等待结算, 处理跳过动画或返回匹配界面, 编排见 auction_settle.run_settle。"""
        return auction_settle.run_settle(self._settle_actions(), boxes, deadline)

    def _finish_auction(
        self, boxes: AuctionBoxes, skip_results: list[Box], deadline: float
    ) -> None:
        """拍卖结束处理: 跳过动画/一键出售/退出/收尾, 编排见 auction_settle.finish_auction。"""
        auction_settle.finish_auction(self._settle_actions(), boxes, skip_results, deadline)

    def _settle_actions(self) -> auction_settle.SettleActions:
        """为结算阶段装配其所需的界面与跨域动作, 见 auction_settle.SettleActions。"""
        return auction_settle.SettleActions(
            is_match_screen=self._is_match_screen,
            is_bid_screen=self._is_bid_screen,
            ocr=self.ocr,
            wait_ocr=self.wait_ocr,
            next_frame=self.next_frame,
            sleep=self.sleep,
            operate_click=self.operate_click,
            wait_operate_click=self._wait_operate_click,
            remaining_timeout=self._remaining_timeout,
            read_result_value=self._read_result_value,
            record_result_value=self._record_result_value,
            detect_one_click_sell=self._detect_one_click_sell,
            sell_mode=self._sell_mode,
            sell_on_settlement=self._sell_on_settlement_screen,
            observe_post_round=self._observe_post_round_on_main_screen,
            run_post_round=self._run_post_round_actions,
            dismiss_notice=self._dismiss_notice_popup,
            finish_auction=self._finish_auction,
            poll_interval=self.POLL_INTERVAL,
            log_info=self.log_info,
            log_warning=self.log_warning,
        )

    def _observe_post_round_on_main_screen(self, boxes: AuctionBoxes, deadline: float) -> None:
        """已经回到主界面时的结算后观测, 编排见 auction_post_round。"""
        self._post_round_coordinator().observe(
            boxes, deadline, inventory_full_timeout=auction_sell.INVENTORY_FULL_TIMEOUT
        )

    def _sell_on_settlement_screen(
        self, boxes: AuctionBoxes, deadline: float, result_value: int | None = None
    ) -> None:
        """结算界面「一键出售」, 实现见 auction_sell.sell_on_settlement_screen。"""
        auction_sell.sell_on_settlement_screen(self, boxes, deadline, result_value)

    def _run_post_round_actions(self, boxes: AuctionBoxes, deadline: float) -> None:
        """结算后的观测与低保领取编排, 各项操作通过 AuctionPostRoundActions 注入。"""
        self._post_round_coordinator().run(
            boxes, deadline, inventory_full_timeout=auction_sell.INVENTORY_FULL_TIMEOUT
        )

    def _post_round_coordinator(self) -> auction_post_round.AuctionPostRoundCoordinator:
        """为结算后协调器装配其所需的最小 UI 和领域动作。"""
        actions = auction_post_round.PostRoundActions(
            remaining_timeout=self._optional_timeout,
            confirm_main_screen=lambda boxes, timeout: bool(
                self.wait_ocr(
                    box=boxes.main_title,
                    match=RE_MAIN_TITLE,
                    time_out=timeout,
                    raise_if_not_found=False,
                    settle_time=0.5,
                )
            ),
            dismiss_notice=self._dismiss_notice_popup,
            uses_collection_sell=self._uses_collection_sell,
            detect_inventory_full=self._detect_inventory_full,
            observe_asset=self._observe_main_asset,
            welfare_enabled=lambda: self._assist_enabled(ASSIST_WELFARE),
            claim_welfare=self._claim_welfare_if_needed,
            set_state=lambda state: setattr(self, "_post_round_state", state),
            result_value=lambda: self._current_result_value,
            # 收尾兜底: 正常情况下弹窗已在 observe 开头关掉, 这里只处理流程中途
            # 新弹出的残留. 按「检测到才点」执行, 没有弹窗时零点击,
            # 不再无条件盲点空白区域.
            close_popup=lambda boxes: self._dismiss_notice_popup(
                boxes, None, "结算后收尾", timeout=1.0
            ),
            log_info=self.log_info,
            log_warning=self.log_warning,
        )
        return auction_post_round.AuctionPostRoundCoordinator(actions)

    def _sell_mode(self) -> str:
        """读取出售模式, 未知值按「不出售」处理, 规则见 auction_sell.normalize_mode。"""
        return auction_sell.normalize_mode(self.config.get(CONF_SELL_MODE, SELL_MODE_OFF))

    def _assist_enabled(self, feature: str) -> bool:
        """判断「辅助功能」多选框里是否勾选了某个功能。

        多选框存的是勾选项列表, 未勾选时为空列表。值不是列表(用户手工改成字符串或
        配置仍为旧 bool)时一律按未勾选处理: 少领一次低保金、少装备一次仪器都是可
        恢复的, 不该因为脏配置去点不存在的按钮。
        """
        selected = self.config.get(CONF_ASSIST_FEATURES, ())
        if not isinstance(selected, list):
            return False
        return feature in selected

    def _uses_collection_sell(self) -> bool:
        """本轮是否需要走「藏品仓库」出售流程, 判定见 auction_sell.uses_collection_sell。"""
        return auction_sell.uses_collection_sell(self._sell_mode())

    def _detect_inventory_full(self, boxes: AuctionBoxes, timeout: float) -> bool | None:
        """检测主界面的库存不足提示, 实现见 auction_sell.detect_inventory_full。"""
        return auction_sell.detect_inventory_full(self, boxes, timeout)

    def _observe_main_asset(self, boxes: AuctionBoxes, deadline: float) -> int | None:
        """读取主界面资产值, 实现见 auction_welfare.observe_main_asset。"""
        return auction_welfare.observe_main_asset(self, boxes, deadline)

    def _claim_welfare_if_needed(
        self, boxes: AuctionBoxes, deadline: float, asset_value: int | None
    ) -> bool:
        """资产低于阈值时领取低保金, 阈值判定见 auction_welfare.claim_welfare_if_needed。"""
        return auction_welfare.claim_welfare_if_needed(
            self, self._welfare_state, boxes, deadline, asset_value
        )

    # --- 界面状态判定 ---
    def _is_match_screen(self, boxes: AuctionBoxes) -> bool:
        return bool(self.ocr(box=boxes.match, match=RE_MATCH))

    def _is_confirm_screen(self, boxes: AuctionBoxes) -> bool:
        return bool(self.ocr(box=boxes.popup_ok, match=RE_CONFIRM))

    def _bid_panel_open(self, boxes: AuctionBoxes) -> bool:
        """数字键盘弹窗是否已打开, 键盘态判定的唯一入口 (背景见 auction-notes 2.7)。"""
        return bool(self.ocr(box=boxes.bid_keypad, match=RE_BID_PANEL))

    def _is_bid_screen(self, boxes: AuctionBoxes) -> bool:
        """判断是否已在出价界面(含数字键盘已弹出的状态)。

        键盘弹窗会盖住 BOX_BID, 那时只能靠弹窗上的文案认出界面, 否则会空等到超时。
        键盘态优先判定: 它是更具体的形态, 命中就不必再读 BOX_BID。
        """
        if self._bid_panel_open(boxes):
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
        """点掉挡在流程前面的弹窗, 两类弹窗的识别与关闭见 auction_popup。"""
        return auction_popup.dismiss_notice_popup(self, boxes, deadline, reason, timeout=timeout)

    # --- 超时辅助 ---
    @staticmethod
    def _remaining_timeout(
        deadline: float | None, limit: float, message: str = "单轮拍卖超时"
    ) -> float:
        """必须完成的步骤的等待预算: 受 deadline 限制, deadline 到期时立即失败。

        deadline 传 None 表示调用方没有上级预算(如轮次末尾的独立流程), 原样返回 limit。
        message 供「传进来的不是整轮 deadline 而是阶段 deadline」的调用点区分日志:
        匹配阶段的局部预算(MATCH_TIMEOUT)用尽时整轮往往还剩几百秒, 沿用「单轮拍卖
        超时」会让排查的人以为 600 秒跑满了。
        """
        if deadline is None:
            return limit
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaitFailedException(message)
        return min(limit, remaining)

    @staticmethod
    def _optional_timeout(deadline: float | None, limit: float) -> float | None:
        """可选观测步骤的等待预算: deadline 用尽返回 None, 由调用方跳过本次观测。

        与 _remaining_timeout 的区别: 观测失败(没测到)不该让已经成功的轮次崩掉,
        所以预算耗尽不抛异常。返回 None 而不是 0: 框架的 wait_* 把 time_out=0
        解释成默认 10 秒等待(见 auction-notes 1.2), 本原语保证「没预算」永远不会
        流回框架 API —— 调用方必须先判 None, 再把正数预算传给 wait_*。
        """
        if deadline is None:
            return limit
        remaining = min(limit, deadline - time.monotonic())
        return remaining if remaining > 0 else None

    def _bounded_sleep(self, deadline: float | None, delay: float) -> None:
        """执行受 deadline 限制的短暂操作等待。"""
        self.sleep(self._remaining_timeout(deadline, delay))

    # --- 配置读取辅助 ---
    def _config_int(self, key: str, default: int = 0, *, warn: str | None = None) -> int:
        """读取整数配置, 非法时回退到默认值并可选输出告警。"""
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            if warn:
                self.log_warning(warn)
            return default

    def _config_decimal(self, key: str, default: str = "0") -> Decimal:
        """读取十进制配置, 非法时回退到默认值。

        用于参与 Decimal 运算的配置(加价数值): 配置原值本身就是文本框里的字符串,
        直接解析能保留用户填的全部精度, 而先经 float 中转再 str() 只剩 17 位有效数字。
        非法值一律回退默认值, 与 _config_int 一样不抛异常 —— 调用方另有兜底。
        """
        try:
            return Decimal(str(self.config.get(key, default)))
        except (ArithmeticError, ValueError):
            return Decimal(default)

    def _config_int_list(self, key: str, *, warn: str | None = None) -> list[int]:
        """读取整数列表配置, 任一项非法时返回空列表并可选输出告警。"""
        try:
            return [int(item) for item in self.config.get(key, [])]
        except (TypeError, ValueError):
            if warn:
                self.log_warning(warn)
            return []

    def _quality_list(self, key: str) -> list[str]:
        """读取某个出售品质清单, 值不是列表时按空清单处理。"""
        raw = self.config.get(key, [])
        if not isinstance(raw, (list, tuple)):
            return []
        return [name for name in QUALITY_KEYS if name in raw]

    def _sell_qualities(self) -> list[str]:
        """读取本轮要出售的品质清单(勾选即出售, 清洗规则见 _quality_list)。

        曾按当日低保阶段在双清单间自动切换, 已合并为单一「出售品质」配置;
        低保与资产的联动说明见配置项描述与 auction-notes 4。
        """
        return self._quality_list(CONF_SELL_QUALITIES)

    def _validate_price_config(self) -> None:
        """任务开始前校验价格相关配置, 非法时直接终止任务。

        出价面板打开后才发现非法配置, 会以每轮 3 次重试的方式空转, 必须在入口拦截。
        校验范围随出价模式变化, 未使用的价格配置不参与校验; 模式分发的唯一
        实现在 auction_bid_price, 与算价共用同一张注册表。
        """
        auction_bid_price.validate_price_config(self._bid_mode(), self)

    def _validate_instrument_sequence(self) -> None:
        """勾选「竞拍仪器」时校验「仪器槽位序列」并缓存解析结果, 非法直接终止任务。

        序列非法若拖到出价阶段才发现, 只会表现为每场仪器被静默跳过, 在入口拦下。
        运行中的改动由 _refresh_instrument_slots 每口重读跟进; 这里同时记下配置
        原文并打一条生效序列日志, 供用户对照每口的「X 号槽位(点击第 M 行)」。
        未勾选「竞拍仪器」时不校验: 该配置此刻在面板上是隐藏的, 脏值不应拦住任务。
        """
        if not self._assist_enabled(ASSIST_INSTRUMENT):
            return
        raw = str(self.config.get(CONF_INSTRUMENT_SEQUENCE, "") or "")
        self._instrument_slots = auction_assist.parse_instrument_sequence(raw)
        self._instrument_seq_raw = raw
        self.log_info(self._describe_instrument_slots())

    def _describe_instrument_slots(self) -> str:
        """当前生效的仪器槽位轮换序列, 用于启动与运行中改动时的日志对照。"""
        if self._instrument_slots:
            return "仪器槽位序列: " + "→".join(str(slot) for slot in self._instrument_slots)
        return "仪器槽位序列未配置, 按 1→2→3→4→5 默认轮换"

    def _refresh_instrument_slots(self) -> None:
        """每口消耗前重读「仪器槽位序列」, 运行中改动即时生效(沿革见 auction-notes 7)。

        序列原先只在任务入口解析一次, 无限轮模式下运行中填写的序列整场不生效
        (2026-10-04 用户反馈)。按配置原文去重: 原文没变不重解析不刷日志; 解析
        失败只对新的原文告警一次并沿用上一次合法结果 —— 运行中因改配置终止任务
        会把现场留在拍品中间, 比沿用旧序列代价更高。
        """
        raw = str(self.config.get(CONF_INSTRUMENT_SEQUENCE, "") or "")
        if raw == self._instrument_seq_raw:
            return
        self._instrument_seq_raw = raw
        try:
            slots = auction_assist.parse_instrument_sequence(raw)
        except ValueError as e:
            self.log_warning(f"仪器槽位序列非法, 沿用上一次的解析结果: {e}")
            return
        if slots != self._instrument_slots:
            self._instrument_slots = slots
            # 游标归零: 旧游标位置对新序列没有意义, 改动从新序列第 1 项开始生效。
            self._instrument_seq = 0
            self.log_info(self._describe_instrument_slots())

    def _validate_instrument_group(self) -> None:
        """勾选「竞拍仪器」时校验「仪器组」取值并缓存组名, 非法直接终止任务。

        下拉框本身限定了取值, 这里拦的是手改配置文件写进来的脏值: 组名错一个字,
        装备校验会在组列表里永远找不到该组而每轮空转放弃。未勾选「竞拍仪器」时
        不校验(配置此刻在面板上隐藏), 沿用默认组(INSTRUMENT_GROUP_SUPREME)。
        """
        if not self._assist_enabled(ASSIST_INSTRUMENT):
            return
        group = str(self.config.get(CONF_INSTRUMENT_GROUP, INSTRUMENT_GROUP_SUPREME))
        if group not in INSTRUMENT_GROUPS:
            raise ValueError(f"仪器组只能是下拉候选之一: {group!r}")
        self._instrument_group = group

    def _warn_if_no_sellable_quality(self) -> None:
        """出售品质清单为空时给出告警: 开了出售模式却没有可出售的品质。

        配置本身不非法(用户可以随时改), 所以只告警不拦截。但满仓时这种配置会让
        出售一直报「成功」却清不出空间, 之后每轮出价都失败, 提前说清楚更好排查。
        """
        if not self._uses_collection_sell():
            return

        if self._quality_list(CONF_SELL_QUALITIES):
            return
        self.log_warning(
            f"「{CONF_SELL_QUALITIES}」没有勾选品质, 本次运行不会清掉任何藏品"
        )

    def _read_estimate_value(
        self,
        box: Box,
        timeout: float,
        label: str = "当前估价",
        label_keywords: Iterable[str] = ("估价",),
    ) -> tuple[int | None, bool]:
        """读估价数字: 按标签定位, 取标签右侧数字, 实现见 auction_reading.read_estimate_value。"""
        return auction_reading.read_estimate_value(self, box, timeout, label, label_keywords)

    def _read_asset_value(
        self, box: Box, timeout: float, label: str = "资产", *, reject_partial: bool = False
    ) -> int | None:
        """OCR 解析资产数值, 实现见 auction_reading.read_asset_value。"""
        return auction_reading.read_asset_value(
            self, box, timeout, label, reject_partial=reject_partial
        )

    def _read_result_value(self, boxes: AuctionBoxes, deadline: float) -> int | None:
        """读结算面板的成交价值: 读取预算内反复重读, 读不出返回 None。

        结算价值与出价面板估价共用同一段屏幕位置(初值取 BOX_ESTIMATE, 依据见
        auction-notes 3), 标签关键词放宽到「估价/价值/成交」。单帧读取会把界面
        过渡帧读成 None —— f818fbf 起该读数决定「低于此价才卖红」是否放行, 读
        不出的代价从少一条记录变成整件不卖, 所以在预算内重读, 命中可解析且不
        贴边的帧即返回(沿革见 auction-notes 7)。贴边读数可能缺末位, 一律按未读
        出处理, 不因重读放宽; 仍读不出时留一帧 OCR 原文供校准(标签词与识别区域
        均未实机验证, 见 auction-notes 3)。
        """
        timeout = self._optional_timeout(deadline, self.RESULT_VALUE_READ_TIMEOUT)
        if timeout is None:
            return None
        try:
            inner_deadline = time.monotonic() + timeout
            while True:
                value, tight = self._read_estimate_value(
                    boxes.result_value,
                    max(0.1, inner_deadline - time.monotonic()),
                    "结算价值",
                    label_keywords=RESULT_VALUE_LABELS,
                )
                if value is not None and not tight:
                    return value
                if time.monotonic() >= inner_deadline:
                    break
                self.sleep(min(0.2, inner_deadline - time.monotonic()))
        except TaskDisabledException:
            raise
        except Exception as e:
            # 结算价值是纯观测: 界面过渡帧里出现任何识别异常都按未读出处理,
            # 不能让一条记录赔上已经拍成的整轮。
            self._log_aux_error("结算价值读取异常, 按未读出处理", e, quiet=True)
            return None
        self._log_result_value_raw(boxes)
        return None

    def _log_result_value_raw(self, boxes: AuctionBoxes) -> None:
        """结算价值读不出时留一帧区域 OCR 原文: 标签词不匹配/框偏移靠它定位。"""
        try:
            texts = self.ocr(box=boxes.result_value, match=None, log=False)
        except Exception as e:
            self._log_aux_error("结算价值区域 OCR 原文留痕失败", e, quiet=True)
            return
        names = [b.name for b in texts if b.name] if texts else []
        if names:
            self.log_warning(f"结算价值区域 OCR 原文: {names}, 未解析出成交价值")
        else:
            self.log_warning("结算价值区域未 OCR 到任何文本, 请检查识别区域标定")

    def _detect_one_click_sell(self, boxes: AuctionBoxes, deadline: float) -> list:
        """结算界面「一键出售」按钮 OCR 检测, 实现见 auction_sell.detect_one_click_sell。"""
        return auction_sell.detect_one_click_sell(self, boxes, deadline)

    def _record_result_value(self, value: int, confirmed: bool = False) -> None:
        """记录本轮结算面板读数: confirmed 表示「一键出售」已识别到(成交判据)。

        默认 False 是保守方向: 漏传参按未确认处理, 只少一条成交记录, 不误计。
        未确认时只保留原始观测写回(_current_result_value 供结算后观测使用),
        不写「上轮成交价值」、不进会话累计、不触发截图 —— 流拍的结算面板也可能
        读出非零数字, 成交判定以按钮命中为准(见 auction-notes 4)。
        """
        self._current_result_value = value
        if not confirmed:
            self.log_info(f"结算面板读数 {value}, 未识别到「一键出售」, 不计为成交")
            return
        self.info_set("上轮成交价值", value)
        self.log_info(f"本轮成交价值: {value}")
        if value <= 0:
            return

        self._session_result_rounds += 1
        self._session_result_total += value
        screenshot_min = self._config_int(CONF_RESULT_SCREENSHOT_MIN, 0)
        if screenshot_min <= 0 or value < screenshot_min:
            return
        try:
            self.screenshot(name=f"auction_result_{value}")
        except TaskDisabledException:
            raise
        except Exception as e:
            # 截图失败只少一张留档, 不能把已经拍成的轮次拖成失败.
            self._log_aux_error("成交价值截图保存失败", e, quiet=True)
            return
        self.log_info(f"成交价值 {value} 达到截图下限 {screenshot_min}, 已保存截图")

    # --- 自动加价计算 ---
    def _bid_mode(self) -> str:
        """读取出价模式, 默认「自定义价格」。

        模式分发唯一实现在 auction_bid_price 的注册表, 运行时算价与入口校验
        共用同一张表, 新增模式只需在注册表加一项; 取值非法时由注册表抛
        带可选值的 ValueError(见 auction-notes 4), 本方法只负责读取。
        """
        return self.config.get(CONF_BID_MODE, BID_MODE_CUSTOM)

    def _calculate_auction_price(
        self, boxes: AuctionBoxes | None = None, deadline: float | None = None
    ) -> int:
        """计算当前出价应输入的价格, 模式分发的单一来源在 auction_bid_price。"""
        return auction_bid_price.calculate_price(self, boxes, deadline)

    def _apply_heart_increment(self, price: int) -> int:
        """检测到永恒之心时追加固定加价, 实现见 auction_bid_price.apply_heart_increment。"""
        return auction_bid_price.apply_heart_increment(self, price)

    def _resolve_bid_prices(self) -> list[int]:
        """读取并解析 6 个每轮指定价格, 实现见 auction_bid_price.resolve_bid_prices。"""
        return auction_bid_price.resolve_bid_prices(self)

    def _read_stable_asset_value(
        self,
        box: Box,
        timeout: float,
        label: str,
        *,
        skip_zero: bool = False,
    ) -> int | None:
        """连续读到相同值才采用的稳定读取, 实现见 auction_reading.read_stable_asset_value。"""
        return auction_reading.read_stable_asset_value(
            self, box, timeout, label, skip_zero=skip_zero
        )

    def _estimate_bid_price(
        self, boxes: AuctionBoxes | None, deadline: float | None, bid_count: int
    ) -> int:
        """按出价面板估价乘倍率出价, 回退规则见 auction_bid_price.estimate_bid_price。"""
        return auction_bid_price.estimate_bid_price(self, boxes, deadline, bid_count)

    def _smart_bid_price(self, boxes: AuctionBoxes | None, deadline: float | None) -> int:
        """逐口追踪模式出价, 策略与回退见 auction_bid_price.smart_bid_price。"""
        return auction_bid_price.smart_bid_price(self, boxes, deadline)

    def _raise_mode(self) -> str:
        """读取加价方式, 无效值按默认「倍率」处理。

        配置迁移已全部删除。旧配置或手工编辑留下的无效取值不能静默落到「自定义」分支,
        否则同一份价格配置会从指数增长变成线性增长, 所以直接回退到默认方式。
        """
        mode = self.config.get(CONF_RAISE_MODE, RAISE_MODE_MULTIPLE)
        return mode if mode in RAISE_MODES else RAISE_MODE_MULTIPLE

    def _raise_price(self, base_price: int, bid_count: int) -> int:
        """按配置的加价方式计算第 bid_count 次出价的价格。

        实现见 auction_bid_price.raise_price。
        """
        return auction_bid_price.raise_price(self, base_price, bid_count)

    # --- 价格输入 ---
    def _input_fixed_price(
        self, boxes: AuctionBoxes, price: int | None = None, deadline: float | None = None
    ) -> None:
        """数字键盘输入价格并校验, 实现见 auction_keypad.input_price。

        确认成功的价格在这里落账为上轮出价: 键盘模块不回写任务状态,
        快捷复用与逐口追踪下界守卫读到的都是这份值。
        输入是必须连续完成的点击序列, 期间挂起月卡钩子; 校验失败抛出的异常
        会先恢复钩子再向上传播, 下一次出价重试时弹窗兜底仍然生效。
        """
        with auction_interaction.atomic_sequence(self, reason="价格输入"):
            self.last_bid_price = auction_keypad.input_price(
                self._keypad_actions(),
                boxes,
                price,
                deadline=deadline,
                last_bid_price=self.last_bid_price,
            )

    def _keypad_actions(self) -> auction_keypad.KeypadActions:
        """为键盘输入装配其所需的界面动作与输入决策依据。"""
        return auction_keypad.KeypadActions(
            calculate_price=self._calculate_auction_price,
            remaining_timeout=self._remaining_timeout,
            wait_operate_click=self._wait_operate_click,
            dismiss_notice_popup=self._dismiss_notice_popup,
            operate_click=self.operate_click,
            box_of_screen=self.box_of_screen,
            wait_ocr=self.wait_ocr,
            auto_raise_enabled=bool(self.config.get(CONF_AUTO_RAISE, False)),
            bid_notice_popup_timeout=auction_keypad.BID_NOTICE_POPUP_TIMEOUT,
            log_info=self.log_info,
            log_debug=self.log_debug,
            log_warning=self.log_warning,
        )

    # --- 低保金 ---
    def _rollover_welfare_day(self) -> None:
        """跨过每日刷新时刻清空当日领取记录, 切日规则见 auction_welfare.rollover_welfare_day。"""
        auction_welfare.rollover_welfare_day(self._welfare_state)

    def _wait_click_optional(
        self,
        box: Box,
        match: re.Pattern,
        deadline: float | None,
        timeout: float,
        desc: str,
    ) -> bool:
        """等待并点击目标控件, 超时未出现时返回 False; deadline 到期仍会抛出单轮超时。"""
        clicked = self._wait_operate_click(box, match, self._remaining_timeout(deadline, timeout))
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
    def _run_round_end_sell(
        self,
        boxes: AuctionBoxes,
        deadline: float | None = None,
        state: PostRoundState | None = None,
    ) -> None:
        """按出售模式决定本轮是否出售, 实现见 auction_sell.run_round_end_sell。

        出售结论在这里落账: _inventory_stuck 是任务侧的跨轮编排状态, 出售模块
        只报告三态结果(True 仍满仓 / False 已清掉 / None 无新结论); 异常路径
        不会走到落账, 标记保持原值 (误置与误清的代价见 auction-notes 5.2)。
        """
        outcome = auction_sell.run_round_end_sell(self, boxes, deadline, state=state)
        if outcome is not None:
            self._inventory_stuck = outcome

    def _close_warehouse(self, boxes: AuctionBoxes) -> None:
        """关掉藏品仓库界面复位出售模式与勾选态, 实现见 auction_sell.close_warehouse。"""
        auction_sell.close_warehouse(self, boxes)

    def _is_warehouse_open(self, boxes: AuctionBoxes) -> bool:
        """检测藏品仓库界面是否还在, 实现见 auction_sell.is_warehouse_open。"""
        return auction_sell.is_warehouse_open(self, boxes)