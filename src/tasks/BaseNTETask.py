import inspect
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, List

from ok import (
    BaseTask,
    Box,
    CannotFindException,
    Logger,
    WaitFailedException,
    og,
    safe_get,
)

from src import text_black_color
from src.events import ConfirmationRequested, communicate
from src.Labels import Labels
from src.scene.NTEScene import NTEScene
from src.scene.PositionMap import PositionMap
from src.tasks.mixin.CharUIMixin import CharUIMixin
from src.tasks.mixin.MovementMixin import MovementMixin
from src.tasks.mixin.OgMixin import OgMixin
from src.tasks.mixin.RoundMixin import RoundMixin
from src.tasks.mixin.SceneFlowMixin import SceneFlowMixin
from src.tasks.mixin.VisionMixin import VisionMixin
from src.utils import image_utils as iu
from src.utils import vision_utils as vu
from src.utils.log_gate import LogGateMixin

logger = Logger.get_logger(__name__)
stamina_re = re.compile(r"(\d+)/(\d+)")
MSG_MAIN_DETECTION_FAILED = (
    "主界面检测失败。建议操作: \n"
    "1. 关闭显卡滤镜与锐化功能；\n"
    "2. 尝试开启 Windows “自动管理应用的颜色”设置。"
)
MSG_WORLD_DETECTION_FAILED = "大世界检测失败: 请检查游戏内 UI 透明度是否已设置为 1.0。"
MOUSE_CONTROL_WARNING_TITLE = "鼠标占用警告"
MOUSE_CONTROL_WARNING = (
    "即将运行: {mode}\n\n"
    "此任务会频繁控制鼠标。\n"
    "停止方法: 按热键暂停 ok-nte, 再手动停止任务。\n\n"
    "当前热键: {hotkey}\n"
    "如不确定热键, 请点击取消, 确认后再运行。"
)


