import re
from bisect import bisect_right
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from enum import Enum
from sys import exception
from traceback import walk_tb

import cv2
import numpy as np
from ok import Box, Logger, get_bounding_box, safe_get
from ok.task.exceptions import FinishedException, TaskDisabledException

from src import text_white_color
from src.char.BaseChar import BaseChar, Element
from src.char.core.CharFactory import get_char_by_id, get_char_by_impl_id, get_char_by_pos
from src.char.core.CharRegistry import char_registry
from src.char.custom.CustomCharManager import CustomCharManager
from src.combat.CombatCheck import CombatCheck
from src.combat.planner import CombatPlanner, SwitchDecision
from src.events import communicate
from src.Labels import Labels
from src.sound_trigger.SoundCombatContext import ACTION_UNSET, SoundCombatContext
from src.tasks.mixin.CharUIMixin import CharElementUIMixin
from src.utils import game_filters as gf
from src.utils import image_utils as iu
from src.utils.clock import TimePoint

logger = Logger.get_logger(__name__)
cd_regex = re.compile(r"\d{1,2}\.\d")


class NotInCombatException(Exception):
    """未处于战斗状态异常。"""

    pass


@dataclass
class SleepCheckSkip:
    """Checks stay disabled until a task enters its combat phase."""

    sound_combat_context: bool = True
    check_combat: bool = True

    @property
    def all(self) -> bool:
        return all(getattr(self, field.name) for field in fields(self))

    @all.setter
    def all(self, value: bool):
        for field in fields(self):
            setattr(self, field.name, value)


class TeamSurvivalStatus(Enum):
    NO_DEATHS = 1  # 无人死亡
    DEAD = 2  # 死亡
    WIPED = 3  # 团灭


@dataclass
class CombatSession:
    """一次实际战斗的生命周期状态。"""

    combat_start: TimePoint
    switch_enabled: bool = True
    use_ultimate: bool = True
    start_char: "BaseChar | None" = None
    first_engage_char: "BaseChar | None" = None
    first_engage_consumed: bool = False


