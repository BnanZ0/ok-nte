import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.coffee import CoffeeFoodOption, CoffeeRuntime
from src.tasks.daily.CoffeeTask import CoffeeTask
from src.utils import clock


def _runtime_task(config=None):
    task = Mock()
    task.clock = clock
    task.config = {"coffee_key_settle_seconds": 0, "coffee_dry_run": True}
    task.config.update(config or {})
    task.click = Mock()
    task.click_ui = Mock()
    task.send_key = Mock()
    task.swipe = Mock()
    task.scroll = Mock()
    task.sleep = Mock()
    task.operate = Mock(side_effect=lambda func, block=False: func())
    task.ocr_ui = Mock(return_value=[])
    task.frame = None
    task.next_frame = Mock(return_value=None)
    task.width = 2560
    task.height = 1600
    task.ui_point = lambda x, y: (int(task.width * x), int(task.height * y))
    return task


class TestCoffeeRuntime(unittest.TestCase):
    def test_same_price_ocr_conflict_is_rejected_for_fill_candidates(self):
        runtime = CoffeeRuntime(_runtime_task())
        current = [CoffeeFoodOption("生巧雪醇拿铁", price_value=34661)]
        unstable = CoffeeFoodOption("明治", price_value=34661, target="unstable")

        candidates = runtime._fill_product_candidates([unstable], [], "", conflict_options=current)

        self.assertEqual(candidates, [])
        self.assertIn("product_fill_same_price_conflict:明治", runtime.actions)

    def test_identity_price_category_dedupe_is_preserved(self):
        runtime = CoffeeRuntime(_runtime_task())
        options = [
            CoffeeFoodOption("鲜鱼套餐", price_value=100, category="主食"),
            CoffeeFoodOption("鮮魚套餐", price_value=100, category="主食"),
            CoffeeFoodOption("鲜鱼套餐", price_value=120, category="主食"),
        ]

        deduped = runtime._dedupe_food_options(options)

        self.assertEqual(
            [(item.identity, item.price_value) for item in deduped],
            [("鲜鱼套餐", 100), ("鲜鱼套餐", 120)],
        )

    def test_replenish_supply_ignores_cumulative_business_time(self):
        # Regression for issue #185: 累计营业时间 is reset when income is claimed, so a
        # small cumulative time must NOT be read as "recently supplied". Even when the
        # shop shows 00:05:00 and no 补货 button, replenish_supply proceeds to the 补货
        # lookup (returning the safe no-op) instead of short-circuiting on business time.
        # A legacy skip threshold left in config stays inert.
        task = _runtime_task({"coffee_recent_supply_skip_seconds": 1800})
        task.ocr_ui = Mock(
            return_value=[
                SimpleNamespace(text="累计营业时间 00:05:00", x=0, y=0, width=10, height=10)
            ]
        )
        runtime = CoffeeRuntime(task)

        ok, skip_reason, real_purchase = runtime.replenish_supply()

        self.assertTrue(ok)
        self.assertFalse(real_purchase)
        self.assertEqual(skip_reason, "supply_not_needed_or_not_found")
        self.assertFalse(
            any(
                action.startswith("supply_recently_active_not_needed") for action in runtime.actions
            )
        )

    def test_runtime_falls_back_to_task_ocr_when_ocr_ui_absent(self):
        box = SimpleNamespace(text="一咖舍")
        task = SimpleNamespace(
            ocr=Mock(return_value=[box]),
            frame=None,
            width=2560,
            height=1600,
            config={"coffee_dry_run": True},
        )

        runtime = CoffeeRuntime(task)
        result = runtime._task_ocr(0.02, 0.05, 0.98, 0.95, frame=None)

        self.assertEqual(result, [box])
        task.ocr.assert_called_once()

    def test_click_ui_falls_back_to_screen_click_when_task_lacks_click_ui(self):
        task = SimpleNamespace(
            click=Mock(),
            frame=None,
            width=2560,
            height=1600,
            config={"coffee_dry_run": False},
            operate=lambda func, block=False: func(),
            ui_point=lambda x, y: (int(2560 * x), int(1600 * y)),
        )

        runtime = CoffeeRuntime(task)
        runtime._click_ui(0.5, 0.25, "test_action", move=True)

        task.click.assert_called_once_with(1280, 400, after_sleep=1, move=True, down_time=0.01)
        self.assertIn("test_action", runtime.actions)

    def test_tycoon_ascii_marker_is_detected(self):
        runtime = CoffeeRuntime(_runtime_task())

        self.assertTrue(runtime._is_tycoon_texts(["CITY TYCOON"]))

    def test_run_skips_claim_income_when_action_flag_disabled(self):
        runtime = CoffeeRuntime(_runtime_task({"coffee_action_collect_income": False}))
        runtime.open_coffee_shop = Mock(return_value=True)
        runtime.claim_income_if_present = Mock(
            side_effect=AssertionError("must not run when disabled")
        )
        runtime.is_income_report_popup = Mock(
            side_effect=AssertionError("must not check post-claim popup when disabled")
        )
        runtime.optimize_products = Mock()
        runtime.replenish_supply = Mock(return_value=(True, "", False))

        ok, _ = runtime.run()

        self.assertTrue(ok)
        self.assertFalse(runtime.income_claimed)
        runtime.claim_income_if_present.assert_not_called()
        runtime.is_income_report_popup.assert_not_called()
        self.assertIn("collect_income_skipped_disabled", runtime.actions)

    def test_run_skips_optimize_products_when_action_flag_disabled(self):
        runtime = CoffeeRuntime(_runtime_task({"coffee_action_optimize_products": False}))
        runtime.open_coffee_shop = Mock(return_value=True)
        runtime.claim_income_if_present = Mock(return_value=False)
        runtime.is_income_report_popup = Mock(return_value=False)
        runtime.optimize_products = Mock(side_effect=AssertionError("must not run when disabled"))
        runtime.replenish_supply = Mock(return_value=(True, "", False))

        ok, _ = runtime.run()

        self.assertTrue(ok)
        runtime.optimize_products.assert_not_called()
        self.assertIn("optimize_products_skipped_disabled", runtime.actions)

    def test_run_skips_replenish_supply_when_action_flag_disabled(self):
        runtime = CoffeeRuntime(_runtime_task({"coffee_action_replenish_supply": False}))
        runtime.open_coffee_shop = Mock(return_value=True)
        runtime.claim_income_if_present = Mock(return_value=False)
        runtime.is_income_report_popup = Mock(return_value=False)
        runtime.optimize_products = Mock()
        runtime.replenish_supply = Mock(side_effect=AssertionError("must not run when disabled"))

        ok, skip_reason = runtime.run()

        self.assertTrue(ok)
        self.assertEqual(skip_reason, "")
        self.assertFalse(runtime.real_purchase_performed)
        runtime.replenish_supply.assert_not_called()
        self.assertIn("replenish_supply_skipped_disabled", runtime.actions)

    def test_click_product_candidates_forwards_allow_price_fallback(self):
        runtime = CoffeeRuntime(_runtime_task())
        captured = []

        def fake_match(options, candidate, allow_price_fallback=True):
            captured.append(allow_price_fallback)
            return None

        runtime.collect_product_options = Mock(return_value=[])
        runtime._dedupe_food_options = lambda options: list(options)
        runtime._product_scrolls = lambda: 0
        runtime._matching_visible_product_option = fake_match

        runtime._click_product_candidates_single_pass(
            [CoffeeFoodOption("食物-1", price_value=100)],
            1,
            "select_product",
            allow_price_fallback=True,
        )

        self.assertEqual(captured, [True])

    def test_claim_coffee_challenge_reward_does_not_mark_pending_supply_completed(self):
        runtime = CoffeeRuntime(_runtime_task())
        runtime.is_coffee_challenge_result = Mock(return_value=True)
        claim_target = SimpleNamespace(text="领取", x=0, y=0, width=10, height=10)
        runtime.find_button_text_box = Mock(return_value=claim_target)
        runtime.wait_for = Mock(return_value=True)

        result = runtime.claim_or_exit_coffee_challenge_result_if_present()

        self.assertTrue(result)
        self.assertFalse(runtime.pending_supply_completed)
        self.assertIn("claim_coffee_challenge_reward", runtime.actions)

    def test_replenish_supply_runs_after_challenge_claim(self):
        runtime = CoffeeRuntime(_runtime_task())
        runtime.is_coffee_challenge_result = Mock(return_value=True)
        claim_target = SimpleNamespace(text="领取", x=0, y=0, width=10, height=10)
        runtime.find_button_text_box = Mock(return_value=claim_target)
        runtime.wait_for = Mock(return_value=True)
        runtime.claim_or_exit_coffee_challenge_result_if_present()

        runtime.find_text_box = Mock(return_value=None)

        ok, skip_reason, real_purchase = runtime.replenish_supply()

        self.assertTrue(ok)
        self.assertFalse(real_purchase)
        self.assertEqual(skip_reason, "supply_not_needed_or_not_found")
        self.assertNotIn("supply_already_completed_from_pending_confirm", runtime.actions)

    def test_complete_pending_supply_delivery_still_marks_pending_supply_completed(self):
        runtime = CoffeeRuntime(_runtime_task())
        runtime.pending_supply_delivery_present = Mock(return_value=True)
        runtime.finish_home_delivery_flow = Mock(return_value="")
        runtime.close_popup = Mock()
        runtime.wait_for_coffee_shop_panel = Mock(return_value=True)

        result = runtime.complete_pending_supply_delivery_if_present()

        self.assertTrue(result)
        self.assertTrue(runtime.pending_supply_completed)
        self.assertEqual(runtime.pending_supply_error, "")

    def test_price_re_matches_supported_ocr_formats(self):
        cases_match = [
            ("12/h", "12"),
            ("12.34/h", "12.34"),
            ("12 /h", "12"),
            ("12 / h", "12"),
            ("12.5 / h", "12.5"),
            ("12.34/H", "12.34"),
            ("商品12.5/h主食", "12.5"),
            ("0/h", "0"),
            ("999.999 /h", "999.999"),
            (".5/h", "5"),
            ("1234567890.1234567890/h", "1234567890.1234567890"),
        ]
        for text, expected in cases_match:
            with self.subTest(text=text):
                match = CoffeeRuntime.PRICE_RE.search(text)
                self.assertIsNotNone(match, f"expected match for {text!r}")
                self.assertEqual(match.group(1), expected)

    def test_price_re_rejects_unsupported_inputs(self):
        cases_no_match = [
            "no price here",
            "12.x/h",
            "12./h",
            "12//h",
            "12 / / h",
            "12.34a/h",
            "１２/h",
        ]
        for text in cases_no_match:
            with self.subTest(text=text):
                self.assertIsNone(
                    CoffeeRuntime.PRICE_RE.search(text),
                    f"expected no match for {text!r}",
                )


