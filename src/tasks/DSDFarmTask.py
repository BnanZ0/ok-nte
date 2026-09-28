import re
import time

from ok import TaskDisabledException

from src.combat.BaseCombatTask import BaseCombatTask
from src.Labels import Labels
from src.tasks.BaseNTETask import Box
from src.tasks.dsd_refresh import RefreshIcon, classify_refresh_icon
from src.tasks.NTEOneTimeTask import NTEOneTimeTask

SPACE = "&nbsp;" * 4 + "-"

# ruff: noqa: E501
INST = (
    "手动传送一次目标篝火后不要转动视角，直接开始任务。\n\n"
    "巧克力火山-底层最左边的篝火\n"
    f"{SPACE}火山有两层!!! 目标是*底层*整个地图最靠左的篝火\n"
    f"{SPACE}这个篝火只有二周目以后才能到达, 推荐在这里刷到100级\n"
    f"{SPACE}跟跑视频: https://b23.tv/qsEVcDO\n\n"
    "赤龙古堡-龙之高塔室外篝火\n"
    f"{SPACE}龙之高塔只有两个篝火，室外旁边有棵树的篝火\n"
    f"{SPACE}推荐三周目才来这里, 主要目的是刷纽扣\n\n"
    "赤龙古堡-残丝长巷篝火\n"
    f"{SPACE}残丝长巷附近只有三个篝火，唯一在室内的篝火\n"
    f"{SPACE}推荐三周目才来这里, 主要目的是刷纽扣"
)

EN_INST = (
    "After manually teleporting to the target checkpoint, do not rotate the camera. Start the task immediately.\n\n"
    "Chocolate Volcano - Leftmost Checkpoint on the Bottom Layer\n"
    f"{SPACE}The volcano has two layers!!! The target is the leftmost checkpoint on the *Bottom Layer*.\n"
    f"{SPACE}This checkpoint is only accessible in New Game+ (NG+). Recommended for grinding to Lv.100.\n"
    f"{SPACE}Video Guide: https://b23.tv/qsEVcDO\n\n"
    "Red Dragon Castle - Dragon Tower (Outdoor Checkpoint)\n"
    f"{SPACE}There are only two checkpoints in the Dragon Tower; choose the outdoor one next to a tree.\n"
    f"{SPACE}Recommended for NG++ (3rd playthrough), mainly for farming Buttons.\n\n"
    "Red Dragon Castle - Silken Alley Checkpoint\n"
    f"{SPACE}There are only three checkpoints near Silken Alley; this is the only indoor one.\n"
    f"{SPACE}Recommended for NG++ (3rd playthrough), mainly for farming Buttons."
)


