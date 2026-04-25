import re
import time
from typing import TYPE_CHECKING, List

import cv2
import numpy as np
from ok import Logger, safe_get
from skimage.metrics import structural_similarity as ssim

from src import text_white_color
from src.char.BaseChar import Element, Priority
from src.char.CharFactory import get_char_by_name, get_char_by_pos
from src.char.custom.CustomCharManager import CustomCharManager
from src.char.Healer import Healer
from src.combat.CombatCheck import CombatCheck
from src.utils import image_utils as iu

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar

logger = Logger.get_logger(__name__)
cd_regex = re.compile(r"\d{1,2}\.\d")


class NotInCombatException(Exception):
    """未处于战斗状态异常。"""

    pass


class CharDeadException(NotInCombatException):
    """角色死亡异常。"""

    pass


class BaseCombatTask(CombatCheck):
    """基础战斗任务类，封装了游戏"鸣潮"中角色自动化操作的通用逻辑。"""

    hot_key_verified = False  # 热键是否已验证
    freeze_durations = []  # 记录冻结/卡肉的持续时间

    element_ring = (
        Element.WHITE,
        Element.GREEN,
        Element.RED,
        Element.PURPLE,
        Element.BLUE,
        Element.YELLOW,
    )
    element_ring_index = {element: index for index, element in enumerate(element_ring)}
    _element_template_cache = {}
    _element_standard_size = None

    def __init__(self, *args, **kwargs):
        """初始化战斗任务。

        Args:
            *args: 传递给父类的参数。
            **kwargs: 传递给父类的关键字参数。
        """
        super().__init__(*args, **kwargs)
        self.chars: list[BaseChar] = []
        self.mouse_pos = None  # 当前鼠标位置
        self.combat_start = 0  # 战斗开始时间戳

        self.add_text_fix({"Ｅ": "e"})
        self.use_ultimate = True
        self.vibrate_chars_index: list[int] = []
        self.chars_slot_mat = [None, None, None, None]
        self.element_ring_reaction_counts = {}
        self.clear_element_ring_reactions()

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
        min_time = float("inf")
        min_index = -1
        for char in self.chars:
            if char.last_switch_time < min_time:
                min_time = char.last_switch_time
                min_index = char.index
        return min_index

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

    def clear_element_ring_reactions(self):
        self.element_ring_reaction_counts = {
            (self.element_ring[i], self.element_ring[(i + 1) % len(self.element_ring)]): 0
            for i in range(len(self.element_ring))
        }

    def record_element_ring_reaction(self, char_a: "BaseChar", char_b: "BaseChar") -> bool:
        if char_a is None or char_b is None:
            return False
        pair = self._get_element_ring_pair(char_a.element, char_b.element)
        if pair is None:
            return False
        self.element_ring_reaction_counts[pair] = self.element_ring_reaction_counts.get(pair, 0) + 1
        return True

    def find_element_ring_reaction_target(self, source_char: "BaseChar") -> "BaseChar | None":
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
        previous_count = self.element_ring_reaction_counts.get(previous_pair, 0)
        next_count = self.element_ring_reaction_counts.get(next_pair, 0)
        if previous_count <= next_count:
            return previous_target
        return next_target

    def add_freeze_duration(self, start, duration=-1.0, freeze_time=0.1):
        """添加冻结持续时间。用于精确计算技能冷却等。

        Args:
            start (float): 冻结开始时间。
            duration (float, optional): 冻结持续时间。如果为-1.0, 则根据当前时间计算。默认为 -1.0。
            freeze_time (float, optional): 认为发生冻结的最小持续时间。默认为 0.1。
        """
        if duration < 0:
            duration = time.time() - start
        if start > 0 and duration > freeze_time:
            current_time = time.time()
            self.freeze_durations = [
                item for item in self.freeze_durations if item[0] > current_time - 60
            ]
            self.freeze_durations.append((start, duration, freeze_time))

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        """计算扣除冻结时间后经过的时间。

        Args:
            start (float): 开始时间戳。
            intro_motion_freeze (bool, optional): 是否考虑角色入场动画的特殊冻结。默认为 False。

        Returns:
            float: 扣除冻结后实际经过的时间 (秒)。
        """
        if start < 0:
            return 10000
        to_minus = 0
        for freeze_start, duration, freeze_time in self.freeze_durations:
            if start < freeze_start:
                if intro_motion_freeze:
                    if freeze_time == -100:
                        freeze_time = 0
                elif freeze_time == -100:
                    continue
                to_minus += duration - freeze_time
        if to_minus != 0:
            self.log_debug(f"time_elapsed_accounting_for_freeze to_minus {to_minus}")
        return time.time() - start - to_minus

    def refresh_cd(self):
        if self.scene.cd_refreshed:
            return
        index = self.get_current_char().index
        cds = self.cds.get(index)
        if cds is None:
            cds = {}
            self.cds[index] = cds
        cds["time"] = time.time()
        cds["skill"] = 0
        cds["ultimate"] = 0
        texts = self.ocr(
            0.8594, 0.8847, 0.9578, 0.9139, frame_processor=iu.isolate_cd_to_black, match=cd_regex
        )
        for text in texts:
            cd = convert_cd(text)
            if text.x < self.width_of_screen(0.89):
                cds["skill"] = cd
            elif text.x > self.width_of_screen(0.925):
                cds["ultimate"] = cd
        self.scene.cd_refreshed = True
        # self.log_debug(f"cd refreshed: {cds} {time.time() - cds['time']}")

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
        logger.error(message)
        if self.reset_to_false(reason=message):
            logger.error(f"reset to false failed: {message}")
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

    def combat_once(self, wait_combat_time=200, raise_if_not_found=True):
        """执行一次完整的战斗流程。

        Args:
            wait_combat_time (int, optional): 等待进入战斗状态的超时时间 (秒)。默认为 200。
            raise_if_not_found (bool, optional): 如果未找到战斗状态是否抛出异常。默认为 True。
        """
        self.wait_until(
            self.in_combat, time_out=wait_combat_time, raise_if_not_found=raise_if_not_found
        )
        self.load_chars()
        self.info["Combat Count"] = self.info.get("Combat Count", 0) + 1
        try:
            while self.in_combat():
                logger.debug(f"combat_once loop {self.chars}")
                self.get_current_char().perform()
        except CharDeadException as e:
            raise e
        except NotInCombatException as e:
            logger.info(f"combat_once out of combat break {e}")
        self.combat_end()
        self.wait_in_team_and_world(time_out=10, raise_if_not_found=False)

    def _decide_switch_to(self, current_char: "BaseChar", free_intro=False, require_intro=False):
        has_intro = free_intro or current_char.is_cycle_full()
        switch_to = current_char

        if require_intro and not has_intro:
            return switch_to, has_intro

        if has_intro:
            switch_to = self.find_element_ring_reaction_target(current_char)
            if switch_to:
                return switch_to, has_intro

        max_priority = Priority.MIN

        for char in self.chars:
            if char is None:
                continue

            if char == current_char:
                priority = Priority.CURRENT_CHAR
            else:
                priority = char.get_switch_priority(has_intro)
                logger.debug(f"switch_next_char priority: {char} {priority}")

            if priority > max_priority or (
                priority == max_priority and char.last_perform < switch_to.last_perform
            ):
                if priority == max_priority:
                    logger.debug("switch priority equal, determine by last perform")
                max_priority = priority
                switch_to = char

        return switch_to, has_intro

    def switch_next_char(self, current_char: "BaseChar", post_action=None, free_intro=False):
        """切换到下一个最优角色。

        Args:
            current_char (BaseChar): 当前角色对象。
            post_action (callable, optional): 切换后执行的动作 (回调函数)。默认为 None。
            free_intro (bool, optional): 是否强制认为拥有入场技 (通常在协奏值满时)。默认为 False。
        """
        if self.team_size <= 1:
            self.click(interval=0.1)
            return

        current_char.wait_switch_cd()

        switch_to_self_count = 0
        while True:
            switch_to, has_intro = self._decide_switch_to(current_char, free_intro)
            if switch_to != current_char:
                break

            switch_to_self_count += 1
            if switch_to_self_count > 5:
                switch_to = safe_get(self.chars, self.get_longest_idle_char_index())
                if switch_to is not None and switch_to != current_char:
                    logger.warning(
                        f"switch_next_char forced to next char {switch_to} after repeated self selection"
                    )
                    break

            logger.warning(
                f"{current_char} can't find next char to switch to, performing too fast add a normal attack"
            )
            current_char.continues_normal_attack(0.2)

        if switch_to is None or switch_to == current_char:
            logger.warning(f"{current_char} failed to find a valid switch target")
            return

        switch_to.has_intro = has_intro
        logger.info(
            f"switch_next_char {current_char} -> {switch_to} has_intro {switch_to.has_intro}"
        )

        last_click_time = 0.0
        last_decide_time = 0.0
        start_time = time.time()
        self.has_char_slot_changed(switch_to.index, reset_char_slot=True)

        while True:
            self.check_combat()
            current_time = time.time()

            is_char_switched = self.has_char_slot_changed(switch_to.index)

            if not is_char_switched:
                self.click(interval=0.2)
            else:
                self.in_ultimate = False
                current_char.switch_out()
                switch_to.is_current_char = True
                if has_intro:
                    current_char.last_outro_time = time.time()
                break

            if not is_char_switched and not has_intro and current_time - last_decide_time > 0.12:
                last_decide_time = current_time
                new_switch_to, new_has_intro = self._decide_switch_to(
                    current_char, free_intro, require_intro=True
                )
                if new_has_intro and new_switch_to != current_char:
                    switch_to = new_switch_to
                    has_intro = new_has_intro
                    switch_to.has_intro = True
                    logger.info(
                        f"switch_next_char updated target to {switch_to} has_intro {switch_to.has_intro}"
                    )

            if not self.is_in_team():
                logger.info(
                    f"not in world while switching chars_{current_char}_to_{switch_to} {current_time - start_time}"
                )
                # if self.debug:
                #     self.screenshot(f'not in team while switching chars_{current_char}_to_{switch_to} {now - start}')
                # confirm = self.wait_feature('revive_confirm_hcenter_vcenter', threshold=0.8, time_out=2)
                # if confirm:
                #     self.log_info(f'char dead')
                #     if not self.revive_action():
                #         self.raise_not_in_combat(f'char dead', exception_type=CharDeadException)
                if current_time - start_time > self.switch_char_time_out:
                    self.raise_not_in_combat(
                        f"switch too long failed chars_{current_char}_to_{switch_to}, {current_time - start_time}"
                    )
                self.next_frame()
                continue

            if current_time - last_click_time > 0.1:
                self.send_key(switch_to.index + 1)
                self.sleep(0.001)
                last_click_time = current_time

            if current_time - start_time > 10:
                if self.debug:
                    self.screenshot(f"switch_not_detected_{current_char}_to_{switch_to}")
                self.raise_not_in_combat("failed switch chars")

            self.next_frame()

        if has_intro:
            self.record_element_ring_reaction(current_char, switch_to)

        if post_action:
            logger.debug(f"post_action {post_action}")
            post_action(switch_to, has_intro)

        logger.info(f"switch_next_char end {(current_char.last_switch_time - start_time):.3f}s")

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
            raise_exception (bool, optional): 如果找不到当前角色是否抛出异常。默认为 True。

        Returns:
            BaseChar: 当前角色对象 (`BaseChar`) 或 None。
        """
        for char in self.chars:
            if char and char.is_current_char:
                return char
        if raise_exception and not self.in_team()[0]:
            self.raise_not_in_combat("can find current char!!")
        return None

    def combat_end(self):
        """战斗结束时调用的清理方法。"""
        current_char = self.get_current_char(raise_exception=False)
        if current_char:
            self.get_current_char().on_combat_end(self.chars)

    def sleep_check(self):
        """休眠指定时间, 并在休眠前后检查战斗状态。

        Args:
            timeout (float): 休眠的秒数。
            check_combat (bool, optional): 是否在休眠前检查战斗状态。默认为 True。
        """
        # self.log_debug(f'sleep_check {self._in_combat}')
        if self._in_combat:
            self.next_frame()
            if not self.in_combat():
                self.raise_not_in_combat("sleep check not in combat")

    def check_combat(self):
        """检查当前是否处于战斗状态, 如果不是则抛出异常。"""
        if self._in_combat and not self.in_combat():
            # if self.debug:
            #     self.screenshot('not_in_combat_calling_check_combat')
            self.raise_not_in_combat("combat check not in combat")

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

    def _do_load_char(self, index: int, count: int, fixed_slots) -> "BaseChar":
        fixed_slot = safe_get(fixed_slots, index)
        fixed_char_name = ""
        fixed_combo_ref = ""
        if isinstance(fixed_slot, dict):
            fixed_char_name = str(fixed_slot.get("char_name", "") or "").strip()
            fixed_combo_ref = str(fixed_slot.get("combo_ref", "") or "").strip()

        if fixed_char_name:
            self.log_debug(
                f"load_chars use fixed slot {index + 1}: {fixed_char_name} {fixed_combo_ref}"
            )
            return get_char_by_name(
                self, index, fixed_char_name, confidence=1, combo_ref=fixed_combo_ref
            )

        box = self.get_char_box(index)
        if count == 1:
            box = self.shift_char_ui_box(box, expend=True)
        box_scaled = box.scale(1.1, 1.1)

        return get_char_by_pos(self, box_scaled, index, safe_get(self.chars, index))

    def load_chars(self) -> bool:
        """加载队伍中的角色信息。"""
        self.load_hotkey()
        in_team, current_index, count = self.in_team()
        if not in_team or current_index == -1:
            return False

        if count > 4:
            logger.warning(f"char count {count} larger than 4, set to 4")
            count = 4

        elements = self.load_chars_element(count)
        self.clear_element_ring_reactions()
        fixed_team = CustomCharManager().get_fixed_team()
        fixed_slots = fixed_team.get("slots", []) if fixed_team.get("enabled", False) else []
        new_chars = []
        for i in range(count):
            char = self._do_load_char(i, count, fixed_slots)
            char.element = elements[i]
            new_chars.append(char)
        self.chars = new_chars

        healer_count = 0
        self.info_set("Chars", [])
        for char in self.chars:
            if char is not None:
                char.reset_state()
                if isinstance(char, Healer):
                    healer_count += 1
                if char.index == current_index:
                    char.is_current_char = True
                else:
                    char.is_current_char = False
                self.log_info(
                    f"loaded chars success {char} {char.char_name} {char.confidence:.2f} {char.element}"
                )
                self.info_add_to_list("Chars", f"{char.char_name}: {char.combo_label}")

        if self.team_size > 0:
            self.combat_start = time.time()
            return True
        return False

    def load_chars_element(self, count=4) -> List[Element]:
        def preprocess_image(image):
            return iu.binarize_bgr_by_adaptive_center(image)

        results = []
        target_elements = [
            Element.BLUE,
            Element.GREEN,
            Element.RED,
            Element.PURPLE,
            Element.YELLOW,
            Element.WHITE,
        ]

        base_box = self.get_base_char_element_box()

        if not self._element_template_cache:
            ref_img = cv2.imread(f"assets/esper_icons/{Element.BLUE.value}.png")
            if ref_img is not None:
                h, w = ref_img.shape[:2]
                self._element_standard_size = (w, h)

            for element in target_elements:
                raw_template = cv2.imread(
                    f"assets/esper_icons/{element.value}.png", cv2.IMREAD_UNCHANGED
                )
                if raw_template is not None:
                    raw_template[raw_template[:, :, 3] == 0] = [0, 0, 0, 0]
                    template_bin = preprocess_image(raw_template[:, :, :3])
                    _, mask = cv2.threshold(template_bin, 127, 255, cv2.THRESH_BINARY)
                    self._element_template_cache[element] = (template_bin, mask)

        standard_size = self._element_standard_size

        vertical_spacing = int(self.height * 176 / 1440)

        for i in range(count):
            current_box = base_box.copy(y_offset=vertical_spacing * i)

            crop_img = preprocess_image(current_box.crop_frame(self.frame))
            crop_h, crop_w = crop_img.shape[:2]
            _, standard_h = standard_size
            target_w = int(crop_w * (standard_h / crop_h))
            crop_resized = cv2.resize(crop_img, (target_w, standard_h), interpolation=cv2.INTER_NEAREST)
            # iu.show_images(crop_resized, f"crop_resized_{i}")

            best_element = Element.DEFAULT
            max_score = -1.0

            for element in target_elements:
                template_data = self._element_template_cache.get(element)
                if template_data is None:
                    continue
                template_img, template_mask = template_data

                match_score = 0
                if crop_resized is not None and template_img is not None:
                    res = cv2.matchTemplate(
                        crop_resized, template_img, cv2.TM_CCORR_NORMED, mask=template_mask
                    )
                    _, match_score, _, _ = cv2.minMaxLoc(res)

                if match_score > max_score:
                    max_score = match_score
                    best_element = element

            current_box.confidence = max_score
            current_box.name = best_element.name
            results.append(best_element)
            self.draw_boxes(boxes=current_box, color="red")
            self.log_debug(
                f"char_{i + 1} identified as {best_element.name} (score: {max_score:.4f})"
            )

        return results

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

    def has_char_slot_changed(self, index: int, reset_char_slot: bool = False) -> bool:
        def check_size(img1, img2):
            h1, w1 = img1.shape[:2]
            h2, w2 = img2.shape[:2]

            if (h1, w1) != (h2, w2):
                img2 = cv2.resize(img2, (w1, h1), interpolation=cv2.INTER_AREA)
            return img1, img2

        confidence = 1
        frame = self.frame
        feature_name = f"char_{index + 1}_text"
        box = self.get_box_by_name(feature_name)
        current_mat = box.crop_frame(frame)
        if reset_char_slot:
            self.chars_slot_mat[index] = None
        if self.chars_slot_mat[index] is not None:
            img1, img2 = check_size(self.chars_slot_mat[index], current_mat)
            confidence = ssim(img1, img2, channel_axis=-1)
            self.log_debug(f"compare_char_slot: confidence {confidence}")
        self.chars_slot_mat[index] = current_mat
        return confidence < 0.7


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