class TestCoffeeTaskConfig(unittest.TestCase):
    def _task(self, config=None):
        task = object.__new__(CoffeeTask)
        task.config = config or {}
        return task

    def test_actions_requested_defaults_to_income_restock_buy(self):
        task = self._task(
            {
                CoffeeTask.CONF_COLLECT_INCOME: True,
                CoffeeTask.CONF_RESTOCK_GOODS: True,
                CoffeeTask.CONF_BUY_GOODS: True,
                CoffeeTask.CONF_OPTIMIZE_PRODUCTS: False,
            }
        )

        self.assertEqual(
            CoffeeTask._actions_requested(task),
            [
                CoffeeTask.CONF_COLLECT_INCOME,
                CoffeeTask.CONF_RESTOCK_GOODS,
                CoffeeTask.CONF_BUY_GOODS,
            ],
        )

    def test_supply_requested_requires_both_restock_and_buy(self):
        task = self._task({CoffeeTask.CONF_RESTOCK_GOODS: True, CoffeeTask.CONF_BUY_GOODS: False})
        self.assertFalse(CoffeeTask._supply_requested(task))

        task = self._task({CoffeeTask.CONF_RESTOCK_GOODS: True, CoffeeTask.CONF_BUY_GOODS: True})
        self.assertTrue(CoffeeTask._supply_requested(task))

    def test_apply_runtime_config_maps_keys(self):
        task = self._task(
            {
                CoffeeTask.CONF_PRODUCT_SLOTS: "3",
                CoffeeTask.CONF_RESTOCK_DURATION: "8h",
                CoffeeTask.CONF_PRICE_TABLE: "disabled",
                CoffeeTask.CONF_COLLECT_INCOME: True,
                CoffeeTask.CONF_OPTIMIZE_PRODUCTS: True,
                CoffeeTask.CONF_RESTOCK_GOODS: True,
                CoffeeTask.CONF_BUY_GOODS: True,
            }
        )

        CoffeeTask._apply_runtime_config(task)

        self.assertEqual(task.config["coffee_product_target_slots"], 3)
        self.assertEqual(task.config["coffee_max_supply_slots"], 3)
        self.assertEqual(task.config["coffee_supply_duration"], "8h")
        self.assertEqual(task.config["coffee_price_table"], "disabled")
        self.assertTrue(task.config["coffee_allow_pending_supply_completion"])
        self.assertTrue(task.config["coffee_action_collect_income"])
        self.assertTrue(task.config["coffee_action_optimize_products"])
        self.assertTrue(task.config["coffee_action_replenish_supply"])

    def test_apply_runtime_config_auto_translates_to_24h(self):
        task = self._task(
            {
                CoffeeTask.CONF_PRODUCT_SLOTS: "auto",
                CoffeeTask.CONF_RESTOCK_DURATION: "auto",
                CoffeeTask.CONF_PRICE_TABLE: "auto",
            }
        )

        CoffeeTask._apply_runtime_config(task)

        self.assertEqual(task.config["coffee_product_target_slots"], 0)
        self.assertEqual(task.config["coffee_supply_duration"], "24小时")
        self.assertFalse(task.config["coffee_allow_pending_supply_completion"])
        self.assertFalse(task.config["coffee_action_collect_income"])
        self.assertFalse(task.config["coffee_action_optimize_products"])
        self.assertFalse(task.config["coffee_action_replenish_supply"])

    def test_apply_runtime_config_writes_supply_flag_only_when_both_restock_and_buy(self):
        task = self._task(
            {
                CoffeeTask.CONF_RESTOCK_GOODS: True,
                CoffeeTask.CONF_BUY_GOODS: False,
            }
        )

        CoffeeTask._apply_runtime_config(task)

        self.assertFalse(task.config["coffee_action_replenish_supply"])

    def test_do_run_skips_when_no_actions_enabled(self):
        task = self._task(
            {
                CoffeeTask.CONF_COLLECT_INCOME: False,
                CoffeeTask.CONF_RESTOCK_GOODS: False,
                CoffeeTask.CONF_BUY_GOODS: False,
                CoffeeTask.CONF_OPTIMIZE_PRODUCTS: False,
            }
        )
        messages = []
        task.log_info = lambda message, *args, **kwargs: messages.append(("info", message))
        task.log_error = lambda message, *args, **kwargs: messages.append(("error", message))

        self.assertTrue(CoffeeTask.do_run(task))
        self.assertIn(("info", "一咖舍未启用任何动作"), messages)


if __name__ == "__main__":
    unittest.main()
