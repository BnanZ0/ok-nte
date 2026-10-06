from ok import TaskDisabledException

from src.coffee import ALLOWED_DURATIONS, CoffeeRuntime
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class CoffeeTask(NTEOneTimeTask, BaseNTETask):
    """一咖舍自动化任务.

    覆盖领取收益、商品优化、补货购买. 默认开启领取收益和补货购买,
    商品优化默认关闭以避免未明确选择就替换商品.
    """

    DEFAULT_MOVE = True

    CONF_MODE = "模式"
    MODE_CLAIM_AND_RESTOCK = "领取/补货"
    MODE_AUTO = "自动化"

    CONF_COLLECT_INCOME = "领取收益"
    CONF_RESTOCK_GOODS = "补货货物"
    CONF_BUY_GOODS = "购买货物送货上门"
    CONF_OPTIMIZE_PRODUCTS = "优化商品"
    CONF_RESTOCK_DURATION = "补货时长"
    CONF_PRODUCT_SLOTS = "商品位数量"
    CONF_PRICE_TABLE = "价格表"

    AUTO = "auto"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "一咖舍"
        self.visible = False
        # 一咖舍页面的所有 OCR 判定 (商品名、价格表、营收弹窗、补货时长选项等)
        # 仅在简体中文 UI 下匹配, 因此只对 zh_CN 暴露此任务.
        self.default_config.update(
            {
                self.CONF_MODE: self.MODE_CLAIM_AND_RESTOCK,
                self.CONF_COLLECT_INCOME: True,
                self.CONF_RESTOCK_GOODS: True,
                self.CONF_BUY_GOODS: True,
                self.CONF_OPTIMIZE_PRODUCTS: False,
                self.CONF_RESTOCK_DURATION: self.AUTO,
                self.CONF_PRODUCT_SLOTS: self.AUTO,
                self.CONF_PRICE_TABLE: self.AUTO,
            }
        )
        self.config_description.update(
            {
                self.CONF_COLLECT_INCOME: "领取一咖舍累计收益",
                self.CONF_RESTOCK_GOODS: "在原料库存界面进行补货",
                self.CONF_BUY_GOODS: "补货时允许购买货物并送货上门",
                self.CONF_OPTIMIZE_PRODUCTS: "根据价格和趋势尝试优化一咖舍商品",
                self.CONF_RESTOCK_DURATION: "补货时长(auto 表示 24 小时优先, 失败时回退到更短时长)",
                self.CONF_PRODUCT_SLOTS: "商品位数量(auto 由当前已解锁数量决定)",
                self.CONF_PRICE_TABLE: "价格表识别(disabled 跳过商品优化以避免未识别价格的替换)",
            }
        )
        options = [self.MODE_CLAIM_AND_RESTOCK]
        # if self.is_chinese():
        #     options.append(self.MODE_AUTO)
        self.config_type.update(
            {
                self.CONF_MODE: {
                    "type": "drop_down",
                    "options": options,
                    "sub_configs": {
                        self.MODE_AUTO: [
                            self.CONF_COLLECT_INCOME,
                            self.CONF_RESTOCK_GOODS,
                            self.CONF_BUY_GOODS,
                            self.CONF_OPTIMIZE_PRODUCTS,
                            self.CONF_RESTOCK_DURATION,
                            self.CONF_PRODUCT_SLOTS,
                            self.CONF_PRICE_TABLE,
                        ],
                    },
                },
                self.CONF_RESTOCK_DURATION: {
                    "type": "drop_down",
                    "options": [self.AUTO, *ALLOWED_DURATIONS],
                },
                self.CONF_PRODUCT_SLOTS: {
                    "type": "drop_down",
                    "options": [self.AUTO, "1", "2", "3", "4", "5"],
                },
                self.CONF_PRICE_TABLE: {
                    "type": "drop_down",
                    "options": [self.AUTO, "disabled"],
                },
            }
        )

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("CoffeeTask error", e)
            raise

    def do_run(self) -> bool:
        if self.config.get(self.CONF_MODE) == self.MODE_CLAIM_AND_RESTOCK:
            return self.claim_coffee()

        self.log_info("正在执行一咖舍自动化")
        self._apply_runtime_config()

        actions_requested = self._actions_requested()
        if not actions_requested:
            self.log_info("一咖舍未启用任何动作")
            return True

        runtime = CoffeeRuntime(self)
        ok, skip_reason = runtime.run()
        if not ok:
            self.log_error(f"一咖舍执行失败: {skip_reason or 'unknown'}")
            return False

        if runtime.income_claimed or runtime.real_purchase_performed or runtime.selected_options:
            self.log_info("一咖舍执行完成")
            return True
        self.log_info(f"一咖舍无可执行动作: {skip_reason or 'noop'}")
        return True

    def _apply_runtime_config(self):
        """把 task 配置项映射到 runtime 读取的 ``coffee_*`` 键.

        runtime 通过 ``self._config_get`` 读取 task.config 上的键, 使用统一前缀
        减少与 task 级别配置项命名冲突.
        """
        slots = (
            str(self.config.get(self.CONF_PRODUCT_SLOTS, self.AUTO) or self.AUTO).strip().lower()
        )
        try:
            slots_value = 0 if slots == self.AUTO else max(1, min(5, int(slots)))
        except (TypeError, ValueError):
            slots_value = 0
        duration = str(self.config.get(self.CONF_RESTOCK_DURATION, self.AUTO) or self.AUTO).strip()
        if duration.lower() == self.AUTO:
            duration = "24小时"
        self.config.update(
            {
                "coffee_product_target_slots": slots_value,
                "coffee_max_supply_slots": slots_value,
                "coffee_supply_duration": duration,
                "coffee_price_table": str(
                    self.config.get(self.CONF_PRICE_TABLE, self.AUTO) or self.AUTO
                ),
                "coffee_allow_pending_supply_completion": self._supply_requested(),
                "coffee_action_collect_income": bool(
                    self.config.get(self.CONF_COLLECT_INCOME, False)
                ),
                "coffee_action_optimize_products": bool(
                    self.config.get(self.CONF_OPTIMIZE_PRODUCTS, False)
                ),
                "coffee_action_replenish_supply": self._supply_requested(),
            }
        )

    def _actions_requested(self):
        return [
            key
            for key in (
                self.CONF_COLLECT_INCOME,
                self.CONF_RESTOCK_GOODS,
                self.CONF_BUY_GOODS,
                self.CONF_OPTIMIZE_PRODUCTS,
            )
            if bool(self.config.get(key, False))
        ]

    def _supply_requested(self):
        return bool(
            self.config.get(self.CONF_RESTOCK_GOODS, False)
            and self.config.get(self.CONF_BUY_GOODS, False)
        )

    def claim_coffee(self):
        """领取一咖舍奖励"""

        def action():
            self.openF5panel()
            self.operate_click(*self.pos.panels.f5.coffee)
            self.sleep(0.5)
            return self.wait_panel(Labels.f5_coffee_panel)

        self.log_info("正在领取一咖舍奖励")
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到一咖舍面板")
            return False
        self.sleep(1)

        # 提取收益
        self.wait_until(
            lambda: not self.find_one(Labels.f5_coffee_panel),
            pre_action=lambda: self.operate_click(0.188, 0.877, interval=1),
            time_out=10,
        )
        self.sleep(1)
        self.wait_until(
            lambda: self.find_one(Labels.f5_coffee_panel),
            pre_action=lambda: self.operate_click(0.072, 0.886, interval=1),
            time_out=10,
            settle_time=0.5,
        )
        self.sleep(1)

        # 进入补货
        self.wait_until(
            lambda: not self.find_one(Labels.f5_coffee_panel),
            pre_action=lambda: self.operate_click(0.115, 0.530, interval=1),
            time_out=10,
            settle_time=0.5,
        )
        self.sleep(1)

        # 补货
        self.operate_click(0.340, 0.785)  # 24hr
        self.sleep(1)
        self.operate_click(0.717, 0.787)  # 补货
        self.sleep(1)
        self.operate_click(0.595, 0.776)  # 送货上门
        self.sleep(1)
        self.operate_click(0.600, 0.656)  # 确认
        return True
