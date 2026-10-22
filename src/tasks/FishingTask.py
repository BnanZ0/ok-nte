from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np
from ok import TaskDisabledException, WaitFailedException

from src import text_white_color
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.flow.scene_flow import SceneReplan, StepFailure, StepPolicy
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.ui.task_icons import Icon
from src.utils import image_utils as iu


@dataclass
class FishingSession:
    awaiting_result_round: int | None = None
    interrupted_control_round: int | None = None


class FishingTask(NTEOneTimeTask, BaseNTETask):
    CONF_CONTROL_MODE = "控条模式"
    CONF_TAP_MULTIPLIER = "点按时长倍率"
    CONF_AUTO_BUY_BAIT = "自动补饵卖鱼"

    MODE_HOLD = "长按"
    MODE_TAP = "点按"

    ENTER_SCENE_TIMEOUT = 5
    MENU_ACTION_TIMEOUT = 10
    RETURN_READY_TIMEOUT = 60
    CONTROL_TIMEOUT = 30
    STALLED_BAIT_SECONDS = 5

    class FishingStep(Enum):
        ENTER = "enter"
        CAST = "cast"
        WAIT_BITE = "wait_bite"
        CONTROL = "control"
        RESULT = "result"
        OPEN_SELL = "open_sell"
        SELL = "sell"
        FISH_HOLD = "fish_hold"
        OPEN_BAIT = "open_bait"
        BUY_BAIT = "buy_bait"
        CONFIRM_BAIT = "confirm_bait"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动钓鱼"
        self.description = "自动完成一轮或多轮钓鱼"
        self.group_name = "都市闲趣"
        self.group_icon = Icon.GAME
        self.add_rounds_config()
        self.default_config.update(
            {
                self.CONF_CONTROL_MODE: self.MODE_HOLD,
                self.CONF_TAP_MULTIPLIER: 1.0,
                self.CONF_AUTO_BUY_BAIT: True,
            }
        )
        self.config_description.update(
            {
                self.CONF_CONTROL_MODE: f"{self.MODE_HOLD}：平滑流畅, 易过冲\n"
                f"{self.MODE_TAP}: 安全较慢, 防过冲",
                self.CONF_TAP_MULTIPLIER: "点按模式专用。用于微调每次按键的持续时间",
                self.CONF_AUTO_BUY_BAIT: "抛竿失败时，补充默认鱼饵并出售鱼获后重试",
            }
        )
        self.config_type.update(
            {
                self.CONF_CONTROL_MODE: {
                    "type": "drop_down",
                    "options": [self.MODE_HOLD, self.MODE_TAP],
                    "sub_configs": {
                        self.MODE_TAP: self.CONF_TAP_MULTIPLIER,
                    },
                },
            }
        )
        self._morph_kernel = np.ones((3, 3), dtype=np.uint8)
        self._last_direction = None
        self._bar_active_key = None
        self._fishing_session: FishingSession | None = None
        self.add_exit_after_config()
        self._configure_scene_flow()

    def _configure_scene_flow(self):
        flow = self.scene_flow
        step = self.FishingStep
        return_to_ready = flow.transition(
            lambda: self.send_key("esc"),
            interval=2,
            timeout=self.RETURN_READY_TIMEOUT,
        )
        flow.guard(step.ENTER, self.is_in_team, self._enter_fishing_from_interaction, priority=100)
        flow.step(
            step.CAST,
            self.is_ready_to_cast,
            self._cast,
            next=(step.CAST, step.WAIT_BITE, step.CONTROL, step.RESULT),
            policy=StepPolicy(max_attempts=4, interval=2),
            on_failure=self._route_cast_failure,
        )
        flow.step(
            step.WAIT_BITE,
            self.is_waiting_bite,
            self._wait_bite,
            next=(step.WAIT_BITE, step.CONTROL, step.RESULT, step.CAST),
            policy=StepPolicy(interval=2),
        )
        flow.step(
            step.CONTROL,
            self.is_playing_fish,
            self._control,
            next=(step.CONTROL, step.RESULT, step.CAST),
            policy=StepPolicy(max_attempts=1),
            on_failure=self._route_control_failure,
        )
        flow.step(
            step.RESULT,
            self.has_success_overlay,
            self._collect_result,
            next=(step.CAST,),
            transition=return_to_ready,
        )
        flow.step(
            step.OPEN_SELL,
            self.is_ready_to_cast,
            self._open_sell_menu,
            next=(step.SELL,),
            policy=StepPolicy(max_attempts=3, interval=2),
            on_failure=self._route_restock_failure,
        )
        flow.step(
            step.SELL,
            self.is_sell_menu,
            self._sell,
            next=(step.FISH_HOLD,),
            policy=StepPolicy(max_attempts=3, interval=2),
            on_failure=self._route_restock_failure,
        )
        flow.step(
            step.FISH_HOLD,
            self.is_fish_hold,
            self._close_fish_hold,
            next=(step.OPEN_BAIT,),
            policy=StepPolicy(max_attempts=3, interval=2),
            transition=return_to_ready,
            on_failure=self._route_restock_failure,
        )
        flow.step(
            step.OPEN_BAIT,
            self.is_ready_to_cast,
            self._open_bait_menu,
            next=(step.BUY_BAIT, step.CAST),
            policy=StepPolicy(max_attempts=3, interval=2),
            on_failure=self._route_restock_failure,
        )
        flow.step(
            step.BUY_BAIT,
            self.is_bait_shop,
            self._buy_bait,
            next=(step.CONFIRM_BAIT,),
            policy=StepPolicy(max_attempts=3, interval=2),
            transition=return_to_ready,
            on_failure=self._route_restock_failure,
        )
        flow.step(
            step.CONFIRM_BAIT,
            self.is_ready_to_cast,
            self._confirm_bait,
            next=(step.CAST,),
            policy=StepPolicy(max_attempts=3, interval=2),
            on_failure=self._route_restock_failure,
        )
        flow.recovery(self._recover_fishing_scene, interval=2, max_attempts=180, timeout=360)

    def run(self):
        super().run()
        try:
            self.reset_runtime_state()
            self._publish_config_info()
            return self._run_fishing_flow()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.screenshot("fishing_unexpected_exception")
            self.log_error("FishingTask error", e)
            raise

    def _run_fishing_flow(self):
        self.start_rounds()
        session = FishingSession()
        self._fishing_session = session

        try:
            if not self.scene_flow.run(
                lambda: not self.has_remaining_rounds(),
                start=self.FishingStep.CAST,
                poll_interval=0.1,
            ):
                raise WaitFailedException("无法恢复钓鱼流程")
        finally:
            self._fishing_session = None
        self.info_set("当前阶段", "任务结束")
        self.finish_rounds()

    def _cast(self):
        if not self.begin_round():
            return
        self._set_stage("抛竿")
        self.send_key("f", interval=2, action_name="cast_rod_f")

    def _open_sell_menu(self):
        self._set_stage("打开卖鱼界面")
        self.wait_strict(
            self.is_sell_menu,
            pre_action=lambda: self.send_key("q", interval=2, action_name="open_fish_sell"),
            time_out=self.MENU_ACTION_TIMEOUT,
        )

    def _sell(self):
        self._set_stage("卖鱼")
        self.wait_strict(
            self.is_fish_hold,
            pre_action=lambda: self.operate_click(
                0.076,
                0.386,
                interval=2,
                action_name="open_fish_hold",
            ),
            time_out=self.MENU_ACTION_TIMEOUT,
        )

    def _close_fish_hold(self):
        self._set_stage("鱼舱")
        if self.find_one(Labels.fish_one_click_sell):
            if not self.wait_click_confirm(
                lambda: self.operate_click(0.556, 0.898, interval=2),
                raise_if_not_found=False,
            ):
                self.log_info("一键出售未完成，可能当前鱼获不可出售，跳过出售")
        else:
            self.log_info("鱼舱内没有可出售鱼获，跳过出售")

    def _open_bait_menu(self):
        self._set_stage("打开鱼饵界面")
        self.wait_click_confirm(lambda: self.send_key("e", interval=2))
        self.wait_strict(self._detect_bait_interface, time_out=10)

    def _buy_bait(self):
        self._set_stage("购买鱼饵")
        self.wait_strict(
            lambda: self.find_one(Labels.default_fish_bait_big),
            pre_action=self._click_default_bait,
            time_out=10,
        )

        def buy_action():
            self.operate_click(0.9520, 0.8812)
            self.sleep(1)
            self.operate_click(0.8715, 0.9542)
            self.sleep(1)

        self.wait_click_confirm(buy_action)

    def _confirm_bait(self):
        self._set_stage("确认鱼饵")
        self.wait_click_confirm(lambda: self.send_key("e", interval=2))

    def _wait_bite(self):
        self._set_stage("等待咬钩")
        if not self.has_remaining_rounds():
            return
        self.send_key("f", interval=2, action_name="bite_f")

    def _control(self):
        session = self._require_fishing_session()
        self._record_missing_result_before_next_control(session)
        self._set_stage("控条")
        self.log_info("进入溜鱼状态")
        try:
            self.control_until_finish()
        except SceneReplan:
            session.interrupted_control_round = self.current_round
            raise
        session.awaiting_result_round = self.current_round
        session.interrupted_control_round = None

    def _collect_result(self):
        session = self._require_fishing_session()
        self._set_stage("结算成功")
        if self._completed_control_round(session):
            self.add_success()
            self.log_round_info("钓鱼成功")
            self._clear_completed_control(session)
            self.sleep(1)

    def _record_missing_result_before_next_control(self, session: FishingSession):
        if not session.awaiting_result_round:
            return
        self.add_failed("下一轮控条前未检测到成功面板")
        self._clear_completed_control(session)

    def _require_fishing_session(self) -> FishingSession:
        if self._fishing_session is None:
            raise RuntimeError("Fishing flow action requires an active session")
        return self._fishing_session

    def _route_cast_failure(self, _failure: StepFailure) -> FishingStep:
        session = self._require_fishing_session()
        if self.config.get(self.CONF_AUTO_BUY_BAIT, True):
            self.log_warning("未检测到进入抛竿状态，开始买饵补货")
            return self.FishingStep.OPEN_SELL
        self._capture_cast_failure_info()
        self.add_failed("未检测到进入抛竿状态")
        self._clear_completed_control(session)
        return self.FishingStep.CAST

    def _route_control_failure(self, _failure: StepFailure) -> FishingStep:
        session = self._require_fishing_session()
        self.add_failed("溜鱼状态失败")
        self._clear_completed_control(session)
        return self.FishingStep.CAST

    def _route_restock_failure(self, _failure: StepFailure) -> FishingStep:
        session = self._require_fishing_session()
        self.log_warning("补货流程失败，结束当前轮次")
        self.add_failed("补货流程失败")
        self._clear_completed_control(session)
        return self.FishingStep.CAST

    @staticmethod
    def _completed_control_round(session: FishingSession) -> int | None:
        return session.awaiting_result_round or session.interrupted_control_round

    @staticmethod
    def _clear_completed_control(session: FishingSession):
        session.awaiting_result_round = None
        session.interrupted_control_round = None

    def control_until_finish(self):
        check_timer = self.clock.timer(timeout=1)
        timer = self.clock.timer(timeout=self.CONTROL_TIMEOUT)
        bait_visible_since = self.clock.UNSET
        try:
            while not timer.expired():
                state = self.detect_fishing_bar_state()
                if self.is_valid_bar_state(state):
                    self.apply_bar_control(state)
                else:
                    self._clear_bar_key_if_hold_mode()

                if check_timer.expired():
                    self.scene_flow.safe_point()
                    if self.has_success_overlay():
                        return True
                    if self.has_fish_start():
                        if not bait_visible_since:
                            bait_visible_since = self.clock.now()
                        elif self.clock.now() - bait_visible_since > self.STALLED_BAIT_SECONDS:
                            return False
                    else:
                        bait_visible_since = self.clock.UNSET

                self.sleep(0.01)
                if timer.expired():
                    self.log_warning("溜鱼状态超时")
                    raise WaitFailedException()
        finally:
            self._clear_bar_key_if_hold_mode()

    def _recover_fishing_scene(self):
        self._clear_bar_key_if_hold_mode()
        self._set_stage("恢复钓鱼界面")
        self.send_key("esc")

    def _enter_fishing_from_interaction(self):
        self._set_stage("寻找钓鱼交互点")
        self.wait_strict(self.find_interac, time_out=self.ENTER_SCENE_TIMEOUT)

        confirm_box = self.box_of_screen(0.927, 0.827, 0.975, 0.912)
        start_button = self.wait_strict(
            lambda: self.find_confirm(box=confirm_box),
            post_action=lambda: self.send_key("f", interval=2),
        )

        def click_extra_confirm():
            extra_confirm_box = self.box_of_screen(0.656, 0.618, 0.700, 0.699)
            if button := self.find_confirm(box=extra_confirm_box):
                self.operate_click(button, action_name="extra_confirm", interval=2)

        self.wait_strict(
            self.has_fish_start,
            pre_action=lambda: self.operate_click(
                start_button,
                action_name="start_fish",
                interval=2,
            ),
            post_action=click_extra_confirm,
            time_out=30,
        )

    def _detect_bait_interface(self):
        if self.has_fish_start():
            return "fish_start"
        if self.find_one(Labels.fish_shop):
            return "fish_shop"
        return None

    def _click_default_bait(self):
        bait_box = self.box_of_screen(0.025, 0.118, 0.344, 0.516)
        box = self.find_one(Labels.default_fish_bait, box=bait_box, threshold=0.8)
        if box:
            self.operate_click(box, interval=1)
            return True
        return False

    def wait_click_confirm(
        self,
        pre_action=None,
        range=None,
        on_found=None,
        time_out=10,
        settle_time=1.0,
        raise_if_not_found=True,
    ):
        if range is None:
            range = (0.641, 0.610, 0.713, 0.698)
        return super().wait_click_confirm(
            pre_action=pre_action,
            range=range,
            on_found=on_found,
            time_out=time_out,
            settle_time=settle_time,
            raise_if_not_found=raise_if_not_found,
        )

    def wait_strict(
        self,
        condition,
        time_out=0,
        pre_action=None,
        post_action=None,
    ):
        return self.wait_until(
            condition,
            time_out=time_out,
            pre_action=pre_action,
            post_action=post_action,
            settle_time=1,
            raise_if_not_found=True,
        )

    def _capture_cast_failure_info(self):
        self.send_key("f")
        text = self.ocr(0.4090, 0.4778, 0.5914, 0.5188, frame=self.frame)
        self.log_error("未检测到进入抛竿状态", notify=True)
        if text:
            self.log_warning(f"检测到文字: {text}")

    def apply_bar_control(self, state: dict):
        mode = self.config.get(self.CONF_CONTROL_MODE, self.MODE_HOLD)
        if mode == self.MODE_TAP:
            self.apply_bar_control_discrete(state)
        else:
            self.apply_bar_control_hold(state)

    def apply_bar_control_hold(self, state: dict):
        pointer_center, pointer_width, zone_center, zone_width = self._bar_metrics(state)
        error = pointer_center - zone_center
        abs_error = abs(error)
        deadzone = max(2, int(pointer_width * 3))

        if abs_error <= deadzone:
            self._set_bar_key(None)
            self.log_debug_gated(
                f"指针已锁定中心: pointer={pointer_center}, target={zone_center}",
                interval=2,
            )
            return

        key = "d" if error < 0 else "a"
        self._set_bar_key(key)

    def apply_bar_control_discrete(self, state: dict):
        pointer_center, _, zone_center, zone_width = self._bar_metrics(state)
        dist_from_center = pointer_center - zone_center
        abs_dist = abs(dist_from_center)

        if abs_dist <= max(2, int(zone_width * 0.08)):
            self.log_debug_gated(
                f"指针已锁定中心: pointer={pointer_center}, target={zone_center}",
                interval=2,
            )
            return

        key = "d" if dist_from_center < 0 else "a"
        ratio = min(1.0, abs_dist / (zone_width / 2))
        curve = ratio * ratio * (3 - 2 * ratio)
        hold = 0.01 + curve * 0.18

        if key != self._last_direction:
            hold *= 0.6
        self._last_direction = key

        multiplier = float(self.config.get(self.CONF_TAP_MULTIPLIER, 1.0))
        hold = min(0.2, max(0.01, hold * multiplier))
        self.send_key(key, down_time=hold)

    def _set_bar_key(self, key):
        if key == self._bar_active_key:
            return

        if self._bar_active_key is not None:
            self.send_key_up(self._bar_active_key)
            self._bar_active_key = None

        if key is not None:
            self.send_key_down(key)
            self._bar_active_key = key

    def _clear_bar_key_if_hold_mode(self):
        if self.config.get(self.CONF_CONTROL_MODE, self.MODE_HOLD) == self.MODE_HOLD:
            self._set_bar_key(None)

    def _bar_metrics(self, state: dict):
        return (
            int(state["pointer_center"]),
            max(1, int(state["pointer_width"])),
            int(state["zone_center"]),
            max(1, int(state["zone_width"])),
        )

    def is_valid_bar_state(self, state):
        if state is None:
            return False
        zone_left = int(state.get("zone_left", 0))
        zone_right = int(state.get("zone_right", 0))
        pointer_center = int(state.get("pointer_center", -1))
        pointer_width = int(state.get("pointer_width", -1))
        image_width = max(1, int(state.get("image_width", 1)))
        zone_width = max(0, int(state.get("zone_width", zone_right - zone_left)))
        ratio = zone_width / image_width
        if not (0.05 <= ratio <= 0.55):
            return False
        if not (0 <= pointer_center < image_width):
            return False
        if pointer_width < 0:
            return False

        edge_zone = zone_left <= 1 or zone_right >= image_width - 2
        if edge_zone and abs(pointer_center - int((zone_left + zone_right) / 2)) > int(
            image_width * 0.38
        ):
            return False
        return True

    def detect_fishing_bar_state(self):
        box = self.box_of_screen(0.3164, 0.0646, 0.6875, 0.0743, name="fishing_bar")
        image = box.crop_frame(self.frame)
        if image is None or image.size == 0:
            return None

        green_mask = iu.filter_by_hsv(
            image, iu.HSVRange((50, 150, 160), (160, 220, 255)), return_mask=True
        )
        yellow_mask = iu.filter_by_hsv(
            image, iu.HSVRange((20, 60, 195), (55, 200, 255)), return_mask=True
        )

        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, self._morph_kernel)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, self._morph_kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, self._morph_kernel)
        yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_CLOSE, self._morph_kernel)

        pointer_center, pointer_width = self._detect_pointer_center(yellow_mask)
        zone = self._detect_control_zone(green_mask)
        if zone is None:
            return None

        zone_left, zone_right = zone
        zone_width = zone_right - zone_left
        return {
            "zone_left": zone_left,
            "zone_right": zone_right,
            "zone_center": zone_left + zone_width // 2,
            "zone_width": zone_width,
            "image_width": int(image.shape[1]),
            "pointer_center": pointer_center,
            "pointer_width": pointer_width,
            "in_zone": zone_left <= pointer_center <= zone_right,
        }

    def _detect_pointer_center(self, yellow_mask):
        yellow_contours, _ = cv2.findContours(
            yellow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not yellow_contours:
            return -1, -1
        yellow_max_contour = max(yellow_contours, key=cv2.contourArea)
        px, _, pw, _ = cv2.boundingRect(yellow_max_contour)
        return px + pw // 2, pw

    @staticmethod
    def _detect_control_zone(green_mask):
        green_contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in green_contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w >= 5 and h >= 5:
                candidates.append((x, y, w, h, w * h))
        if not candidates:
            return None

        candidates.sort(key=lambda item: item[4], reverse=True)
        top_candidates = sorted(candidates[:2], key=lambda item: item[0])

        zone_left = top_candidates[0][0]
        if len(top_candidates) == 1:
            zone_right = top_candidates[0][0] + top_candidates[0][2]
        else:
            zone_right = max(
                top_candidates[0][0] + top_candidates[0][2],
                top_candidates[1][0] + top_candidates[1][2],
            )
        return zone_left, zone_right

    def is_playing_fish(self):
        is_fishing_ui_hidden = not self.has_fish_bait() and not self.has_fish_start()
        has_valid_control_bar = self.is_valid_bar_state(self.detect_fishing_bar_state())
        return is_fishing_ui_hidden and has_valid_control_bar

    def is_ready_to_cast(self):
        return self.has_fish_bait() and self.has_fish_start()

    def is_waiting_bite(self):
        return not self.has_fish_bait() and self.has_fish_start()

    def is_sell_menu(self):
        return self.find_one(Labels.fish_sell) is not None

    def is_fish_hold(self):
        return self.find_one(Labels.fish_hold) is not None

    def is_bait_shop(self):
        return self.find_one(Labels.fish_shop) is not None

    def has_success_overlay(self):
        return self.find_one(Labels.fish_sucess)

    def has_fish_start(self):
        def frame_process(img):
            return iu.create_color_mask(img, text_white_color)

        return self.find_one(Labels.fish_start, frame_processor=frame_process)

    def has_fish_bait(self):
        def frame_process(img):
            return iu.create_color_mask(img, text_white_color)

        return self.find_one(Labels.fish_bait, frame_processor=frame_process)

    def reset_runtime_state(self):
        self._set_bar_key(None)
        self._last_direction = None
        self._bar_active_key = None

    def _publish_config_info(self):
        self.info_set("控条模式", self.config.get(self.CONF_CONTROL_MODE, self.MODE_HOLD))
        self.info_set(
            "自动补饵卖鱼",
            "开启" if self.config.get(self.CONF_AUTO_BUY_BAIT, True) else "关闭",
        )
        self.info_set("当前阶段", "")

    def _set_stage(self, stage: str):
        if self.info_get("当前阶段") != stage:
            self.info_set("当前阶段", stage)
