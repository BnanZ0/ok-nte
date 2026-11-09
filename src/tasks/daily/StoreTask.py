from ok import TaskDisabledException

from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.daily.store.contracts import StoreFlow, StorePurchaseResult
from src.tasks.daily.store.nine_nights import NineNightsStoreFlow
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class StoreTask(NTEOneTimeTask, BaseNTETask):
    """商店购买"""

    SHOPS: tuple[type[StoreFlow], ...] = (NineNightsStoreFlow,)
    CONF_SHOP_DESCRIPTION = "纽扣商店购买异构单元"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "商店购买"
        self.visible = False
        for shop in self.SHOPS:
            self.default_config[shop.display_name] = shop.enabled_by_default
            self.config_description[shop.display_name] = self.CONF_SHOP_DESCRIPTION

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("StoreTask error", e, notify=True)
            raise

    def do_run(self) -> bool | None:
        shops = self.selected_shops()
        if not shops:
            self.log_info("未选择商店, 跳过商店购买")
            return None

        results = {shop.display_name: self._run_shop(shop) for shop in shops}
        self.ensure_main()
        failed = [name for name, result in results.items() if result is False]
        if failed:
            self.log_error(f"商店购买失败: {failed}", notify=True)
            return False
        if all(result is None for result in results.values()):
            return None
        self.log_info("商店购买完成")
        return True

    def selected_shops(self):
        return [shop(self) for shop in self.SHOPS if self.config.get(shop.display_name, False)]

    def _run_shop(self, shop) -> bool | None:
        self.log_info(f"正在前往商店: {shop.display_name}")
        if not shop.open():
            self.log_error(f"无法进入商店: {shop.display_name}", notify=True)
            return False
        result = shop.purchase()
        if result is None:
            return None
        self._log_result(result)
        return result.ok

    def _log_result(self, result: StorePurchaseResult):
        self.log_info(f"已购买: {result.bought}")
        if result.sold_out:
            self.log_info(f"未出现在售卖区(视为售罄): {result.sold_out}")
        if result.failed:
            self.log_error(f"购买失败: {result.failed}")