class BaseNTETask(
    SceneFlowMixin,
    CharUIMixin,
    MovementMixin,
    VisionMixin,
    RoundMixin,
    OgMixin,
    LogGateMixin,
    BaseTask,
):
    CONF_CLAIM_REWARD_COUNT = "领取奖励次数"
    DEFAULT_MOVE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scene: NTEScene | None = None
        self.key_config = self.get_global_config("Game Hotkey Config")
        self.monthly_card_config = self.get_global_config("Monthly Card Config")
        self.sound_config = self.get_global_config("Sound Trigger Config")
        self.pos = PositionMap(self)
        self._init_char_ui_state()
        self.next_monthly_card_start = 0
        self._last_interval_action_time = {}
        self._action_interval_lock = threading.Lock()
        self.scene_flow.interrupt(self.check_monthly_card, self.handle_monthly_card)

    def add_claim_reward_count_config(self, default=0):
        self.default_config.update({self.CONF_CLAIM_REWARD_COUNT: default})
        self.config_description.update(
            {self.CONF_CLAIM_REWARD_COUNT: "设置为0则领取当前体力可领取的全部奖励"}
        )

    def confirm_mouse_control_warning(
        self, *, mode: str | None = None, close_delay_seconds: int = 3
    ) -> bool:
        try:
            hotkey = og.executor.basic_options.get("Start/Stop")
        except Exception:
            hotkey = "--"

        if mode is None:
            mode = self.tr(self.name)

        confirmation = ConfirmationRequested(
            self.tr(MOUSE_CONTROL_WARNING_TITLE),
            self.tr(MOUSE_CONTROL_WARNING).format(mode=mode, hotkey=hotkey),
            rich_text=False,
            hide_cancel=False,
            close_delay_seconds=close_delay_seconds,
        )
        communicate.confirmation_requested.emit(confirmation)
        return confirmation.wait_for_response()

    @property
    def thread_pool_executor(self) -> ThreadPoolExecutor | None:
        if og.my_app is None:
            return None
        return og.my_app.get_thread_pool_executor()

    @staticmethod
    def submit_periodic_task(delay, task, *args, **kwargs):
        """
        提交一个循环任务到线程池。
        如果要停止循环，任务函数应返回 False。

        :param task: 要执行的函数
        :param delay: 每次执行后的间隔时间（秒）
        :param args: 位置参数
        :param kwargs: 关键字参数
        """
        if og.my_app is None:
            return
        return og.my_app.submit_periodic_task(delay, task, *args, **kwargs)

    def openvino_detect(
        self,
        frame=None,
        sync: bool = False,
        box: Box = None,
        threshold: float = 0.7,
        force: bool = False,
        mask_regions=None,
    ) -> List[Box] | None:
        if og.my_app is None:
            return []
        if box is None:
            box = self.box_of_screen(0, 0, 1, 1, name="openvino")
        else:
            box.name = "openvino"
        self.draw_boxes(boxes=box, color="blue")
        if frame is None:
            frame = self.frame
        results = og.my_app.openvino_detect(
            image=frame,
            sync=sync,
            box=box,
            threshold=threshold,
            force=force,
            mask_regions=mask_regions,
        )
        if results:
            self.draw_boxes(boxes=results, color="red")
        return results

    def openvino_clear_cache(self):
        """清空缓存"""
        if og.my_app is None:
            return
        og.my_app.openvino_clear_cache()

    def get_last_openvino_image(self):
        if og.my_app is None:
            return None
        return getattr(og.my_app, "openvino_latest_image", None)

    @property
    def openvino_available(self):
        return getattr(og.my_app, "openvino_available", None)

    # fmt: off
    def click(self, x: int | Box | List[Box] = -1, y=-1, move_back=None, name=None,
              interval=-1, move=None, down_time=0.02, after_sleep=0, key='left',
              hcenter=False, vcenter=False, action_name=None) -> Any:
        if action_name is not None:
            if not self._check_action_interval(action_name, interval):
                return False
            interval = -1

        if move is None:
            move = self.DEFAULT_MOVE
        if move_back is None:
            move_back = move
        return super().click(
            x, y, move_back=move_back, name=name, interval=interval, move=move,
            down_time=down_time, after_sleep=after_sleep, key=key,
            hcenter=hcenter, vcenter=vcenter
        )

    def operate_click(self, x: int | Box | List[Box] = -1, y=-1, restore_cursor=True, name=None,
                      interval=-1, down_time=0.02, after_sleep=0, key='left',
                      hcenter=False, vcenter=False, action_name=None) -> Any:
        action_name = action_name or "operate_click"
        if not self._check_action_interval(action_name, interval):
            return False
        result = self.operate(
            lambda: self.click(
                x, y, name=name, interval=-1, move=True, down_time=down_time,
                after_sleep=0, key=key, hcenter=hcenter, vcenter=vcenter
            ),
            block=True,
            restore_cursor=restore_cursor,
        )
        self.sleep(after_sleep)
        return result

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0, action_name=None) -> bool:
        if action_name is not None:
            if not self._check_action_interval(action_name, interval):
                return False
            interval = -1
        return super().send_key(
            key, down_time=down_time, interval=interval, after_sleep=after_sleep
        )

    def scroll(self, x, y, count):
        if isinstance(x, float) and isinstance(y, float):
            x, y = min(x, 1.0) * self.width, min(y, 1.0) * self.height
        return super().scroll(x=x, y=y, count=count)
    # fmt: on

    def _check_action_interval(self, action_name: Any, interval: float) -> bool:
        if interval <= 0:
            return True
        # action_name must be a stable identifier, not a dynamic value.
        with self._action_interval_lock:
            now = time.time()
            last_time = self._last_interval_action_time.get(action_name, 0)
            if now - last_time < interval:
                return False
            self._last_interval_action_time[action_name] = now
            return True

    def _get_interval_func_key(self, func: Callable):
        bound_func = getattr(func, "__func__", None)
        if bound_func is not None:
            return (
                "func_interval",
                "bound_method",
                id(getattr(func, "__self__", None)),
                bound_func,
            )

        code = getattr(func, "__code__", None)
        if code is not None:
            return ("func_interval", "code", code, None)

        try:
            hash(func)
        except TypeError:
            return ("func_interval", "id", id(func), None)
        return ("func_interval", "callable", func, None)

    def run_with_interval(
        self,
        func: Callable,
        interval: float,
        *args,
        action_name=None,
        **kwargs,
    ) -> Any:
        """按函数自己的时间间隔执行，未到间隔时返回 False。"""
        action_name = action_name or self._get_interval_func_key(func)
        if not self._check_action_interval(action_name, interval):
            return False
        return func(*args, **kwargs)

    def operate(self, func: Callable, block=True, restore_cursor=True):
        from src.interaction.NTEInteraction import NTEInteraction

        if isinstance(self.executor.interaction, NTEInteraction):
            return self.executor.interaction.operate(func, block, restore_cursor=restore_cursor)
        else:
            return func()

    def move_mouse_relative(self, dx, dy):
        from src.interaction.NTEInteraction import NTEInteraction

        if isinstance(self.executor.interaction, NTEInteraction):
            self.bring_to_front(after_sleep=1)
            return self.executor.interaction.move_mouse_relative(dx, dy)

    def get_char_box(self, index: int):
        box = self.get_box_by_name(f"box_char_{index + 1}")
        if self._char_ui_offset:
            box = self._shift_char_ui_box(box)
        return box

    def is_char_at_index(self, index, threshold=0.5, frame=None, char_count=None):
        detection = self._get_current_char_detection(frame=frame, char_count=char_count)
        score = detection.scores[index]
        new = f"idx {index} conf {score:.3f} {detection.reason}"
        if detection.accepted and detection.index == index and score < threshold:
            self.info_set("current char", new)
            return True
        self.run_with_interval(lambda: self.info_set("current char", new), 0.5)

    def is_in_team(self, frame=None) -> Box | None:
        frame = self.frame if frame is None else frame
        if frame is None:
            self.log_warning("Received an empty or None frame. Skipping...")
            time.sleep(1)
            return
        box = self.find_one(
            Labels.health_bar_slash,
            mask_function=iu.mask_corners,
            horizontal_variance=0.01,
            vertical_variance=0.005,
            frame=frame,
        )
        # self.log_debug(f"is_in_team {box}")
        return box

    def in_team(self):
        if not self.is_in_team():
            return False, -1, 0

        if self.scene is not None:
            state, timestamp = self.scene.get_is_in_team_record()
            if state and (to_sleep := 0.5 - (time.time() - timestamp)) > 0:
                self.sleep(to_sleep)

        arr = self._update_char_ui_offset()

        # self.log_debug(f"in_team {arr}")
        exist_count = 0
        for i in range(len(arr)):
            if arr[i] is not None:
                exist_count += 1

        if exist_count == 0:
            self.log_warning("in_team exist_count is 0")
            return False, -1, 0

        current = self.get_current_char_index(char_count=exist_count)
        if current != -1 and safe_get(arr, current) is None:
            exist_count += 1

        if current == -1:
            self.log_warning("in_team not found current char")
            return False, -1, 0

        self.scene.set_logged_in()
        return True, current, exist_count

    def in_world(self) -> bool:
        res = self.check_mini_map_arrow()
        return len(res) == 1

    def check_mini_map_arrow(self) -> list[dict]:
        frame = self.frame
        box = self.box_of_screen(0.0691, 0.1083, 0.0949, 0.1493, name="in_world")
        # now = time.time()
        res, _ = self.find_rotated_template(
            Labels.mini_map_arrow,
            box=box,
            frame=frame,
            threshold=0.75,
            template_angle=15.5,
            frame_processor=lambda cropped: iu.binarize_bgr_by_brightness(
                cropped, threshold=200, to_bgr=False
            ),
        )
        # self.log_debug(f"in_world {res}, cost {time.time() - now} ms")
        return res

    def in_team_and_world(self):
        in_team = self.is_in_team()
        in_world = self.in_world()
        return in_team and in_world

    def wait_in_team(
        self, time_out=30, raise_if_not_found=True, esc=False, settle_time=0
    ) -> Box | None:
        success = self.wait_until(
            self.is_in_team,
            time_out=time_out,
            raise_if_not_found=False,
            post_action=lambda: self.back(after_sleep=2) if esc else None,
            settle_time=settle_time,
        )
        if success:
            self.sleep(0.1)
        elif raise_if_not_found:
            msg = self.tr(MSG_MAIN_DETECTION_FAILED)
            self.log_error(msg, notify=True)
            raise CannotFindException(msg)
        return success

    def wait_in_team_and_world(self, time_out=30, raise_if_not_found=True, esc=False):
        success = self.wait_until(
            self.in_team_and_world,
            time_out=time_out,
            raise_if_not_found=False,
            post_action=lambda: self.back(after_sleep=2) if esc else None,
        )
        if success:
            self.sleep(0.1)
        elif raise_if_not_found:
            msg = self.tr(MSG_MAIN_DETECTION_FAILED)
            msg += "\n" + self.tr(MSG_WORLD_DETECTION_FAILED)
            self.log_error(msg, notify=True)
            raise CannotFindException(msg)
        return success

    def set_pynput_interaction(self):
        self.bring_to_front()
        self.set_interaction(1)

    def set_post_interaction(self):
        self.set_interaction(0)

    def set_interaction(self, idx=0):
        """
        通过索引 (idx) 设置交互方法。
        会从配置的交互列表中读取指定索引的方法。
        """

        def get_name(m):
            return getattr(m, "__name__", str(m))

        methods: list = og.device_manager.windows_capture_config.get("interaction", [])
        available_options = [get_name(m) for m in methods]

        m = safe_get(methods, idx)
        if m is None:
            self.log_error(
                f"无法设置交互方式：索引 {idx} 越界。当前可用选择有: {available_options}"
            )
            return
        og.device_manager.set_interaction(m)
        self.log_info(f"已切换交互式方式: {get_name(m)}")

    @property
    def interac_box(self):
        interac_box = self.get_box_by_name(Labels.interactable)
        interac_box = interac_box.copy(
            x_offset=-interac_box.width * 0.3,
            y_offset=-interac_box.height * 2.5,
            width_offset=interac_box.width * 0.6,
            height_offset=interac_box.height * 5,
            name="search_interac",
        )
        return interac_box

    def find_interac(self):
        return self.find_one(
            Labels.interactable,
            box=self.interac_box,
            threshold=0.7,
            mask_function=interac_mask,
            use_gray_scale=True,
        )

    def walk_until_interac(self, direction="w", time_out=10, raise_if_not_found=False):
        ret = False
        try:
            self.middle_click(after_sleep=0.2)
            self.send_key_down(direction)
            ret = bool(
                self.wait_until(
                    self.find_interac,
                    time_out=time_out,
                    raise_if_not_found=raise_if_not_found,
                )
            )
        finally:
            self.send_key_up(direction)
        return ret

    def find_traval_button(self):
        box = self.get_box_by_name(Labels.teleport)
        w = box.width - (box.x - self.width_of_screen(0.99))
        y = -box.width * 0.2
        box = box.copy(y_offset=y, width_offset=w, height_offset=-y)
        return self.find_one(Labels.teleport, box=box)

    def _execute_map_teleport(self, find_func, zoom="mid", time_out=5):
        """
        通用的地图传送核心逻辑
        :param find_func: 查找传送点的具体策略函数
        :param time_out: 查找超时时间
        :param action_name: 动作名称（用于日志和点击操作）
        """
        # 1. 确保在主界面并打开地图
        self.ensure_main()
        self.wait_until(
            lambda: self.find_one(Labels.map_city_tycoon_activities),
            time_out=10,
            pre_action=lambda: self.send_key("m", interval=2),
            raise_if_not_found=True,
        )

        if zoom is not None:
            self.sleep(0.5)
            if isinstance(zoom, str):
                if zoom == "max":
                    self.operate_click(0.050, 0.372)
                elif zoom == "mid":
                    self.operate_click(0.050, 0.525)
                elif zoom == "min":
                    self.operate_click(0.050, 0.680)
            elif callable(zoom):
                zoom()
            self.sleep(0.5)

        # 2. 执行传入的自定义查找策略
        teleport = self.wait_until(find_func, time_out=time_out, raise_if_not_found=True)
        self.log_info(f"found map teleport {teleport}")

        # 3. 点击传送点并执行传送(Travel)
        self.operate_click(teleport)
        if not self.wait_feature(Labels.close_button, threshold=0.8, time_out=1):
            box = self.box_of_screen(0.578, 0.426, 0.607, 0.580)
            to_find = [Labels.map_big_teleport, Labels.map_small_teleport]
            max_conf = 0
            max_box = None
            for feature_name in to_find:
                feature = self.find_sift_feature(feature_name, box=box)
                if feature and feature.confidence > max_conf:
                    max_conf = feature.confidence
                    max_box = feature
            if max_box:
                self.operate_click(box, after_sleep=1)
        self.click_traval_button()

        return teleport

    def click_nearest_map_teleport(self, zoom="mid", threshold=0.7, time_out=5):
        """
        从屏幕中心向外延展搜索，点击最近的传送点
        """
        to_find = [Labels.map_big_teleport, Labels.map_small_teleport]
        template_boxes = [self.get_box_by_name(label) for label in to_find]
        max_template_size = max(
            max(template_box.width, template_box.height) for template_box in template_boxes
        )
        step = max(max_template_size, self.width_of_screen(0.02), 1)
        center_x = self.width_of_screen(0.5)
        center_y = self.height_of_screen(0.5)
        max_radius = max(self.width, self.height)

        # 定义中心向外扩展的查找策略
        def find_nearest_teleport():
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

        # 调用通用方法
        return self._execute_map_teleport(
            find_func=find_nearest_teleport, zoom=zoom, time_out=time_out
        )

    def click_map_teleport(self, box, zoom="mid", threshold=0.7, time_out=5):
        """
        在指定的 box 区域内搜索并点击传送点
        :param box: Box 对象，指定查找的固定范围
        """
        to_find = [Labels.map_big_teleport, Labels.map_small_teleport]

        # 定义特定区域的查找策略
        def find_teleport_in_box():
            return self.find_best_match_in_box(box, to_find, threshold=threshold)

        # 调用通用方法
        return self._execute_map_teleport(
            find_func=find_teleport_in_box, zoom=zoom, time_out=time_out
        )

    def click_traval_button(self, travel_btn=None, raise_if_not_found=True):
        if not isinstance(travel_btn, Box):
            travel_btn = self.wait_until(
                self.find_traval_button, time_out=10, raise_if_not_found=raise_if_not_found
            )
        if not travel_btn:
            return False
        self.sleep(0.1)
        self.wait_until(
            lambda: not self.find_traval_button(),
            pre_action=lambda: self.operate_click(travel_btn, interval=2),
            time_out=20,
            settle_time=0.5,
            raise_if_not_found=raise_if_not_found,
        )
        self.sleep(0.1)
        return True

    def openF1panel(self):
        if hasattr(self, "reset_to_false"):
            self.reset_to_false()

        def action():
            if self.is_in_team():
                self.send_key("f1", after_sleep=1)
                self.log_info("send f1 key to open the panel")
            return self.wait_panel(Labels.f1_panel)

        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("can't find panel, make sure f1 is the hotkey for panel", notify=True)
            raise CannotFindException("can't find panel, make sure f1 is the hotkey for panel")
        self.sleep(0.5)
        return result

    def openF2panel(self):
        if hasattr(self, "reset_to_false"):
            self.reset_to_false()

        def action():
            if self.is_in_team():
                self.send_key("f2", after_sleep=1)
                self.log_info("send f2 key to open the panel")
            return self.wait_panel(Labels.f2_panel)

        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("can't find panel, make sure f2 is the hotkey for panel", notify=True)
            raise CannotFindException("can't find panel, make sure f2 is the hotkey for panel")
        self.sleep(0.5)
        return result

    def openF5panel(self):
        if hasattr(self, "reset_to_false"):
            self.reset_to_false()

        def action():
            if self.is_in_team():
                self.send_key("f5", after_sleep=1)
                self.log_info("send f5 key to open the panel")
            return self.wait_panel(Labels.f5_panel)

        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("can't find panel, make sure f5 is the hotkey for panel", notify=True)
            raise CannotFindException("can't find panel, make sure f5 is the hotkey for panel")
        self.sleep(1.5)
        return result

    def openESCpanel(self):
        if hasattr(self, "reset_to_false"):
            self.reset_to_false()

        def action():
            if self.is_in_team():
                self.send_key("esc", after_sleep=1)
                self.log_info("send esc key to open the panel")
            return self.wait_panel(Labels.esc_option, box=Labels.box_all_esc_options, threshold=0.3)

        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("can't find panel, make sure esc is the hotkey for panel", notify=True)
            raise CannotFindException("can't find panel, make sure esc is the hotkey for panel")
        self.sleep(0.5)
        return result

    def wait_panel(self, feature, box=None, threshold=0.7, time_out=5):
        result = self.wait_until(
            lambda: self.find_one(feature, box=box, threshold=threshold),
            time_out=time_out,
            settle_time=0.5,
        )
        logger.info(f"found {feature} {result}")
        return result

    def ensure_main(self, esc=True, in_world=False, time_out=30):
        self.info_set("current task", f"wait main esc={esc}")
        if not self.scene.logged_in():
            time_out = 600
        if not self.wait_until(
            lambda: self.is_main(esc=esc, in_world=in_world),
            time_out=time_out,
            raise_if_not_found=False,
            settle_time=0.25,
        ):
            msg = self.tr(MSG_MAIN_DETECTION_FAILED)
            if in_world:
                msg += "\n" + self.tr(MSG_WORLD_DETECTION_FAILED)
            self.log_error(msg, notify=True)
            raise CannotFindException(msg)
        self.sleep(0.5)
        self.info_set("current task", None)

    def is_main(self, esc=True, in_world=True):
        in_team_or_world = False
        if in_world:
            in_team_or_world = bool(self.in_team_and_world())
        else:
            in_team_or_world = bool(self.is_in_team())
        if in_team_or_world:
            self.scene.set_logged_in()
            return True
        if self.handle_monthly_card(log=False):
            return True
        if self.wait_login():
            return True
        if esc:
            self.send_key("esc", action_name="is_main", interval=2)

    def find_monthly_card(self):
        return self.find_one(Labels.monthly_card)

    def check_monthly_card(self):
        if self.next_monthly_card_start > 0:
            if (
                0 < time.time() - self.next_monthly_card_start < 120
                and not self.is_in_team()
                and self.find_monthly_card()
            ):
                return True
        return False

    def handle_monthly_card(self, log=True):
        monthly_card = self.find_monthly_card()
        if monthly_card is not None:
            self.log_info("monthly_card found click")
            deadline = time.time() + 20
            settle = -1
            while time.time() < deadline:
                if self.find_monthly_card():
                    self.operate_click(0.50, 0.89, after_sleep=2)
                    settle = -1
                    continue
                if self.find_one(Labels.reward_popup):
                    self.send_key("esc", after_sleep=2)
                    settle = -1
                    continue
                if settle < 0:
                    settle = time.time()
                elif time.time() - settle > 2:
                    break
            else:
                raise WaitFailedException()
            self.set_check_monthly_card(next_day=True)
        elif log:
            self.log_warning_gated("monthly_card not found")
        return monthly_card is not None

    def set_check_monthly_card(self, next_day=False):
        if self.monthly_card_config.get("Check Monthly Card"):
            now = datetime.now()
            hour = self.monthly_card_config.get("Monthly Card Time")
            # Calculate the next 4 o'clock in the morning
            next_four_am = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now >= next_four_am or next_day:
                next_four_am += timedelta(days=1)
            next_monthly_card_start_date_time = next_four_am - timedelta(seconds=30)
            # Subtract 1 minute from the next 4 o'clock in the morning
            self.next_monthly_card_start = next_monthly_card_start_date_time.timestamp()
            logger.info(
                "set next monthly card start time to {}".format(next_monthly_card_start_date_time)
            )
        else:
            self.next_monthly_card_start = 0

    def wait_login(self):
        if not self.scene.logged_in():
            if self.is_in_team():
                return True
            self.handle_monthly_card(log=False)
            if self.find_one(Labels.login_setting):
                self.log_info("found login_setting, bring_to_front and click")
                if not self.is_foreground():
                    self.bring_to_front()
                    self.sleep(3)
                self.operate_click(0.499, 0.865, after_sleep=3)
                return False

    def back_to_login(self):
        self.ensure_main(in_world=False)
        box = self.box_of_screen(0.6352, 0.6125, 0.7168, 0.7083, hcenter=True)

        def action():
            self.openESCpanel()
            self.operate_click(*self.pos.panels.esc.back_to_login)
            self.sleep(0.5)
            return self.find_confirm(box=box)

        if self.retry_on_action(action, self.ensure_main):
            if self.wait_click_confirm(range=box):
                self.scene.set_logged_in(False)
                return True

    def find_treasure(self):
        # now = time.time()
        result = self.find_one(
            Labels.treasure,
            box=self.pos.screen.main_viewport.to_box(),
            threshold=0.7,
            use_gray_scale=True,
        )
        # if result:
        #     self.log_info(f"find_treasure conf {result.confidence}, cost {time.time() - now}s")
        return result

    def walk_to_treasure(self):
        if self.walk_to_box(
            self.find_treasure, end_condition=self.find_interac, y_offset=0.1, x_threshold=0.15
        ):
            return True

    def rotate_and_find_treasure(self):
        return self.rotate_and_find(self.find_treasure, self.find_interac)

    def rotate_and_find(self, find, interrupt):
        def sleep(sec):
            deadline = time.time() + sec
            while time.time() < deadline:
                if interrupt():
                    return True
                self.sleep(0.1)

        if result := find():
            return result
        if interrupt():
            return

        for _ in range(4):
            self.send_key("a")
            if sleep(0.3):
                return
            self.middle_click()
            if sleep(1):
                return
            if result := find():
                return result

    def send_interac(self, handle_claim=True):
        if self.find_interac():
            self.send_key("f", after_sleep=0.8)
            if not handle_claim:
                return True
            if not self.handle_claim_button():
                return True

    def handle_claim_button(self):
        while self.wait_until(self.has_claim, raise_if_not_found=False, time_out=1.5):
            self.sleep(0.5)
            self.send_key("esc")
            self.sleep(0.5)
            logger.info("handle_claim_button found a claim reward")
        return True

    def has_claim(self):
        return not self.is_in_team() and self.find_all_claim()

    def find_all_claim(self) -> List[Box]:
        box = self.box_of_screen(0.2645, 0.6167, 0.7352, 0.6785, name="reward_area")
        return self.find_feature(Labels.claim_icon, box=box)

    def get_stamina(self):
        def fix_stamina_ocr_slash(text):
            if len(text) < 4:
                return text

            numerator = text[:-4]
            maybe_slash = text[-4]
            denominator = text[-3:]

            if maybe_slash in ["1", "l", "|"]:
                return f"{numerator}/{denominator}"

            return text

        boxes = self.wait_ocr(0.814, 0.029, 0.898, 0.083, raise_if_not_found=False)
        if not boxes:
            self.screenshot("stamina_error")
            return -1
        current = 0
        for box in boxes:
            box.name = fix_stamina_ocr_slash(box.name)
            if match := stamina_re.search(box.name):
                current = int(match.group(1))
        self.info_set("当前体力", current)
        return current

    def retry_on_action(
        self,
        action: Callable,
        reset_action: Callable | None = None,
        attempt=3,
        raise_if_failed=False,
    ):
        result = None
        count = 0

        sig = inspect.signature(action)
        params = sig.parameters
        has_count_param = "count" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )

        while not result and count <= attempt:
            count += 1
            if has_count_param:
                result = action(count=count)  # 建议用关键字传参更安全
            else:
                result = action()
            if not result and reset_action is not None:
                reset_action()
        if raise_if_failed and not result:
            raise WaitFailedException()
        return result

    def wait_click_confirm(
        self,
        pre_action: Any | None = None,
        range: tuple[float, float, float, float] | Box | None = None,
        on_found: Any | None = None,
        time_out=10,
        settle_time=0.25,
        raise_if_not_found=True,
    ):
        if range is None:
            box = self.pos.screen.main_viewport.to_box()
        elif isinstance(range, Box):
            box = range
        else:
            box = self.box_of_screen(*range, hcenter=True)
        button = self.wait_until(
            lambda: self.find_confirm(box=box),
            pre_action=pre_action,
            time_out=time_out,
            settle_time=settle_time,
            raise_if_not_found=raise_if_not_found,
        )
        if not button:
            return False
        self.sleep(0.1)
        if callable(on_found):
            on_found()
            self.sleep(0.1)
        result = self.wait_until(
            lambda: not self.find_confirm(box=box),
            pre_action=lambda: self.operate_click(button, interval=2),
            time_out=time_out,
            settle_time=settle_time,
            raise_if_not_found=raise_if_not_found,
        )
        return bool(result)

    def find_confirm(self, box=None, threshold=0.7) -> Box:
        if not isinstance(box, Box):
            box = self.pos.screen.main_viewport.to_box()
        return self.find_best_match_in_box(
            box=box,
            to_find=[Labels.confirm_btn_1, Labels.confirm_btn_2],
            threshold=threshold,
            mask_function=confirm_mask,
        )

    def find_confirms(self, box=None, threshold=0.7) -> list[Box]:
        if not isinstance(box, Box):
            box = self.pos.screen.main_viewport.to_box()
        match_feature: list[list[Box]] = []
        for feature_name in [Labels.confirm_btn_1, Labels.confirm_btn_2]:
            features = self.find_feature(
                feature_name=feature_name,
                box=box,
                threshold=threshold,
                mask_function=confirm_mask,
            )
            if features:
                match_feature.append(features)

        return vu.suppress_boxes(match_feature)

    @staticmethod
    def get_app_locale() -> str | None:
        """get app locale."""

        try:
            return og.app.locale.name()
        except Exception:
            return None

    def is_chinese(self) -> bool:
        """判断当前应用语言是否为中文"""

        return "zh" in self.get_app_locale()

    def open_f1_domain_page(self):
        self.openF1panel()

        box = self.box_of_screen(0.785, 0.022, 0.814, 0.076, name="stamina_icon")
        self.wait_until(
            lambda: self.find_one(Labels.stamina_icon, box=box),
            pre_action=lambda: self.operate_click(*self.pos.panels.f1.domain, interval=0.5),
            settle_time=0.5,
            time_out=10,
        )

    def _get_health_box(self, frame):
        box = self.is_in_team(frame=frame)
        if box is None:
            return
        return box.copy(x_offset=box.width, width_offset=box.width * 3)

    def _get_health_snapshot(self, frame):
        """截取当前出场角色血条颜色快照，用于快速判断切人是否已经发生。"""

        health_box = self._get_health_box(frame)
        if health_box is None:
            return None
        cropped = health_box.crop_frame(frame)
        snapshot = iu.create_color_mask(cropped, char_health_color, to_bgr=False)
        return snapshot

    def is_health_changed(self, frame):
        """判断当前出场血条是否已经不同于切人前快照。"""

        def frame_processor(cv):
            return iu.create_color_mask(cv, char_health_color, to_bgr=False)

        snapshot = self.scene.health_snapshot()
        if snapshot is None:
            snapshot = self.scene.health_snapshot(image=self._get_health_snapshot(frame))
            return False if snapshot is not None else None

        health_box = self._get_health_box(frame)
        if health_box is None:
            return None
        health_box = health_box.scale(1.1)
        if self.find_one(
            "health_snapshot",
            template=snapshot,
            box=health_box,
            frame=frame,
            frame_processor=frame_processor,
            threshold=0.9,
        ):
            return False
        return True

    def run_and_check_changed(
        self,
        action,
        snap_box: Box,
        check_box: Box | None = None,
        after_sleep=0.25,
        threshold=0.85,
    ):
        if not callable(action):
            return
        if check_box is None:
            check_box = snap_box.scale(1.2)
        snapshot = snap_box.crop_frame(self.frame)
        action()
        self.sleep(after_sleep)
        if not self.find_one("snapshot", template=snapshot, box=check_box, threshold=threshold):
            return True

    def scroll_and_is_end(
        self,
        x,
        y,
        count,
        snap_box: Box,
        check_box: Box | None = None,
        after_sleep=0.25,
        threshold=0.85,
    ):
        return not self.run_and_check_changed(
            action=lambda: self.operate(
                lambda: self.scroll(x, y, count=count), block=True, restore_cursor=False
            ),
            snap_box=snap_box,
            check_box=check_box,
            after_sleep=after_sleep,
            threshold=threshold,
        )

    def find_exit(self, box=None):
        if box is None:
            box = self.box_of_screen(0.004, 0.012, 0.061, 0.097)
        return self.find_best_match_in_box(
            box,
            [Labels.exit_1, Labels.exit_2],
            threshold=0.7,
            mask_function=exit_mask,
        )

    def exit_anomaly(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.is_in_team():
                if not self.find_exit():
                    if self.wait_until(
                        lambda: self.is_in_team() and not self.find_exit(),
                        settle_time=0.5,
                        time_out=1,
                    ):
                        return True
                else:
                    self.send_key("esc")

            if self.wait_click_confirm(
                range=(0.619, 0.607, 0.709, 0.709),
                raise_if_not_found=False,
                time_out=1,
            ):
                self.sleep(2)


def interac_mask(image):
    mask = iu.create_color_mask(image, interac_pink_color, to_bgr=False)
    dilated_mask = iu.morphology_mask(mask, kernel_size=5, to_bgr=False)
    return dilated_mask


def confirm_mask(image):
    mask = iu.create_color_mask(image, text_black_color, to_bgr=False)
    dilated_mask = iu.morphology_mask(mask, kernel_size=5, to_bgr=False)
    return dilated_mask


def exit_mask(image):
    mask = iu.create_color_mask(image, exit_white_color, to_bgr=False)
    dilated_mask = iu.morphology_mask(mask, kernel_size=5, to_bgr=False)
    return dilated_mask


interac_pink_color = {
    "r": (197, 221),
    "g": (71, 78),
    "b": (119, 133),
}

exit_white_color = {
    "r": (226, 246),
    "g": (226, 246),
    "b": (227, 247),
}

char_health_color = {
    "r": (160, 210),
    "g": (160, 210),
    "b": (160, 210),
}
