import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

from ok import Config, TaskDisabledException, WaitFailedException
from ok.core.config_schema import build_config_fields

import src.tasks.AutoBidAuctionTask as auction_module
from src.tasks.AutoBidAuctionTask import (
    RE_CANCEL,
    RE_CLAIM,
    RE_MAIN_TITLE,
    RE_ONE_CLICK_SELL,
    RE_POPUP_CLOSE_HINT,
    AuctionState,
    AutoBidAuctionTask,
    PostRoundState,
)
from src.tasks.BaseNTETask import BaseNTETask


def _config(**values) -> Mock:
    """构造只认识给定键的配置对象, 未给出的键返回调用方传入的默认值。"""
    return Mock(get=lambda key, default=None: values.get(key, default))


def _make_task(config: dict | None = None) -> AutoBidAuctionTask:
    """构造跳过 ok 框架初始化的任务实例, 只保留被测方法需要的依赖。"""
    task = AutoBidAuctionTask.__new__(AutoBidAuctionTask)
    task.log_info = Mock()
    task.log_debug = Mock()
    task.log_warning = Mock()
    task.log_error = Mock()
    task.sleep = Mock()
    task.next_frame = Mock()
    task.config = _config(**(config or {}))
    task.ocr = Mock(return_value=[])
    task.wait_ocr = Mock(return_value=[])
    task.wait_until = Mock(return_value=True)
    task.operate_click = Mock(return_value=True)
    task.box_of_screen = Mock(return_value=Mock())
    task.find_monthly_card = Mock(return_value=None)
    task.check_monthly_card = Mock(return_value=False)
    task.handle_monthly_card = Mock()
    # 框架的 wait_click_ocr 直接走 click_box, 不会保存和还原鼠标位置,
    # 后台执行时会把用户的鼠标留在游戏窗口内; 任务内任何调用都视为回归。
    task.wait_click_ocr = Mock(side_effect=AssertionError("不应使用 wait_click_ocr"))
    task.current_bid_count = 0
    task.last_bid_price = None
    task._post_round_state = PostRoundState()
    task._sell_failures = 0
    task._inventory_stuck = False
    return task


