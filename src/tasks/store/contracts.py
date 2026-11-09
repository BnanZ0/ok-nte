from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.Labels import Labels

if TYPE_CHECKING:
    from src.tasks.BaseNTETask import BaseNTETask

    _StoreFlowTaskProxy = BaseNTETask
else:

    class _StoreFlowTaskProxy:
        pass


@dataclass
class StorePurchaseResult:
    """一次商店购买的结果汇总. 已售罄与购买失败分开记录, 便于上层判断是否算成功."""

    shop_id: str
    bought: list[str] = field(default_factory=list)
    sold_out: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


class StoreFlow(_StoreFlowTaskProxy):
    """单个商店的进入与购买流程, 任务能力经 __getattr__ 透传.

    子类只需实现 open(), 并声明 ITEM_LABELS 与 ITEM_LIST_BOX;
    商品详情页到获得物品是游戏通用界面, 由基类统一处理.
    """

    shop_id: str = ""
    display_name: str = ""
    enabled_by_default: bool = False

    ITEM_LABELS: tuple[str, ...] = ()
    ITEM_LIST_BOX: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

    PANEL_TIMEOUT = 5
    BUY_TIMEOUT = 5
    CONFIRM_TIMEOUT = 1
    REWARD_TIMEOUT = 5

    # 购买弹窗为通用界面, 各商店按钮位置一致
    BUY_BUTTON_POS = (0.660, 0.740)
    # 只覆盖确认按钮, 避开左侧取消按钮
    CONFIRM_BOX = (0.649, 0.608, 0.707, 0.711)
    # 通用「获得物品」弹窗
    REWARD_BOX = (0.453, 0.248, 0.548, 0.424)

    def __init__(self, task: BaseNTETask):
        self.task = task

    def __getattr__(self, name: str) -> Any:
        if name == "task":  # 避免 self.task 未赋值时无限递归
            raise AttributeError(name)
        return getattr(self.task, name)

    def open(self) -> bool:
        """进入该商店界面, 成功返回 True."""
        raise NotImplementedError

    def purchase(self) -> StorePurchaseResult | None:
        """购买 ITEM_LABELS 中列出的商品, 返回 None 表示本次没有可购买的商品."""
        if not self.ITEM_LABELS:
            self.log_warning("未配置待购商品模板, 跳过购买")
            return None

        box = self.box_of_screen(*self.ITEM_LIST_BOX, name=f"{self.shop_id}_items")
        result = StorePurchaseResult(self.shop_id)
        for label in self.ITEM_LABELS:
            item = self.find_one(label, box=box)
            if item is None:
                self.log_info(f"商品不在售卖区, 按售罄处理: {label}")
                result.sold_out.append(label)
                continue
            if self.buy_item(item):
                result.bought.append(label)
            else:
                result.failed.append(label)
        return result

    def buy_item(self, item) -> bool:
        """选中商品并拉满数量购买, 以「获得物品」弹窗判成功."""
        self.operate_click(item, after_sleep=1)
        max_button = self.wait_panel(Labels.buy_max, time_out=self.BUY_TIMEOUT)
        if not max_button:
            self.log_error("无法打开商品详情页")
            return False
        self.operate_click(max_button, after_sleep=0.5)

        self.operate_click(*self.BUY_BUTTON_POS, after_sleep=1)
        if self.wait_click_confirm(
            range=self.CONFIRM_BOX,
            time_out=self.CONFIRM_TIMEOUT,
            raise_if_not_found=False,
        ):
            self.log_info("已确认购买弹窗")

        reward_box = self.box_of_screen(*self.REWARD_BOX, name="reward_popup")
        if not self.wait_until(
            lambda: self.find_one(Labels.reward_popup, box=reward_box),
            time_out=self.REWARD_TIMEOUT,
            raise_if_not_found=False,
        ):
            self.log_error("购买后未出现获得物品弹窗")
            return False
        # 关掉弹窗留在商店页, 便于继续买其它商品
        self.send_key("esc", after_sleep=1)
        self.log_info("购买成功")
        return True
