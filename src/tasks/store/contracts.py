from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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

    新增商店只需实现子类并注册到 StoreTask.SHOPS, 不需要改动主流程.
    """

    shop_id: str = ""
    display_name: str = ""
    enabled_by_default: bool = False

    def __init__(self, task: BaseNTETask):
        self.task = task

    def __getattr__(self, name: str) -> Any:
        if name == "task":  # 避免 self.task 未赋值时无限递归
            raise AttributeError(name)
        return getattr(self.task, name)

    def open(self) -> bool:
        """从 F4 面板进入该商店界面, 成功返回 True."""
        raise NotImplementedError

    def purchase(self) -> StorePurchaseResult | None:
        """购买商品, 返回 None 表示本次没有可购买的商品."""
        raise NotImplementedError
