import re
import time
from dataclasses import dataclass
from threading import Event

from ok import TaskDisabledException

from src import text_white_color
from src.combat.BaseCombatTask import BaseCombatTask
from src.heist_path.HeistEntrancePath import HeistEntrancePath
from src.heist_path.HeistPathA import HeistPathA
from src.heist_path.HeistPathB import HeistPathB
from src.heist_path.HeistPathC import HeistPathC
from src.Labels import Labels
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.tasks.trigger.SkipDialogTask import SkipDialogTask
from src.utils import game_filters as gf
from src.utils import image_utils as iu


class AbortException(Exception):
    pass


@dataclass
class CharacterSwitchState:
    role: str
    keys: list[str]
    index: int = 0
    deadline: float = 0

    @property
    def current_key(self):
        return self.keys[self.index]

    def advance(self):
        self.index += 1
        return self.index < len(self.keys)


def _inst_line(text: str, color: str = "", *, bold: bool = False, indent: int = 0):
    content = f"{'&nbsp;' * (indent * 4)}{text}"
    if bold:
        content = f"<strong>{content}</strong>"
    return f'<span style="color:{color};">{content}</span>'


def _inst_gap():
    return '<span style="font-size:4px;">&nbsp;</span>'


# ruff: disable[E501]
INST = "<br>".join(
    [
        _inst_line("📍 步骤起点：站在可互动小吱的位置开始", "#FF5555", bold=True),
        _inst_line("⚙️ 镜头设置", "#FF5555", bold=True),
        _inst_line("└─ 控制 ➔ 摄像机设置", "#FE821D", bold=True, indent=1),
        _inst_line("├─ 移动镜头修正：禁用", "#FE821D", bold=True, indent=2),
        _inst_line("└─ 按下锁定镜头回正：启用", "#FE821D", bold=True, indent=2),
        _inst_line("⚠️ 必备条件：至少有一个复活道具", "#FF5555", bold=True),
        _inst_line(
            "🥷 避战方式：翳【长按 Shift】/ 浔【长按攻击】/ 残虹【点按G】", "#FF5555", bold=True
        ),
        _inst_gap(),
        _inst_line("路径1推荐设置", bold=True),
        _inst_line("FPS: 60~120", indent=1),
        _inst_line("战斗角色: 主角 / 哈尼娅 / 浔", indent=1),
        _inst_line("跑图角色: 薄荷", indent=1),
        _inst_line("避战角色(可选): 翳 / 浔", indent=1),
        _inst_gap(),
        _inst_line("路径2推荐设置", bold=True),
        _inst_line("画质：性能 | 分辨率: 1080P | FPS: 60 | 插帧: 关闭", indent=1),
        _inst_line("跑图角色: 薄荷", indent=1),
        _inst_line("早雾避战：", indent=1),
        _inst_line(
            "战斗角色: 早雾（必须，战斗角色中最前，其他战斗角色随意，可塞安魂曲） / 主角 / 哈尼娅",
            indent=2,
        ),
        _inst_line("避战角色: 翳", indent=2),
        _inst_gap(),
        _inst_line("路径3推荐设置", bold=True),
        _inst_line("画质：性能 | 分辨率: 1080P | FPS: 60 | 插帧: 关闭", indent=1),
        _inst_line("跑图角色: 薄荷", indent=1),
        _inst_line("避战角色: 残虹(必须)", indent=1),
        _inst_line("战斗角色: 随意 (战斗角色随意，可塞安魂曲) / 主角 / 哈尼娅", indent=1),
    ]
)