class BaseCombatTask(CharElementUIMixin, CombatCheck):
    """基础战斗任务类，封装了游戏中角色自动化操作的通用逻辑。"""

    hot_key_verified = False  # 热键是否已验证
    FREEZE_DURATION_RETENTION_SECONDS = 20 * 60

    element_reactions = (
        "创生",
        "覆纹",
        "浊燃",
        "黯星",
        "浸染",
        "延滞",
    )

    element_ring = (
        Element.WHITE,
        Element.GREEN,
        Element.RED,
        Element.PURPLE,
        Element.BLUE,
        Element.YELLOW,
    )
    element_ring_index = {element: index for index, element in enumerate(element_ring)}

    def __init__(self, *args, **kwargs):
        """初始化战斗任务。

        Args:
            *args: 传递给父类的参数。
            **kwargs: 传递给父类的关键字参数。
        """
        super().__init__(*args, **kwargs)
        self.sleep_check_skip = SleepCheckSkip()
        self.sleep_check_interval = 0.1
        self.chars: list[BaseChar] = []
        self.mouse_pos = None  # 当前鼠标位置
        self._combat_session: CombatSession | None = None

        self.add_text_fix({"Ｅ": "e"})
        self.vibrate_chars_index: list[int] = []
        self.chars_slot_mat = [None, None, None, None]
        self.element_reaction_counts = {}
        self.combat_planner = CombatPlanner(self)
        self.clear_element_reactions()
        self.preheat_element_template_cache_async()
        CustomCharManager().preheat_feature_cache_async()

    @property
    def combat_session(self) -> CombatSession:
        """返回当前战斗会话, 必要时按默认策略创建。"""

        if self._combat_session is None:
            self._combat_session = CombatSession(combat_start=self.clock.now())
        return self._combat_session

    @combat_session.setter
    def combat_session(self, value: CombatSession | None) -> None:
        self._combat_session = value

    def begin_combat_session(self) -> CombatSession:
        """初始化本场战斗, 执行一次首切并记录实际首发角色。

        此方法是战斗开始阶段唯一的副作用入口。首发角色已记录时直接复用。
        Checks are enabled for a new session; repeated calls preserve temporary pauses.
        """

        session = self.combat_session
        if session.start_char is None:
            self.sleep_check_skip.all = False
            session.combat_start = self.clock.now()
            self.click(after_sleep=0.25)
            self.switch_to_combat_start_char()
            session.start_char = self.get_current_char(raise_exception=False)
            logger.info(f"combat session started, start char: {session.start_char}")
        return session

    def record_first_engage(self, char: "BaseChar") -> None:
        """记录本场首次实际执行战斗逻辑的角色。"""

        session = self.combat_session
        if session.first_engage_char is None:
            session.first_engage_char = char
            logger.info(f"combat first engage: {char}")

    def is_first_engage(self, char: "BaseChar") -> bool:
        """返回角色是否为本场第一个实际执行战斗逻辑的角色。"""

        return self.combat_session.first_engage_char is char

    def consume_first_engage(self, char: "BaseChar") -> bool:
        """仅在本场首次登场角色首次消费时返回 ``True``。"""

        session = self.combat_session
        if session.first_engage_consumed or session.first_engage_char is not char:
            return False
        session.first_engage_consumed = True
        return True

    @property
    def team_size(self):
        """获取当前队伍人数。

        Returns:
            int: 当前队伍中的角色数量。
        """
        return len(self.chars)

    def get_next_char_index(self):
        """获取下一个角色的索引。

        Returns:
            int: 下一个角色的索引。
        """
        current_index = self.get_current_char().index
        next_index = (current_index + 1) % len(self.chars)
        return next_index

    def get_longest_idle_char_index(self) -> int:
        """获取最久没有登场角色的索引。

        Returns:
            int: 角色的索引。如果没有角色，返回 -1。
        """
        if not self.chars:
            return -1
        return min(self.chars, key=lambda char: char.last_switch_time).index

    def _get_element_ring_pair(self, element_a: Element, element_b: Element):
        index_a = self.element_ring_index.get(element_a)
        index_b = self.element_ring_index.get(element_b)
        if index_a is None or index_b is None or index_a == index_b:
            return None
        ring_size = len(self.element_ring)
        if (index_a + 1) % ring_size == index_b:
            return element_a, element_b
        if (index_b + 1) % ring_size == index_a:
            return element_b, element_a
        return None

    def clear_element_reactions(self):
        self.element_reaction_counts = {
            (self.element_ring[i], self.element_ring[(i + 1) % len(self.element_ring)]): 0
            for i in range(len(self.element_ring))
        }
        self._update_element_reaction_info()

    def _update_element_reaction_info(self):
        if not self.debug:
            return
        reaction_info = []
        for index, reaction_name in enumerate(self.element_reactions):
            pair = (
                self.element_ring[index],
                self.element_ring[(index + 1) % len(self.element_ring)],
            )
            count = self.element_reaction_counts.get(pair, 0)
            if count > 0:
                reaction_info.append(f"{reaction_name}: {count}")
        self.info_set("环合反应", reaction_info)

    def can_element_react(self, char_a: "BaseChar", char_b: "BaseChar") -> bool:
        """检查两名角色的元素能否环合; 满资源状态由切人决策另行检查。"""

        return (
            char_a is not None
            and char_b is not None
            and char_a is not char_b
            and self._get_element_ring_pair(char_a.element, char_b.element) is not None
        )

    def record_element_reaction(self, char_a: "BaseChar", char_b: "BaseChar") -> bool:
        if char_a is None or char_b is None:
            return False
        pair = self._get_element_ring_pair(char_a.element, char_b.element)
        if pair is None:
            return False
        self.element_reaction_counts[pair] = self.element_reaction_counts.get(pair, 0) + 1

        self._update_element_reaction_info()
        return True

    def find_element_reaction_target(self, source_char: "BaseChar") -> "BaseChar | None":
        if source_char is None:
            return None
        source_element_index = self.element_ring_index.get(source_char.element)
        if source_element_index is None:
            return None

        ring_size = len(self.element_ring)
        previous_element = self.element_ring[(source_element_index - 1) % ring_size]
        next_element = self.element_ring[(source_element_index + 1) % ring_size]

        previous_target = None
        next_target = None
        for char in self.chars:
            if char is None or char.index == source_char.index:
                continue
            if char.element == previous_element and (
                previous_target is None or char.last_switch_time < previous_target.last_switch_time
            ):
                previous_target = char
            elif char.element == next_element and (
                next_target is None or char.last_switch_time < next_target.last_switch_time
            ):
                next_target = char

        if previous_target is None:
            return next_target
        if next_target is None:
            return previous_target

        previous_pair = self._get_element_ring_pair(source_char.element, previous_target.element)
        next_pair = self._get_element_ring_pair(source_char.element, next_target.element)
        previous_count = self.element_reaction_counts.get(previous_pair, 0)
        next_count = self.element_reaction_counts.get(next_pair, 0)
        if previous_count <= next_count:
            return previous_target
        return next_target

    def add_freeze_duration(self, start: float, duration=-1.0, freeze_time=0.1):
        """添加冻结持续时间。用于精确计算技能冷却等。

        Args:
            start (float): 单调时钟的冻结开始时间。
            duration (float, optional): 冻结持续时间。如果为-1.0, 则根据当前时间计算。默认为 -1.0。
            freeze_time (float, optional): 认为发生冻结的最小持续时间。默认为 0.1。
        """
        if not start or start < 0:
            return
        if duration < 0:
            duration = self.clock.now() - start
        if duration > freeze_time:
            current_time = self.clock.now()
            while (
                self.freeze_durations
                and self.freeze_durations[0][0]
                <= current_time - self.FREEZE_DURATION_RETENTION_SECONDS
            ):
                self.freeze_durations.popleft()
            freeze_duration = (start, duration, freeze_time)
            if not self.freeze_durations or start >= self.freeze_durations[-1][0]:
                self.freeze_durations.append(freeze_duration)
                return

            records = list(self.freeze_durations)
            insert_at = bisect_right(records, start, key=lambda item: item[0])
            records.insert(insert_at, freeze_duration)
            self.freeze_durations.clear()
            self.freeze_durations.extend(records)

    def time_elapsed_accounting_for_freeze(self, start: float, intro_motion_freeze=False) -> float:
        """计算扣除冻结时间后经过的时间。

        Args:
            start (float): 单调时钟的开始时间。
            intro_motion_freeze (bool, optional): 是否考虑角色入场动画的特殊冻结。默认为 False。

        Returns:
            float: 扣除冻结后实际经过的时间 (秒)。
        """
        if not start or start < 0:
            return 10000
        to_minus = 0
        for freeze_start, duration, freeze_time in reversed(self.freeze_durations):
            if freeze_start <= start:
                break
            if intro_motion_freeze:
                if freeze_time == -100:
                    freeze_time = 0
            elif freeze_time == -100:
                continue
            if duration < freeze_time:
                duration = freeze_time
            to_minus += duration
        if to_minus != 0:
            self.log_debug_gated(f"time_elapsed_accounting_for_freeze to_minus {to_minus}")
        return self.clock.now() - start - to_minus

    def refresh_cd(self):
        if self.scene.cd_refreshed:
            return
        index = self.get_current_char().index
        cds = self.cds.get(index)
        if cds is None:
            cds = {}
            self.cds[index] = cds
        cds["time"] = self.clock.now()
        cds["skill"] = 0
        cds["ultimate"] = 0
        cd_boxes = [
            self.get_box_by_name(Labels.box_skill).scale(1.5),
            self.get_box_by_name(Labels.box_ultimate).scale(1.5),
        ]

        def process_cd_frame(image):
            image = gf.isolate_text_to_black(image)
            return iu.mask_outside_boxes(image, cd_boxes)

        texts = self.ocr(
            box=get_bounding_box(cd_boxes),
            frame_processor=process_cd_frame,
            match=cd_regex,
        )
        for name, box in zip(("skill", "ultimate"), cd_boxes):
            for text in box.in_boundary(texts):
                cds[name] = convert_cd(text)
        self.scene.cd_refreshed = True
        # self.log_debug(f"cd refreshed: {cds} {self.clock.now() - cds['time']}")

    def get_cd(self, box_name, char_index=None):
        self.refresh_cd()
        if char_index is None:
            char_index = self.get_current_char().index
        if cds := self.cds.get(char_index):
            time_elapsed = self.time_elapsed_accounting_for_freeze(cds["time"])
            return cds[box_name] - time_elapsed
        else:
            return 0

    def revive_action(self):
        # TODO: 復活邏輯
        pass

    def raise_not_in_combat(self, message, exception_type=None):
        """抛出未在战斗状态的异常。

        Args:
            message (str): 异常信息。
            exception_type (Exception, optional): 要抛出的异常类型。默认为 NotInCombatException。
        """
        logger.warning(message)
        if exception_type is None:
            exception_type = NotInCombatException
        raise exception_type(message)

    def available(self, name, check_color=True, check_cd=True):
        """检查指定名称的技能或动作是否可用 (通过颜色百分比和冷却时间判断)。

        Args:
            name (str): 技能或动作的名称 (例如 'skill', 'ultimate')。

        Returns:
            bool: 如果可用则返回 True, 否则 False。
        """
        if check_color:
            current = self.box_highlighted(name)
        else:
            current = 1
        if current > 0 and (not check_cd or not self.has_cd(name)):
            return True

    def box_highlighted(self, name):
        current = self.calculate_color_percentage(
            text_white_color, self.get_box_by_name(f"box_{name}")
        )
        if current > 0:
            current = 1
        else:
            current = 0
        return current

    def combat_once(
        self,
        wait_combat_time=200,
        max_combat_time=1200,
        raise_if_not_found=True,
        retarget_turn=True,
    ):
        """执行一次完整的战斗流程。

        Args:
            wait_combat_time (int, optional): 等待进入战斗状态的超时时间 (秒)。默认为 200。
            raise_if_not_found (bool, optional): 如果未找到战斗状态是否抛出异常。默认为 True。
        """
        try:
            self.wait_until(
                self.in_combat, time_out=wait_combat_time, raise_if_not_found=raise_if_not_found
            )
            self.begin_combat_session()
            self.info["Combat Count"] = self.info.get("Combat Count", 0) + 1
            with self.retarget_turn_policy(enable=retarget_turn):
                timer = self.clock.timer(timeout=max_combat_time)
                while self.in_combat():
                    logger.debug(f"combat_once loop {self.chars}")
                    self.get_current_char(raise_exception=True).perform()
                    if timer.expired():
                        self.raise_not_in_combat(
                            f"Combat maximum duration of {max_combat_time}s reached."
                        )
        except NotInCombatException as e:
            logger.info(f"combat_once out of combat break {e}")
        finally:
            team_status = (
                TeamSurvivalStatus.DEAD
                if any(char is not None and char.is_dead for char in self.chars)
                else TeamSurvivalStatus.NO_DEATHS
            )
            self.combat_end()

        if not self.wait_in_team(time_out=5, raise_if_not_found=False):
            team_status = TeamSurvivalStatus.WIPED

        return team_status

    def _decide_switch_to(
        self,
        current_char: "BaseChar",
        free_intro=False,
        require_intro=False,
    ):
        return self.combat_planner.decide_switch(
            current_char,
            free_intro=free_intro,
            require_intro=require_intro,
        )

    def _wait_switch_in_guard(
        self,
        current_char: "BaseChar",
        switch_to: "BaseChar",
        has_intro: bool,
    ) -> None:
        guard = self.combat_planner.switch_in_guard(current_char, switch_to, has_intro)
        if not guard.should_delay():
            return

        start_time = self.clock.now()
        reason = guard.reason or f"{switch_to} switch in guard"
        logger.info(f"switch in delayed: {reason}")
        while guard.should_delay() and self.clock.now() - start_time < guard.timeout:
            self.check_combat()
            if guard.while_waiting is None:
                current_char.click_with_interval()
            else:
                guard.while_waiting()
            self.sleep(max(guard.poll_interval, 0.01))

        if guard.should_delay():
            logger.warning(
                f"switch in guard timeout after {self.clock.now() - start_time:.2f}s: {reason}"
            )
        else:
            logger.info(
                f"switch in guard released after {self.clock.now() - start_time:.2f}s: {reason}"
            )

    def _set_current_char(self, current_char: "BaseChar | None", switch_to: "BaseChar", has_intro):
        self.in_animation = False
        if current_char:
            current_char.switch_out()
            if has_intro:
                current_char.last_outro_time = self.clock.now()
        switch_to.is_current_char = True
        switch_to.has_intro = has_intro

    def _switch_to_char(
        self,
        decision: SwitchDecision,
        current_char: "BaseChar | None" = None,
        post_action=None,
        free_intro=False,
        retry_intro=False,
        log_prefix="switch char",
        time_out=10,
    ):
        switch_to = decision.target
        has_intro = decision.has_intro
        current_char_name = current_char.ufn_name if current_char else "None"
        self.combat_planner.prepare_switch(decision)
        switch_to.has_intro = has_intro
        intro_replanned = False
        start_time = self.clock.now()
        self.scene.clear_health_snapshot()
        switch_key_sent_at = self.clock.UNSET
        last_index_check = self.clock.UNSET

        logger.info(
            f"{log_prefix} {current_char_name} -> {switch_to.ufn_name}, has_intro {has_intro}"
        )

        with self.skip_sleep_checks() as skip:
            skip.check_combat = True

            while True:
                current_time = self.clock.now()
                elapsed = current_time - start_time
                switch_to_name = switch_to.ufn_name
                frame = self.next_frame()

                if self.is_in_team(frame=frame):
                    self.check_combat()
                else:
                    info = f"{log_prefix} not in team {elapsed}s"
                    if elapsed > 5:
                        self.raise_not_in_combat(info)

                    if self._mark_dead_char_if_detected(switch_to):
                        return

                    self.log_info_gated(info)
                    self.sleep(0.01)
                    continue
                if self.scene.health_snapshot() is None:
                    self.is_health_changed(frame)

                detected_reason, last_index_check = self._switch_detection_reason(
                    switch_to,
                    frame,
                    switch_key_sent_at,
                    current_time,
                    last_index_check,
                    start_time,
                    time_out,
                )
                if detected_reason:
                    logger.info(f"{log_prefix} detected by {detected_reason}")
                    self._set_current_char(current_char, switch_to, has_intro)
                    break

                intro_ready = current_char is not None and (
                    free_intro or current_char.is_cycle_full()
                )
                # A health change must belong to the target of the key already sent.
                if (
                    retry_intro
                    and not switch_key_sent_at
                    and not has_intro
                    and not intro_replanned
                    and intro_ready
                ):
                    intro_replanned = True
                    new_decision = self._decide_switch_to(
                        current_char,
                        free_intro,
                        require_intro=True,
                    )
                    new_switch_to = new_decision.target
                    new_has_intro = new_decision.has_intro
                    if new_has_intro and new_switch_to != current_char:
                        if not new_decision.skip_switch_waits:
                            self._wait_switch_in_guard(current_char, new_switch_to, new_has_intro)
                        self.combat_planner.prepare_switch(new_decision)
                        decision = new_decision
                        switch_to = new_switch_to
                        has_intro = new_has_intro
                        switch_to.has_intro = True
                        switch_to_name = switch_to.ufn_name
                        logger.info(
                            f"{log_prefix} updated target to {switch_to_name}, "
                            f"has_intro {switch_to.has_intro}"
                        )

                self.send_key(
                    switch_to.index + 1,
                    action_name="switch_char_send",
                    interval=0.15,
                    down_time=0.05,
                )
                self.sleep(0.001)
                self.click(action_name="switch_char_click", interval=0.3)
                if not switch_key_sent_at:
                    switch_key_sent_at = current_time

                if elapsed > time_out:
                    if self.debug:
                        self.screenshot(
                            f"switch_not_detected_{current_char_name}_to_{switch_to_name}"
                        )
                    switch_to.mark_dead(f"switch char timeout {time_out}s")
                    return

                self.sleep(0.01)

        entry_reaction = bool(
            has_intro
            and current_char is not None
            and self.record_element_reaction(current_char, switch_to)
        )
        self.combat_planner.complete_switch(
            decision,
            previous_char=current_char,
            entry_reaction=entry_reaction,
        )

        if post_action:
            logger.debug(f"post_action {post_action}")
            post_action(switch_to, has_intro)

        logger.info(f"{log_prefix} end {(self.clock.now() - start_time):.3f}s")

    def _mark_dead_char_if_detected(self, switch_to: "BaseChar"):
        if self.find_confirm(self.box_of_screen(0.655, 0.694, 0.709, 0.787, hcenter=True)):
            switch_to.mark_dead("not in team while revive confirm is visible")
            self.ensure_main(in_world=False)
            return True
        return False

    def _switch_detection_reason(
        self,
        switch_to: "BaseChar",
        frame,
        switch_key_sent_at,
        current_time,
        last_index_check,
        start_time,
        time_out,
    ):
        if bool(switch_key_sent_at) and current_time - switch_key_sent_at >= 0.04:
            if self.is_health_changed(frame) is True:
                return "active health change", last_index_check

        if current_time - last_index_check < 0.35:
            return None, last_index_check

        use_index_fallback = (
            self.scene.health_snapshot() is None
            or not switch_key_sent_at
            or current_time - switch_key_sent_at > 0.45
            or current_time - start_time > max(time_out - 0.75, time_out * 0.8)
        )
        if not use_index_fallback:
            return None, last_index_check

        last_index_check = current_time
        if self.is_char_at_index(switch_to.index, frame=frame, char_count=self.team_size):
            return "char index fallback", last_index_check
        return None, last_index_check

    def switch_next_char(self, current_char: "BaseChar", post_action=None, free_intro=False):
        """切换到下一个最优角色。

        Args:
            current_char (BaseChar): 当前角色对象。
            post_action (callable, optional): 切换后执行的动作 (回调函数)。默认为 None。
            free_intro (bool, optional): 是否强制认为拥有入场技 (通常在协奏值满时)。默认为 False。
        """
        if not self.combat_session.switch_enabled or self.team_size <= 1:
            self.click(after_sleep=0.1)
            return

        decision = self.combat_planner.decide_switch(
            current_char,
            free_intro=free_intro,
        )
        switch_to = decision.target
        has_intro = decision.has_intro
        if switch_to is None or switch_to == current_char:
            current_char.click_with_interval()
            self.run_with_interval(
                lambda: logger.debug(
                    f"planner keeps current char {current_char}: {decision.reason}"
                ),
                0.5,
                action_name=("planner_keep_current", current_char.index, decision.reason),
            )
            return

        if not decision.skip_switch_waits:
            self._wait_switch_in_guard(current_char, switch_to, has_intro)
            current_char.wait_switch_cd()

        self._switch_to_char(
            decision,
            current_char=current_char,
            post_action=post_action,
            free_intro=free_intro,
            retry_intro=True,
            log_prefix=f"planner switch_next_char ({decision.reason})",
        )

    def switch_other_char(self, current_char: "BaseChar"):
        from src.tasks.trigger.AutoCombatTask import AutoCombatTask

        if isinstance(self, AutoCombatTask):
            current_char.logger.debug("AutoCombatTask, skip switch_other_char")
            return
        if not self.combat_session.switch_enabled:
            current_char.logger.debug("combat character switching disabled by task policy")
            return
        target = next(
            (
                char
                for char in self.chars
                if char and char.index != current_char.index and not char.is_dead
            ),
            None,
        )
        if target is None:
            current_char.logger.info("No living teammate available after combat")
            return

        next_char = str(target.index + 1)
        current_char.logger.debug(
            f"{current_char.ufn_name} on_combat_end {current_char.index} "
            f"switch next char: {next_char}"
        )
        start = self.clock.now()
        while self.clock.now() - start < 6:
            in_team, current_index, _ = self.in_team()
            if in_team and current_index != current_char.index:
                for char in filter(None, self.chars):
                    char.is_current_char = char.index == current_index
                break
            self.send_key(next_char)
            current_char.sleep(0.2, False)
        current_char.logger.debug(
            f"switch_other_char on_combat_end {current_char.index} switch end"
        )

    def switch_to_combat_start_char(self):
        if not self.combat_session.switch_enabled:
            logger.info("combat start switch disabled by task policy")
            return

        current_char = self.get_current_char(raise_exception=False)
        decision = self.combat_planner.decide_combat_start_char(current_char)
        switch_to = decision.target
        if switch_to is None:
            return
        if current_char == switch_to:
            logger.info(f"combat start char already current {switch_to}")
            return

        self._switch_to_char(
            decision,
            current_char=current_char,
            log_prefix=f"planner combat start ({decision.reason})",
        )

    def get_ultimate_key(self):
        """获取终结技技能的按键。

        Returns:
            str: 终结技技能的按键字符串。
        """
        return self.key_config["Ultimate Key"]

    def get_skill_key(self):
        """获取技能的按键。

        Returns:
            str: 技能的按键字符串。
        """
        return self.key_config["Skill Key"]

    def get_arc_key(self):
        """获取弧盘技能的按键。

        Returns:
            str: 弧盘技能的按键字符串。
        """
        return self.key_config["Arc Key"]

    def has_skill_cd(self):
        """检查技能是否在冷却中。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.has_cd("skill")

    def has_ult_cd(self):
        """检查终结技技能是否在冷却中。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.has_cd("ultimate")

    def has_cd(self, box_name, char_index=None):
        """检查指定UI区域是否处于冷却状态 (通过检测特定颜色的点和数字)。

        Args:
            box_name (str): UI区域的名称 (例如 'skill', 'ultimate')。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.get_cd(box_name, char_index) > 0

    def get_current_char(self, raise_exception=False) -> "BaseChar":
        """获取当前操作的角色对象。

        Args:
            raise_exception (bool, optional): 如果找不到当前角色是否抛出异常。默认为 False。

        Returns:
            BaseChar: 当前角色对象 (`BaseChar`) 或 None。
        """
        for char in self.chars:
            if char and char.is_current_char:
                return char
        if raise_exception:
            self.screenshot("get_current_char_failed")
            self.raise_not_in_combat("can find current char!!")
        return None

    def combat_end(self):
        """战斗结束时调用的清理方法。"""
        # Both combat loops call this from finally, while the original error is available.
        self._report_external_error(exception())
        self.sleep_check_skip.all = True
        try:
            self.reset_to_false()

            current_char = self.get_current_char(raise_exception=False)
            if current_char:
                try:
                    self.get_current_char().on_combat_end(self.chars)
                except Exception as e:
                    self._report_external_error(e)
                    self.log_error(f"{current_char.ufn_name} on_combat_end error", e)

            self._clear_dead_chars()
        finally:
            SoundCombatContext().clear_task_if(self)
            self.combat_session = None

    def _report_external_error(self, error):
        if not isinstance(error, Exception) or isinstance(
            error, (NotInCombatException, TaskDisabledException, FinishedException)
        ):
            return
        source = getattr(error, "_combat_char", None)
        if not isinstance(source, BaseChar) or getattr(source, "task", None) is not self:
            source = None
            for frame, _ in walk_tb(error.__traceback__):
                candidate = frame.f_locals.get("self")
                if isinstance(candidate, BaseChar) and getattr(candidate, "task", None) is self:
                    source = candidate
        character = combo = ""
        impl_id = getattr(source, "impl_id", "")
        if impl_id:
            character = source.ufn_name
            combo = CustomCharManager().get_impl_name(impl_id, with_source_prefix=True)
        message = char_registry.describe_external_error(
            error, character=character, combo=combo, impl_id=impl_id
        )
        if message is not None:
            self._notify_external_error(message)

    def _notify_external_error(self, message):
        self.log_error(message)
        communicate.info_bar_requested.emit(
            title=self.tr("外置代码"), content=message, success=False, duration=-1
        )

    def _clear_dead_chars(self):
        for char in self.chars:
            if char is not None:
                char.clear_dead()

    def _wrap_wait_until_action(self, action):
        def wrapped_action():
            if action is not None:
                action()
            self.sleep(0.001)

        return wrapped_action

    def wait_until(
        self,
        condition,
        time_out=0,
        pre_action=None,
        post_action=None,
        settle_time=-1,
        raise_if_not_found=False,
    ):
        return super().wait_until(
            condition,
            time_out=time_out,
            pre_action=self._wrap_wait_until_action(pre_action),
            post_action=post_action,
            settle_time=settle_time,
            raise_if_not_found=raise_if_not_found,
        )

    @contextmanager
    def skip_sleep_checks(self) -> Generator[SleepCheckSkip, None, None]:
        """Temporarily change checks. Keep combat-session start/end outside this scope."""
        old_values = {
            field.name: getattr(self.sleep_check_skip, field.name)
            for field in fields(self.sleep_check_skip)
        }
        try:
            yield self.sleep_check_skip
        finally:
            for check, old_value in old_values.items():
                setattr(self.sleep_check_skip, check, old_value)

    def can_sound_trigger(self) -> bool:
        """Allow sound actions when their checks are enabled and no animation is playing."""
        return not self.sleep_check_skip.sound_combat_context and not self.in_animation

    def sleep_check(self):
        if self.can_sound_trigger() and SoundCombatContext.should_interrupt_combat():
            context = SoundCombatContext()
            if context.is_bound_to(self):
                self.log_info("Combat sleep interrupted by sound action")
                context.execute_pending_action(expected_task=self)
                if context.is_bound_to(self):
                    context.wait_for_resume()

        if not self.sleep_check_skip.check_combat:
            self.check_combat()

    def _apply_sound_config(self, dodge_action=ACTION_UNSET, counter_action=ACTION_UNSET):
        sound_context = SoundCombatContext()
        if self.sound_config:
            enable = self.sound_config.get("Enable Sound Trigger", True)
            dodge_all_attacks = self.sound_config.get("Dodge All Attacks", True)
            dodge_thresh = self.sound_config.get("Dodge Threshold", 0.13)
            counter_thresh = self.sound_config.get("Counter Attack Threshold", 0.12)
            dodge_thresh = np.clip(dodge_thresh, 0.0, 1.0)
            counter_thresh = np.clip(counter_thresh, 0.0, 1.0)
            sound_context.update_config(enable, dodge_all_attacks, dodge_thresh, counter_thresh)
        sound_context.update_task(self, dodge_action=dodge_action, counter_action=counter_action)

    def check_combat(self):
        """检查当前是否处于战斗状态, 如果不是则抛出异常。"""
        if self._in_combat:
            if not self.in_combat():
                # if self.debug:
                #     self.screenshot('not_in_combat_calling_check_combat')
                self.raise_not_in_combat("combat check not in combat")

    def in_combat(self):
        with self.skip_sleep_checks() as skip:
            skip.check_combat = True
            return super().in_combat()

    def set_key(self, key, box):
        best = self.find_best_match_in_box(box, ["t", "e", "r", "q"], threshold=0.7)
        logger.debug(f"set_key best match {key}: {best}")
        if best and best.name != self.key_config[key]:
            self.key_config[key] = best.name
            self.log_info(f"set_key {key} to {best.name}")

    def load_hotkey(self):
        """加载游戏内技能热键。"""
        for key, value in self.key_config.items():
            self.info_set(key, value)
        return self.key_config

    def has_char(self, char_cls):
        for char in self.chars:
            if isinstance(char, char_cls):
                return char

    def _do_load_char(self, index: int, fixed_slots) -> "BaseChar":
        fixed_slot = safe_get(fixed_slots, index)
        fixed_char_id = ""
        fixed_impl_id = ""
        if isinstance(fixed_slot, dict):
            fixed_char_id = fixed_slot.get("char_id", "")
            fixed_impl_id = fixed_slot.get("impl_id", "")
            if fixed_char_id:
                char_info = CustomCharManager().get_character_info_by_id(fixed_char_id)
                if not char_info:
                    self.logger.warning(f"Fixed char {index} not found: {fixed_char_id}")
                    fixed_char_id = ""
                    fixed_impl_id = ""
                else:
                    fixed_char_name = char_info["char_name"]
                    # A preset can pin a character without pinning its combo. In that
                    # case keep using the character's current global implementation.
                    if not fixed_impl_id:
                        fixed_impl_id = char_info["impl_id"]
                    self.logger.info(f"Using fixed char {index}: {fixed_char_name} {fixed_impl_id}")
                    return get_char_by_id(
                        self, index, fixed_char_id, confidence=1, impl_id=fixed_impl_id
                    )
            if fixed_impl_id:
                self.logger.info(f"Using fixed implementation {index}: {fixed_impl_id}")
                return get_char_by_impl_id(self, index, fixed_impl_id, confidence=1)

        box_scaled = self.get_char_box(index).scale(1.1, 1.1)

        return get_char_by_pos(self, box_scaled, index, safe_get(self.chars, index))

    def load_chars(self) -> bool:
        """加载队伍中的角色信息。"""
        try:
            return self._load_chars()
        except Exception as error:
            # CombatCheck handles detection failures, so diagnose before it catches them.
            self._report_external_error(error)
            raise
        finally:
            messages = set(char_registry.get_external_errors())
            previous = getattr(self, "_reported_external_load_errors", set())
            self._reported_external_load_errors = messages
            for message in sorted(messages - previous):
                self._notify_external_error(message)

    def _load_chars(self) -> bool:
        ret = False
        timer = self.clock.timer(precise=True)
        self.load_hotkey()
        in_team, current_index, count = self.in_team()
        if not in_team or current_index == -1:
            return ret

        if count > 4:
            logger.warning(f"char count {count} larger than 4, set to 4")
            count = 4
        self.log_info(f"load_chars count {count} current_index {current_index}")

        self.clear_element_reactions()
        fixed_team = CustomCharManager().get_fixed_team()
        fixed_slots = fixed_team.get("slots", []) if fixed_team.get("enabled", False) else []
        new_chars = []
        indices_to_detect = []
        for i in range(count):
            char = self._do_load_char(i, fixed_slots)
            new_chars.append(char)
            if char.element is Element.DEFAULT:
                indices_to_detect.append(i)

        if indices_to_detect:
            detected_elements = self.load_chars_element(indices_to_detect)
            for i in indices_to_detect:
                new_chars[i].element = detected_elements.get(i, Element.DEFAULT)

        elements = [char.element for char in new_chars]
        self.chars = new_chars
        self.combat_planner.reset(self.chars)
        self.info_set("chars element", elements)
        self.info_set("chars", [])
        for char in self.chars:
            if char is not None:
                char.reset_state()
                if char.index == current_index:
                    char.is_current_char = True
                else:
                    char.is_current_char = False
                name = char.ufn_name
                conf = char.confidence
                elem = char.element
                self.log_info(f"load char success {char} {name} {conf:.2f} {elem}")
                self.info_add_to_list("chars", f"{char.ufn_name}: {char.impl_id or 'BaseChar'}")

        if self.team_size > 0:
            ret = True
            self._apply_sound_config()
        logger.debug(f"load_chars cost {timer.elapsed():.3f}s")
        return ret

    def is_cycle_full(self) -> bool:
        img = self.box_of_screen_scaled(
            2560, 1440, 944, 1316, width_original=66, height_original=66
        ).crop_frame(self.frame)
        h, w = img.shape[:2]
        side = h

        # 1. 预处理：灰度化 + 二值化
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

        # 2. 构造环形掩模 (Mask) —— 进一步排除干扰
        # 环厚度约 12%，我们可以只看这个半径范围内的像素
        mask = np.zeros((h, w), dtype=np.uint8)
        center = (w // 2, h // 2)
        outer_r = side // 2
        inner_r = int(outer_r * (1 - 0.15))  # 稍微多给一点余量，取15%
        cv2.circle(mask, center, outer_r, 255, -1)
        cv2.circle(mask, center, inner_r, 0, -1)

        # 应用掩模，只保留环形区域
        ring_only = cv2.bitwise_and(thresh, thresh, mask=mask)

        # 3. 取样区定义 (核心：对比顶部和底部)
        # 取顶部中心 10%x10% 的区域，以及底部中心同样的区域
        roi_size = int(side * 0.1)
        margin = int(side * 0.02)  # 避开最边缘可能存在的黑边

        # 顶部采样区 (12点钟方向)
        top_roi = ring_only[
            margin : margin + roi_size, (w // 2 - roi_size // 2) : (w // 2 + roi_size // 2)
        ]

        # 底部采样区 (6点钟方向)
        bottom_roi = ring_only[
            (h - margin - roi_size) : (h - margin),
            (w // 2 - roi_size // 2) : (w // 2 + roi_size // 2),
        ]

        # 4. 计算白色像素密度
        top_density = np.sum(top_roi == 255)
        bottom_density = np.sum(bottom_roi == 255)

        # 5. 精准判断逻辑
        # 如果满了，top_density 应该和 bottom_density 非常接近
        # 如果没满（有缺口），top_density 会显著低于 bottom_density
        if bottom_density == 0:
            return False  # 防止除以0

        ratio = top_density / bottom_density

        # 阈值建议：如果 ratio > 0.9，认为已经满了
        # “差一点点”的时候，由于缺口正好在顶部，这个 ratio 会瞬间降到 0.5 以下甚至更低
        is_full = ratio > 0.9

        return is_full

    def walk_until_combat(
        self, direction="w", time_out=10, run=False, delay=0, raise_if_not_found=False
    ):
        ret = False
        try:
            self.middle_click(after_sleep=0.2)
            self.send_key_down(direction)
            if run:
                self.sleep(0.1)
                self.send_key("lshift")
            ret = bool(
                self.wait_until(
                    self.in_combat,
                    time_out=time_out,
                    raise_if_not_found=raise_if_not_found,
                )
            )
            self.sleep(delay)
        finally:
            self.send_key_up(direction)
        return ret

    def ultimate_available(self, index) -> Box | None:
        def mask_function(image):
            return iu.mask_corners(image, ratio_w=0.5, ratio_h=0.5, corners="all")

        def overlap_confidence(x, y, template, search_area, mask):
            height, width = template.shape[:2]
            hit = search_area[y : y + height, x : x + width]
            if hit.shape[:2] != template.shape[:2]:
                return 0.0

            active = mask > 0
            template_active = (template > 0) & active
            hit_active = (hit > 0) & active
            template_count = template_active.sum()
            hit_count = hit_active.sum()
            if template_count == 0 or hit_count == 0:
                return 0.0

            intersection = np.logical_and(template_active, hit_active).sum()
            precision = intersection / hit_count
            recall = intersection / template_count
            return min(precision, recall)

        def find_best_overlap(template, search_area, mask, search_box):
            template_height, template_width = template.shape[:2]
            search_height, search_width = search_area.shape[:2]
            if template_height > search_height or template_width > search_width:
                return None

            best_confidence = 0.0
            best_x = 0
            best_y = 0
            for y in range(search_height - template_height + 1):
                for x in range(search_width - template_width + 1):
                    confidence = overlap_confidence(x, y, template, search_area, mask)
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_x = x
                        best_y = y

            return Box(
                search_box.x + best_x,
                search_box.y + best_y,
                template_width,
                template_height,
                best_confidence,
                Labels.ult_ready,
            )

        box = self.get_box_by_name(Labels.ult_ready)
        box = self._shift_char_ui_box(box, expend=True)
        target_box = self.get_box_by_char_spacing(box, index).scale(1.1)
        self.draw_boxes(boxes=target_box, color="blue")

        feature = self.get_feature_by_name(Labels.ult_ready).mat
        mask = mask_function(feature)
        # image = target_box.scale(1.1).crop_frame(self.frame)

        # iu.show_images([feature, image], ["feature", "image"])
        search_area = gf.ultimate_ready_filter(target_box.crop_frame(self.frame))
        ret = find_best_overlap(feature, search_area, mask, target_box)
        conf = ret.confidence if ret else -1
        if ret and ret.confidence >= 0.7:
            ret.name = str(index)
            self.draw_boxes(boxes=ret, color="red")
        else:
            ret = None
        self.log_info("char:{}, ult:{}, conf:{}".format(index, bool(ret), conf))
        # self.run_with_interval(
        #     lambda: self.log_info(
        #         "char:{}, ult:{}, conf:{}".format(index, bool(ret), conf)
        #     ),
        #     interval=1,
        #     action_name="ultimate_available",
        # )
        return ret


def convert_cd(text):
    """
    Strips a string to only keep the first part that matches the regex pattern.
    Args:
      text: The input string.
      pattern: The regex pattern to match.
    Returns:
      The first matching substring, or None if no match is found.
    """
    try:
        return float(text.name)
    except ValueError:
        match = re.search(cd_regex, text.name)
        if match:
            return float(match.group(0))
        else:
            return 1
