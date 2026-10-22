from ok import TaskDisabledException

from src.combat.BaseCombatTask import BaseCombatTask
from src.Labels import Labels
from src.sound_trigger.SoundCombatContext import SoundCombatContext
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class WhirlwindTask(NTEOneTimeTask, BaseCombatTask):
    TARGET_NAVIGATION_ANGLE = -156
    NAVIGATION_ANGLE_TOLERANCE = 4.5
    NAVIGATION_FAILED_TIMEOUT = 4
    NAVIGATION_FAILED_RETRY_INTERVAL = 0.25
    NAVIGATION_CONFIRM_DELAY = 0.5
    NAVIGATION_CLAMP_TIMEOUT = 20
    NAVIGATION_CLAMP_ENTER_ANGLE = 15
    NAVIGATION_CLAMP_LOOP_INTERVAL = 0.05
    NAVIGATION_CLAMP_MICRO_INTERVAL = 0.25
    CONFIG_DIFF_OPTION = "难度选项"
    CONFIG_RELOGIN = "每2小时重新登入"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动小旋风"
        self.description = "可交互「小旋风」下按开始"
        self.default_config.update(
            {
                self.CONFIG_DIFF_OPTION: 1,
                self.CONFIG_RELOGIN: False,
            }
        )
        self.relogin_time = self.clock.UNSET

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("WhirlwindTask Error", e)
        finally:
            SoundCombatContext().clear_task_if(self)

    def do_run(self):
        self.set_relogin_time()
        self.sleep(1)
        self.sleep_check_skip.all = True
        self._apply_sound_config(dodge_action=self._dodge_with_skill)
        cond1 = not self.is_boss()
        cond2 = not self.find_interac()
        try:
            while True:
                self.check_relogin()
                if cond1:
                    if cond2:
                        self.navigate()
                        self.sleep(0.1)
                    self.start_interac()
                self.start_combat()
                cond1 = True
                cond2 = True
        finally:
            self.sleep_check_skip.all = True
            self._release_navigation_keys()

    def sleep_check(self):
        super().sleep_check()
        if self.check_monthly_card():
            self.handle_monthly_card()

    def check_relogin(self):
        if not self.config.get(self.CONFIG_RELOGIN):
            return
        if self.clock.now() > self.relogin_time:
            self.log_info("重新登入游戏")
            self.back_to_login()
            self.ensure_main()
            self.set_relogin_time()

    def set_relogin_time(self):
        self.relogin_time = self.clock.now() + 2 * 60 * 60

    def _dodge_with_skill(self):
        self.send_key(self.get_skill_key())

    def start_interac(self):
        self.wait_until(
            self.find_dialog_history,
            pre_action=self.scroll_and_interac,
            time_out=30,
            raise_if_not_found=True,
        )

        diff_option = self.config.get(self.CONFIG_DIFF_OPTION, 1)
        ratio_x = 0.701
        ratio_y = 0.542 + 0.068 * (diff_option - 1)

        self.wait_until(
            self.is_in_team,
            pre_action=lambda: self.operate_click(ratio_x, ratio_y, interval=2),
            settle_time=1,
            time_out=60,
        )

    def scroll_and_interac(self):
        if self.is_in_team():
            if self.send_key("f", interval=0.25):
                self.scroll_relative(0.5, 0.5, -1)
        else:
            self.run_with_interval(
                lambda: self.log_info("not in_team skip scroll_and_interac"), interval=1
            )

    def find_dialog_history(self):
        return self.find_one(
            Labels.dialog_history,
            threshold=0.8,
            box=self.pos.screen.dialog_icon.to_box(),
        )

    def start_combat(self):
        if not self.is_boss():
            self.send_key_down("w")
            self.sleep(0.2)
            self.send_key("lshift")
            self.wait_until(self.is_boss)
            self.sleep(1)
            self.send_key_up("w")
        with self.skip_sleep_checks() as skip:
            skip.all = True
            self.sleep(1.10)
            self.send_key(self.get_skill_key())
            self.sleep(1.25)
            self.send_key(self.get_skill_key())
            self.sleep(0.10)
            skip.sound_combat_context = False
            while True:
                self.click()
                self.sleep(0.15)
                self.send_key(self.get_ultimate_key())
                self.sleep(0.15)
                if not self.is_in_team():
                    if self.check_monthly_card():
                        self.operate_click(0.50, 0.89, interval=2)
                elif not self.is_boss():
                    break
        self.sleep(2)
        self.wait_in_team(time_out=60, settle_time=1)
        self.click(key="middle")
        self.sleep(1)

    def navigate(self):
        self.wait_in_team()
        self.send_key("lctrl", after_sleep=0.25)
        failed_start = None
        last_error = None
        try:
            while True:
                angle = self._navigation_angle()
                if angle is None:
                    if failed_start is None:
                        failed_start = self.clock.now()
                    elif self.clock.now() - failed_start > self.NAVIGATION_FAILED_TIMEOUT:
                        return False
                    self.sleep(self.NAVIGATION_FAILED_RETRY_INTERVAL)
                    continue
                failed_start = None
                self.log_info(f"angle {angle} tartget {self.TARGET_NAVIGATION_ANGLE}")
                error = self._normalize_angle(self.TARGET_NAVIGATION_ANGLE - angle)
                if abs(error) <= self.NAVIGATION_ANGLE_TOLERANCE:
                    if self._confirm_navigation_angle():
                        return self._advance_after_navigation(self._navigation_side_key(angle))
                    continue

                side_key = self._navigation_side_key(angle)
                if self._is_navigation_clamped(last_error, error):
                    return self._advance_after_navigation(side_key)

                last_error = error
                self._fine_tune_navigation(side_key, abs(error), angle)
        finally:
            self.sleep(0.25)
            self.send_key("lctrl")
            self._release_navigation_keys()

    def _is_navigation_clamped(self, last_error, current_error):
        if last_error is None:
            return False
        if last_error * current_error >= 0:
            return False
        return max(abs(last_error), abs(current_error)) <= self.NAVIGATION_CLAMP_ENTER_ANGLE

    def _advance_after_navigation(self, side_key):
        start = self.clock.now()
        next_micro_tune = self.clock.UNSET
        self.send_key_down("w")
        try:
            while self.clock.now() - start < self.NAVIGATION_CLAMP_TIMEOUT:
                if self.find_dialog_history():
                    return True
                self.scroll_and_interac()

                angle = self._navigation_angle()
                if angle is not None:
                    error = self._normalize_angle(self.TARGET_NAVIGATION_ANGLE - angle)
                    if abs(error) <= self.NAVIGATION_ANGLE_TOLERANCE:
                        continue
                    side_key = self._navigation_side_key(angle)

                now = self.clock.now()
                if now >= next_micro_tune:
                    self._micro_tune_after_clamp(side_key)
                    next_micro_tune = self.clock.now() + self.NAVIGATION_CLAMP_MICRO_INTERVAL

                self.sleep(self.NAVIGATION_CLAMP_LOOP_INTERVAL)
            return False
        finally:
            self.send_key_up("w")

    def _micro_tune_after_clamp(self, side_key):
        self.log_info("_micro_tune_after_clamp")
        self.send_key(side_key)
        self.sleep(0.05)
        self.click(key="middle")

    def _navigation_angle(self):
        ret = self.check_mini_map_arrow()
        if not ret:
            return None
        return ret[0].get("angle")

    def _confirm_navigation_angle(self):
        self.sleep(self.NAVIGATION_CONFIRM_DELAY)
        self.send_key("w")

        angle = self._navigation_angle()
        if angle is None:
            return False

        error = self._normalize_angle(self.TARGET_NAVIGATION_ANGLE - angle)
        return abs(error) <= self.NAVIGATION_ANGLE_TOLERANCE

    def _navigation_side_key(self, current_angle):
        error = self._normalize_angle(self.TARGET_NAVIGATION_ANGLE - current_angle)
        return "d" if error < 0 else "a"

    def _fine_tune_navigation(self, side_key, angle_diff, current_angle):
        turn_key = self._best_navigation_turn_key(current_angle, side_key)
        if turn_key is not None:
            self.send_key(turn_key)
            self.sleep(0.5)
        else:
            try:
                self.send_key_down(side_key)
                self.send_key("w")
            finally:
                self.send_key_up(side_key)
            self.sleep(0.05)

        self.click(key="middle")
        self.sleep(0.75)
        self.send_key("w")
        self.sleep(0.5)
        self.next_frame()

    def _best_navigation_turn_key(self, current_angle, side_key):
        current_diff = abs(self._normalize_angle(self.TARGET_NAVIGATION_ANGLE - current_angle))

        side_angle_offset = -90 if side_key == "d" else 90
        side_turn_angle = self._normalize_angle(current_angle + side_angle_offset)
        turn_around_angle = self._normalize_angle(current_angle + 180)

        side_turn_diff = abs(self._normalize_angle(self.TARGET_NAVIGATION_ANGLE - side_turn_angle))
        turn_around_diff = abs(
            self._normalize_angle(self.TARGET_NAVIGATION_ANGLE - turn_around_angle)
        )
        if side_turn_diff >= current_diff and turn_around_diff >= current_diff:
            return None
        return "s" if turn_around_diff < side_turn_diff else side_key

    def _release_navigation_keys(self):
        self.send_key_up("w")
        self.send_key_up("a")
        self.send_key_up("d")