EN_INST = "<br>".join(
    [
        _inst_line(
            "📍 Starting Point: Stand at the interactable Chiz to begin", "#FF5555", bold=True
        ),
        _inst_line("⚙️ Camera Settings", "#FF5555", bold=True),
        _inst_line("└─ Controls ➔ Camera Settings", "#FE821D", bold=True, indent=1),
        _inst_line("├─ Movement Camera Correction: Disabled", "#FE821D", bold=True, indent=2),
        _inst_line("└─ Press to Reset Camera: Enabled", "#FE821D", bold=True, indent=2),
        _inst_line("⚠️ Prerequisite: At least one revival item", "#FF5555", bold=True),
        _inst_line(
            "🥷 Avoid Combat: Skia [Hold Shift] / Hotori [Hold Attack]", "#FF5555", bold=True
        ),
        _inst_gap(),
        _inst_line("Recommended Settings for Route 1", bold=True),
        _inst_line("FPS: 60~120", indent=1),
        _inst_line("Combat Team: Zero / Haniel / Hotori", indent=1),
        _inst_line("Exploration: Mint", indent=1),
        _inst_line("Stealth (Optional): Skia / Hotori", indent=1),
        _inst_gap(),
        _inst_line("Recommended Settings for Route 2", bold=True),
        _inst_line(
            "Graphics: Performance | Resolution: 1080P | FPS: 60 | Frame Interpolation: Off",
            indent=1,
        ),
        _inst_line("Exploration: Mint", indent=1),
        _inst_line("Stealth (Sakiri):", indent=1),
        _inst_line(
            "Combat Team: Sakiri (Required, must be in 1st slot; others flexible, can include Lacrimosa) / Zero / Haniel",
            indent=2,
        ),
        _inst_line("Stealth Character: Skia", indent=2),
        _inst_gap(),
        _inst_line("Recommended Settings for Route 3", bold=True),
        _inst_line(
            "Graphics: Performance | Resolution: 1080P | FPS: 60 | Frame Interpolation: Off",
            indent=1,
        ),
        _inst_line("Exploration: Mint", indent=1),
        _inst_line("Stealth Character: Zankou (Required)", indent=1),
        _inst_line("Combat Team: Flexible (Any, can include Lacrimosa) / Zero / Haniel", indent=1),
    ]
)
# ruff: enable[E501]


