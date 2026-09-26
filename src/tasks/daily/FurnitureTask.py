from ok import TaskDisabledException

from src.combat.BaseCombatTask import BaseCombatTask
from src.Labels import Labels
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class FurnitureTask(NTEOneTimeTask, BaseCombatTask):
    CONF_MAMMON = "挑战玛门"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "异象家具"
        self.group_name = "日常/周常"
        self.visible = False
        self.default_config.update({self.CONF_MAMMON: True})

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("FurnitureTask error", e)
            raise

    def do_run(self) -> bool:
        return self.claim_anomaly_furniture()

    def claim_anomaly_furniture(self):
        """领取异象家具奖励"""

        self.log_info("正在领取异象家具奖励")

        furniture_list = [
            Labels.anomaly_fluff,
        ]

        if self.config.get(self.CONF_MAMMON):
            furniture_list.append(Labels.anomaly_mammon)

        furniture_results = {}
        for furniture in furniture_list:
            try:
                claimed = self.claim_furniture(furniture)
            except TaskDisabledException:
                raise
            except Exception as e:
                self.log_error(f"领取异象家具失败: {furniture}", e)
                claimed = False

            furniture_results[furniture] = claimed
            result = "成功" if claimed else "失败"
            self.log_info(f"异象家具 {furniture} 领取{result}")

        all_claimed = all(furniture_results.values())
        if all_claimed:
            self.log_info("异象家具奖励全部领取成功")
        else:
            self.log_error("异象家具奖励未能全部领取成功")
        return all_claimed

    def open_house_panel(self):
        def action():
            self.openF5panel()
            self.operate_click(*self.pos.panels.f5.house)
            self.sleep(0.5)
            return self.wait_panel(Labels.f5_house_panel)

        if self.find_one(Labels.f5_house_panel):
            return True
        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到房产面板")
            return False
        self.sleep(1)
        return True

    def check_house_lock(self, ratio_y):
        box = self.box_of_screen(0.050, ratio_y - 0.1, width=0.054, height=0.079, hcenter=True)
        return self.find_one(Labels.f5_house_lock, box=box)

    def check_house_card(self, ratio_y, snapshot):
        box = self.get_box_by_name(Labels.box_house_list_snapshot)
        box.y = int(ratio_y * self.height) - box.height
        search_box = box.copy(y_offset=-box.height, height_offset=box.height)

        click = True
        if snapshot is None:
            click = False
            snapshot = box.crop_frame(self.frame)
        else:
            if self.find_one(
                "house_card_snapshot", template=snapshot, box=search_box, threshold=0.9
            ):
                click = False
            else:
                snapshot = box.crop_frame(self.frame)
        return click, snapshot

    def teleport_to_furniture(self, furniture):
        house_box = self.box_of_screen(0.507, 0.476, 0.956, 0.795, hcenter=True)

        shown = 4
        ratio_x = 0.079
        ratio_y = 0.308
        gap = 0.183
        scroll_per_item = 6

        click_card_snapshot = None
        scroll = True
        scroll_times = 0
        i = 0
        if not self.open_house_panel():
            return False

        # 寻找目标家具
        while scroll or i < shown:
            self.next_frame()
            if scroll:
                target_y = ratio_y
            else:
                target_y = ratio_y + gap * i
                i += 1

            # 检查房子是否解锁
            if self.check_house_lock(target_y):
                self.sleep(0.25)
            else:
                click_card, candidate_snapshot = self.check_house_card(
                    target_y, click_card_snapshot
                )
                if click_card_snapshot is None:
                    click_card_snapshot = candidate_snapshot
                    check_furniture = True
                else:
                    check_furniture = not click_card

                if click_card:
                    box = self.get_box_by_name(Labels.box_house_preview_snapshot)
                    preview_snapshot = box.crop_frame(self.frame)

                    self.operate_click(ratio_x, target_y)
                    check_furniture = bool(
                        self.wait_until(
                            lambda: (
                                not self.find_one(
                                    "preview_snapshot", template=preview_snapshot, box=box
                                )
                            ),
                            time_out=2.5,
                            raise_if_not_found=False,
                        )
                    )
                    click_card_snapshot = candidate_snapshot

                if check_furniture and self.find_sift_feature(furniture, box=house_box):
                    break

            # 滚动并检查是否成功滚动
            if scroll:
                scroll_times += 1
                box = self.get_box_by_name(Labels.box_house_list_snapshot)
                search_box = box.copy(y_offset=-box.height, height_offset=box.height)
                scroll = not self.scroll_and_is_end(
                    ratio_x,
                    ratio_y,
                    -scroll_per_item,
                    snap_box=box,
                    check_box=search_box,
                    threshold=0.9,
                )
        else:
            self.log_info(f"not found furniture {furniture}")
            self.operate(
                lambda: (
                    self.scroll(ratio_x, ratio_y, scroll_per_item * (scroll_times + 2)),
                    self.sleep(0.25),
                ),
                block=True,
            )
            return False

        # 传送至目标房子
        self.wait_until(
            lambda: not self.find_one(Labels.f5_house_panel),
            pre_action=lambda: self.operate_click(0.891, 0.951, after_sleep=1),
        )
        self.click_traval_button()
        return self.wait_in_team(time_out=300, settle_time=1)

    def claim_furniture(self, furniture):
        if not self.teleport_to_furniture(furniture):
            return False

        # 打开异象家具
        def action_1():
            try:
                self.send_key_down("lalt")
                self.sleep(0.25)
                self.operate_click(0.465, 0.056)
            finally:
                self.send_key_up("lalt")
            self.sleep(2)
            if not self.is_in_team():
                return True

        self.retry_on_action(action_1, attempt=10, raise_if_failed=True)

        # 切换领取页面
        confirm_box = self.box_of_screen(0.913, 0.887, 0.967, 0.976)

        def action_2():
            self.operate_click(0.924, 0.174)
            self.sleep(1)
            if confirm := self.find_confirm(box=confirm_box):
                return confirm

        confirm = self.retry_on_action(action_2, attempt=10, raise_if_failed=True)

        if furniture == Labels.anomaly_fluff:
            ret = self.operate_click(confirm, after_sleep=0.5)
        else:
            ret = self.click_furniture(furniture)

        self.ensure_main()
        return ret

    def click_furniture(self, furniture):
        box_left = self.box_of_screen(0.024, 0.181, 0.278, 0.775, hcenter=True)
        self.wait_until(
            lambda: self.find_sift_feature(furniture, box=box_left), raise_if_not_found=True
        )
        self.sleep(0.5)
        box_right = self.box_of_screen(0.738, 0.236, 0.805, 0.959, hcenter=True)

        # 点击异象家具
        def action():
            box = self.find_sift_feature(furniture, box=box_left)
            if not box:
                return False

            self.operate_click(box, after_sleep=1)

            if not self.find_sift_feature(furniture, box=box_right):
                return

            self.operate_click(0.978, 0.848, after_sleep=0.5)
            self.operate_click(box, after_sleep=1)

            if self.find_sift_feature(furniture, box=box_right):
                return True

            self.sleep(0.5)

        self.retry_on_action(action, attempt=10, raise_if_failed=True)

        # 二次确认异象家具
        self.wait_until(
            lambda: self.find_sift_feature(furniture, box=box_right), raise_if_not_found=True
        )

        # 领取目标家具
        self.sleep(0.5)
        self.operate(
            lambda: (
                self.click(0.938, 0.283, move=True),
                self.sleep(0.1),
                self.click(0.938, 0.303, move=True),
            ),
            block=True,
        )
        self.sleep(0.5)
        return self.after_claim_action(furniture)

    def after_claim_action(self, furniture):
        match furniture:
            case Labels.anomaly_mammon:
                return self.claim_mammon()
            case _:
                return True

    def claim_mammon(self):
        exc_msg = "mammon has record"

        def check_record():
            mammon_record = self.ocr(0.634, 0.609, 0.762, 0.674, name="mammon_record")
            if self.parse_ocr_number(mammon_record) > 0:
                raise Exception(exc_msg)

        def action():
            self.walk_to_treasure()
            self.send_interac(handle_claim=False)
            if self.wait_click_confirm(
                range=(0.4168, 0.8153, 0.4609, 0.9049), raise_if_not_found=False, time_out=1.5
            ):
                return True

        run_mammon = False
        ret = False
        try:
            run_mammon = self.wait_click_confirm(
                range=(0.628, 0.712, 0.682, 0.815),
                time_out=10,
                raise_if_not_found=False,
                on_found=check_record,
            )
        except Exception as e:
            if str(e) == exc_msg:
                ret = True
            else:
                raise

        if run_mammon:
            if self.walk_until_combat(run=True):
                self.combat_once()
                self.rotate_and_find_treasure()
                if self.retry_on_action(action):
                    return True
            self.exit_anomaly()
        self.ensure_main()
        return ret