class DSDFarmTask(NTEOneTimeTask, BaseCombatTask):
    CONF_LOCATION = "位置"
    CONF_USE_ULT = "使用终结技"
    CONF_DONT_SWITCH = "战斗时不切人"
    CONF_MAX_COMBAT_TIME = "战斗时长上限"
    CONF_WAIT_FULL_DURATION = "战斗后等待至时长上限"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "九百九十九夜"
        self.description = "挂机刷经验"
        _locale = self.get_app_locale()
        self.instructions = INST if _locale and "zh" in _locale else EN_INST
        self.locations = [
            "巧克力火山-底层最左边的篝火",
            "赤龙古堡-龙之高塔室外篝火",
            "赤龙古堡-残丝长巷篝火",
        ]
        self.add_rounds_config()
        self.default_config.update(
            {
                self.CONF_LOCATION: self.locations[0],
                self.CONF_USE_ULT: True,
                self.CONF_DONT_SWITCH: False,
                self.CONF_MAX_COMBAT_TIME: 1200,
                self.CONF_WAIT_FULL_DURATION: False,
            }
        )

        self.config_type.update(
            {
                self.CONF_LOCATION: {
                    "type": "drop_down",
                    "options": self.locations,
                },
            }
        )
        self.combat_detect_policy.miss_required = 3
        self.combat_detect_policy.uncertain_seconds = 2
        self.do_teleport_on_spot = False
        self.team_dead = False

    def run(self):
        self.sleep_check_skip.all = True
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("DSDFarmTask Error", e)
        finally:
            self.sleep_check_skip.all = False

    def do_run(self):
        self.do_teleport_on_spot = False
        self.deside_map_zoom()
        self.start_rounds()
        while self.begin_round():
            self.open_bonfire()
            self.refresh_monster()
            self.ensure_main()
            if self.do_teleport_on_spot:
                self.sleep(0.5)
                self.teleport_on_spot()
                self.ensure_main()
            self.deside_action()
            self.next_frame()
            self.add_success()
        self.finish_rounds()

    def open_bonfire(self):
        self.wait_until(self.find_interac, time_out=10, raise_if_not_found=True)
        self.wait_until(
            lambda: not self.is_in_team(),
            pre_action=lambda: self.send_interac(handle_claim=False),
            time_out=10,
            raise_if_not_found=True,
        )
        self.sleep(2)

    def sleep_check(self):
        super().sleep_check()
        if self.check_monthly_card():
            self.handle_monthly_card()

    def refresh_monster(self):
        unknown_retries = 0

        def click_no_remind():
            box = self.wait_until(
                lambda: self.find_one(Labels.no_remind_today, horizontal_variance=0.1), time_out=3
            )
            if box:
                self.operate_click(box, after_sleep=0.5)

        def try_refresh():
            nonlocal unknown_retries
            cooldown = self.monster_refresh_cooldown()
            if cooldown is None:
                self.sleep(0.5)
                self.next_frame()
                cooldown = self.monster_refresh_cooldown()
            ready = cooldown is None and self.monster_refresh_icon() is RefreshIcon.READY
            if not ready:
                if cooldown is None:
                    if unknown_retries >= 1:
                        raise TimeoutError("Cannot identify bonfire refresh button after retry")
                    unknown_retries += 1
                # 篝火界面的冷却数字不会实时更新, 退出等待后重新进入。
                self.ensure_main()
                wait_seconds = cooldown + 1 if cooldown is not None else 61
                reason = "刷新怪物冷却中" if cooldown is not None else "刷新按钮状态不确定"
                message = f"{reason}, 原地等待 {wait_seconds} 秒后重新进入篝火"
                self.log_info(message)
                self.info_set("current task", message)
                try:
                    self.sleep(wait_seconds)
                finally:
                    self.info_set("current task", None)
                self.log_info("冷却等待结束, 重新进入篝火检查刷新")
                self.open_bonfire()
                return False

            self.operate_click(0.057, 0.218, after_sleep=0.5)
            self.next_frame()
            confirm_box = self.box_of_screen(0.650, 0.611, 0.707, 0.708, hcenter=True)
            if self.find_confirm(box=confirm_box):
                self.wait_click_confirm(
                    range=confirm_box,
                    on_found=click_no_remind,
                    time_out=3,
                )

            # 成功刷新后按钮开始新的 60 秒冷却, 无弹窗时也以此为准。
            def refreshed():
                cooldown = self.monster_refresh_cooldown()
                if cooldown is not None:
                    return 55 <= cooldown <= 60
                if self.monster_refresh_icon() is not RefreshIcon.COOLDOWN:
                    return False
                self.sleep(0.25)
                self.next_frame()
                return self.monster_refresh_icon() is RefreshIcon.COOLDOWN

            return self.wait_until(refreshed, time_out=3, settle_time=0)

        self.wait_until(
            try_refresh,
            post_action=lambda: self.sleep(2),
            settle_time=0,
            time_out=120,
            raise_if_not_found=True,
        )

    def monster_refresh_icon(self):
        box = self.box_of_screen(0.042, 0.199, 0.074, 0.245)
        return classify_refresh_icon(box.crop_frame(self.frame))

    def monster_refresh_cooldown(self):
        for box in self.ocr(0.035, 0.195, 0.082, 0.248):
            match = re.fullmatch(r"\s*(\d{1,2})\s*[sS秒]?\s*", box.name)
            if match and 0 <= int(match[1]) <= 60:
                return int(match[1])
        return None

    def deside_map_zoom(self):
        location = self.config.get(self.CONF_LOCATION, None)
        if location == self.locations[0]:
            self.map_zoom(zoom="max")
        elif location == self.locations[1]:
            self.map_zoom(zoom="mid")
        elif location == self.locations[2]:
            self.map_zoom(zoom="mid")

    def deside_action(self):
        self.do_teleport_on_spot = False
        location = self.config.get(self.CONF_LOCATION, None)

        if location == self.locations[0]:
            self.location_0()
        elif location == self.locations[1]:
            self.location_1()
        elif location == self.locations[2]:
            self.location_2()

    def location_0(self):
        if self.walk_until_combat(run=True, delay=1):
            self.deside_combat_action()
        self.sleep(0.5)
        self.ensure_teleport(lambda: self.teleport_to_nearest_bonfire())

    def location_1(self):
        self.send_key_down("w")
        self.sleep(0.37)
        self.send_key_down("lshift")
        self.sleep(0.12)
        self.send_key_up("lshift")
        self.sleep(4.11)
        self.send_key_up("w")
        self.sleep(0.51)
        self.send_key_down("s")
        self.sleep(0.40)
        self.send_key_up("s")
        self.sleep(0.18)
        self.send_key_down("d")
        self.sleep(0.36)
        self.send_key_down("w")
        self.sleep(0.5)
        for _ in range(5):
            self.send_key_down("d")
            self.sleep(0.5)
            self.send_key_up("d")
            self.sleep(0.8)
        self.sleep(2)
        self.send_key_up("w")
        if self.wait_until(self.in_combat, time_out=10):
            self.deside_combat_action()
        self.sleep(0.5)
        box = self.box_of_screen(0.498, 0.102, 0.931, 0.827)
        self.ensure_teleport(lambda: self.teleport_to_top_bonfire(box))

    def location_2(self):
        self.send_key_down("w")
        self.sleep(0.20)
        self.send_key("lshift")
        self.sleep(2.80)
        self.send_key_down("a")
        self.sleep(0.10)
        self.send_key_up("w")
        self.sleep(2.10)
        self.send_key_up("a")
        if self.wait_until(self.in_combat, time_out=10):
            self.deside_combat_action()
        self.sleep(0.5)
        box = self.box_of_screen(0.410, 0.234, 0.560, 0.556)
        self.do_teleport_on_spot = True
        self.ensure_teleport(lambda: self.teleport_to_top_bonfire(box))

    def ensure_teleport(self, fun):
        origin_fun = None
        if self.team_dead:
            origin_fun = fun
            fun = self.teleport_on_spot
        switch = False
        while True:
            if fun():
                return True
            self.ensure_main()
            self.sleep(0.5)
            key = "w" if switch else "s"
            self.send_key(key, down_time=3)
            switch = not switch
            if origin_fun:
                fun = origin_fun

    def deside_combat_action(self):
        with self.skip_sleep_checks() as skip:
            skip.all = False
            max_combat_time = self.config.get(self.CONF_MAX_COMBAT_TIME, 1200)

            session = self.combat_session
            session.switch_enabled = not self.config.get(self.CONF_DONT_SWITCH, False)
            session.use_ultimate = self.config.get(self.CONF_USE_ULT, True)
            start_combat = time.time()
            self.combat_once(max_combat_time=max_combat_time)
            self.team_dead = False
            while not self.is_in_team():
                self.team_dead = True
                self.operate_click(0.501, 0.777, after_sleep=0.5)
                self.send_key("esc", after_sleep=2)
                self.next_frame()
            if self.team_dead:
                return
            if self.config.get(self.CONF_WAIT_FULL_DURATION):
                remaining_time = max_combat_time - (time.time() - start_combat)
                if remaining_time > 0:
                    self.log_info(f"战斗提前结束，原地等待 {remaining_time:.1f} 秒以补齐时长上限。")
                    self.sleep(remaining_time)

    def map_zoom(self, zoom="max"):
        self.ensure_main()
        self.open_map()
        if zoom == "max":
            self.operate_click(0.050, 0.378)
        elif zoom == "mid":
            self.operate_click(0.050, 0.527)
        self.sleep(1)
        self.ensure_main()

    def open_map(self):
        self.wait_until(
            lambda: self.find_one(Labels.map_zoom_in),
            time_out=30,
            pre_action=lambda: self.send_key("m", interval=2),
            raise_if_not_found=True,
        )
        self.sleep(1)

    def teleport_to_nearest_bonfire(self, threshold=0.7, time_out=10):
        self.ensure_main()
        self.open_map()
        to_find = [Labels.bonfire_teleport]
        template_boxes = [self.get_box_by_name(label) for label in to_find]
        max_template_size = max(
            max(template_box.width, template_box.height) for template_box in template_boxes
        )
        step = max(max_template_size, self.width_of_screen(0.02), 1)
        center_x = self.width_of_screen(0.5)
        center_y = self.height_of_screen(0.5)
        max_radius = max(self.width, self.height)

        def find_teleport():
            radius = step
            while radius <= max_radius:
                x = max(0, center_x - radius)
                y = max(0, center_y - radius)
                to_x = min(self.width, center_x + radius)
                to_y = min(self.height, center_y + radius)
                box = Box(x=x, y=y, to_x=to_x, to_y=to_y, name="nearest_map_teleport")
                teleport = self.find_best_match_in_box(box, to_find, threshold=threshold)
                if teleport:
                    return teleport
                radius += step

        teleport = self.wait_until(find_teleport, time_out=time_out, raise_if_not_found=True)
        self.log_info(f"found nearest map teleport {teleport}")
        self.operate_click(teleport, action_name="click_nearest_map_teleport")
        self.sleep(0.5)
        return self.click_traval_button(raise_if_not_found=False)

    def teleport_to_bonfire(self, box: Box = None, threshold=0.7, order=1):
        self.ensure_main()
        self.open_map()
        if not box:
            box = self.pos.screen.main_viewport.to_box()

        teleports = self.find_feature(Labels.bonfire_teleport, box=box, threshold=threshold)
        if not teleports:
            return False

        self.log_info(f"found map teleports {teleports}")

        center_box = self.pos.screen.center.to_box()
        teleports.sort(key=lambda tp: tp.center_distance(center_box))

        if len(teleports) >= order:
            teleport = teleports[order - 1]
        else:
            teleport = teleports[-1]

        self.operate_click(teleport, action_name="click_map_teleport")
        self.sleep(0.5)
        return self.click_traval_button(raise_if_not_found=False)

    def teleport_to_top_bonfire(self, box: Box, threshold=0.7):
        self.ensure_main()
        self.open_map()

        teleports = self.find_feature(Labels.bonfire_teleport, box=box, threshold=threshold)
        if not teleports:
            return False

        self.log_info(f"found map teleports {teleports}")

        teleport = min(teleports, key=lambda teleport: teleport.y)
        self.operate_click(teleport, action_name="click_map_teleport")
        self.sleep(0.5)
        return self.click_traval_button(raise_if_not_found=False)

    def teleport_on_spot(self):
        self.ensure_main()
        self.open_map()
        self.operate_click(0.5, 0.5, action_name="click_map_teleport")
        self.sleep(0.5)
        return self.click_traval_button(raise_if_not_found=False)