class AutoHeistTask(NTEOneTimeTask, BaseCombatTask):
    CONF_PATH = "路径"
    CONF_FIGHTER = "战斗角色"
    CONF_RUNNER = "跑图角色"
    CONF_AVOIDER = "避战角色"
    CONF_AVOID_MTH = "避战方法"
    ROLE_FIGHTER = "fighter"
    ROLE_RUNNER = "runner"
    ROLE_AVOIDER = "avoider"
    AVOID_METHOD_DASH = "长按shift"
    AVOID_METHOD_ATTACK = "长按攻击"
    AVOID_METHOD_G = "点按G"
    LOCK_PICK_MATCH_THRESHOLD = 0.75
    DEFAULT_SLEEP_CHECK_INTERVAL = 1
    FAST_SLEEP_CHECK_INTERVAL = 0.1
    SWITCH_CHECK_DURATION = 1
    QUICK_PICK_START_DELAY = 0.3
    QUICK_PICK_INTERVAL = 0.2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动粉爪大劫案"
        self.group_name = "都市闲趣"
        _locale = self.get_app_locale()
        self.instructions = INST if _locale and "zh" in _locale else EN_INST
        self.paths = {
            "路径1(路线参考自B站UP: 早柚大魔王丶)": HeistPathA,
            "路径2(在路径1基础上优化了大厅到办公层的路线)": HeistPathB,
            "路径3(使用残虹避战，更加安全)": HeistPathC,
        }
        path_names = list(self.paths.keys())
        self.avoid_methods = [self.AVOID_METHOD_DASH, self.AVOID_METHOD_ATTACK, self.AVOID_METHOD_G]
        self.add_rounds_config()
        self.default_config.update(
            {
                self.CONF_PATH: path_names[0],
                self.CONF_FIGHTER: ["4", "1"],
                self.CONF_RUNNER: ["3"],
                self.CONF_AVOIDER: ["2"],
                self.CONF_AVOID_MTH: self.AVOID_METHOD_DASH,
            }
        )
        self.config_description.update(
            {
                self.CONF_PATH: "使用任何路径前请先阅读使用说明",
                self.CONF_FIGHTER: "选1~2个",
                self.CONF_RUNNER: "选1个",
                self.CONF_AVOIDER: "选0~1个",
            }
        )

        options = ["1", "2", "3", "4"]
        self.config_type.update(
            {
                self.CONF_PATH: {
                    "type": "drop_down",
                    "options": path_names,
                },
                self.CONF_AVOID_MTH: {
                    "type": "drop_down",
                    "options": self.avoid_methods,
                },
                self.CONF_FIGHTER: {"type": "multi_selection", "options": options},
                self.CONF_RUNNER: {"type": "multi_selection", "options": options},
                self.CONF_AVOIDER: {"type": "multi_selection", "options": options},
            }
        )
        self.sleep_check_interval = self.DEFAULT_SLEEP_CHECK_INTERVAL
        self._scroll_switch = False
        self._scroll_count = 0
        self._scroll_time = 0
        self._dead_fighter_keys = []
        self._current_fighter_key = None
        self._switch_state: CharacterSwitchState | None = None
        self._handling_switch_state = False
        self._quick_pick_event = Event()
        self._quick_pick_ready_at = 0
        self._held_keys = set()
        self._interaction_watch_active = False
        self._interaction_watch_found = False
        self._aborting_heist = False

    def run(self):
        super().run()
        try:
            return self._run_loop()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("自动粉爪大劫案出错", e)
            raise

    def _run_loop(self):
        self._check_setup()
        self._start_quick_pick_loop()
        self.start_rounds()
        self.info_set("总方斯获取数", 0)
        self.info_set("总粉爪币获取数", 0)

        while self.has_remaining_rounds():
            self.ensure_main()
            if not self._ensure_heist_entrance():
                self.next_frame()
                continue

            if not self.begin_round():
                continue
            self._prepare_round()

            self._run_heist_round()

            self.next_frame()

        self.finish_rounds()

    def _ensure_heist_entrance(self):
        if self.wait_until(self.find_interac, time_out=10, raise_if_not_found=False):
            return True

        if self.in_team_and_world():
            self._return_to_heist_entrance()
            return bool(self.wait_until(self.find_interac, time_out=20, raise_if_not_found=False))

        return False

    def _return_to_heist_entrance(self):
        self.log_info("当前在大世界中，将传送到粉爪总部附近的塔")
        self.click_nearest_map_teleport()
        self.wait_in_team(settle_time=1)
        try:
            HeistEntrancePath(self).run_path()
        except AbortException as e:
            self.log_warning(e)

    def _start_quick_pick_loop(self):
        self.log_info("quick_pick_loop start")
        self.submit_periodic_task(0.01, self._quick_pick_loop)

    def _prepare_round(self):
        self._dead_fighter_keys = []

    def _add_rewards_to_summary(self, earnfcash, earnpcoin):
        self.add_success()
        self.info_add("总方斯获取数", earnfcash)
        self.info_add("总粉爪币获取数", earnpcoin)

    def _run_heist_round(self):
        if not self.wait_until(self.find_interac, time_out=20, raise_if_not_found=True):
            return None

        self.enter_heist()
        if not self._wait_until_heist_loaded():
            self.abort_heist()
            return None

        try:
            self.run_path()
        except AbortException as e:
            self.log_error("路线终止", e)
            self.abort_heist()
            return None

        return self.exit_heist()

    def _wait_until_heist_loaded(self):
        skip_task = self.get_task_by_class(SkipDialogTask)
        self.wait_until(
            self.in_heist,
            post_action=lambda: skip_task.check_skip(),
            time_out=600,
        )
        self.wait_until(
            lambda: not self.in_heist(),
            time_out=60,
        )
        return self.wait_until(
            self.in_heist,
            time_out=60,
        )

    def sleep_check(self):
        if self._aborting_heist:
            return
        self._poll_interaction_watch()
        self._poll_character_switch()
        if self.check_monthly_card() and self.handle_monthly_card():
            raise AbortException("found monthly_card")

    def _update_sleep_check_interval(self):
        needs_fast_poll = (
            self._interaction_watch_active and not self._interaction_watch_found
        ) or self._switch_state is not None
        self.sleep_check_interval = (
            self.FAST_SLEEP_CHECK_INTERVAL if needs_fast_poll else self.DEFAULT_SLEEP_CHECK_INTERVAL
        )

    def start_interaction_watch(self):
        """开始后台交互点监视。

        路径里用于长距离移动或过机关时提前开监视；后续必须调用
        `stop_interaction_watch()` 校验期间是否出现过交互点。
        """
        self._interaction_watch_active = True
        self._interaction_watch_found = False
        self._update_sleep_check_interval()
        self.info_set("交互检查", "检查中")
        self.log_info("start interaction watch")

    def stop_interaction_watch(self):
        """结束后台交互点监视，并在未发现交互点时中断本轮路径。"""
        if not self._interaction_watch_active and not self._interaction_watch_found:
            self.log_warning("stop interaction watch ignored: not started")
            return False

        found = self._interaction_watch_found
        self._interaction_watch_active = False
        self._interaction_watch_found = False
        self._update_sleep_check_interval()
        self.info_set("交互检查", None)

        if not found:
            raise AbortException("not found interac")
        return found

    def _poll_interaction_watch(self):
        if not self._interaction_watch_active or self._interaction_watch_found:
            return self._interaction_watch_found
        if self.find_interac():
            self._interaction_watch_found = True
            self._interaction_watch_active = False
            self._update_sleep_check_interval()
            self.info_set("交互检查", "已找到")
            self.log_info("interaction watch succeeded")
        return self._interaction_watch_found

    def _reset_route_checks(self):
        self._switch_state = None
        self._handling_switch_state = False
        self._interaction_watch_active = False
        self._interaction_watch_found = False
        self._update_sleep_check_interval()
        self.info_set("交互检查", None)

    def _begin_character_switch(self, role, keys, check_switched=False):
        if not keys:
            raise AbortException(f"{role} {keys} dead or empty")

        self._switch_state = CharacterSwitchState(role=role, keys=keys)
        self._update_sleep_check_interval()
        key = self._send_current_switch_key()
        if check_switched:
            return self._wait_character_switch_success(role, key)
        return key

    def _send_current_switch_key(self):
        state = self._switch_state
        if state is None:
            return None
        key = state.current_key
        if state.role == self.ROLE_FIGHTER:
            self._current_fighter_key = key
        state.deadline = time.time() + self.SWITCH_CHECK_DURATION
        self.send_key(key)
        return key

    def _wait_character_switch_success(self, role, key):
        last_key = key

        def get_switch_key():
            state = self._switch_state
            return state.current_key if state is not None else last_key

        def is_switched():
            nonlocal last_key
            state = self._switch_state
            if state is not None:
                last_key = state.current_key
            return self.is_char_at_index(int(last_key) - 1)

        def switch_action():
            self.send_key(get_switch_key(), action_name="switch_char", interval=0.5)

        if not self.wait_until(is_switched, pre_action=switch_action, time_out=10):
            self._switch_state = None
            self._update_sleep_check_interval()
            self.screenshot("character_switch_failed")
            raise AbortException(f"{role} switch to {last_key} failed")

        state = self._switch_state
        if state is not None:
            last_key = state.current_key
        self._switch_state = None
        self._update_sleep_check_interval()
        return last_key

    def _poll_character_switch(self):
        if self._switch_state is None or self._handling_switch_state:
            return

        state = self._switch_state
        if time.time() > state.deadline:
            self._switch_state = None
            self._update_sleep_check_interval()
            return

        if self.is_in_team():
            return

        self._handling_switch_state = True
        try:
            role = state.role
            key = state.current_key
            self.log_info(f"{role} char {key} is dead")
            if role == self.ROLE_FIGHTER and key not in self._dead_fighter_keys:
                self._dead_fighter_keys.append(key)
            self.ensure_in_team()

            if not state.advance():
                self._switch_state = None
                self._update_sleep_check_interval()
                self.screenshot("character_switch_failed")
                raise AbortException(f"{role} {state.keys} dead or empty")
            self._send_current_switch_key()
        finally:
            self._handling_switch_state = False

    def get_heist_rewards(self):
        cash = self.ocr(
            0.359, 0.595, 0.500, 0.642, frame_processor=gf.isolate_black_text, name="cash"
        )
        coin = self.ocr(
            0.654, 0.595, 0.789, 0.641, frame_processor=gf.isolate_black_text, name="coin"
        )
        return self.parse_ocr_number(cash), self.parse_ocr_number(coin)

    def abort_heist(self):
        self.log_round_info("出现异常，将退出粉爪副本")
        self.add_failed()
        self._aborting_heist = True
        self._reset_route_checks()

        try:
            self.ensure_main(in_world=False)

            if self.is_in_team_outside_heist():
                self.log_round_info("当前已在队伍界面且不在粉爪副本中，跳过退出副本")
                return True

            if not self.wait_click_confirm(
                lambda: self.send_key("esc", action_name="quit_heist", interval=2),
                range=(0.6465, 0.6125, 0.7047, 0.7049),
                time_out=60,
                raise_if_not_found=False,
            ):
                self.log_round_info("退出粉爪副本超时，跳过本轮清理")
                return False

            self.log_round_info("已退出粉爪副本")
            return True
        finally:
            self._aborting_heist = False
            self._update_sleep_check_interval()

    # 进入粉爪副本
    def enter_heist(self):
        skip_task = self.get_task_by_class(SkipDialogTask)
        box = self.box_of_screen(0.916, 0.083, 0.941, 0.128, hcenter=True)

        def in_panel():
            return self.find_one(Labels.close_button, box=box)

        def action():
            if not self.is_in_team():
                self.operate_click(0.727, 0.471, action_name="enter_heist", interval=2)
                self.sleep(0.1)
                skip_task.check_skip()
            else:
                self.send_key("f", action_name="enter_heist", interval=1)

        self.wait_until(
            in_panel,
            pre_action=action,
            time_out=20,
        )
        self.sleep(0.5)
        self.wait_until(
            lambda: not in_panel(),
            pre_action=lambda: self.operate_click(0.7734, 0.8824, interval=1),
            time_out=20,
        )
        self.sleep(0.5)

    def has_extract_panel(self):
        """检查当前画面是否出现“安全撤离”面板。"""
        return self.find_one(Labels.heist_exit_panel, threshold=0.8)

    def is_in_team_outside_heist(self):
        """判断角色已回到队伍界面，但已经不在粉爪副本内。"""
        return self.in_team_and_world() and not self.in_heist()

    # 离开粉爪副本
    def exit_heist(self):
        if not self.wait_until(
            self.has_extract_panel,
            pre_action=lambda: self.send_key("f", interval=1),
        ):
            if self.is_in_team():
                if self.in_heist():
                    self.log_round_info("未发现撤离面板且在粉爪副本中，离开副本")
                    self.abort_heist()
                else:
                    self.log_round_info("当前已在队伍界面且不在粉爪副本中，跳过离开副本")
                return False

        self.sleep(1)
        rewards = self.get_heist_rewards()
        if not self.wait_click_confirm(
            pre_action=lambda: self.operate_click(0.604, 0.701, interval=1),
            range=(0.5359, 0.8139, 0.5852, 0.9062),
            time_out=20,
            raise_if_not_found=False,
        ):
            self.log_round_info("点击撤离超时")
            self.abort_heist()
            return False
        self._add_rewards_to_summary(*rewards)
        self.sleep(1)
        self.wait_in_team(time_out=600)
        self.log_round_info("已离开粉爪副本")
        return True

    def send_key_down(self, key, after_sleep=0):
        """按住按键。

        在路径脚本中按住 `f` 会启动快速拾取循环：周期性发送 `f` 并轻微滚轮，
        直到 `send_key_up("f")` 或路径结束清理。
        """
        if key == "f":
            self._start_quick_pick()
            self._scroll_switch = False
            return
        self._held_keys.add(key)
        return super().send_key_down(key, after_sleep)

    def send_key_up(self, key, after_sleep=0):
        """松开按键；松开 `f` 会停止快速拾取循环。"""
        if key == "f":
            self._reset_quick_pick()
            return
        try:
            return super().send_key_up(key, after_sleep)
        finally:
            self._held_keys.discard(key)

    def _reset_quick_pick(self):
        self._quick_pick_event.clear()
        self._quick_pick_ready_at = 0

    def _start_quick_pick(self):
        if not self._quick_pick_event.is_set():
            self._quick_pick_ready_at = time.time() + self.QUICK_PICK_START_DELAY
        self._quick_pick_event.set()

    def _release_held_keys(self):
        held_keys = list(self._held_keys)
        self._held_keys.clear()
        for key in held_keys:
            try:
                super().send_key_up(key)
            except Exception as e:
                self.log_error(f"release held key {key} failed", e)

    def _quick_pick_loop(self):
        if self._should_stop_quick_pick_loop():
            return False
        if self.executor.paused:
            time.sleep(0.1)
            return

        if self._quick_pick_event.is_set() and time.time() >= self._quick_pick_ready_at:
            if self._check_action_interval("quick_pick", self.QUICK_PICK_INTERVAL):
                interaction = self.executor.interaction
                if interaction is not None:
                    interaction.send_key("f", 0.002)
            time.sleep(0.001)
            if self._should_stop_quick_pick_loop():
                return False
            self._alternate_scroll(interval=self.QUICK_PICK_INTERVAL)

    def _should_stop_quick_pick_loop(self):
        if not self.enabled or not self.running:
            self.log_info("quick_pick_loop stop")
            return True
        return False

    def _alternate_scroll(self, interval=0):
        if time.time() - self._scroll_time >= interval:
            time.sleep(0.01)
            if self._should_stop_quick_pick_loop():
                return False
            interaction = self.executor.interaction
            if interaction is None:
                return False
            if self._scroll_switch:
                interaction.scroll(0, 0, 1)
            else:
                interaction.scroll(0, 0, -1)
            self._scroll_time = time.time()
            self._scroll_count += 1
            if self._scroll_count >= 3:
                self._scroll_count = 0
                self._scroll_switch = not self._scroll_switch

    def run_path(self):
        path_cls = self._get_path_cls()
        path = path_cls(self)
        failed = False
        try:
            path.run_path()
        except Exception:
            failed = True
            raise
        finally:
            self._release_held_keys()
            self._reset_quick_pick()
            if self._interaction_watch_active:
                if failed:
                    self._reset_route_checks()
                else:
                    self.stop_interaction_watch()

    def ensure_in_team(self):
        """等待回到队伍界面；等待期间会周期性按 `esc` 关闭可能的面板。"""
        self.wait_until(
            self.is_in_team,
            pre_action=lambda: self.send_key("esc", action_name="ensure_in_team", interval=2),
        )

    def check_current_floor(self, floor=1):
        """检查左上角楼层是否为指定 LG 楼层，不匹配则中断路径。"""
        floor_str = "LG" + str(floor)
        ret = self.wait_ocr(0.04, 0.235, 0.11, 0.275, match=re.compile("LG.*"), time_out=10)
        if ret and floor_str in ret[0].name:
            return
        raise AbortException(f"not in floor {floor}")

    def in_heist(self):
        """判断当前是否在粉爪副本内：需要队伍 UI 和副本计时器同时存在。"""
        return self.is_in_team() and self.find_one(Labels.heist_timer)

    def switch_to_fighter(self, check_switched=False):
        """切换到可用战斗角色。

        会按配置中的战斗角色顺序尝试，并跳过本轮已判定死亡的角色。
        `check_switched=True` 时会校验当前角色头像已切换到目标位置。
        返回当前发送的角色键位。
        """
        keys = list(self.config.get(self.CONF_FIGHTER, []))
        dead_keys = set(self._dead_fighter_keys)
        keys = [item for item in keys if item not in dead_keys]
        return self._begin_character_switch(self.ROLE_FIGHTER, keys, check_switched)

    def switch_to_runner(self, check_switched=False):
        """切换到跑图角色；若角色死亡或配置为空会中断路径。

        `check_switched=True` 时会校验当前角色头像已切换到目标位置。
        """
        keys = self.config.get(self.CONF_RUNNER, [])
        return self._begin_character_switch(self.ROLE_RUNNER, keys, check_switched)

    def avoider_strategy_index(self):
        """返回避战策略索引。

        `-1` 表示未配置避战角色，路径应走无避战角色的路线\n
        `0` 表示长按 shift, `1` 表示长按攻击, `2`表示点按G
        """
        keys = self.config.get(self.CONF_AVOIDER, [])
        if not keys:
            return -1
        method_name = self.config.get(self.CONF_AVOID_MTH)
        if method_name not in self.avoid_methods:
            self.log_warning(f"unknown avoid method {method_name}, use {self.AVOID_METHOD_DASH}")
            return 0
        return self.avoid_methods.index(method_name)

    def switch_to_avoider(self, check_switched=False):
        """切换到避战角色；未配置时只记录日志并返回。

        `check_switched=True` 时会校验当前角色头像已切换到目标位置。
        """
        keys = self.config.get(self.CONF_AVOIDER, [])
        if not keys:
            self.log_info("no avoider")
            return
        return self._begin_character_switch(self.ROLE_AVOIDER, keys, check_switched)

    def perform_avoidance_action(self):
        """按当前配置执行一次避战动作。

        长按 shift 会短暂按住 `w + lshift`；长按攻击会长按鼠标左键。
        """
        method_name = self.config.get(self.CONF_AVOID_MTH)
        if method_name == self.AVOID_METHOD_DASH:
            self.send_key_down("w")
            self.sleep(0.1)
            self.send_key_down("lshift")
            self.sleep(1.0)
            self.send_key_up("lshift")
            self.sleep(0.1)
            self.send_key_up("w")
        elif method_name == self.AVOID_METHOD_ATTACK:
            self.click(down_time=0.6)
        elif method_name == self.AVOID_METHOD_G:
            self.send_key("g", down_time=1)

    def clear_current_combat(self):
        """处理并等待当前小战斗结束。

        会切到战斗角色、攻击直到红色血条消失，再切回跑图角色。
        若战斗超时或所有战斗角色不可用会中断路径。
        """
        _key = self.switch_to_fighter(check_switched=True)
        self.wait_until(self.has_health_bar)
        deadline = time.time() + 60
        settle = -1
        in_team_settle = -1
        while time.time() < deadline:
            self.send_key_down("space")
            self.sleep(0.05)
            self.mouse_down()
            self.sleep(0.05)
            self.send_key_up("space")
            self.sleep(0.05)
            self.mouse_up()
            self.sleep(0.50)
            if not self.is_in_team():
                if in_team_settle < 0:
                    in_team_settle = time.time()
                if time.time() - in_team_settle > 1:
                    self.log_info(f"fighter {_key} dead, try next")
                    self._dead_fighter_keys.append(_key)
                    self.ensure_in_team()
                    _key = self.switch_to_fighter()
            else:
                in_team_settle = -1
                _key = self._current_fighter_key or _key
            self.send_key_down(_key)
            self.sleep(0.02)
            self.send_key_up(_key)
            self.next_frame()
            if not self._find_red_health_bar(10):
                if settle < 0:
                    settle = time.time()
                if time.time() - settle > 2:
                    break
            else:
                settle = -1
        else:
            raise AbortException("timeout for clear_current_combat")
        self.switch_to_runner(check_switched=True)

    def wait_and_interact(
        self,
        direction=None,
        interact=True,
        key_up_sleep=0.7,
        is_lock=False,
        time_out=10,
        raise_timeout=False,
    ):
        """等待交互点并可选择按 `f` 交互。

        `direction` 不为空时会先松开该方向键再交互，避免移动导致错过交互。
        `interact=False` 只等待交互点出现。
        `is_lock=True` 会额外等待撬锁 UI 出现并结束。
        """
        ret = self.wait_until(self.find_interac, time_out=time_out)
        if interact and direction is not None:
            self.send_key_up(direction)
            self.sleep(key_up_sleep)
        if not ret:
            if raise_timeout:
                raise AbortException("timeout for wait_and_interact")
            else:
                return False
        elif not interact:
            return True
        self.wait_until(
            lambda: not self.find_interac(), pre_action=lambda: self.send_key("f", interval=1)
        )
        if is_lock:
            self.wait_until(self.is_lock_pick_active, settle_time=0.5, time_out=2)
            self.wait_until(lambda: not self.is_lock_pick_active(), settle_time=0.5)
            return not self.find_interac()
        return True

    def loot_safes_while_walking(
        self, direction=None, min_walk_time=0, time_out=10, hold=False, send_pick=False
    ):
        """边移动边处理沿途保险箱。

        发现撬锁交互提示时会暂停移动，等待撬锁完成后继续走。
        `min_walk_time` 表示开始后至少移动多久再处理撬锁提示/启动 `send_pick`。
        `hold=True` 表示结束时保留方向键按下状态，交给后续路径处理。
        """
        start_time = time.time()
        deadline = start_time + time_out
        earliest_lock_pick_time = start_time + min_walk_time
        pick_started = False

        def wait_until_pick_time():
            nonlocal pick_started
            remaining = earliest_lock_pick_time - time.time()
            if remaining > 0:
                self.sleep(remaining)
            if send_pick and not pick_started:
                self.send_key_down("f")
                pick_started = True

        if direction is not None:
            self.send_key_down(direction)
        while time.time() < deadline:
            now = time.time()
            if send_pick and not pick_started and now >= earliest_lock_pick_time:
                self.send_key_down("f")
                pick_started = True
            if self.find_one(Labels.heist_interac_lock_pick, vertical_variance=0.05):
                if now < earliest_lock_pick_time:
                    wait_until_pick_time()
                lock_pick = time.time()
                if direction is not None:
                    self.send_key_up(direction)
                self.wait_until(self.is_lock_pick_active, settle_time=0.5)
                self.wait_until(lambda: not self.is_lock_pick_active(), settle_time=0.5)
                self.sleep(0.50)
                deadline += time.time() - lock_pick
                if direction is not None:
                    self.send_key_down(direction)
            self.next_frame()
        if direction is not None and not hold:
            self.send_key_up(direction)

    def wait_for_safe_loot(self, time_out=10, raise_timeout=False):
        """等待当前保险箱撬锁/拾取完成。

        `raise_timeout=True` 时超时会中断路径；否则超时只安静返回。
        """
        deadline = time.time() + time_out
        while time.time() < deadline:
            if self.find_one(Labels.heist_interac_lock_pick, vertical_variance=0.05):
                self.wait_until(self.is_lock_pick_active, settle_time=0.5)
            if self.is_lock_pick_active():
                self.wait_until(lambda: not self.is_lock_pick_active(), settle_time=0.5)
                self.sleep(0.50)
                break
            self.next_frame()
        else:
            if raise_timeout:
                raise AbortException("timeout for wait_for_safe_loot")

    def is_lock_pick_active(self):
        """检查撬锁转盘 UI 是否正在显示。"""
        box = self.get_box_by_name(Labels.heist_lock_pick).scale(1.5)
        res, _ = self.find_rotated_template(
            Labels.heist_lock_pick,
            box=box,
            threshold=self.LOCK_PICK_MATCH_THRESHOLD,
            frame_processor=lambda cropped: iu.create_color_mask(cropped, text_white_color),
        )
        return len(res) == 1

    def try_open_exit(self, direction=None):
        """尝试打开当前出口并返回是否可撤离。

        会等待出口交互点、按 `f` 打开面板；如果出现撤离面板，说明该出口可用，
        随后会按 `esc` 回到队伍界面。
        """
        if self.wait_until(self.find_interac):
            if direction is not None:
                self.send_key_up(direction)
                self.sleep(0.30)
            ret = None
            if self.wait_until(
                lambda: not self.is_in_team(),
                pre_action=lambda: self.send_key("f", interval=2),
                time_out=2,
            ):
                ret = self.wait_until(
                    self.has_extract_panel,
                    time_out=2,
                )
                if ret:
                    self.sleep(0.30)
                    self.ensure_in_team()
            return ret
        else:
            raise AbortException("not found exit interaction")

    def walk_until_extract_panel(self, direction=None, time_out=10):
        """沿指定方向移动并持续按 `f`，直到出现安全撤离面板。"""
        if direction is not None:
            self.send_key_down(direction)
        self.wait_until(
            self.has_extract_panel,
            pre_action=lambda: self.send_key("f", interval=0.25),
            time_out=time_out,
        )
        if direction is not None:
            self.send_key_up(direction)

    def wait_team_ui_settle(self):
        self.wait_until(lambda: not self.is_in_team(), time_out=1)
        self.wait_in_team(settle_time=0.25)

    def sleep_send_key(self, time_out, key, interval=0.2):
        deadline = time.time() + time_out
        while time.time() < deadline:
            self.send_key(key, interval=interval)
            self.sleep(0.01)

    def _get_path_cls(self):
        path_name = self.config.get(self.CONF_PATH)
        return self.paths.get(path_name, next(iter(self.paths.values())))

    def _check_setup(self):
        idx = self.avoider_strategy_index()
        cls = self._get_path_cls()

        if cls is HeistPathC and idx != 2:
            msg = (
                "路径3避战方式配置错误"
                if self.is_chinese()
                else "Path 3 avoidance of combat configuration incorrect"
            )
            self.log_error(msg, notify=True)
            raise TaskDisabledException