class _FakeTime:
    """可控时钟: 让 sleep 推进时间, 以便断言重试时机而不用真的等待。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)


def _make_configured_task() -> AutoBidAuctionTask:
    """只跑完 __init__ 的配置装配, 不触碰 ok 框架初始化。"""
    task = AutoBidAuctionTask.__new__(AutoBidAuctionTask)
    task.default_config = {}
    task.config_description = {}
    task.add_rounds_config = Mock()
    task.add_exit_after_config = Mock()
    with patch.object(BaseNTETask, "__init__", return_value=None):
        AutoBidAuctionTask.__init__(task)
    return task


def _stub_round_loop(task: AutoBidAuctionTask, total_polls: int) -> None:
    """把轮次循环收敛到固定次数, 避免依赖真实配置和 info 面板。"""
    task.start_rounds = Mock()
    task._validate_price_config = Mock()
    task._build_boxes = Mock(return_value=Mock())
    task.finish_rounds = Mock()
    task.add_failed = Mock()
    task.begin_round = Mock(return_value=True)

    polls = {"count": 0}

    def has_remaining_rounds():
        polls["count"] += 1
        return polls["count"] <= total_polls

    task.has_remaining_rounds = has_remaining_rounds


class TestAuctionSleepCheckHook(unittest.TestCase):
    def test_sleep_check_closes_monthly_card_popup(self):
        """月卡时间窗内出现弹窗时, 睡眠钩子先关闭弹窗再继续当前轮次。"""
        task = _make_task()
        task.check_monthly_card.return_value = True

        task.sleep_check()

        task.handle_monthly_card.assert_called_once()

    def test_sleep_check_is_noop_outside_window(self):
        """不在月卡时间窗内时睡眠钩子没有额外动作, 避免每次 sleep 都白点一次。"""
        task = _make_task()

        task.sleep_check()

        task.handle_monthly_card.assert_not_called()

    @patch.object(BaseNTETask, "__init__", return_value=None)
    def test_init_enables_sleep_check_interval(self, _base_init):
        """ok-script 只在 sleep_check_interval >= 0 时调用钩子, 默认 -1 会让月卡处理静默失效。"""
        task = AutoBidAuctionTask.__new__(AutoBidAuctionTask)
        task.default_config = {}
        task.config_description = {}
        task.add_rounds_config = Mock()
        task.add_exit_after_config = Mock()

        AutoBidAuctionTask.__init__(task)

        self.assertGreaterEqual(task.sleep_check_interval, 0)
        self.assertEqual(task.sleep_check_interval, AutoBidAuctionTask.SLEEP_CHECK_INTERVAL)


class TestAuctionBlockingPopupRecovery(unittest.TestCase):
    def test_recover_blocking_popup_closes_popup_found_by_feature(self):
        """时间窗已过(任务在 5 点后才启动)时, 靠弹窗特征兜底关闭, 避免每轮空转到超时。"""
        task = _make_task()
        task.find_monthly_card.return_value = object()

        task._recover_blocking_popup()

        task.handle_monthly_card.assert_called_once()

    def test_recover_blocking_popup_dismisses_notice_popups(self):
        """入场费/异常出价/满仓提示共用一套模板, 整轮失败后也要兜一次。"""
        task = _make_task()
        task._dismiss_notice_popup = Mock()

        task._recover_blocking_popup(Mock())

        task._dismiss_notice_popup.assert_called_once()

    def test_recover_blocking_popup_is_noop_without_popup(self):
        task = _make_task()

        task._recover_blocking_popup()

        task.handle_monthly_card.assert_not_called()

    def test_recover_blocking_popup_swallows_handler_failure(self):
        """兜底处理本身失败时只告警, 不能把异常抛出去中止整个任务。"""
        task = _make_task()
        task.find_monthly_card.return_value = object()
        task.handle_monthly_card.side_effect = WaitFailedException("月卡关闭失败")

        task._recover_blocking_popup()

        task.log_warning.assert_called_once()

    def test_recover_blocking_popup_propagates_task_disabled(self):
        task = _make_task()
        task.find_monthly_card.return_value = object()
        task.handle_monthly_card.side_effect = TaskDisabledException("disabled")

        with self.assertRaises(TaskDisabledException):
            task._recover_blocking_popup()


class TestAuctionRoundFailureHandling(unittest.TestCase):
    def test_failed_round_triggers_popup_recovery(self):
        """整轮失败后按弹窗特征兜底一次, 覆盖月卡时间窗已过的情况。"""
        task = _make_task()
        _stub_round_loop(task, total_polls=1)
        task._run_single_round = Mock(side_effect=WaitFailedException("boom"))
        task._recover_blocking_popup = Mock()

        task.do_run()

        task.add_failed.assert_called_once()
        task._recover_blocking_popup.assert_called_once()

    def test_successful_round_does_not_trigger_popup_recovery(self):
        task = _make_task()
        _stub_round_loop(task, total_polls=1)
        task._run_single_round = Mock(return_value=None)
        task._recover_blocking_popup = Mock()

        task.do_run()

        task.add_failed.assert_not_called()
        task._recover_blocking_popup.assert_not_called()

    def test_task_disabled_is_not_treated_as_round_failure(self):
        task = _make_task()
        _stub_round_loop(task, total_polls=1)
        task._run_single_round = Mock(side_effect=TaskDisabledException("disabled"))
        task._recover_blocking_popup = Mock()

        with self.assertRaises(TaskDisabledException):
            task.do_run()

        task.add_failed.assert_not_called()

    def test_invalid_config_still_finishes_the_rounds(self):
        """入场校验抛错时轮次汇总和结束通知不能跟着一起丢。

        校验若放在 try 之外, finish_rounds 不会执行: 用户收不到任务结束通知,
        info 面板也停在「进行中」。
        """
        task = _make_task()
        _stub_round_loop(task, total_polls=1)
        task._run_single_round = Mock()
        task._validate_price_config = Mock(side_effect=ValueError("基础价必须为正整数"))

        with self.assertRaises(ValueError):
            task.do_run()

        task._run_single_round.assert_not_called()
        task.finish_rounds.assert_called_once()

    def test_run_warns_about_contradictory_sell_config(self):
        """两条入场告警都必须在 do_run 里真的被调用, 光有方法不算。

        告警只在启动时说一次, 漏调不会让任何流程失败, 因此最容易被静默删掉。
        """
        task = _make_task()
        _stub_round_loop(task, total_polls=1)
        task._run_single_round = Mock()
        task._warn_if_no_sellable_quality = Mock()
        task._warn_if_extra_sell_is_redundant = Mock()

        task.do_run()

        task._warn_if_no_sellable_quality.assert_called_once()
        task._warn_if_extra_sell_is_redundant.assert_called_once()

    def test_warnings_run_after_price_validation(self):
        """校验失败时不该再报出售配置的告警 —— 先处理真正会拦下任务的问题。"""
        task = _make_task()
        _stub_round_loop(task, total_polls=1)
        task._run_single_round = Mock()
        task._validate_price_config = Mock(side_effect=ValueError("基础价必须为正整数"))
        task._warn_if_extra_sell_is_redundant = Mock()

        with self.assertRaises(ValueError):
            task.do_run()

        task._warn_if_extra_sell_is_redundant.assert_not_called()


class TestAuctionWelfareDialogClose(unittest.TestCase):
    """低保金每日次数用尽(5/5)时弹窗仍会打开但没有领取按钮, 必须继续关闭。"""

    def _boxes(self) -> Mock:
        return Mock()

    def test_claim_button_missing_still_closes_dialog(self):
        task = _make_task()
        task._wait_click_optional = Mock(side_effect=[True, False])
        task._close_welfare_dialog = Mock(return_value=True)

        claimed = task._try_claim_welfare(self._boxes(), None)

        self.assertTrue(claimed)
        task._close_welfare_dialog.assert_called_once()

    def test_claim_success_also_closes_dialog(self):
        task = _make_task()
        task._wait_click_optional = Mock(return_value=True)
        task._close_welfare_dialog = Mock(return_value=True)

        claimed = task._try_claim_welfare(self._boxes(), None)

        self.assertTrue(claimed)
        task._close_welfare_dialog.assert_called_once()

    def test_welfare_button_missing_skips_everything(self):
        task = _make_task()
        task._wait_click_optional = Mock(return_value=False)
        task._close_welfare_dialog = Mock(return_value=True)

        claimed = task._try_claim_welfare(self._boxes(), None)

        self.assertFalse(claimed)
        task._close_welfare_dialog.assert_not_called()

    def test_close_dialog_returns_true_when_already_closed(self):
        task = _make_task()
        task._is_welfare_dialog_open = Mock(return_value=False)
        task._wait_click_optional = Mock()

        self.assertTrue(task._close_welfare_dialog(self._boxes(), None))
        task._wait_click_optional.assert_not_called()

    def test_close_dialog_retries_cancel_until_closed(self):
        """第一次点击取消没生效时必须继续重试, 否则弹窗会一直挡住拍卖界面。"""
        task = _make_task()
        # 循环内每次点击取消后各查一次: 第 1 轮仍开着, 第 2 轮关闭。
        task._is_welfare_dialog_open = Mock(side_effect=[True, True, True, False])
        task._wait_click_optional = Mock(return_value=True)

        self.assertTrue(task._close_welfare_dialog(self._boxes(), None))
        self.assertEqual(task._wait_click_optional.call_count, 2)

    def test_close_dialog_reports_failure_after_retries(self):
        task = _make_task()
        task._is_welfare_dialog_open = Mock(return_value=True)
        task._wait_click_optional = Mock(return_value=True)

        self.assertFalse(task._close_welfare_dialog(self._boxes(), None))
        self.assertEqual(
            task._wait_click_optional.call_count, AutoBidAuctionTask.WELFARE_CLOSE_RETRIES
        )

    def test_recover_blocking_popup_closes_stuck_welfare_dialog(self):
        """整轮失败后按界面特征兜底关闭残留的低保金弹窗。"""
        task = _make_task()
        task._is_welfare_dialog_open = Mock(return_value=True)
        task._close_welfare_dialog = Mock(return_value=True)

        task._recover_blocking_popup(self._boxes())

        task._close_welfare_dialog.assert_called_once()
        task.handle_monthly_card.assert_not_called()


class TestAuctionInventoryFullSell(unittest.TestCase):
    """满仓会挡住拍卖(库存不足无法出价), 不能只等轮次间隔。"""

    def setUp(self):
        # current_round 是只读 property, 需要覆盖到类上才能构造不同轮次。
        self._round_patcher = patch.object(
            AutoBidAuctionTask, "current_round", new_callable=PropertyMock
        )
        self.current_round = self._round_patcher.start()
        self.addCleanup(self._round_patcher.stop)

    def _task(self, *, interval: int, current_round: int, mode: str | None = None):
        task = _make_task()
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_SELL_MODE: (
                    AutoBidAuctionTask.SELL_MODE_INTERVAL if mode is None else mode
                ),
                AutoBidAuctionTask.CONF_SELL_INTERVAL: interval,
                AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES: [],
            }
        )
        self.current_round.return_value = current_round
        task._sell_collections = Mock(return_value=True)
        return task

    def test_sells_when_inventory_full_before_interval(self):
        """间隔设为 3 时, 第 2 轮满仓必须立刻出售而不是等到第 3 轮。"""
        task = self._task(interval=3, current_round=2)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=True))

        task._sell_collections.assert_called_once()

    def test_sells_on_interval_when_inventory_not_full(self):
        task = self._task(interval=3, current_round=3)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=False))

        task._sell_collections.assert_called_once()

    def test_skips_when_neither_condition_met(self):
        task = self._task(interval=3, current_round=2)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=False))

        task._sell_collections.assert_not_called()

    def test_off_mode_never_sells(self):
        """模式为「不出售」时, 满仓和间隔都不该触发出售。"""
        task = self._task(interval=3, current_round=3, mode=AutoBidAuctionTask.SELL_MODE_OFF)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=True))

        task._sell_collections.assert_not_called()

    def test_invalid_interval_falls_back_to_inventory_cleanup(self):
        """「按间隔出售」下间隔为 0 是无效配置, 退化成满仓清理而不是完全不出售。"""
        task = self._task(interval=0, current_round=2)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=True))

        task._sell_collections.assert_called_once()

    def test_invalid_interval_does_not_sell_on_round_without_full(self):
        task = self._task(interval=0, current_round=3)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=False))

        task._sell_collections.assert_not_called()

    def test_full_mode_ignores_interval(self):
        """「满仓时清理」不看轮次: 间隔命中也不出售。"""
        task = self._task(interval=3, current_round=3, mode=AutoBidAuctionTask.SELL_MODE_FULL)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=False))

        task._sell_collections.assert_not_called()

    def test_full_mode_sells_when_inventory_full(self):
        task = self._task(interval=3, current_round=2, mode=AutoBidAuctionTask.SELL_MODE_FULL)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=True))

        task._sell_collections.assert_called_once()

    def test_detects_inventory_when_state_unknown(self):
        """结算后处理没跑到时状态未知, 出售分支要自己补一次满仓检测。"""
        task = self._task(interval=3, current_round=2)
        task._detect_inventory_full = Mock(return_value=True)

        task._sell_collections_on_interval(Mock())

        task._sell_collections.assert_called_once()

    def test_unknown_mode_is_treated_as_off(self):
        """脏配置(拼写错误/旧值残留)必须按「不出售」处理, 不能意外清空仓库。"""
        task = self._task(interval=3, current_round=3, mode="随便写的模式")

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=True))

        task._sell_collections.assert_not_called()

    def test_uses_collection_sell_follows_config(self):
        task = self._task(interval=0, current_round=1, mode=AutoBidAuctionTask.SELL_MODE_OFF)
        self.assertFalse(task._uses_collection_sell())

        task.config = _config(
            **{AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_INTERVAL}
        )
        self.assertTrue(task._uses_collection_sell())

        task.config = _config(
            **{AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_FULL}
        )
        self.assertTrue(task._uses_collection_sell())

    def _post_round_task(self, mode: str, *, inventory_full: bool):
        task = self._task(interval=3, current_round=2, mode=mode)
        task._dismiss_notice_popup = Mock()
        task._detect_inventory_full = Mock(return_value=inventory_full)
        task._sell_collections_with_escalation = Mock(return_value=True)
        return task

    def test_post_round_actions_only_observe_and_never_sell(self):
        """结算后处理只观测, 出售统一由轮次末尾决定。

        两处都动手会让同一轮卖两次: 第二次面对已被卖空的仓库读到「出售价值 0」,
        白白累计失败次数, 最终触发「放宽保留品质」把用户要保留的藏品也卖掉。
        """
        task = self._post_round_task(AutoBidAuctionTask.SELL_MODE_FULL, inventory_full=True)

        task._run_post_round_actions(Mock(), None)

        task._sell_collections_with_escalation.assert_not_called()
        self.assertTrue(task._post_round_state.observed)
        self.assertTrue(task._post_round_state.inventory_full)

    def test_post_round_actions_record_the_observation(self):
        task = self._post_round_task(AutoBidAuctionTask.SELL_MODE_INTERVAL, inventory_full=False)

        task._run_post_round_actions(Mock(), None)

        task._sell_collections_with_escalation.assert_not_called()
        self.assertTrue(task._post_round_state.observed)
        self.assertFalse(task._post_round_state.inventory_full)

    def test_off_mode_does_not_even_probe_the_inventory(self):
        """「不出售」模式不该为满仓检测花时间。"""
        task = self._post_round_task(AutoBidAuctionTask.SELL_MODE_OFF, inventory_full=True)

        task._run_post_round_actions(Mock(), None)

        task._sell_collections_with_escalation.assert_not_called()
        task._detect_inventory_full.assert_not_called()


class TestAuctionConditionalSell(unittest.TestCase):
    """满仓或领完低保后追加出售指定颜色藏品。"""

    def _task(self, qualities) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES: qualities,
                AutoBidAuctionTask.CONF_KEEP_QUALITIES: ["品质红"],
            }
        )
        return task

    def test_no_extra_qualities_without_condition(self):
        task = self._task(["品质紫"])

        self.assertEqual(task._extra_sell_qualities(PostRoundState(inventory_full=False)), [])

    def test_extra_qualities_on_inventory_full(self):
        task = self._task(["品质紫"])

        self.assertEqual(
            task._extra_sell_qualities(PostRoundState(inventory_full=True)), ["品质紫"]
        )

    def test_extra_qualities_on_welfare_claimed(self):
        task = self._task(["品质紫"])

        self.assertEqual(
            task._extra_sell_qualities(PostRoundState(welfare_claimed=True)), ["品质紫"]
        )

    def test_unknown_qualities_are_ignored(self):
        task = self._task(["品质紫", "不存在的品质"])

        self.assertEqual(
            task._extra_sell_qualities(PostRoundState(inventory_full=True)), ["品质紫"]
        )

    def test_quality_filters_click_extra_quality_even_if_kept(self):
        """追加出售的品质要覆盖「保留藏品品质」, 否则满仓时仍卖不掉。"""
        task = self._task(["品质紫"])
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_KEEP_QUALITIES: ["品质红", "品质紫"],
                AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES: ["品质紫"],
            }
        )

        task._select_quality_filters(None, ["品质紫"])

        clicked = [tuple(call.args) for call in task.box_of_screen.call_args_list]
        self.assertEqual(len(clicked), len(AutoBidAuctionTask.QUALITY_BOXES) - 1)
        self.assertNotIn(AutoBidAuctionTask.QUALITY_BOXES[5], clicked)  # 品质红仍保留

    def test_quality_filters_keep_configured_qualities(self):
        task = self._task([])

        task._select_quality_filters(None)

        clicked = [tuple(call.args) for call in task.box_of_screen.call_args_list]
        self.assertEqual(len(clicked), len(AutoBidAuctionTask.QUALITY_BOXES) - 1)
        self.assertNotIn(AutoBidAuctionTask.QUALITY_BOXES[5], clicked)  # 品质红


class TestAuctionBidMode(unittest.TestCase):
    """新增出价模式: 每轮指定价格 / 按系统估价。"""

    def _task(self, **values) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(**values)
        return task

    # --- 每轮指定价格 ---
    @staticmethod
    def _prices(*values: int) -> dict[str, int]:
        """按出价顺序把价格映射到 6 个「第N次出价价格」配置项。"""
        return dict(zip(AutoBidAuctionTask.CONF_BID_PRICES, values))

    def test_there_is_one_price_config_per_bid_round(self):
        self.assertEqual(len(AutoBidAuctionTask.CONF_BID_PRICES), 6)
        self.assertEqual(AutoBidAuctionTask.MAX_BID_ROUNDS, 6)

    def test_each_round_uses_its_own_price(self):
        """6 次出价各自取自己的价格: 第1次 99999 / 第2次 123456 / 第3次 886 / 第4次 1314520。"""
        prices = (99999, 123456, 886, 1314520, 50000, 60000)
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(*prices),
            }
        )

        for bid_index, expected in enumerate(prices):
            with self.subTest(bid=bid_index + 1):
                task.current_bid_count = bid_index
                self.assertEqual(task._calculate_auction_price(), expected)

    def test_unset_round_reuses_previous_price(self):
        """只填前 3 次时, 后面的回合沿用第 3 次的价格。"""
        task = self._task(**self._prices(100, 200, 300))

        self.assertEqual(task._resolve_bid_prices(), [100, 200, 300, 300, 300, 300])

    def test_blank_round_in_the_middle_reuses_previous_price(self):
        """中间漏填的回合沿用上一次的价格, 而不是被跳过。"""
        task = self._task(**self._prices(100, 0, 300))

        self.assertEqual(task._resolve_bid_prices(), [100, 100, 300, 300, 300, 300])

    def test_first_bid_price_is_required(self):
        """第 1 次出价没有价格时无法出价, 必须在任务入口拦截。"""
        task = self._task(**self._prices(0, 500))

        self.assertEqual(task._resolve_bid_prices(), [])

    def test_bid_price_accepts_string_values(self):
        """配置界面可能把数字存成字符串, 解析要容忍。"""
        task = self._task(**self._prices("99999", "abc"))

        self.assertEqual(task._resolve_bid_prices(), [99999, 99999, 99999, 99999, 99999, 99999])

    def test_list_mode_reuses_last_price_beyond_configured_rounds(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(100, 200),
            }
        )

        task.current_bid_count = 5
        self.assertEqual(task._calculate_auction_price(), 200)

    def test_list_mode_ignores_auto_raise(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                AutoBidAuctionTask.CONF_AUTO_RAISE: True,
                AutoBidAuctionTask.CONF_RAISE_MODE: AutoBidAuctionTask.RAISE_MODE_MULTIPLE,
                AutoBidAuctionTask.CONF_RAISE_VALUE: "1.6",
                AutoBidAuctionTask.CONF_FIXED_PRICE: 1000,
                **self._prices(100),
            }
        )

        task.current_bid_count = 2
        self.assertEqual(task._calculate_auction_price(), 100)

    # --- 按系统估价 ---
    def test_estimate_mode_uses_estimate_times_ratio(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_ESTIMATE,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "1.5",
                AutoBidAuctionTask.CONF_FIXED_PRICE: 1,
            }
        )
        task._read_asset_value = Mock(return_value=35301)

        self.assertEqual(task._calculate_auction_price(Mock(), None), 52952)

    def test_estimate_mode_falls_back_to_base_price(self):
        """估价被遮挡识别不到时不能中断整场拍卖, 只回退到基础价。"""
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_ESTIMATE,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "1",
                AutoBidAuctionTask.CONF_FIXED_PRICE: 777,
            }
        )
        task._read_asset_value = Mock(return_value=None)
        # 稳定读取会重试到 ESTIMATE_STABLE_TIMEOUT, 用假时钟避免测试真的等 10 秒。
        clock = _FakeTime()
        task.sleep = Mock(side_effect=clock.sleep)

        with patch.object(auction_module, "time", clock):
            self.assertEqual(task._calculate_auction_price(Mock(), None), 777)

    def test_custom_mode_keeps_legacy_behavior(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_CUSTOM,
                AutoBidAuctionTask.CONF_FIXED_PRICE: 1000,
                AutoBidAuctionTask.CONF_AUTO_RAISE: False,
            }
        )

        task.current_bid_count = 3
        self.assertEqual(task._calculate_auction_price(), 1000)

    # --- 入口校验 ---
    def test_validate_rejects_missing_first_bid_price(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(0, 200),
            }
        )

        with self.assertRaises(ValueError):
            task._validate_price_config()

    def test_validate_accepts_list_mode_without_base_price(self):
        """每轮指定价格不使用基础价, 基础价非法不应拦住任务。"""
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                AutoBidAuctionTask.CONF_FIXED_PRICE: "abc",
                **self._prices(100, 200, 300, 400, 500, 600),
            }
        )

        task._validate_price_config()

    # --- 第 6 次必须高于第 5 次 ---
    def test_validate_rejects_sixth_bid_lower_than_fifth(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(100, 200, 300, 400, 500, 400),
            }
        )

        with self.assertRaises(ValueError):
            task._validate_price_config()

    def test_validate_rejects_sixth_bid_equal_to_fifth(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(100, 200, 300, 400, 500, 500),
            }
        )

        with self.assertRaises(ValueError):
            task._validate_price_config()

    def test_validate_rejects_unset_sixth_bid(self):
        """第 6 次留 0 会沿用第 5 次的价格, 与第 5 次相等, 同样要被拦截。"""
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(100, 200, 300),
            }
        )

        with self.assertRaises(ValueError) as ctx:
            task._validate_price_config()

        # 报错必须点名是哪个配置项没填, 否则用户不知道该改哪里。
        self.assertIn(AutoBidAuctionTask.CONF_BID_PRICES[5], str(ctx.exception))

    def test_validate_accepts_sixth_bid_higher_than_fifth(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(100, 200, 300, 400, 500, 501),
            }
        )

        task._validate_price_config()

    def test_ladder_rule_only_applies_to_the_last_bid(self):
        """规则只针对最后一次出价, 前面的回合允许相等或任意填写。"""
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_LIST,
                **self._prices(100, 100, 100, 100, 100, 101),
            }
        )

        task._validate_price_config()

    def test_validate_rejects_non_positive_ratio(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_BID_MODE: AutoBidAuctionTask.BID_MODE_ESTIMATE,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "0",
            }
        )

        with self.assertRaises(ValueError):
            task._validate_price_config()

    def test_validate_rejects_invalid_base_price_in_custom_mode(self):
        task = self._task(**{AutoBidAuctionTask.CONF_FIXED_PRICE: 0})

        with self.assertRaises(ValueError):
            task._validate_price_config()


class TestAuctionCursorRestore(unittest.TestCase):
    """后台执行时点击必须还原鼠标位置, 否则鼠标会留在游戏窗口内。"""

    def test_confirm_bid_price_uses_cursor_restoring_click(self):
        task = _make_task()
        # 第一次给「确认出价」, 第二次给弹窗探测(未命中弹窗, 不该多点一次).
        task.wait_ocr = Mock(side_effect=[[Mock()], []])

        task._confirm_bid_price(Mock(), None)

        task.operate_click.assert_called_once()

    def test_wait_click_optional_uses_cursor_restoring_click(self):
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])

        self.assertTrue(task._wait_click_optional(Mock(), RE_CANCEL, None, 3, "取消按钮"))
        task.operate_click.assert_called_once()

    def test_sell_collections_uses_cursor_restoring_click(self):
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])
        task._select_quality_filters = Mock(return_value=5)
        task._read_sell_value = Mock(return_value=12345)

        self.assertTrue(task._sell_collections(Mock(), None))
        task.operate_click.assert_called()

    def test_wait_operate_click_returns_false_without_target(self):
        task = _make_task()
        task.wait_ocr = Mock(return_value=[])

        self.assertFalse(task._wait_operate_click(Mock(), RE_CLAIM, 3))
        task.operate_click.assert_not_called()


class TestAuctionSellModeGuard(unittest.TestCase):
    """进入出售模式与品质勾选都要校验, 否则会点错按钮并谎报成功。

    仓库初始视图的「出售」圆钮与出售模式的「取消」圆钮位置相同, 上一次出售中途失败会
    把仓库留在出售模式; 此时再点一次「出售」等于取消, 后续品质勾选与确认出售全部落空,
    日志却仍会打印「藏品出售完成」。
    """

    def _task(self, sell_mode: bool) -> AutoBidAuctionTask:
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])
        task._is_sell_mode = Mock(return_value=sell_mode)
        task._select_quality_filters = Mock(return_value=5)
        task._read_sell_value = Mock(return_value=12345)
        return task

    def test_already_in_sell_mode_skips_the_sell_button(self):
        task = self._task(sell_mode=True)
        boxes = Mock()

        self.assertTrue(task._sell_collections(boxes, None))

        clicked = [call.args[0] for call in task.operate_click.call_args_list]
        self.assertNotIn(boxes.sell, clicked)
        self.assertIn(boxes.confirm_sell, clicked)

    def test_initial_view_still_clicks_the_sell_button(self):
        task = self._task(sell_mode=False)
        boxes = Mock()

        self.assertTrue(task._sell_collections(boxes, None))

        clicked = [call.args[0] for call in task.operate_click.call_args_list]
        self.assertIn(boxes.sell, clicked)
        self.assertIn(boxes.confirm_sell, clicked)

    def test_sell_flow_reports_failure_when_no_quality_is_selected(self):
        """勾选没生效时不能再报告成功, 否则满仓问题会被日志掩盖。"""
        task = self._task(sell_mode=True)
        task._read_sell_value = Mock(return_value=0)

        self.assertFalse(task._sell_collections(Mock(), None))
        self.assertIn("出售", str(task.log_warning.call_args))

    def test_sell_mode_detection_reads_the_sell_value_label(self):
        task = _make_task()
        boxes = Mock()

        task.wait_ocr = Mock(return_value=[Mock()])
        self.assertTrue(task._is_sell_mode(boxes, 1))
        self.assertIs(task.wait_ocr.call_args.kwargs["box"], boxes.sell_label)

    def test_sell_mode_detection_returns_false_without_raising(self):
        task = _make_task()
        task.wait_ocr = Mock(return_value=[])

        self.assertFalse(task._is_sell_mode(Mock(), 1))
        self.assertFalse(task.wait_ocr.call_args.kwargs["raise_if_not_found"])


class TestAuctionQualitySelection(unittest.TestCase):
    """品质勾选要返回点击次数并按可配置间隔点击, 供上层校验勾选是否生效。"""

    def _task(self, keep=("品质红",)) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(**{AutoBidAuctionTask.CONF_KEEP_QUALITIES: list(keep)})
        return task

    def test_selection_returns_number_of_clicks(self):
        task = self._task()

        self.assertEqual(task._select_quality_filters(None), 5)
        self.assertEqual(task.operate_click.call_count, 5)

    def test_selection_keeps_configured_qualities(self):
        task = self._task(keep=("品质红", "品质紫"))

        self.assertEqual(task._select_quality_filters(None), 4)

    def test_selection_returns_zero_when_every_quality_is_kept(self):
        task = self._task(keep=AutoBidAuctionTask.QUALITY_KEYS)

        self.assertEqual(task._select_quality_filters(None), 0)
        task.operate_click.assert_not_called()

    def test_selection_uses_the_default_gap(self):
        task = self._task()

        task._select_quality_filters(None)

        task.sleep.assert_called_with(AutoBidAuctionTask.SELL_QUALITY_GAP)

    def test_selection_logs_override_only_for_kept_qualities(self):
        """只有「本来要保留」的品质被追加才叫追加, 其余品质本来就会卖。"""
        task = self._task(keep=("品质红", "品质紫"))

        task._select_quality_filters(None, ("品质紫",))

        logged = [str(call.args[0]) for call in task.log_info.call_args_list]
        self.assertIn("条件触发, 追加出售品质紫", logged)
        self.assertNotIn("保留品质紫", logged)

    def test_selection_does_not_claim_an_override_for_already_sold_qualities(self):
        """追加的品质不在保留列表里时它本来就会卖, 日志不该说「追加」(会让人以为配置生效)。"""
        task = self._task(keep=("品质红",))

        count = task._select_quality_filters(None, ("品质白",))

        logged = [str(call.args[0]) for call in task.log_info.call_args_list]
        self.assertEqual(count, 5)
        self.assertNotIn("条件触发, 追加出售品质白", logged)
        self.assertIn("选择品质白", logged)

    def test_redundant_extra_does_not_change_which_qualities_are_sold(self):
        """追加的品质不在保留列表时, 被点击的品质与不配追加完全一致。"""
        with_extra = self._task(keep=("品质红",))
        without = self._task(keep=("品质红",))

        with_extra._select_quality_filters(None, ("品质白",))
        without._select_quality_filters(None, ())

        self.assertEqual(
            with_extra.box_of_screen.call_args_list,
            without.box_of_screen.call_args_list,
        )

    def test_kept_quality_in_extra_does_change_which_qualities_are_sold(self):
        """对照: 追加的品质在保留列表里时, 它确实会多出一次点击。"""
        with_extra = self._task(keep=("品质红", "品质紫"))
        without = self._task(keep=("品质红", "品质紫"))

        with_extra._select_quality_filters(None, ("品质紫",))
        without._select_quality_filters(None, ())

        self.assertNotEqual(
            with_extra.box_of_screen.call_args_list,
            without.box_of_screen.call_args_list,
        )


class TestAuctionNoSellableQualityWarning(unittest.TestCase):
    """「保留全部品质」+「开了出售模式」是自相矛盾的配置, 要在入场就说清楚。

    这种配置下出售会一直报成功却清不出空间, 满仓后每轮出价都失败, 提前告警更好排查。
    """

    def _task(self, mode: str, keep, extra=()) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_SELL_MODE: mode,
                AutoBidAuctionTask.CONF_KEEP_QUALITIES: list(keep),
                AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES: list(extra),
            }
        )
        return task

    def test_warns_when_every_quality_is_kept(self):
        task = self._task(AutoBidAuctionTask.SELL_MODE_FULL, AutoBidAuctionTask.QUALITY_KEYS)

        task._warn_if_no_sellable_quality()

        self.assertIn(AutoBidAuctionTask.CONF_KEEP_QUALITIES, str(task.log_warning.call_args))

    def test_stays_quiet_when_something_can_be_sold(self):
        task = self._task(AutoBidAuctionTask.SELL_MODE_FULL, ("品质红",))

        task._warn_if_no_sellable_quality()

        task.log_warning.assert_not_called()

    def test_extra_sell_qualities_count_as_sellable(self):
        """追加出售的品质会覆盖保留列表, 所以「全保留 + 追加」不是空配置。"""
        task = self._task(
            AutoBidAuctionTask.SELL_MODE_FULL,
            AutoBidAuctionTask.QUALITY_KEYS,
            ("品质红",),
        )

        task._warn_if_no_sellable_quality()

        task.log_warning.assert_not_called()

    def test_off_mode_never_warns(self):
        task = self._task(AutoBidAuctionTask.SELL_MODE_OFF, AutoBidAuctionTask.QUALITY_KEYS)

        task._warn_if_no_sellable_quality()

        task.log_warning.assert_not_called()


class TestAuctionRedundantExtraSellWarning(unittest.TestCase):
    """「追加出售品质」里勾了不在「保留品质」里的品质时要在入场就说清楚。

    追加只在「该品质本来要保留」时才改变行为: 不在保留列表里的品质本来就会出售, 勾进追加
    等于没勾。而这个组合又很自然(看到「追加出售」就把低价值品质勾上), 所以必须告警,
    否则用户会以为配置生效了。
    """

    def _task(self, mode: str, keep, extra) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_SELL_MODE: mode,
                AutoBidAuctionTask.CONF_KEEP_QUALITIES: list(keep),
                AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES: list(extra),
            }
        )
        return task

    def test_warns_when_extra_is_not_kept(self):
        task = self._task(AutoBidAuctionTask.SELL_MODE_FULL, ("品质红",), ("品质白",))

        task._warn_if_extra_sell_is_redundant()

        message = str(task.log_warning.call_args)
        self.assertIn("品质白", message)
        self.assertIn(AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES, message)

    def test_names_only_the_redundant_qualities(self):
        task = self._task(
            AutoBidAuctionTask.SELL_MODE_FULL,
            ("品质红", "品质紫"),
            ("品质白", "品质紫"),
        )

        task._warn_if_extra_sell_is_redundant()

        message = str(task.log_warning.call_args)
        self.assertIn("品质白", message)
        self.assertNotIn("品质紫", message)

    def test_stays_quiet_when_every_extra_quality_is_kept(self):
        """全部追加品质都在保留列表里 = 配置真的会生效, 不该告警。"""
        task = self._task(
            AutoBidAuctionTask.SELL_MODE_FULL,
            ("品质红", "品质紫"),
            ("品质紫",),
        )

        task._warn_if_extra_sell_is_redundant()

        task.log_warning.assert_not_called()

    def test_stays_quiet_when_nothing_is_appended(self):
        task = self._task(AutoBidAuctionTask.SELL_MODE_FULL, ("品质红",), ())

        task._warn_if_extra_sell_is_redundant()

        task.log_warning.assert_not_called()

    def test_unknown_quality_names_are_ignored(self):
        """脏配置里的未知名称既不会生效, 也不该被拿出来说。"""
        task = self._task(AutoBidAuctionTask.SELL_MODE_FULL, ("品质红",), ("品质不存在",))

        task._warn_if_extra_sell_is_redundant()

        task.log_warning.assert_not_called()

    def test_dirty_extra_value_does_not_crash(self):
        """脏配置(手改 JSON)可能给出非序列的值, 必须安全跳过而不是崩在迭代上。

        字符串虽然也不会崩(迭代出的是单字, 永远匹配不上品质名), 但 int / None 会直接
        抛 TypeError, 所以类型守卫是必要的。
        """
        for dirty in ("品质白", 5, None):
            with self.subTest(dirty=dirty):
                task = _make_task()
                task.config = _config(
                    **{
                        AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_FULL,
                        AutoBidAuctionTask.CONF_KEEP_QUALITIES: ["品质红"],
                        AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES: dirty,
                    }
                )

                task._warn_if_extra_sell_is_redundant()

                task.log_warning.assert_not_called()

    def test_off_mode_never_warns(self):
        task = self._task(AutoBidAuctionTask.SELL_MODE_OFF, ("品质红",), ("品质白",))

        task._warn_if_extra_sell_is_redundant()

        task.log_warning.assert_not_called()

    def test_one_click_mode_never_warns(self):
        """一键出售不筛品质, 追加列表对它没有意义。"""
        task = self._task(AutoBidAuctionTask.SELL_MODE_ONE_CLICK, ("品质红",), ("品质白",))

        task._warn_if_extra_sell_is_redundant()

        task.log_warning.assert_not_called()

    def test_interval_mode_warns_too(self):
        task = self._task(AutoBidAuctionTask.SELL_MODE_INTERVAL, ("品质红",), ("品质白",))

        task._warn_if_extra_sell_is_redundant()

        task.log_warning.assert_called_once()


class TestAuctionOneClickSell(unittest.TestCase):
    """「拍卖成功一键出售」: 结算界面点游戏自带的按钮, 再点空白区域关掉「获得物品」提示。

    这条路径不碰藏品仓库, 所以既不做满仓检测, 也不筛品质 —— 它走的是另一条独立分支。
    """

    def _task(self, mode: str | None = None) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_SELL_MODE: (
                    AutoBidAuctionTask.SELL_MODE_ONE_CLICK if mode is None else mode
                )
            }
        )
        return task

    def test_clicks_the_button_then_the_blank_area(self):
        task = self._task()
        boxes = Mock()
        task._wait_operate_click = Mock(return_value=True)
        task.wait_ocr = Mock(return_value=[Mock()])

        task._sell_on_settlement_screen(boxes, auction_module.time.monotonic() + 60)

        task._wait_operate_click.assert_called_once()
        self.assertIs(task._wait_operate_click.call_args.args[0], boxes.one_click_sell)
        self.assertIs(task.operate_click.call_args.args[0], boxes.popup_blank)

    def test_detects_the_popup_without_clicking_its_text(self):
        """关闭要点空白区域, 不是点提示文字本身。"""
        task = self._task()
        boxes = Mock()
        task._wait_operate_click = Mock(return_value=True)
        task.wait_ocr = Mock(return_value=[Mock()])

        task._sell_on_settlement_screen(boxes, auction_module.time.monotonic() + 60)

        self.assertIs(task.wait_ocr.call_args.kwargs["box"], boxes.popup_close_hint)
        self.assertIs(task.wait_ocr.call_args.kwargs["match"], RE_POPUP_CLOSE_HINT)
        self.assertIsNot(task.operate_click.call_args.args[0], boxes.popup_close_hint)

    def test_button_regex_matches_the_label(self):
        task = self._task()
        task._wait_operate_click = Mock(return_value=False)

        task._sell_on_settlement_screen(Mock(), auction_module.time.monotonic() + 60)

        self.assertIs(task._wait_operate_click.call_args.args[1], RE_ONE_CLICK_SELL)

    def test_button_regex_tolerates_the_dropped_first_glyph(self):
        """首字「一」是单笔画, 检测模型裁剪偏紧时会直接丢掉它, 只读出「键出售」。

        实测同一张 1920x1080 截图放大 2 倍能读出完整文字、放大 3/4 倍读不出 ——
        这是检测本身的抖动, 不是截图差异, 所以正则必须同时接受两种写法。
        """
        self.assertIsNotNone(RE_ONE_CLICK_SELL.search("一键出售"))
        self.assertIsNotNone(RE_ONE_CLICK_SELL.search("键出售"))

    def test_popup_regex_matches_the_close_hint(self):
        """只匹配「点击空白」: 整句较长, 尾部被认坏时仍要能命中。"""
        self.assertIsNotNone(RE_POPUP_CLOSE_HINT.search("点击空白区域关闭"))

    def test_missing_button_is_not_a_failure(self):
        """流拍时按钮不出现, 不该因此让整轮失败, 也不该去点空白区域。"""
        task = self._task()
        task._wait_operate_click = Mock(return_value=False)
        task.wait_ocr = Mock(return_value=[Mock()])

        task._sell_on_settlement_screen(Mock(), auction_module.time.monotonic() + 60)

        task.operate_click.assert_not_called()

    def test_missing_popup_warns_and_does_not_click(self):
        task = self._task()
        task._wait_operate_click = Mock(return_value=True)
        task.wait_ocr = Mock(return_value=[])

        task._sell_on_settlement_screen(Mock(), auction_module.time.monotonic() + 60)

        task.operate_click.assert_not_called()
        self.assertIn("获得物品", str(task.log_warning.call_args))

    def test_exhausted_deadline_skips_instead_of_raising(self):
        """结算已经完成, 时间不够只能跳过, 不能抛异常把整轮判成失败。"""
        task = self._task()
        task._wait_operate_click = Mock(return_value=True)

        task._sell_on_settlement_screen(Mock(), auction_module.time.monotonic() - 1)

        task._wait_operate_click.assert_not_called()
        task.operate_click.assert_not_called()
        self.assertIn("时间已用尽", str(task.log_warning.call_args))

    def test_runs_between_skip_animation_and_exit(self):
        """必须插在「跳过动画」之后、「退出拍卖」之前: 按钮只在结算界面存在。"""
        task = self._task()
        order: list[str] = []
        task.operate_click = Mock(side_effect=lambda *a, **kw: order.append("skip"))
        task._sell_on_settlement_screen = Mock(side_effect=lambda *a: order.append("sell"))
        task._wait_operate_click = Mock(side_effect=lambda *a, **kw: order.append("exit") or True)
        task.wait_ocr = Mock(return_value=[])

        task._finish_auction(Mock(), [Mock()], auction_module.time.monotonic() + 60)

        self.assertEqual(order, ["skip", "sell", "exit"])

    def test_other_modes_do_not_touch_the_settlement_screen(self):
        for mode in (
            AutoBidAuctionTask.SELL_MODE_OFF,
            AutoBidAuctionTask.SELL_MODE_FULL,
            AutoBidAuctionTask.SELL_MODE_INTERVAL,
        ):
            with self.subTest(mode=mode):
                task = self._task(mode)
                task._wait_operate_click = Mock(return_value=True)
                task.wait_ocr = Mock(return_value=[])
                task._sell_on_settlement_screen = Mock()

                task._finish_auction(Mock(), [Mock()], auction_module.time.monotonic() + 60)

                task._sell_on_settlement_screen.assert_not_called()

    def test_one_click_mode_does_not_use_the_warehouse_flow(self):
        """一键出售不碰仓库: 不做满仓检测, 也不在轮次末尾出售。"""
        task = self._task()
        task._sell_collections = Mock(return_value=True)
        task._detect_inventory_full = Mock(return_value=False)

        self.assertFalse(task._uses_collection_sell())

        task._sell_collections_on_interval(Mock())

        task._sell_collections.assert_not_called()
        task._detect_inventory_full.assert_not_called()

    def test_one_click_mode_never_sells_even_when_the_state_says_full(self):
        """即使调用点带着「满仓」状态进来, 也不能去卖仓库。

        「满仓卡住」那条重试分支是无条件调用 _sell_collections_on_interval 的(它不先问
        模式), 所以这条守卫只能靠函数自己拦 —— 拦不住就会在一键出售模式下突然跑去开仓库。
        """
        task = self._task()
        task._sell_collections = Mock(return_value=True)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=True))

        task._sell_collections.assert_not_called()

    def test_one_click_mode_never_warns_about_kept_qualities(self):
        """它不筛选品质, 所以「保留品质全选」对它不是矛盾配置。"""
        task = _make_task()
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_ONE_CLICK,
                AutoBidAuctionTask.CONF_KEEP_QUALITIES: list(AutoBidAuctionTask.QUALITY_KEYS),
                AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES: [],
            }
        )

        task._warn_if_no_sellable_quality()

        task.log_warning.assert_not_called()


class TestAuctionSellValueCheck(unittest.TestCase):
    """勾完品质要读「出售价值」, 读到 0 说明点击全部落空。

    品质圆点每点一次界面都会重绘, 间隔太短会让后续点击落空, 表现为只卖掉一种品质;
    原流程不校验就直接点确认出售并报告成功, 满仓因此一直清不掉。

    「读不出」和「读到 0」要分开处理: 读不出是重绘空白态, 只能换帧重读, 重勾会把刚勾上
    的品质点掉(双重取反); 换帧后仍是 0 才说明这一遍真的一个都没勾上, 那时重勾一次是
    修正(仓库里残留着上一次异常退出留下的勾选, 被这一遍全点掉了), 不是取反。
    """

    def _task(self, values) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(**{AutoBidAuctionTask.CONF_KEEP_QUALITIES: ["品质红"]})
        task._read_sell_value = Mock(side_effect=list(values))
        task._select_quality_filters = Mock(return_value=5)
        return task

    def test_positive_value_needs_no_retry(self):
        task = self._task([12345])

        self.assertEqual(task._ensure_sell_value(Mock(), None, 5), 12345)
        task._select_quality_filters.assert_not_called()
        task.log_warning.assert_not_called()

    def test_zero_value_is_retried_by_reading_again(self):
        task = self._task([0, 999])

        self.assertEqual(task._ensure_sell_value(Mock(), None, 5), 999)
        self.assertEqual(task._read_sell_value.call_count, 2)
        task._select_quality_filters.assert_not_called()
        task.log_warning.assert_called()

    def test_retry_switches_frame_before_reading_again(self):
        """必须换帧: 两次读取落在同一帧上会读到同样的空白值。"""
        task = self._task([0, 999])

        task._ensure_sell_value(Mock(), None, 5)

        task.next_frame.assert_called_once()

    def test_persistent_zero_reselects_once_and_gives_up(self):
        """换帧重读后仍是 0, 才按「残留勾选被这一遍点掉」处理, 重勾一次。

        仓库里残留着上一次异常退出留下的勾选时, 这一遍无条件点击会把它们全部点掉
        (出售价值读到 0); 再点一遍同一批品质能把状态拉回来。只重勾一次, 不无限重试。
        """
        task = self._task([0, 0, 0])

        self.assertEqual(task._ensure_sell_value(Mock(), None, 5), 0)
        self.assertEqual(task._select_quality_filters.call_count, 1)
        self.assertIn("重新勾选", str(task.log_warning.call_args))

    def test_reselect_recovers_the_value_when_the_warehouse_was_dirty(self):
        """残留勾选被点掉后, 重勾一次应当重新读到正数并判定生效。"""
        task = self._task([0, 0, 12345])

        self.assertEqual(task._ensure_sell_value(Mock(), None, 5), 12345)
        self.assertEqual(task._select_quality_filters.call_count, 1)

    def test_reselect_passes_the_extra_sell_qualities(self):
        """重勾必须用同一批品质, 否则满仓放宽时会把要保留的品质重新勾回来。"""
        task = self._task([0, 0, 12345])

        task._ensure_sell_value(Mock(), None, 5, ["品质红"])

        self.assertEqual(task._select_quality_filters.call_args.args[1], ["品质红"])

    def test_unreadable_value_does_not_reselect(self):
        """读不出时只重读, 不重勾 —— 重勾会点成反选, 把已经勾上的品质取消掉。"""
        task = self._task([None, None])

        self.assertIsNone(task._ensure_sell_value(Mock(), None, 5))
        task._select_quality_filters.assert_not_called()
        task.log_warning.assert_called()

    def test_unreadable_value_is_retried_before_giving_up(self):
        """界面重绘期间数值区会短暂空白, 线上 4 次出售有 3 次是这样, 要重读一次。"""
        task = self._task([None, 4242])

        self.assertEqual(task._ensure_sell_value(Mock(), None, 5), 4242)
        self.assertEqual(task._read_sell_value.call_count, 2)
        task._select_quality_filters.assert_not_called()

    def test_nothing_selected_is_not_treated_as_failure(self):
        task = self._task([0])

        self.assertIsNone(task._ensure_sell_value(Mock(), None, 0))
        task._read_sell_value.assert_not_called()
        task.log_warning.assert_not_called()

    def test_sell_value_reader_reuses_the_asset_value_parser(self):
        task = _make_task()
        boxes = Mock()

        self.assertIsNone(task._read_sell_value(boxes, 1))
        self.assertIs(task.wait_ocr.call_args.kwargs["box"], boxes.sell_value)


class TestAuctionSelectionConfirmed(unittest.TestCase):
    """勾选是否生效必须三态判断: 读到正数 / 读到 0 / 读不出。

    线上 4 次出售有 3 次读不出「出售价值」(界面重绘中的空白态), 原判据
    `not (selected > 0 and value == 0)` 把 None 也算成功 —— 等于根本没有校验。
    """

    def test_positive_value_confirms_the_selection(self):
        self.assertTrue(AutoBidAuctionTask._is_selection_confirmed(5, 12345))

    def test_zero_value_means_the_selection_failed(self):
        self.assertFalse(AutoBidAuctionTask._is_selection_confirmed(5, 0))

    def test_unreadable_value_is_unconfirmed_not_success(self):
        self.assertIsNone(AutoBidAuctionTask._is_selection_confirmed(5, None))

    def test_nothing_selected_is_not_a_failure(self):
        self.assertTrue(AutoBidAuctionTask._is_selection_confirmed(0, None))

    def test_nothing_selected_is_a_failure_when_a_sale_is_required(self):
        """满仓时一个品质都没勾上, 不能算成功。

        否则会把满仓标记清掉, 仓库一件没腾却报告「藏品出售完成」, 之后每轮出价都失败。
        """
        self.assertFalse(AutoBidAuctionTask._is_selection_confirmed(0, None, require_sale=True))

    def test_require_sale_does_not_change_the_other_verdicts(self):
        self.assertTrue(AutoBidAuctionTask._is_selection_confirmed(5, 12345, require_sale=True))
        self.assertFalse(AutoBidAuctionTask._is_selection_confirmed(5, 0, require_sale=True))
        self.assertIsNone(AutoBidAuctionTask._is_selection_confirmed(5, None, require_sale=True))


class TestAuctionSellUnconfirmed(unittest.TestCase):
    """读不出出售价值时不能打印「藏品出售完成」。"""

    def _task(self) -> AutoBidAuctionTask:
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])
        task._is_sell_mode = Mock(return_value=True)
        task._select_quality_filters = Mock(return_value=6)
        task._ensure_sell_value = Mock(return_value=None)
        return task

    def test_unconfirmed_sell_does_not_claim_success(self):
        task = self._task()

        self.assertFalse(task._sell_collections(Mock(), None))
        self.assertNotIn("藏品出售完成", str(task.log_info.call_args_list))

    def test_unconfirmed_sell_says_it_could_not_be_confirmed(self):
        """日志要区分「读不出」和「勾选没生效」, 否则又回到排查时看不出原因。

        这两条告警是支持流程唯一的信息来源(用户把日志发过来定位问题),
        文案本身就是契约。
        """
        task = self._task()

        task._sell_collections(Mock(), None)

        self.assertIn("未读出", str(task.log_warning.call_args))

    def test_zero_value_sell_says_nothing_was_cleared(self):
        task = self._task()
        task._ensure_sell_value = Mock(return_value=0)

        task._sell_collections(Mock(), None)

        self.assertIn("未生效", str(task.log_warning.call_args))

    def test_unconfirmed_sell_still_clicks_confirm_and_closes(self):
        """读不出也要把确认出售和关闭点掉, 否则会卡在出售模式影响下一次。"""
        task = self._task()
        boxes = Mock()

        task._sell_collections(boxes, None)

        clicked = [call.args[0] for call in task.operate_click.call_args_list]
        self.assertIn(boxes.confirm_sell, clicked)
        self.assertIn(boxes.close, clicked)

    def test_confirmed_sell_still_reports_success(self):
        task = self._task()
        task._ensure_sell_value = Mock(return_value=888)

        self.assertTrue(task._sell_collections(Mock(), None))
        self.assertIn("藏品出售完成", str(task.log_info.call_args_list))

    def test_required_sale_with_nothing_selected_does_not_claim_success(self):
        """满仓时一个品质都没勾上(保留列表覆盖全部品质), 不能报告出售完成。"""
        task = self._task()
        task._select_quality_filters = Mock(return_value=0)
        task._ensure_sell_value = Mock(return_value=None)

        self.assertFalse(task._sell_collections(Mock(), None, require_sale=True))
        self.assertNotIn("藏品出售完成", str(task.log_info.call_args_list))
        self.assertIn("没有勾选任何品质", str(task.log_warning.call_args))

    def test_optional_sale_with_nothing_selected_is_not_a_failure(self):
        """不要求出售时(未满仓), 没有可卖的东西不算失败, 也不谎报出售完成。"""
        task = self._task()
        task._select_quality_filters = Mock(return_value=0)
        task._ensure_sell_value = Mock(return_value=None)

        self.assertTrue(task._sell_collections(Mock(), None))
        self.assertNotIn("藏品出售完成", str(task.log_info.call_args_list))


class TestAuctionSellAbortClosesWarehouse(unittest.TestCase):
    """出售流程无论成功还是异常退出, 都必须把仓库关掉。

    把「出售模式 + 已勾选的品质」留给下一轮, 下次进来会检测到「已在出售模式」而跳过
    点「出售」, 然后无条件再点一遍同一批品质 —— 全部取反成未勾选; 满仓放宽时还会
    连带卖掉用户明确要保留的品质。关闭按钮无文字图标, 在主界面点它是空操作。
    """

    def _task(self, error: Exception) -> AutoBidAuctionTask:
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])
        task._is_sell_mode = Mock(return_value=False)
        task._select_quality_filters = Mock(side_effect=error)
        return task

    def test_timeout_abort_still_closes_the_warehouse(self):
        task = self._task(WaitFailedException("单轮拍卖超时"))
        boxes = Mock()

        with self.assertRaises(WaitFailedException):
            task._sell_collections(boxes, None)

        self.assertIn(boxes.close, [call.args[0] for call in task.operate_click.call_args_list])

    def test_unexpected_error_abort_still_closes_the_warehouse(self):
        task = self._task(RuntimeError("boom"))
        boxes = Mock()

        self.assertFalse(task._sell_collections(boxes, None))

        self.assertIn(boxes.close, [call.args[0] for call in task.operate_click.call_args_list])


class TestAuctionSellFailureEscalation(unittest.TestCase):
    """出售连续失败要升级处理: 满仓卖不掉会让后续出价全部失败。"""

    def _task(self, results) -> AutoBidAuctionTask:
        task = _make_task()
        task._sell_collections = Mock(side_effect=list(results))
        return task

    def test_success_resets_the_failure_counter(self):
        task = self._task([False, True])
        task._sell_failures = 3
        task._inventory_stuck = True

        self.assertTrue(
            task._sell_collections_with_escalation(Mock(), None, (), inventory_full=True)
        )
        self.assertEqual(task._sell_failures, 0)
        self.assertFalse(task._inventory_stuck)

    def test_failure_below_threshold_does_not_escalate(self):
        task = self._task([False])

        self.assertFalse(
            task._sell_collections_with_escalation(Mock(), None, (), inventory_full=True)
        )
        self.assertEqual(task._sell_collections.call_count, 1)
        self.assertTrue(task._inventory_stuck)

    def test_repeated_failure_escalates_by_dropping_kept_qualities(self):
        """满仓时把仓库腾空优先于保留配置里的品质。"""
        task = self._task([False, True])
        task._sell_failures = AutoBidAuctionTask.SELL_FAILURE_ESCALATE_AFTER - 1

        self.assertTrue(
            task._sell_collections_with_escalation(Mock(), None, (), inventory_full=True)
        )

        escalated = task._sell_collections.call_args.args[2]
        self.assertEqual(set(escalated), set(AutoBidAuctionTask.QUALITY_KEYS))

    def test_escalation_keeps_the_extra_qualities(self):
        task = self._task([False, True])
        task._sell_failures = AutoBidAuctionTask.SELL_FAILURE_ESCALATE_AFTER - 1

        task._sell_collections_with_escalation(Mock(), None, ["品质红"], inventory_full=True)

        escalated = task._sell_collections.call_args.args[2]
        self.assertIn("品质红", escalated)

    def test_persistent_failure_marks_the_inventory_as_stuck(self):
        task = self._task([False, False])
        task._sell_failures = AutoBidAuctionTask.SELL_FAILURE_ESCALATE_AFTER - 1

        self.assertFalse(
            task._sell_collections_with_escalation(Mock(), None, (), inventory_full=True)
        )
        self.assertTrue(task._inventory_stuck)

    def test_not_full_does_not_mark_the_inventory_as_stuck(self):
        """没满仓时出售失败不该让下一轮跳过拍卖。"""
        task = self._task([False])

        task._sell_collections_with_escalation(Mock(), None, (), inventory_full=False)

        self.assertFalse(task._inventory_stuck)

    def test_full_warehouse_requires_the_sale_to_succeed(self):
        """满仓时要把 require_sale 传下去, 否则「一件没卖」会被当成成功。

        满仓清理报成功后 _inventory_stuck 被清掉, 下一轮不会再重试清理, 而出价
        在满仓下必然失败 —— 只能靠这条把「没清掉」如实报成失败。
        """
        task = self._task([False])

        task._sell_collections_with_escalation(Mock(), None, (), inventory_full=True)

        self.assertTrue(task._sell_collections.call_args.kwargs["require_sale"])

    def test_escalated_attempt_also_requires_the_sale(self):
        task = self._task([False, True])
        task._sell_failures = AutoBidAuctionTask.SELL_FAILURE_ESCALATE_AFTER - 1

        task._sell_collections_with_escalation(Mock(), None, (), inventory_full=True)

        self.assertTrue(task._sell_collections.call_args.kwargs["require_sale"])

    def test_not_full_does_not_require_the_sale(self):
        task = self._task([False])

        task._sell_collections_with_escalation(Mock(), None, (), inventory_full=False)

        self.assertFalse(task._sell_collections.call_args.kwargs["require_sale"])

    def test_not_full_never_drops_the_kept_qualities(self):
        """非满仓的失败多半是界面读数抖动, 不该白白卖掉用户明确要保留的品质。"""
        task = self._task([False, False])
        task._sell_failures = AutoBidAuctionTask.SELL_FAILURE_ESCALATE_AFTER - 1

        self.assertFalse(
            task._sell_collections_with_escalation(Mock(), None, (), inventory_full=False)
        )
        # 只应尝试一次, 没有放宽后的第二次调用.
        self.assertEqual(task._sell_collections.call_count, 1)

    def test_not_full_keeps_the_failure_counter_for_a_later_full_attempt(self):
        """未满仓时计数继续累积, 之后真的满仓仍要能放宽。"""
        task = self._task([False, False, True])
        task._sell_failures = AutoBidAuctionTask.SELL_FAILURE_ESCALATE_AFTER - 1

        task._sell_collections_with_escalation(Mock(), None, (), inventory_full=False)
        self.assertEqual(task._sell_failures, AutoBidAuctionTask.SELL_FAILURE_ESCALATE_AFTER)

        self.assertTrue(
            task._sell_collections_with_escalation(Mock(), None, (), inventory_full=True)
        )
        escalated = task._sell_collections.call_args.args[2]
        self.assertEqual(set(escalated), set(AutoBidAuctionTask.QUALITY_KEYS))


class TestAuctionNoticePopup(unittest.TestCase):
    """提示类弹窗(入场费确认/异常出价/满仓提示)共用一套模板, 一个区域兜住。"""

    def test_popup_is_clicked_when_the_confirm_button_is_seen(self):
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])
        boxes = Mock()

        self.assertTrue(task._dismiss_notice_popup(boxes, None, "测试"))
        self.assertIs(task.wait_ocr.call_args.kwargs["box"], boxes.exception_area)
        task.operate_click.assert_called_once_with(boxes.exception_area, after_sleep=0.3)

    def test_popup_probe_polls_with_a_timeout(self):
        """单帧 ocr 会漏掉刚出现的弹窗, 之后整条流程卡在弹窗上。"""
        task = _make_task()
        task.wait_ocr = Mock(return_value=[])

        self.assertFalse(task._dismiss_notice_popup(Mock(), None, "测试"))
        self.assertGreater(task.wait_ocr.call_args.kwargs["time_out"], 0)
        self.assertFalse(task.wait_ocr.call_args.kwargs["raise_if_not_found"])

    def test_popup_probe_uses_the_given_budget(self):
        """每次出价都要查一遍, 热路径要能用更小的预算。"""
        task = _make_task()
        task.wait_ocr = Mock(return_value=[])

        task._dismiss_notice_popup(Mock(), None, "测试", timeout=0.5)

        self.assertEqual(task.wait_ocr.call_args.kwargs["time_out"], 0.5)

    def test_expired_deadline_skips_the_probe(self):
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])

        expired = auction_module.time.monotonic() - 1
        self.assertFalse(task._dismiss_notice_popup(Mock(), expired, "测试"))
        task.wait_ocr.assert_not_called()


class TestAuctionInventoryFullProbe(unittest.TestCase):
    """满仓检测是可选观测步骤, 没有时间时不能抛异常拖垮结算后处理。"""

    def test_expired_deadline_returns_none_without_raising(self):
        """没时间时必须返回 None(未检测), 不能返回 False(确定没满仓)。

        返回 False 会被写进 PostRoundState, 轮次末尾的 _sell_collections_on_interval
        看到「不是 None」就不再补测 —— 满仓被静默漏掉, 之后每轮都卡在「开始匹配」上。
        """
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])

        self.assertIsNone(task._detect_inventory_full(Mock(), 0))
        task.wait_ocr.assert_not_called()

    def test_detects_the_insufficient_banner(self):
        task = _make_task()
        task.wait_ocr = Mock(return_value=[Mock()])

        self.assertTrue(task._detect_inventory_full(Mock(), 3))

    def test_not_full_is_false_not_none(self):
        """有时间但没读到提示时是「确定没满仓」, 必须返回 False 而不是 None。"""
        task = _make_task()
        task.wait_ocr = Mock(return_value=[])

        self.assertFalse(task._detect_inventory_full(Mock(), 3))

    def test_round_end_probe_covers_undetected_state(self):
        """结算后没测出结论时, 轮次末尾必须补测一次, 否则满仓会被漏掉。"""
        task = _make_task({AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_FULL})
        task._detect_inventory_full = Mock(return_value=True)
        task._sell_collections_with_escalation = Mock(return_value=True)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=None))

        task._detect_inventory_full.assert_called_once()
        task._sell_collections_with_escalation.assert_called_once()

    def test_round_end_probe_skips_known_state(self):
        """已经有结论时不该重复 OCR。"""
        task = _make_task({AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_FULL})
        task._detect_inventory_full = Mock(return_value=True)
        task._sell_collections_with_escalation = Mock(return_value=True)

        task._sell_collections_on_interval(Mock(), state=PostRoundState(inventory_full=False))

        task._detect_inventory_full.assert_not_called()
        task._sell_collections_with_escalation.assert_not_called()


class TestAuctionInventoryStuckRound(unittest.TestCase):
    """满仓且出售未成功时不该继续空转出价。"""

    def _task(self) -> AutoBidAuctionTask:
        task = _make_task()
        task._exec_auction_round = Mock(return_value=True)
        task.add_success = Mock()
        task.add_failed = Mock()
        task._sell_collections_on_interval = Mock()
        # current_round 是只读属性, 读的是 _round_state.index.
        task._round_state = Mock(index=3, total_text="10")
        return task

    def test_stuck_inventory_skips_the_auction_round(self):
        task = self._task()
        task._inventory_stuck = True

        task._run_single_round(Mock())

        task._exec_auction_round.assert_not_called()
        task.add_failed.assert_called_once()
        task._sell_collections_on_interval.assert_called_once()

    def test_stuck_retry_does_not_require_detecting_full_again(self):
        """满仓是上一轮已经判定过的结论, 重试清理不该再赌一次 OCR。

        满仓提示条会被弹窗遮住, 重新检测失败就什么都不做, 于是每轮都走这条分支却
        一件藏品都清不掉, 变成无限跳过拍卖。
        """
        task = self._task()
        task._inventory_stuck = True

        task._run_single_round(Mock())

        state = task._sell_collections_on_interval.call_args.kwargs["state"]
        self.assertTrue(state.inventory_full)

    def test_normal_round_still_runs_the_auction(self):
        task = self._task()
        task._inventory_stuck = False

        task._run_single_round(Mock())

        task._exec_auction_round.assert_called_once()
        task.add_success.assert_called_once()

    def test_normal_round_does_not_sell_without_an_observation(self):
        """结算后处理没跑到时画面状态未知, 不能去点仓库入口白等超时。"""
        task = self._task()
        task._inventory_stuck = False

        task._run_single_round(Mock())

        task._sell_collections_on_interval.assert_not_called()

    def test_normal_round_sells_after_a_successful_observation(self):
        task = self._task()
        task._inventory_stuck = False

        def exec_round(_boxes):
            # 模拟 _finish_auction 走完结算后观测再返回.
            task._post_round_state = PostRoundState(inventory_full=True, observed=True)
            return True

        task._exec_auction_round = Mock(side_effect=exec_round)

        task._run_single_round(Mock())

        task._sell_collections_on_interval.assert_called_once()


class TestAuctionEstimateStableRead(unittest.TestCase):
    """当前估价在界面刚出现时会跳动几次, 第一次识别到的不是最终值。

    用户反馈: 估价会跳动几个值, 第一次识别到的不是当前估价。
    单次 OCR 一命中就返回, 会把跳动中的中间值当成估价, 按它出价。
    """

    def setUp(self):
        self.clock = _FakeTime()
        patcher = patch.object(auction_module, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task(self, values) -> AutoBidAuctionTask:
        task = _make_task()
        task.sleep = Mock(side_effect=self.clock.sleep)
        # 读数序列用完后一直重复最后一个值, 模拟"跳完就稳定"。
        task._read_asset_value = Mock(side_effect=list(values) + [list(values)[-1]] * 20)
        return task

    def test_returns_only_after_consecutive_identical_reads(self):
        task = self._task([100, 200, 300, 300, 300])

        self.assertEqual(task._read_stable_asset_value(Mock(), 10, "当前估价"), 300)
        # 300 连续 3 次相同之后还要满足最短观察窗口, 所以读取次数多于 5 次。
        self.assertGreaterEqual(task._read_asset_value.call_count, 5)

    def test_jumping_value_is_not_used_early(self):
        """跳动中的中间值不能被采用, 否则会按错误的估价出价。"""
        task = self._task([100, 200, 300, 300, 300])

        value = task._read_stable_asset_value(Mock(), 10, "当前估价")

        self.assertNotIn(value, (100, 200))

    def test_refetches_a_frame_between_reads(self):
        """不换帧时两次读取会落在同一帧上, 读到同样的中间值, 白等。"""
        task = self._task([100, 200, 300, 300, 300])

        task._read_stable_asset_value(Mock(), 10, "当前估价")

        # 每次重读前都要换帧, 采用的那一次不再换帧。
        self.assertEqual(task.next_frame.call_count, task._read_asset_value.call_count - 1)

    def test_value_is_not_used_before_the_minimum_observe_window(self):
        """中间值会稳定停留到面板打开后 2.6 秒, 观察窗口不够长就会采信它。

        线上日志: 19:16 那局的中间值 35,365 一直持续到 2.63 秒才变成真值 37,979;
        19:18 那局恰好在 2.63 秒返回, 拿到了残缺的 197(同局真实估价数万)。
        """
        task = self._task([197, 197, 197, 5197, 5197, 5197])

        value = task._read_stable_asset_value(Mock(), 10, "当前估价", skip_zero=True)

        self.assertEqual(value, 5197)

    def test_leading_digit_dropped_by_ocr_is_not_used(self):
        """估价数字右对齐, OCR 会漏读最左侧首位数字, 且漏读状态因画面静止而连续出现。

        线上日志: 真实 `6,486` 的读数依次是 6486 -> 486 -> 486 -> 486(被采用);
        真实 `22,778` 的读数在 22,778 / 2,778 之间交替, 攒不满连续相同而超时。
        漏读只会让位数变少, 所以位数更少的读数不能顶掉已经读到的完整值。
        """
        task = self._task([6486, 486, 486, 486])

        self.assertEqual(task._read_stable_asset_value(Mock(), 10, "当前估价"), 6486)

    def test_unreadable_reads_do_not_discard_a_good_value(self):
        """残缺读数被过滤成未读出后, 之前读到的完整值仍然要保留并采用。

        线上日志 18:56 那局: 真实 `6,544`, 读数序列是 ",544" 连着 9 次加 "6,544" 两次。
        按「连续相同」判定会超时, 最后拿残缺的 544 出价。
        """
        task = self._task([None, None, 6544, None, None, None])

        value = task._read_stable_asset_value(Mock(), 10, "当前估价", skip_zero=True)

        self.assertEqual(value, 6544)

    def test_partial_number_text_is_detected(self):
        """千位分隔符前面空着说明首位数字被漏读, 这类文本不是完整读数。"""
        self.assertTrue(AutoBidAuctionTask._is_partial_number_text(",544"))
        self.assertTrue(AutoBidAuctionTask._is_partial_number_text("：,523"))
        self.assertFalse(AutoBidAuctionTask._is_partial_number_text("5,734"))
        self.assertFalse(AutoBidAuctionTask._is_partial_number_text("486"))

    def test_read_asset_value_drops_partial_estimate_text(self):
        """估价区域读到残缺文本时按未读出处理, 交给上层重读; 稳定的数字区域保持原行为。"""
        task = _make_task()
        text_box = Mock()
        text_box.name = ",544"
        task.wait_ocr = Mock(return_value=[text_box])

        self.assertIsNone(task._read_asset_value(Mock(), 1, "当前估价", reject_partial=True))
        self.assertEqual(task._read_asset_value(Mock(), 1, "资产"), 544)

    def test_keeps_last_value_when_never_stable(self):
        """一直跳动时不能回退成基础价, 最后一次读数比丢弃更接近真实值。"""
        task = _make_task()
        task.sleep = Mock(side_effect=self.clock.sleep)
        task._read_asset_value = Mock(
            side_effect=lambda *a, **k: task._read_asset_value.call_count * 100
        )

        value = task._read_stable_asset_value(Mock(), 5, "当前估价")

        self.assertEqual(value, task._read_asset_value.call_count * 100)
        self.assertGreater(task._read_asset_value.call_count, 1)
        task.log_warning.assert_called()

    def test_returns_none_without_warning_when_never_readable(self):
        task = _make_task()
        task.sleep = Mock(side_effect=self.clock.sleep)
        task._read_asset_value = Mock(return_value=None)

        self.assertIsNone(task._read_stable_asset_value(Mock(), 5, "当前估价"))
        task.log_warning.assert_not_called()

    def test_zero_placeholder_is_skipped_until_the_real_value(self):
        """面板滚出数字前会先显示 0, 0 是占位读数不是估价。

        线上日志证据: 估价区域 OCR 到的是 `当前估价：` + `0`(conf 0.97), 连续 0.36 秒都是 0,
        随后才滚出真实数字。把 0 当结果会算出 0 元出价。
        """
        task = self._task([0, 0, 0, 35301, 35301, 35301])

        value = task._read_stable_asset_value(Mock(), 10, "当前估价", skip_zero=True)

        self.assertEqual(value, 35301)
        self.assertGreater(task._read_asset_value.call_count, 3)

    def test_zero_does_not_reset_the_stability_counter(self):
        """中间夹一次 0 不应把已经攒够的连续次数清零, 否则真值永远攒不满。"""
        task = self._task([100, 0, 100, 100])

        self.assertEqual(task._read_stable_asset_value(Mock(), 10, "当前估价", skip_zero=True), 100)

    def test_zero_is_kept_when_skip_zero_is_off(self):
        """默认不跳过 0, 避免影响「出售价值为 0」这类把 0 当有效读数的调用方。"""
        task = self._task([0, 0, 0])

        self.assertEqual(task._read_stable_asset_value(Mock(), 10, "出售价值"), 0)

    def test_only_zero_reads_are_reported_as_unreadable(self):
        """只读到 0 时按未读出处理, 不能把 0 当成"稳定读数"交给调用方。"""
        task = self._task([0, 0, 0])

        self.assertIsNone(task._read_stable_asset_value(Mock(), 5, "当前估价", skip_zero=True))
        task.log_warning.assert_called()


class TestAuctionEstimateBidPrice(unittest.TestCase):
    """按系统估价出价必须走稳定读取, 并在读不到时回退基础价。"""

    def _task(self, **values) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(**values)
        return task

    def test_estimate_mode_uses_the_stable_reader(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_FIXED_PRICE: 1,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "2",
            }
        )
        task._read_stable_asset_value = Mock(return_value=300)
        boxes = Mock()

        self.assertEqual(task._estimate_bid_price(boxes, None, 1), 600)
        self.assertIs(task._read_stable_asset_value.call_args.args[0], boxes.estimate)

    def test_estimate_read_skips_zero_placeholders(self):
        """估价读取必须跳过 0 占位读数, 否则 0 会被当成结果。"""
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_FIXED_PRICE: 1,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "2",
            }
        )
        task._read_stable_asset_value = Mock(return_value=300)
        boxes = Mock()

        task._estimate_bid_price(boxes, None, 1)

        self.assertTrue(task._read_stable_asset_value.call_args.kwargs["skip_zero"])

    def test_zero_reading_does_not_become_a_zero_bid(self):
        """估价为 0 时不能按 0 元出价, 应回退到基础价。"""
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_FIXED_PRICE: 7,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "1",
            }
        )
        task._read_stable_asset_value = Mock(return_value=0)

        self.assertEqual(task._estimate_bid_price(Mock(), None, 1), 7)
        task.log_warning.assert_called()

    def test_zero_reading_is_not_reported_as_a_read_failure(self):
        """读到 0 和读不到是两件事, 日志必须能区分。

        排查线上问题时这两条日志的含义完全不同: 「识别失败」指向 OCR/遮挡,
        「价格无效」指向估价本身是 0。混在一起会把面板没加载完误判成 OCR 失灵。
        """
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_FIXED_PRICE: 7,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "1",
            }
        )
        task._read_stable_asset_value = Mock(return_value=0)

        task._estimate_bid_price(Mock(), None, 1)

        self.assertNotIn("识别失败", str(task.log_warning.call_args))

    def test_falls_back_to_base_price_when_estimate_unreadable(self):
        task = self._task(
            **{
                AutoBidAuctionTask.CONF_FIXED_PRICE: 7,
                AutoBidAuctionTask.CONF_ESTIMATE_RATIO: "1",
            }
        )
        task._read_stable_asset_value = Mock(return_value=None)

        self.assertEqual(task._estimate_bid_price(Mock(), None, 1), 7)
        task.log_warning.assert_called()


class TestAuctionMainScreenTitle(unittest.TestCase):
    """拍卖结束的「回到主界面」判据从「我的资产」改成「即刻落槌」。

    「我的资产」在结算等界面也会出现, 用它判"已回主界面"会提前放行;
    「即刻落槌」只在拍卖主界面出现, 位置与藏品仓库标题是同一个槽位。
    """

    def test_main_title_shares_the_warehouse_title_slot(self):
        """用户要求主界面标题坐标与藏品仓库标题一致, 改一个别忘另一个。"""
        self.assertEqual(AutoBidAuctionTask.BOX_MAIN_TITLE, AutoBidAuctionTask.BOX_WAREHOUSE_TITLE)

    def test_main_title_regex_matches_only_the_auction_hall(self):
        self.assertTrue(RE_MAIN_TITLE.search("即刻落槌"))
        self.assertFalse(RE_MAIN_TITLE.search("我的资产"))
        self.assertFalse(RE_MAIN_TITLE.search("藏品仓库"))

    def _task(self, title_found: bool) -> AutoBidAuctionTask:
        task = _make_task()
        # wait_ocr 第一次给 _wait_operate_click(退出按钮), 第二次给主界面标题。
        task.wait_ocr = Mock(side_effect=[[Mock()], [Mock()] if title_found else []])
        task._run_post_round_actions = Mock()
        return task

    def test_title_seen_runs_post_round_actions(self):
        task = self._task(title_found=True)
        boxes = Mock()

        task._finish_auction(boxes, [Mock()], auction_module.time.monotonic() + 60)

        self.assertIs(task.wait_ocr.call_args_list[1].kwargs["box"], boxes.main_title)
        self.assertIs(task.wait_ocr.call_args_list[1].kwargs["match"], RE_MAIN_TITLE)
        task._run_post_round_actions.assert_called_once()

    def test_missing_title_skips_post_round_actions(self):
        task = self._task(title_found=False)

        task._finish_auction(Mock(), [Mock()], auction_module.time.monotonic() + 60)

        task._run_post_round_actions.assert_not_called()
        self.assertIn("即刻落槌", str(task.log_warning.call_args))


class TestAuctionMatchClickProbe(unittest.TestCase):
    """点击开始匹配后界面毫无变化时立刻重试, 不再白等 MATCH_CLICK_TIMEOUT。

    历史日志里点击成功后界面切换只要 0.6 秒, 而失败时要等满 30 秒才重试,
    09-14 一天因此白等约 11 分钟。
    """

    def setUp(self):
        self.clock = _FakeTime()
        patcher = patch.object(auction_module, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task(self, *, match: bool, confirm=False, bid=False, skip=False) -> AutoBidAuctionTask:
        task = _make_task()
        task._is_match_screen = Mock(return_value=match)
        task._is_confirm_screen = Mock(return_value=confirm)
        task._is_bid_screen = Mock(return_value=bid)
        task._is_skip_screen = Mock(return_value=skip)
        task._wait_operate_click = Mock(return_value=True)
        task.sleep = Mock(side_effect=self.clock.sleep)
        return task

    def test_retries_immediately_when_button_never_left(self):
        """按钮仍在原位说明点击没生效, 应在 MATCH_PROBE_TIMEOUT 内交回上层重试。"""
        task = self._task(match=True)
        started = self.clock.now

        self.assertIsNone(task._handle_match_click(Mock(), self.clock.now + 120))

        elapsed = self.clock.now - started
        self.assertLessEqual(elapsed, AutoBidAuctionTask.MATCH_PROBE_TIMEOUT)
        self.assertLess(elapsed, AutoBidAuctionTask.MATCH_CLICK_TIMEOUT)

    def test_waits_full_timeout_while_screen_is_loading(self):
        """按钮已消失(处于加载动画)但目标界面未出现时, 仍应等满 MATCH_CLICK_TIMEOUT。"""
        task = self._task(match=False)
        started = self.clock.now

        self.assertIsNone(task._handle_match_click(Mock(), self.clock.now + 120))

        self.assertGreaterEqual(self.clock.now - started, AutoBidAuctionTask.MATCH_CLICK_TIMEOUT)

    def test_probe_waits_before_giving_up(self):
        """按钮残留时要先观察 MATCH_PROBE_TIMEOUT 秒, 不能第一帧就判点击失败。"""
        task = self._task(match=True)

        self.assertIsNone(task._handle_match_click(Mock(), self.clock.now + 120))

        expected_polls = AutoBidAuctionTask.MATCH_PROBE_TIMEOUT / AutoBidAuctionTask.POLL_INTERVAL
        self.assertGreaterEqual(task.sleep.call_count, expected_polls)

    def test_returns_confirm_as_soon_as_it_appears(self):
        task = self._task(match=False, confirm=True)

        self.assertEqual(
            task._handle_match_click(Mock(), self.clock.now + 120), AuctionState.CONFIRM
        )
        self.assertEqual(task.sleep.call_count, 0)

    def test_returns_skip_state(self):
        task = self._task(match=False, skip=True)

        self.assertEqual(task._handle_match_click(Mock(), self.clock.now + 120), AuctionState.SKIP)

    def test_missing_button_returns_none_without_waiting(self):
        task = self._task(match=True)
        task._wait_operate_click = Mock(return_value=False)
        started = self.clock.now

        self.assertIsNone(task._handle_match_click(Mock(), self.clock.now + 120))

        self.assertEqual(self.clock.now, started)

    def test_stage_budget_expiry_reports_match_stage(self):
        """匹配阶段的局部预算用尽时整轮往往还剩几百秒, 异常消息不能是「单轮拍卖超时」。"""
        task = self._task(match=True)

        with self.assertRaises(WaitFailedException) as ctx:
            task._handle_match_click(Mock(), self.clock.now)

        self.assertIn("匹配阶段", str(ctx.exception))
        self.assertNotIn("单轮拍卖超时", str(ctx.exception))


class TestAuctionBidModeConfigVisibility(unittest.TestCase):
    """出价模式决定哪些价格配置可见, 且不能有字段被永久隐藏。"""

    def _visible_keys(self, task: AutoBidAuctionTask, mode: str, **overrides) -> set[str]:
        config = dict(task.default_config)
        config[AutoBidAuctionTask.CONF_BID_MODE] = mode
        config.update(overrides)
        fields = build_config_fields(config, task.config_description, task.config_type)
        return {field["key"] for field in fields}

    def test_custom_mode_shows_legacy_price_configs(self):
        task = _make_configured_task()
        visible = self._visible_keys(task, AutoBidAuctionTask.BID_MODE_CUSTOM)

        self.assertIn(AutoBidAuctionTask.CONF_FIXED_PRICE, visible)
        self.assertIn(AutoBidAuctionTask.CONF_AUTO_RAISE, visible)
        self.assertIn(AutoBidAuctionTask.CONF_RAISE_MODE, visible)
        self.assertIn(AutoBidAuctionTask.CONF_SPECIAL_ROUND, visible)
        self.assertFalse(set(AutoBidAuctionTask.CONF_BID_PRICES) & visible)
        self.assertNotIn(AutoBidAuctionTask.CONF_ESTIMATE_RATIO, visible)

    def test_list_mode_shows_all_six_bid_prices(self):
        task = _make_configured_task()
        visible = self._visible_keys(task, AutoBidAuctionTask.BID_MODE_LIST)

        self.assertEqual(set(AutoBidAuctionTask.CONF_BID_PRICES) - visible, set())
        self.assertNotIn(AutoBidAuctionTask.CONF_FIXED_PRICE, visible)
        self.assertNotIn(AutoBidAuctionTask.CONF_AUTO_RAISE, visible)
        self.assertNotIn(AutoBidAuctionTask.CONF_ESTIMATE_RATIO, visible)

    def test_estimate_mode_shows_only_ratio(self):
        task = _make_configured_task()
        visible = self._visible_keys(task, AutoBidAuctionTask.BID_MODE_ESTIMATE)

        self.assertIn(AutoBidAuctionTask.CONF_ESTIMATE_RATIO, visible)
        self.assertNotIn(AutoBidAuctionTask.CONF_FIXED_PRICE, visible)
        self.assertFalse(set(AutoBidAuctionTask.CONF_BID_PRICES) & visible)

    def test_every_price_config_is_reachable_in_some_mode(self):
        """sub_configs 里的键名写错会让字段在所有模式下都隐藏, 用户根本改不了。"""
        task = _make_configured_task()
        price_keys = {
            task.CONF_FIXED_PRICE,
            task.CONF_AUTO_RAISE,
            task.CONF_RAISE_MODE,
            task.CONF_RAISE_VALUE,
            task.CONF_RAISE_ROUND,
            task.CONF_SPECIAL_ROUND,
            task.CONF_SPECIAL_ROUNDS,
            task.CONF_SPECIAL_ROUND_PRICE,
            task.CONF_ESTIMATE_RATIO,
            *task.CONF_BID_PRICES,
        }
        # 子配置的可见性还取决于父项的当前值, 这里把相关开关都打开。
        toggles = {
            task.CONF_AUTO_RAISE: True,
            task.CONF_SPECIAL_ROUND: True,
            task.CONF_RAISE_MODE: task.RAISE_MODE_MULTIPLE,
        }

        reachable: set[str] = set()
        for mode in (task.BID_MODE_CUSTOM, task.BID_MODE_LIST, task.BID_MODE_ESTIMATE):
            reachable |= self._visible_keys(task, mode, **toggles)

        self.assertEqual(price_keys - reachable, set())

    def test_other_configs_are_never_hidden_by_bid_mode(self):
        """出价模式只应影响价格相关配置, 别把藏品/低保金配置一起藏掉。"""
        task = _make_configured_task()
        unrelated = {
            task.CONF_SELL_MODE,
            task.CONF_SELL_INTERVAL,
            task.CONF_KEEP_QUALITIES,
            task.CONF_EXTRA_SELL_QUALITIES,
            task.CONF_ASSIST_FEATURES,
        }
        # 出售子项由「出售藏品模式」控制可见性, 这里固定成会展示它们的模式。
        sell_on = {task.CONF_SELL_MODE: task.SELL_MODE_INTERVAL}

        for mode in (task.BID_MODE_CUSTOM, task.BID_MODE_LIST, task.BID_MODE_ESTIMATE):
            visible = self._visible_keys(task, mode, **sell_on)
            self.assertEqual(unrelated - visible, set(), f"{mode} 下配置项被误隐藏")


class TestAuctionSellModeConfigVisibility(unittest.TestCase):
    """出售相关配置收在「出售藏品模式」下, 只有选到对应模式才展开。"""

    def _visible_keys(self, task: AutoBidAuctionTask, mode: str, **overrides) -> set[str]:
        config = dict(task.default_config)
        config[AutoBidAuctionTask.CONF_SELL_MODE] = mode
        config.update(overrides)
        fields = build_config_fields(config, task.config_description, task.config_type)
        return {field["key"] for field in fields}

    def test_default_mode_keeps_the_panel_short(self):
        """默认「拍卖成功一键出售」时, 面板上只留一个模式下拉框。"""
        task = _make_configured_task()
        self.assertEqual(task.default_config[task.CONF_SELL_MODE], task.SELL_MODE_ONE_CLICK)
        visible = self._visible_keys(task, task.SELL_MODE_ONE_CLICK)

        self.assertIn(task.CONF_SELL_MODE, visible)
        self.assertNotIn(task.CONF_SELL_INTERVAL, visible)
        self.assertNotIn(task.CONF_KEEP_QUALITIES, visible)
        self.assertNotIn(task.CONF_EXTRA_SELL_QUALITIES, visible)

    def test_full_mode_shows_quality_configs_only(self):
        task = _make_configured_task()
        visible = self._visible_keys(task, task.SELL_MODE_FULL)

        self.assertIn(task.CONF_KEEP_QUALITIES, visible)
        self.assertIn(task.CONF_EXTRA_SELL_QUALITIES, visible)
        self.assertNotIn(task.CONF_SELL_INTERVAL, visible)

    def test_interval_mode_shows_every_sell_config(self):
        task = _make_configured_task()
        visible = self._visible_keys(task, task.SELL_MODE_INTERVAL)

        self.assertIn(task.CONF_SELL_INTERVAL, visible)
        self.assertIn(task.CONF_KEEP_QUALITIES, visible)
        self.assertIn(task.CONF_EXTRA_SELL_QUALITIES, visible)

    def test_every_sell_config_is_reachable_in_some_mode(self):
        """sub_configs 里的键名写错会让字段在所有模式下都隐藏, 用户根本改不了。"""
        task = _make_configured_task()
        sell_keys = {
            task.CONF_SELL_INTERVAL,
            task.CONF_KEEP_QUALITIES,
            task.CONF_EXTRA_SELL_QUALITIES,
        }

        reachable: set[str] = set()
        for mode in task.SELL_MODES:
            reachable |= self._visible_keys(task, mode)

        self.assertEqual(sell_keys - reachable, set())

    def test_one_click_mode_shows_no_sell_configs(self):
        """一键出售不筛品质也没有间隔, 选它时面板不该展开任何子项。"""
        task = _make_configured_task()
        visible = self._visible_keys(task, task.SELL_MODE_ONE_CLICK)

        self.assertIn(task.CONF_SELL_MODE, visible)
        self.assertNotIn(task.CONF_SELL_INTERVAL, visible)
        self.assertNotIn(task.CONF_KEEP_QUALITIES, visible)
        self.assertNotIn(task.CONF_EXTRA_SELL_QUALITIES, visible)

    def test_every_mode_declares_its_sub_configs(self):
        """新增模式时漏写 sub_configs 条目, 将来给它加子项会静默不显示。"""
        task = _make_configured_task()
        declared = task.config_type[task.CONF_SELL_MODE]["sub_configs"]

        self.assertEqual(set(declared), set(task.SELL_MODES))

    def test_sell_mode_is_declared_as_a_drop_down(self):
        """模式必须是下拉框, 否则 sub_configs 不会生效。"""
        task = _make_configured_task()
        fields = build_config_fields(task.default_config, task.config_description, task.config_type)
        mode_field = next(f for f in fields if f["key"] == task.CONF_SELL_MODE)

        self.assertEqual(mode_field["kind"], "select")
        self.assertEqual(set(mode_field["options"]), set(task.SELL_MODES))

    def test_legacy_auto_clear_key_is_gone(self):
        """旧开关合并进模式后不应再注册, 否则面板上会多出一个失效控件。"""
        task = _make_configured_task()
        self.assertNotIn(task.LEGACY_CONF_AUTO_CLEAR, task.default_config)
        self.assertNotIn(task.LEGACY_CONF_AUTO_CLEAR, task.config_type)
        self.assertNotIn(task.LEGACY_CONF_AUTO_CLEAR, task.config_description)


class TestAuctionSellModeMigration(unittest.TestCase):
    """旧版「启用自动清理藏品 / 出售藏品间隔次数」要能迁移到新模式。"""

    def test_auto_clear_maps_to_full_mode(self):
        raw = {AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR: True}

        self.assertEqual(
            AutoBidAuctionTask._migrate_sell_mode(raw), AutoBidAuctionTask.SELL_MODE_FULL
        )

    def test_interval_maps_to_interval_mode(self):
        raw = {
            AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR: False,
            AutoBidAuctionTask.CONF_SELL_INTERVAL: 3,
        }

        self.assertEqual(
            AutoBidAuctionTask._migrate_sell_mode(raw), AutoBidAuctionTask.SELL_MODE_INTERVAL
        )

    def test_auto_clear_wins_over_interval(self):
        """旧版自动清理开启时忽略间隔, 迁移后不能反而变成按间隔出售。"""
        raw = {
            AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR: True,
            AutoBidAuctionTask.CONF_SELL_INTERVAL: 3,
        }

        self.assertEqual(
            AutoBidAuctionTask._migrate_sell_mode(raw), AutoBidAuctionTask.SELL_MODE_FULL
        )

    def test_disabled_legacy_config_maps_to_off(self):
        raw = {
            AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR: False,
            AutoBidAuctionTask.CONF_SELL_INTERVAL: 0,
        }

        self.assertEqual(
            AutoBidAuctionTask._migrate_sell_mode(raw), AutoBidAuctionTask.SELL_MODE_OFF
        )

    def test_boolean_interval_is_not_read_as_one(self):
        """bool 是 int 的子类, True 不该被当成间隔 1 而误判成按间隔出售。"""
        raw = {AutoBidAuctionTask.CONF_SELL_INTERVAL: True}

        self.assertEqual(
            AutoBidAuctionTask._migrate_sell_mode(raw), AutoBidAuctionTask.SELL_MODE_OFF
        )

    def test_non_dict_input_is_treated_as_off(self):
        self.assertEqual(
            AutoBidAuctionTask._migrate_sell_mode(None), AutoBidAuctionTask.SELL_MODE_OFF
        )

    def _migrate(self, raw, *, default_mode=None):
        task = _make_configured_task()
        if default_mode is not None:
            task.default_config[AutoBidAuctionTask.CONF_SELL_MODE] = default_mode
        task.log_info = Mock()
        task._migrate_legacy_sell_config(raw)
        return task

    def test_migration_writes_the_derived_mode_into_defaults(self):
        """写进去的必须是推导结果, 不能是写死的模式。"""
        task = self._migrate(
            {
                AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR: True,
                AutoBidAuctionTask.CONF_SELL_INTERVAL: 4,
            }
        )

        self.assertEqual(
            task.default_config[AutoBidAuctionTask.CONF_SELL_MODE],
            AutoBidAuctionTask.SELL_MODE_FULL,
        )

    def test_migration_is_skipped_when_the_new_key_already_exists(self):
        """已经迁移过的配置不能被旧键再覆盖一次。

        默认值刻意选成旧键推导不出来的模式, 否则「跳过」和「重新推导」结果相同,
        测试就无法区分迁移有没有真的被跳过。
        """
        task = self._migrate(
            {
                AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_OFF,
                AutoBidAuctionTask.CONF_SELL_INTERVAL: 4,
            },
            default_mode=AutoBidAuctionTask.SELL_MODE_FULL,
        )

        self.assertEqual(
            task.default_config[AutoBidAuctionTask.CONF_SELL_MODE],
            AutoBidAuctionTask.SELL_MODE_FULL,
        )

    def test_migration_is_skipped_for_a_brand_new_user(self):
        """没有配置文件(全新用户)时不该写入任何迁移结果。

        load_config 读到空文件会归一成空字典, 所以这里传 {} 而不是 None。
        """
        task = self._migrate({}, default_mode=AutoBidAuctionTask.SELL_MODE_FULL)

        self.assertEqual(
            task.default_config[AutoBidAuctionTask.CONF_SELL_MODE],
            AutoBidAuctionTask.SELL_MODE_FULL,
        )

    def test_migration_is_skipped_when_no_legacy_key_is_present(self):
        task = self._migrate(
            {AutoBidAuctionTask.CONF_KEEP_QUALITIES: ["品质红"]},
            default_mode=AutoBidAuctionTask.SELL_MODE_FULL,
        )

        self.assertEqual(
            task.default_config[AutoBidAuctionTask.CONF_SELL_MODE],
            AutoBidAuctionTask.SELL_MODE_FULL,
        )

    def test_migration_does_not_touch_defaults_when_sell_mode_is_unregistered(self):
        """模式键被移除时迁移必须静默跳过, 不能给 default_config 塞回一个野键。"""
        task = _make_configured_task()
        task.default_config.pop(AutoBidAuctionTask.CONF_SELL_MODE)
        task.log_info = Mock()
        task._migrate_legacy_sell_config({AutoBidAuctionTask.CONF_SELL_INTERVAL: 4})

        self.assertNotIn(AutoBidAuctionTask.CONF_SELL_MODE, task.default_config)

    def test_legacy_config_file_migrates_end_to_end(self):
        """走一遍真实的 Config 读写: 模式落盘, 旧键被清掉, 二次加载保持稳定。"""
        task = _make_configured_task()
        task.log_info = Mock()
        legacy = {
            AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR: False,
            AutoBidAuctionTask.CONF_SELL_INTERVAL: 5,
            AutoBidAuctionTask.CONF_KEEP_QUALITIES: ["品质红"],
        }

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{type(task).__name__}.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

            with patch.object(Config, "config_folder", folder):
                task._migrate_legacy_sell_config(legacy)
                config = Config(type(task).__name__, task.default_config)

                self.assertEqual(
                    config[AutoBidAuctionTask.CONF_SELL_MODE],
                    AutoBidAuctionTask.SELL_MODE_INTERVAL,
                )
                # 用户原来的保留品质不能被迁移弄丢.
                self.assertEqual(config[AutoBidAuctionTask.CONF_KEEP_QUALITIES], ["品质红"])

                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn(AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR, saved)
                self.assertEqual(
                    saved[AutoBidAuctionTask.CONF_SELL_MODE],
                    AutoBidAuctionTask.SELL_MODE_INTERVAL,
                )

                # 二次加载时旧键已不在文件里, 迁移不应再改写已经落盘的模式.
                second = _make_configured_task()
                second.log_info = Mock()
                second.default_config[AutoBidAuctionTask.CONF_SELL_MODE] = (
                    AutoBidAuctionTask.SELL_MODE_OFF
                )
                second._migrate_legacy_sell_config(json.loads(path.read_text(encoding="utf-8")))
                self.assertEqual(
                    second.default_config[AutoBidAuctionTask.CONF_SELL_MODE],
                    AutoBidAuctionTask.SELL_MODE_OFF,
                )


class TestAuctionAssistFeaturesConfig(unittest.TestCase):
    """「启用表情包 / 启用低保金」合并成一个多选框, 面板和判定都要跟着走。"""

    def test_assist_features_render_as_a_multi_selection(self):
        """ok-script 里 bool 只能渲染成开关按钮, 要出勾选框必须声明 multi_selection。"""
        task = _make_configured_task()
        fields = build_config_fields(task.default_config, task.config_description, task.config_type)
        field = next(f for f in fields if f["key"] == task.CONF_ASSIST_FEATURES)

        self.assertEqual(field["kind"], "multi_selection")
        self.assertEqual(set(field["options"]), set(task.ASSIST_FEATURES))

    def test_default_checks_the_welfare_feature(self):
        """默认勾选「低保金」, 表情包仍需用户主动开启。"""
        task = _make_configured_task()

        self.assertEqual(
            task.default_config[task.CONF_ASSIST_FEATURES], [task.ASSIST_WELFARE]
        )

    def test_legacy_assist_switches_are_gone(self):
        """旧开关合并进多选框后不应再注册, 否则面板上会多出两个失效控件。"""
        task = _make_configured_task()

        for key in (task.LEGACY_CONF_USE_EMOTE, task.LEGACY_CONF_USE_WELFARE):
            self.assertNotIn(key, task.default_config)
            self.assertNotIn(key, task.config_type)
            self.assertNotIn(key, task.config_description)

    def _task_with(self, value) -> AutoBidAuctionTask:
        task = _make_task()
        task.config = _config(**{AutoBidAuctionTask.CONF_ASSIST_FEATURES: value})
        return task

    def test_feature_is_enabled_only_when_checked(self):
        task = self._task_with([AutoBidAuctionTask.ASSIST_EMOTE])

        self.assertTrue(task._assist_enabled(AutoBidAuctionTask.ASSIST_EMOTE))
        self.assertFalse(task._assist_enabled(AutoBidAuctionTask.ASSIST_WELFARE))

    def test_empty_selection_disables_everything(self):
        task = self._task_with([])

        self.assertFalse(task._assist_enabled(AutoBidAuctionTask.ASSIST_EMOTE))
        self.assertFalse(task._assist_enabled(AutoBidAuctionTask.ASSIST_WELFARE))

    def test_dirty_value_disables_everything(self):
        """旧 bool 或手工填的字符串一律按未勾选处理, 不能当成已启用去点不存在的按钮。"""
        for value in (None, True, False, "", "表情包"):
            task = self._task_with(value)
            with self.subTest(value=value):
                self.assertFalse(task._assist_enabled(AutoBidAuctionTask.ASSIST_EMOTE))

    def test_emote_is_sent_only_when_checked(self):
        for features in ([AutoBidAuctionTask.ASSIST_EMOTE], []):
            task = self._task_with(features)
            task._read_asset_value = Mock(return_value=1000)
            task._wait_operate_click = Mock(return_value=True)
            task.wait_ocr = Mock(return_value=object())
            task._input_fixed_price = Mock()
            task._is_bid_screen = Mock(return_value=False)
            task._send_emote = Mock()

            task._attempt_bid(Mock(), float("inf"))

            with self.subTest(features=features):
                self.assertEqual(task._send_emote.called, bool(features))

    def test_welfare_is_claimed_only_when_checked(self):
        for features in ([AutoBidAuctionTask.ASSIST_WELFARE], []):
            task = self._task_with(features)
            task._claim_welfare_if_needed = Mock(return_value=True)

            task._run_post_round_actions(Mock(), None)

            with self.subTest(features=features):
                self.assertEqual(task._claim_welfare_if_needed.called, bool(features))
                self.assertEqual(task._post_round_state.welfare_claimed, bool(features))


class TestAuctionAssistConfigMigration(unittest.TestCase):
    """旧版「启用表情包 / 启用低保金」两个开关要能迁移到「启用辅助功能」多选框。"""

    def test_enabled_legacy_switches_become_checked_options(self):
        raw = {
            AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE: True,
            AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE: True,
        }

        self.assertEqual(
            AutoBidAuctionTask._migrate_assist_features(raw),
            [AutoBidAuctionTask.ASSIST_EMOTE, AutoBidAuctionTask.ASSIST_WELFARE],
        )

    def test_only_enabled_switch_is_checked(self):
        raw = {
            AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE: True,
            AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE: False,
        }

        self.assertEqual(
            AutoBidAuctionTask._migrate_assist_features(raw),
            [AutoBidAuctionTask.ASSIST_EMOTE],
        )

    def test_both_off_migrates_to_an_empty_selection(self):
        raw = {
            AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE: False,
            AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE: False,
        }

        self.assertEqual(AutoBidAuctionTask._migrate_assist_features(raw), [])

    def test_truthy_non_boolean_values_are_not_treated_as_enabled(self):
        """旧值只认 True, 手工填的 1 或字符串不该让任务去点表情包按钮。"""
        raw = {
            AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE: "1",
            AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE: 1,
        }

        self.assertEqual(AutoBidAuctionTask._migrate_assist_features(raw), [])

    def _migrate(self, raw, *, default=None):
        task = _make_configured_task()
        if default is not None:
            task.default_config[AutoBidAuctionTask.CONF_ASSIST_FEATURES] = default
        task.log_info = Mock()
        task._migrate_legacy_assist_config(raw)
        return task

    def test_migration_writes_the_derived_selection_into_defaults(self):
        """写进去的必须是推导结果, 不能是写死的勾选项。"""
        task = self._migrate({AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE: True})

        self.assertEqual(
            task.default_config[AutoBidAuctionTask.CONF_ASSIST_FEATURES],
            [AutoBidAuctionTask.ASSIST_WELFARE],
        )

    def test_migration_is_skipped_when_the_new_key_already_exists(self):
        """已经迁移过的配置不能被旧键再覆盖一次。

        默认值刻意选成旧键推导不出来的勾选项, 否则「跳过」和「重新推导」结果相同,
        测试就无法区分迁移有没有真的被跳过。
        """
        task = self._migrate(
            {
                AutoBidAuctionTask.CONF_ASSIST_FEATURES: [],
                AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE: True,
            },
            default=[AutoBidAuctionTask.ASSIST_WELFARE],
        )

        self.assertEqual(
            task.default_config[AutoBidAuctionTask.CONF_ASSIST_FEATURES],
            [AutoBidAuctionTask.ASSIST_WELFARE],
        )

    def test_migration_is_skipped_for_a_brand_new_user(self):
        task = self._migrate({}, default=[AutoBidAuctionTask.ASSIST_EMOTE])

        self.assertEqual(
            task.default_config[AutoBidAuctionTask.CONF_ASSIST_FEATURES],
            [AutoBidAuctionTask.ASSIST_EMOTE],
        )

    def test_migration_does_not_touch_defaults_when_key_is_unregistered(self):
        """多选框键被移除时迁移必须静默跳过, 不能给 default_config 塞回一个野键。"""
        task = _make_configured_task()
        task.default_config.pop(AutoBidAuctionTask.CONF_ASSIST_FEATURES)
        task.log_info = Mock()
        task._migrate_legacy_assist_config({AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE: True})

        self.assertNotIn(AutoBidAuctionTask.CONF_ASSIST_FEATURES, task.default_config)

    def test_legacy_config_file_migrates_end_to_end(self):
        """走一遍真实的 Config 读写: 勾选项落盘, 旧键被清掉, 二次加载保持稳定。"""
        task = _make_configured_task()
        task.log_info = Mock()
        legacy = {
            AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE: True,
            AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE: False,
            AutoBidAuctionTask.CONF_KEEP_QUALITIES: ["品质红"],
        }

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{type(task).__name__}.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

            with patch.object(Config, "config_folder", folder):
                task._migrate_legacy_assist_config(legacy)
                config = Config(type(task).__name__, task.default_config)

                self.assertEqual(
                    config[AutoBidAuctionTask.CONF_ASSIST_FEATURES],
                    [AutoBidAuctionTask.ASSIST_EMOTE],
                )
                # 用户原来的保留品质不能被迁移弄丢.
                self.assertEqual(config[AutoBidAuctionTask.CONF_KEEP_QUALITIES], ["品质红"])

                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn(AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE, saved)
                self.assertNotIn(AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE, saved)
                self.assertEqual(
                    saved[AutoBidAuctionTask.CONF_ASSIST_FEATURES],
                    [AutoBidAuctionTask.ASSIST_EMOTE],
                )


class TestAuctionRaiseModeRename(unittest.TestCase):
    """「加价方式」的取值由「倍数」改名为「倍率」, 三处联动都必须跟上。"""

    def test_options_only_contain_the_new_value(self):
        task = _make_configured_task()

        options = task.config_type[task.CONF_RAISE_MODE]["options"]

        self.assertEqual(options, list(task.RAISE_MODES))
        self.assertNotIn(task.LEGACY_RAISE_MODE_MULTIPLE, options)

    def test_default_value_is_one_of_the_options(self):
        """默认值不在 options 里, 全新用户的下拉框会直接显示空白。"""
        task = _make_configured_task()

        self.assertIn(task.default_config[task.CONF_RAISE_MODE], task.RAISE_MODES)

    def test_every_option_controls_the_raise_value_widget(self):
        """sub_configs 的键就是选项取值, 漏一个会让「加价数值」在该方式下永久隐藏。"""
        task = _make_configured_task()

        sub_configs = task.config_type[task.CONF_RAISE_MODE]["sub_configs"]

        self.assertEqual(set(sub_configs), set(task.RAISE_MODES))
        for mode, children in sub_configs.items():
            self.assertIn(task.CONF_RAISE_VALUE, children, mode)

    def test_raise_value_is_visible_in_every_mode(self):
        task = _make_configured_task()
        base = {
            **task.default_config,
            task.CONF_AUTO_RAISE: True,
            task.CONF_BID_MODE: task.BID_MODE_CUSTOM,
        }

        for mode in task.RAISE_MODES:
            fields = build_config_fields(
                {**base, task.CONF_RAISE_MODE: mode},
                task.config_description,
                task.config_type,
            )
            self.assertIn(task.CONF_RAISE_VALUE, [field["key"] for field in fields], mode)

    def _price(self, mode: str) -> int:
        task = _make_task()
        task.config = _config(
            **{
                AutoBidAuctionTask.CONF_RAISE_MODE: mode,
                AutoBidAuctionTask.CONF_RAISE_VALUE: "1.6",
                AutoBidAuctionTask.CONF_RAISE_ROUND: 0,
            }
        )
        return task._raise_price(100, 1)

    def test_legacy_value_is_normalized_when_read(self):
        """配置文件读不到时迁移不会执行, 读取兜底必须自己把旧取值归一。"""
        task = _make_task()
        task.config = _config(
            **{AutoBidAuctionTask.CONF_RAISE_MODE: AutoBidAuctionTask.LEGACY_RAISE_MODE_MULTIPLE}
        )

        self.assertEqual(task._raise_mode(), AutoBidAuctionTask.RAISE_MODE_MULTIPLE)

    def test_legacy_value_keeps_the_exponential_branch(self):
        """判定串失配会静默退化成线性加价, 这条守住旧配置的价格不被改写。"""
        # 基础价 100, 倍率 1.6, 第 1 次: 指数 160, 退化成自定义则只有 102.
        self.assertEqual(self._price(AutoBidAuctionTask.RAISE_MODE_MULTIPLE), 160)
        self.assertEqual(
            self._price(AutoBidAuctionTask.LEGACY_RAISE_MODE_MULTIPLE),
            self._price(AutoBidAuctionTask.RAISE_MODE_MULTIPLE),
        )

    def test_unknown_value_keeps_the_custom_fallback(self):
        """未知取值仍按「自定义」兜底, 改名不该顺手改掉这条既有语义。"""
        self.assertEqual(
            self._price("手改坏了的取值"),
            self._price(AutoBidAuctionTask.RAISE_MODE_CUSTOM),
        )


class TestAuctionRaiseModeMigration(unittest.TestCase):
    """存量配置里的「倍数」要在加载时改写, 否则下拉框空白且价格静默变线性。"""

    def _migrate(self, raw):
        task = _make_configured_task()
        # 模拟 Config 载入后的内容: 键还在, 取值是旧版写的.
        task.config = dict(raw)
        task.log_info = Mock()
        task._migrate_legacy_raise_mode(raw)
        return task

    def test_legacy_value_is_rewritten(self):
        task = self._migrate(
            {AutoBidAuctionTask.CONF_RAISE_MODE: AutoBidAuctionTask.LEGACY_RAISE_MODE_MULTIPLE}
        )

        self.assertEqual(
            task.config[AutoBidAuctionTask.CONF_RAISE_MODE],
            AutoBidAuctionTask.RAISE_MODE_MULTIPLE,
        )

    def test_current_value_is_left_alone(self):
        task = self._migrate(
            {AutoBidAuctionTask.CONF_RAISE_MODE: AutoBidAuctionTask.RAISE_MODE_PERCENT}
        )

        self.assertEqual(
            task.config[AutoBidAuctionTask.CONF_RAISE_MODE],
            AutoBidAuctionTask.RAISE_MODE_PERCENT,
        )

    def test_missing_key_is_ignored(self):
        task = self._migrate({})

        self.assertEqual(task.config, {})

    def test_migration_is_skipped_when_the_key_is_unregistered(self):
        """键被移除时迁移必须静默跳过, 不能给配置塞回一个野键。"""
        task = _make_configured_task()
        task.default_config.pop(AutoBidAuctionTask.CONF_RAISE_MODE)
        task.config = {}
        task.log_info = Mock()

        task._migrate_legacy_raise_mode(
            {AutoBidAuctionTask.CONF_RAISE_MODE: AutoBidAuctionTask.LEGACY_RAISE_MODE_MULTIPLE}
        )

        self.assertEqual(task.config, {})

    def test_legacy_config_file_migrates_end_to_end(self):
        """走一遍真实的 Config 读写: 新取值落盘, 用户其它配置不丢。"""
        task = _make_configured_task()
        task.log_info = Mock()
        legacy = {
            AutoBidAuctionTask.CONF_RAISE_MODE: AutoBidAuctionTask.LEGACY_RAISE_MODE_MULTIPLE,
            AutoBidAuctionTask.CONF_RAISE_VALUE: "1.6",
        }

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"{type(task).__name__}.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

            with patch.object(Config, "config_folder", folder):
                config = Config(type(task).__name__, task.default_config)
                # Config 不会覆盖已存在的键, 所以旧取值原样载入 —— 正是要迁移的场景.
                self.assertEqual(
                    config[AutoBidAuctionTask.CONF_RAISE_MODE],
                    AutoBidAuctionTask.LEGACY_RAISE_MODE_MULTIPLE,
                )

                task.config = config
                task._migrate_legacy_raise_mode(legacy)

                self.assertEqual(
                    config[AutoBidAuctionTask.CONF_RAISE_MODE],
                    AutoBidAuctionTask.RAISE_MODE_MULTIPLE,
                )
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    saved[AutoBidAuctionTask.CONF_RAISE_MODE],
                    AutoBidAuctionTask.RAISE_MODE_MULTIPLE,
                )
                # 用户自己填的加价数值不能被迁移弄丢.
                self.assertEqual(saved[AutoBidAuctionTask.CONF_RAISE_VALUE], "1.6")


class TestAuctionConfigDescriptions(unittest.TestCase):
    """配置项描述要覆盖完整, 且遵守仓库的 ASCII 标点约定。"""

    def _keys(self, task: AutoBidAuctionTask) -> list[str]:
        # _ 开头的键不渲染到面板, 不需要描述.
        return [key for key in task.default_config if not str(key).startswith("_")]

    def test_every_config_key_has_a_description(self):
        """新增配置项时忘了写描述, 面板上会出现一个没有任何说明的控件。"""
        task = _make_configured_task()

        missing = [key for key in self._keys(task) if not task.config_description.get(key)]

        self.assertEqual(missing, [])

    def test_descriptions_are_not_blank(self):
        task = _make_configured_task()

        blank = [
            key
            for key in self._keys(task)
            if task.config_description.get(key, "") != task.config_description.get(key, "").strip()
            or not task.config_description.get(key, "").strip()
        ]

        self.assertEqual(blank, [])

    def test_descriptions_use_ascii_punctuation(self):
        """AGENTS.md 要求源码字符串用 ASCII `,` `;`, 避免易混淆 Unicode 告警。"""
        task = _make_configured_task()
        full_width = set("，；：（）")

        offenders = {
            key: desc for key, desc in task.config_description.items() if full_width & set(desc)
        }

        self.assertEqual(offenders, {})

    def test_no_description_for_a_removed_config_key(self):
        """删掉配置项却留下描述, 会让人以为那个键还在。"""
        task = _make_configured_task()

        orphans = [key for key in task.config_description if key not in task.default_config]

        self.assertEqual(orphans, [])

    def test_bid_price_descriptions_match_their_index(self):
        """6 个价格共用一套模板, 写错序号会让用户按错误的规则填值。"""
        task = _make_configured_task()

        for index, key in enumerate(task.CONF_BID_PRICES, start=1):
            description = task.config_description[key]
            self.assertIn(f"第 {index} 次出价的价格", description)


class TestAuctionPostRoundTimeoutGrace(unittest.TestCase):
    """结算后处理属于可选的收尾, 单轮时间用尽不该把已经结算成功的轮次判成失败。

    同一个方法里的三个步骤曾经用两套超时口径: 满仓检测和弹窗兜底走 _timeout_or_zero
    (deadline 用尽返回 0), 低保金的资产读取走 _remaining_timeout(抛 WaitFailedException)。
    异常传播出去会连带丢掉写回 _post_round_state 的那一行, 于是本轮既被记为失败,
    轮次末尾又因为 observed 是 False 而跳过出售。
    """

    def _expired(self) -> float:
        return auction_module.time.monotonic() - 1.0

    def test_claim_welfare_skips_when_deadline_used_up(self):
        task = _make_task()
        task._read_asset_value = Mock(return_value=1)

        self.assertFalse(task._claim_welfare_if_needed(Mock(), self._expired()))
        task._read_asset_value.assert_not_called()

    def test_inventory_probe_and_notice_popup_share_the_same_grace(self):
        """三个步骤的口径必须一致: 任一在过期 deadline 上抛异常都会毁掉整轮。"""
        task = _make_task()
        task._read_asset_value = Mock(return_value=1)
        expired = self._expired()

        task._dismiss_notice_popup(Mock(), expired, "探针")
        task._detect_inventory_full(Mock(), task._timeout_or_zero(expired, 3))
        task._claim_welfare_if_needed(Mock(), expired)

    def test_post_round_actions_still_records_observation_on_welfare_timeout(self):
        """低保金领取超时时, 观测结果仍要写回, 否则轮次末尾会连带跳过出售。"""
        task = _make_task(
            {AutoBidAuctionTask.CONF_ASSIST_FEATURES: [AutoBidAuctionTask.ASSIST_WELFARE]}
        )
        task._dismiss_notice_popup = Mock(return_value=False)
        task._claim_welfare_if_needed = Mock(side_effect=WaitFailedException("单轮拍卖超时"))

        task._run_post_round_actions(Mock(), self._expired())

        self.assertTrue(task._post_round_state.observed)
        self.assertFalse(task._post_round_state.welfare_claimed)

    def test_finished_round_still_sells_after_post_round_timeout(self):
        """结算后处理超时不能让本轮变成失败, 也不能连带跳过出售。"""
        from src.tasks.mixin.RoundMixin import RoundState

        task = _make_task(
            {
                AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_FULL,
                AutoBidAuctionTask.CONF_ASSIST_FEATURES: [AutoBidAuctionTask.ASSIST_WELFARE],
            }
        )
        task._round_state = RoundState(total=0, index=1)
        task._dismiss_notice_popup = Mock(return_value=False)
        task._detect_inventory_full = Mock(return_value=True)
        task._read_asset_value = Mock(return_value=1)
        task._try_claim_welfare = Mock(side_effect=WaitFailedException("单轮拍卖超时"))
        task._sell_collections_on_interval = Mock()
        task.add_success = Mock()
        task.add_failed = Mock()
        expired = self._expired()

        def fake_round(boxes):
            # _finish_auction 在结算成功后调用结算后处理, 这里模拟同样的顺序。
            task._run_post_round_actions(boxes, expired)
            return True

        task._exec_auction_round = Mock(side_effect=fake_round)

        task._run_single_round(Mock())

        self.assertTrue(task._post_round_state.observed)
        task.add_success.assert_called_once()
        task.add_failed.assert_not_called()
        task._sell_collections_on_interval.assert_called_once()


class TestAuctionReturnToMatchScreenObservation(unittest.TestCase):
    """_stage_result 的「返回匹配界面」分支不走 _finish_auction, 但结算后观测必须照做。

    那条分支下「跳过动画」和「退出拍卖」按钮都已经不存在, 所以不能复用 _finish_auction;
    可如果因此连观测也省掉, _post_round_state.observed 会一直是 False, 轮次末尾就不出售
    —— 满仓时后续每轮都卡在「开始匹配」上, 而 _inventory_stuck 永远不会置位,
    「满仓时清理」在这条路径上完全失效。
    """

    def _task(self, title_found: bool) -> AutoBidAuctionTask:
        task = _make_task()
        task._is_match_screen = Mock(return_value=True)
        task._is_bid_screen = Mock(return_value=False)
        task.ocr = Mock(return_value=[])
        task.wait_ocr = Mock(return_value=[Mock()] if title_found else [])
        task._finish_auction = Mock()
        task._run_post_round_actions = Mock()
        return task

    def test_return_to_match_screen_runs_post_round_actions(self):
        task = self._task(title_found=True)

        finished = task._stage_result(Mock(), auction_module.time.monotonic() + 60)

        self.assertTrue(finished)
        task._finish_auction.assert_not_called()
        task._run_post_round_actions.assert_called_once()

    def test_missing_main_title_skips_post_round_actions(self):
        """标题没识别到说明画面状态未知, 不能去点不存在的仓库入口白等超时。"""
        task = self._task(title_found=False)

        task._stage_result(Mock(), auction_module.time.monotonic() + 60)

        task._run_post_round_actions.assert_not_called()

    def test_expired_deadline_does_not_raise(self):
        """deadline 用尽时按「没时间」跳过, 不能把已经结束的拍卖判成失败。"""
        task = self._task(title_found=True)

        task._observe_post_round_on_main_screen(Mock(), auction_module.time.monotonic() - 1.0)

        task._run_post_round_actions.assert_not_called()


class TestAuctionResultStageBudget(unittest.TestCase):
    """结算阶段的 RESULT_TIMEOUT 只约束「等待结算」, 不能当成收尾动作的预算。

    修复前 _stage_result 把 result_deadline(= min(整轮 deadline, now + RESULT_TIMEOUT))
    传给了 _finish_auction 与 _observe_post_round_on_main_screen。于是结算阶段空转掉
    80 秒后, 退出按钮只剩 10 秒不到可用, 满仓检测和低保金读取直接按「没时间」跳过;
    空转满 90 秒时收尾动作会抛「单轮拍卖超时」—— 而整轮其实还剩 500 多秒, 一个已经
    拍成的轮次被记成失败, 而且 _post_round_state 的写回被跳过, 轮次末尾也不出售。
    """

    def setUp(self):
        self.clock = _FakeTime()
        patcher = patch.object(auction_module, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task(self, spins: int, *, mode: str = "skip") -> AutoBidAuctionTask:
        task = _make_task({AutoBidAuctionTask.CONF_SELL_MODE: AutoBidAuctionTask.SELL_MODE_OFF})
        task.sleep = Mock(side_effect=self.clock.sleep)
        task.next_frame = Mock()
        task.wait_ocr = Mock(return_value=[Mock()])
        task.operate_click = Mock()
        task._is_bid_screen = Mock(return_value=False)
        task._is_skip_screen = Mock(return_value=False)
        task._is_match_screen = Mock(return_value=False)

        counter = {"n": 0}

        def tick(*args, **kw):
            counter["n"] += 1
            # mode="skip": 第 spins+1 轮才读到「跳过动画」
            # mode="match": 始终不命中「跳过动画」, 由 _is_match_screen 触发返回匹配界面分支
            return [Mock()] if (mode == "skip" and counter["n"] > spins) else []

        task.ocr = tick
        if mode == "match":
            task._is_match_screen = lambda boxes: counter["n"] > spins
        return task

    def test_finish_auction_receives_round_deadline(self):
        """结算空转 89 秒后, 收尾动作拿到的仍是整轮 deadline, 不是阶段 deadline。"""
        task = self._task(int(89.0 / AutoBidAuctionTask.POLL_INTERVAL))
        round_deadline = self.clock.now + AutoBidAuctionTask.ROUND_TIMEOUT
        task._finish_auction = Mock()

        task._stage_result(Mock(), round_deadline)

        task._finish_auction.assert_called_once()
        self.assertEqual(task._finish_auction.call_args[0][2], round_deadline)

    def test_return_to_match_branch_receives_round_deadline(self):
        """「返回匹配界面」分支同理, 否则结算后观测会被整段跳过。"""
        task = self._task(int(89.0 / AutoBidAuctionTask.POLL_INTERVAL), mode="match")
        round_deadline = self.clock.now + AutoBidAuctionTask.ROUND_TIMEOUT
        task._observe_post_round_on_main_screen = Mock()

        task._stage_result(Mock(), round_deadline)

        task._observe_post_round_on_main_screen.assert_called_once()
        self.assertEqual(
            task._observe_post_round_on_main_screen.call_args[0][1], round_deadline
        )

    def test_slow_settlement_still_finishes_the_round(self):
        """结算空转 89.5 秒后走完整个收尾不能抛异常(修复前抛「单轮拍卖超时」)。"""
        task = self._task(int(89.5 / AutoBidAuctionTask.POLL_INTERVAL))
        task._run_post_round_actions = Mock()
        round_deadline = self.clock.now + AutoBidAuctionTask.ROUND_TIMEOUT

        self.assertTrue(task._stage_result(Mock(), round_deadline))
        task._run_post_round_actions.assert_called_once()

    def test_slow_settlement_on_match_branch_keeps_observation(self):
        """「返回匹配界面」分支空转 89.5 秒后, 结算后观测仍要执行。"""
        task = self._task(int(89.5 / AutoBidAuctionTask.POLL_INTERVAL), mode="match")
        task._run_post_round_actions = Mock()
        round_deadline = self.clock.now + AutoBidAuctionTask.ROUND_TIMEOUT

        self.assertTrue(task._stage_result(Mock(), round_deadline))
        task._run_post_round_actions.assert_called_once()

    def test_settlement_wait_itself_is_still_bounded(self):
        """等待结算本身仍受 RESULT_TIMEOUT 约束, 修复不能把这条上限一起放开。

        循环体每轮耗时按 1.2 秒构造(真实环境里 next_frame + 三次 OCR 就是这样),
        否则 RESULT_MAX_LOOPS(180) x POLL_INTERVAL(0.5) 恰好也是 90 秒, 两个条件
        同时到点, 这条断言就分不出到底是时间上限还是次数上限在起作用。
        """
        task = self._task(0)
        task.ocr = Mock(return_value=[])
        task.sleep = Mock(side_effect=lambda seconds: self.clock.sleep(1.2))
        started = self.clock.now
        round_deadline = self.clock.now + AutoBidAuctionTask.ROUND_TIMEOUT

        with self.assertRaises(WaitFailedException):
            task._stage_result(Mock(), round_deadline)

        elapsed = self.clock.now - started
        self.assertLessEqual(elapsed, AutoBidAuctionTask.RESULT_TIMEOUT + 1.2)
        self.assertLess(elapsed, AutoBidAuctionTask.RESULT_MAX_LOOPS * 1.2)


class TestAuctionEstimatePartialReadLogging(unittest.TestCase):
    """「连续 N 次没有新信息」里可能一次完整读数都没有, 日志不能报「读数稳定」。

    画面静止时 OCR 会一直读不出或只读到被 reject_partial 过滤掉的残缺值, 此时 last
    只是唯一一次成功读数, 未必是终值。行为上仍采用它(比回退基础价更接近真实),
    但日志必须说清楚这是可疑读数, 否则排查时看不出这个价格是猜的。
    """

    def setUp(self):
        self.clock = _FakeTime()
        patcher = patch.object(auction_module, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task(self, values) -> AutoBidAuctionTask:
        task = _make_task()
        task.sleep = Mock(side_effect=self.clock.sleep)
        task._read_asset_value = Mock(side_effect=list(values) + [None] * 40)
        return task

    def test_single_valid_read_is_reported_as_suspicious(self):
        task = self._task([6000])

        value = task._read_stable_asset_value(Mock(), 10, "当前估价", skip_zero=True)

        self.assertEqual(value, 6000)
        infos = [str(c.args[0]) for c in task.log_info.call_args_list]
        warnings = [str(c.args[0]) for c in task.log_warning.call_args_list]
        self.assertFalse([msg for msg in infos if "稳定" in msg])
        self.assertTrue([msg for msg in warnings if "有效读数" in msg])

    def test_repeated_identical_reads_are_still_reported_as_stable(self):
        task = self._task([6000, 6000, 6000])

        value = task._read_stable_asset_value(Mock(), 10, "当前估价", skip_zero=True)

        self.assertEqual(value, 6000)
        self.assertTrue([c for c in task.log_info.call_args_list if "稳定" in str(c.args[0])])


class TestAuctionInstructions(unittest.TestCase):
    """任务卡上的「说明」按钮由 task.instructions 驱动。

    instructions 为空时框架会直接把按钮隐藏, 用户看不到任何说明; 内容过长又会让
    qfluentwidgets 的 Dialog 把唯一的「确定」按钮挤出屏幕(该 Dialog 没有滚动区也没有
    高度钳制)。所以这里同时守住「控件存在」「覆盖了面板的每个选项」「行数不超上限」。
    """

    # 遮罩尺寸 = 父窗口(src/config.py 的 window_size = 1200x800), 弹窗高于它就会居中
    # 溢出、上下一起被裁, 底部的「确定」按钮随之消失。实测每行约 16px + 185px 固定开销
    # (标题/边距/按钮行) -> 800px 父窗口的硬上限约 38 行, 留足余量后取 30。
    MAX_INSTRUCTION_LINES = 30

    def test_task_exposes_gui_instructions(self):
        task = _make_configured_task()

        self.assertTrue(task.instructions)

    def test_instructions_fit_the_dialog_height(self):
        """行数超上限时弹窗会高过父窗口, 内容下方的「确定」按钮被裁掉, 用户关不掉。"""
        lines = auction_module.INST.count("<br>") + 1

        self.assertLessEqual(lines, self.MAX_INSTRUCTION_LINES)

    def test_instructions_document_every_panel_option(self):
        text = auction_module.INST
        documented = [
            "循环次数",
            AutoBidAuctionTask.CONF_BID_MODE,
            AutoBidAuctionTask.BID_MODE_ESTIMATE,
            AutoBidAuctionTask.BID_MODE_CUSTOM,
            AutoBidAuctionTask.BID_MODE_LIST,
            AutoBidAuctionTask.CONF_ESTIMATE_RATIO,
            AutoBidAuctionTask.CONF_FIXED_PRICE,
            AutoBidAuctionTask.CONF_AUTO_RAISE,
            AutoBidAuctionTask.CONF_RAISE_MODE,
            AutoBidAuctionTask.CONF_RAISE_VALUE,
            AutoBidAuctionTask.CONF_RAISE_ROUND,
            AutoBidAuctionTask.CONF_SPECIAL_ROUND,
            AutoBidAuctionTask.CONF_SPECIAL_ROUND_PRICE,
            # 6 个出价价格在说明里以首尾两个标签概括, 不再逐条列出。
            AutoBidAuctionTask.CONF_BID_PRICES[0],
            AutoBidAuctionTask.CONF_BID_PRICES[-1],
            AutoBidAuctionTask.CONF_SELL_MODE,
            *AutoBidAuctionTask.SELL_MODES,
            AutoBidAuctionTask.CONF_SELL_INTERVAL,
            AutoBidAuctionTask.CONF_KEEP_QUALITIES,
            AutoBidAuctionTask.CONF_EXTRA_SELL_QUALITIES,
            AutoBidAuctionTask.CONF_ASSIST_FEATURES,
            *AutoBidAuctionTask.ASSIST_FEATURES,
        ]
        for label in documented:
            with self.subTest(label=label):
                self.assertIn(label, text)

    def test_instructions_warn_about_the_last_bid_round(self):
        """第 6 次必须高于第 5 次是唯一会让整轮出价被拒的硬约束, 必须在说明里点明。"""
        text = auction_module.INST

        self.assertIn(f"第 {AutoBidAuctionTask.MAX_BID_ROUNDS} 次", text)
        self.assertIn(f"第 {AutoBidAuctionTask.MAX_BID_ROUNDS - 1} 次", text)

    def test_instructions_warn_about_empty_keep_qualities(self):
        """「保留藏品品质」清空等于全卖, 说明里必须把这个后果写出来。"""
        text = auction_module.INST

        self.assertIn("一个都不勾", text)

    def test_instructions_have_an_upgrade_notes_section(self):
        """合并过配置键的版本必须交代旧键去向, 否则老用户升级后只会觉得功能丢了。"""
        text = auction_module.INST

        self.assertIn("升级后必看", text)
        for legacy in (
            AutoBidAuctionTask.LEGACY_CONF_AUTO_CLEAR,
            AutoBidAuctionTask.LEGACY_CONF_USE_EMOTE,
            AutoBidAuctionTask.LEGACY_CONF_USE_WELFARE,
            AutoBidAuctionTask.LEGACY_RAISE_MODE_MULTIPLE,
        ):
            with self.subTest(legacy=legacy):
                self.assertIn(legacy, text)

    def test_instructions_point_at_the_reset_config_button(self):
        """要让本机现存配置回到默认只能靠面板的「重置配置」, 必须写明入口和代价。"""
        text = auction_module.INST

        self.assertIn("重置配置", text)
        self.assertIn("清掉", text)


if __name__ == "__main__":
    unittest.main()
