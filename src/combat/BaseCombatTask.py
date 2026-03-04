import re
import time
from decimal import Decimal, ROUND_UP, ROUND_DOWN

import cv2
import numpy as np

from ok import Logger, Config
from ok import color_range_to_bound
from ok import safe_get
from ok.feature.Box import get_bounding_box

from src import text_white_color
from src.Labels import Labels
from src.combat.CombatCheck import CombatCheck
from src.char.BaseChar import Priority
from src.char.Healer import Healer
from src.char.CharFactory import get_char_by_pos

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar

logger = Logger.get_logger(__name__)
cd_regex = re.compile(r'\d{1,2}\.\d')


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

    def __init__(self, *args, **kwargs):
        """初始化战斗任务。

        Args:
            *args: 传递给父类的参数。
            **kwargs: 传递给父类的关键字参数。
        """
        super().__init__(*args, **kwargs)
        self.chars: list[BaseChar | None] = [None, None, None, None]  # 角色列表
        self.mouse_pos = None  # 当前鼠标位置
        self.combat_start = 0  # 战斗开始时间戳

        self.add_text_fix({'Ｅ': 'e'})
        self.use_ultimate = True
        self.vibrate_chars_index: list[int] = []

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
            self.freeze_durations = [item for item in self.freeze_durations if item[0] > current_time - 60]
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
            self.log_debug(f'time_elapsed_accounting_for_freeze to_minus {to_minus}')
        return time.time() - start - to_minus

    def refresh_cd(self):
        if self.scene.cd_refreshed:
            return
        index = self.get_current_char().index
        cds = self.cds.get(index)
        if cds is None:
            cds = {}
            self.cds[index] = cds
        cds['time'] = time.time()
        cds['skill'] = 0
        cds['ultimate'] = 0
        texts = self.ocr(0.8594, 0.8847, 0.9578, 0.9139, frame_processor=isolate_cd_to_black, match=cd_regex)
        for text in texts:
            cd = convert_cd(text)
            if text.x < self.width_of_screen(0.89):
                cds['skill'] = cd
            elif text.x > self.width_of_screen(0.925):
                cds['ultimate'] = cd
        self.scene.cd_refreshed = True
        self.log_debug(f'cd refreshed: {cds} {time.time() - cds["time"]}')

    def get_cd(self, box_name, char_index=None):
        self.refresh_cd()
        if char_index is None:
            char_index = self.get_current_char().index
        if cds := self.cds.get(char_index):
            time_elapsed = self.time_elapsed_accounting_for_freeze(cds['time'])
            return cds[box_name] - time_elapsed
        else:
            return 0

    def revive_action(self):
        pass

    def raise_not_in_combat(self, message, exception_type=None):
        """抛出未在战斗状态的异常。

        Args:
            message (str): 异常信息。
            exception_type (Exception, optional): 要抛出的异常类型。默认为 NotInCombatException。
        """
        logger.error(message)
        if self.reset_to_false(reason=message):
            logger.error(f'reset to false failed: {message}')
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
        current = self.calculate_color_percentage(text_white_color,
                                                  self.get_box_by_name(f'box_{name}'))
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
        self.wait_until(self.in_combat, time_out=wait_combat_time, raise_if_not_found=raise_if_not_found)
        self.load_chars()
        self.info['Combat Count'] = self.info.get('Combat Count', 0) + 1
        try:
            while self.in_combat():
                logger.debug(f'combat_once loop {self.chars}')
                self.get_current_char().perform()
        except CharDeadException as e:
            raise e
        except NotInCombatException as e:
            logger.info(f'combat_once out of combat break {e}')
        self.combat_end()
        self.wait_in_team_and_world(time_out=10, raise_if_not_found=False)

    def _decide_switch_to(self, current_char: 'BaseChar', free_intro=False):
        max_priority = Priority.MIN
        switch_to = current_char
        has_intro = free_intro or current_char.is_con_full()
        switch_order: list['BaseChar'] = []

        for char in self.chars:
            if char is None:
                continue

            if char == current_char:
                priority = Priority.CURRENT_CHAR
            else:
                priority = char.get_switch_priority(current_char, has_intro)
                logger.debug(f'switch_next_char priority: {char} {priority}')

            if priority > max_priority or (priority == max_priority and char.last_perform < switch_to.last_perform):
                if priority == max_priority:
                    logger.debug('switch priority equal, determine by last perform')
                max_priority = priority
                switch_to = char
                switch_order.append(char)

        if has_intro:
            for char in reversed(switch_order):
                if char.index in self.vibrate_chars_index:
                    switch_to = char
                    break
                    
        return switch_to, has_intro

    def switch_next_char(self, current_char: 'BaseChar', post_action=None, free_intro=False):
        """切换到下一个最优角色。

        Args:
            current_char (BaseChar): 当前角色对象。
            post_action (callable, optional): 切换后执行的动作 (回调函数)。默认为 None。
            free_intro (bool, optional): 是否强制认为拥有入场技 (通常在协奏值满时)。默认为 False。
        """
        current_char.wait_switch_cd()
        switch_to, has_intro = self._decide_switch_to(current_char, free_intro)

        if switch_to == current_char:
            logger.warning(f"{current_char} can't find next char to switch to, performing too fast add a normal attack")
            current_char.continues_normal_attack(0.2)
            return current_char.switch_next_char()

        switch_to.has_intro = has_intro
        logger.info(f'switch_next_char {current_char} -> {switch_to} has_intro {switch_to.has_intro}')
        
        # if self.debug:
        #     self.screenshot(f'switch_next_char_{current_con}')
        
        last_click_time = 0.0
        start_time = time.time()

        while True:
            self.check_combat()
            current_time = time.time()

            _, current_index, _ = self.in_team()
            if current_index == current_char.index and not switch_to.has_intro:
                new_switch_to, new_has_intro = self._decide_switch_to(current_char, free_intro)
                if new_has_intro:
                    switch_to = new_switch_to
                    has_intro = new_has_intro  # 更新外层状态用于后续逻辑
                    switch_to.has_intro = True

            if current_time - last_click_time > 0.1:
                self.send_key(switch_to.index + 1)
                self.sleep(0.001)
                self.log_debug('switch not detected, send click')
                self.click()
                self.sleep(0.001)
                last_click_time = current_time

            in_team, current_index, _ = self.in_team()

            if not in_team:
                logger.info(f'not in team while switching chars_{current_char}_to_{switch_to} {current_time - start_time}')
                # if self.debug:
                #     self.screenshot(f'not in team while switching chars_{current_char}_to_{switch_to} {now - start}')
                # confirm = self.wait_feature('revive_confirm_hcenter_vcenter', threshold=0.8, time_out=2)
                # if confirm:
                #     self.log_info(f'char dead')
                #     if not self.revive_action():
                #         self.raise_not_in_combat(f'char dead', exception_type=CharDeadException)
                if current_time - start_time > self.switch_char_time_out:
                    self.raise_not_in_combat(f'switch too long failed chars_{current_char}_to_{switch_to}, {current_time - start_time}')
                self.next_frame()
                continue

            if current_index == switch_to.index:
                self.in_ultimate = False
                current_char.switch_out()
                switch_to.is_current_char = True
                if has_intro:
                    current_char.last_outro_time = time.time()
                break
            
            if current_time - start_time > 10:
                if self.debug:
                    self.screenshot(f'switch_not_detected_{current_char}_to_{switch_to}')
                self.raise_not_in_combat('failed switch chars')

            self.next_frame()

        if post_action:
            logger.debug(f'post_action {post_action}')
            post_action(switch_to, has_intro)
            
        logger.info(f'switch_next_char end {(current_char.last_switch_time - start_time):.3f}s')

    def get_ultimate_key(self):
        """获取共鸣解放技能的按键。

        Returns:
            str: 共鸣解放技能的按键字符串。
        """
        return self.key_config['Ultimate Key']

    def get_skill_key(self):
        """获取声骸技能的按键。

        Returns:
            str: 声骸技能的按键字符串。
        """
        return self.key_config['Skill Key']

    def has_skill_cd(self):
        """检查共鸣技能是否在冷却中。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.has_cd('skill')

    def has_ult_cd(self):
        """检查共鸣解放技能是否在冷却中。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.has_cd('ultimate')

    def has_cd(self, box_name, char_index=None):
        """检查指定UI区域是否处于冷却状态 (通过检测特定颜色的点和数字)。

        Args:
            box_name (str): UI区域的名称 (例如 'skill', 'ultimate')。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.get_cd(box_name, char_index) > 0

    def get_current_char(self, raise_exception=False) -> 'BaseChar':
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
            self.raise_not_in_combat('can find current char!!')
        # self.load_chars()
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
                self.raise_not_in_combat('sleep check not in combat')

    def check_combat(self):
        """检查当前是否处于战斗状态, 如果不是则抛出异常。"""
        if self._in_combat and not self.in_combat():
            # if self.debug:
            #     self.screenshot('not_in_combat_calling_check_combat')
            self.raise_not_in_combat('combat check not in combat')

    def set_key(self, key, box):
        best = self.find_best_match_in_box(box, ['t', 'e', 'r', 'q'], threshold=0.7)
        logger.debug(f'set_key best match {key}: {best}')
        if best and best.name != self.key_config[key]:
            self.key_config[key] = best.name
            self.log_info(f'set_key {key} to {best.name}')

    def load_hotkey(self):
        """加载游戏内技能热键。"""
        for key, value in self.key_config.items():
            self.info_set(key, value)
        return self.key_config

    def has_char(self, char_cls):
        for char in self.chars:
            if isinstance(char, char_cls):
                return char

    def load_chars(self) -> bool:
        """加载队伍中的角色信息。"""
        self.load_hotkey()
        in_team, current_index, count = self.in_team()
        if not in_team:
            return False
        # self.log_info('load chars')
        # self.chars = [None, None, None, None]

        MAX_SIZE = 4  # 固定長度

        self.chars = [
            get_char_by_pos(self, self.get_box_by_name(f'box_char_{i+1}'), i, safe_get(self.chars, i))
            if i < count else None
            for i in range(MAX_SIZE)
        ]

        healer_count = 0
        for char in self.chars:
            if char is not None:
                char.reset_state()
                if isinstance(char, Healer):
                    healer_count += 1
                if char.index == current_index:
                    char.is_current_char = True
                else:
                    char.is_current_char = False
        self.combat_start = time.time()
        if sum(1 for c in self.chars if c is not None) > 0:
            self.info_set('Chars', self.chars)
            for c in self.chars:
                if c:
                    self.log_info(f'loaded chars success {c} {c.confidence}')
            return True
        return False

    def _get_aura_box_coords(self) -> dict:
        """
        [内部方法] 统一生成4个角色的裁剪坐标。避免每次检测都重复计算。
        """
        width_percentage = 36 / 2560 
        height_percentage = 60 / 1440
        points = {
            1: (0.9500, 0.1910),
            2: (0.9500, 0.3132),
            3: (0.9500, 0.4361),
            4: (0.9500, 0.5583),
        }
        x_shift = 0.9113 - 0.9500
        y_shift = 0.4256 - 0.4361

        boxes_coords = {}
        for i, point in points.items():
            x, y = point[0] + x_shift, point[1] + y_shift
            boxes_coords[i] = (x, y, x + width_percentage, y + height_percentage)
            
        return boxes_coords

    def _detect_aura_feature(self, cropped, box) -> bool:
        """
        [内部方法] 处理已被裁剪好的图像并检测特征（完全保留你的遮罩和判定逻辑）
        """
        if cropped is None or cropped.size == 0:
            return False

        # 3. 获取裁剪后图像的高宽
        h, w = cropped.shape[:2]

        # 4. 动态生成遮罩 (专门切除右下角)
        mask = np.full((h, w), 255, dtype=np.uint8)
        
        pt_bottom = (int(w * 0.4), h)    
        pt_right = (w, int(h * 0.6))     
        pt_corner = (w, h)               # 右下角绝对顶点
        triangle = np.array([pt_bottom, pt_right, pt_corner], dtype=np.int32)
        cv2.fillPoly(mask, [triangle], 0)
        
        # 将遮罩应用到截图上
        roi_masked = cv2.bitwise_and(cropped, cropped, mask=mask)

        # 5. HSV 颜色特征提取
        hsv = cv2.cvtColor(roi_masked, cv2.COLOR_BGR2HSV)
        
        # 特征一：外侧青色闪电
        lower_cyan = np.array([80, 100, 150])
        upper_cyan = np.array([105, 255, 255])
        mask_cyan = cv2.inRange(hsv, lower_cyan, upper_cyan)
        
        # 特征二：内侧粉红色边缘
        lower_pink1 = np.array([160, 100, 150])
        upper_pink1 = np.array([180, 255, 255])
        lower_pink2 = np.array([0, 100, 150])
        upper_pink2 = np.array([10, 255, 255])
        mask_pink = cv2.bitwise_or(
            cv2.inRange(hsv, lower_pink1, upper_pink1),
            cv2.inRange(hsv, lower_pink2, upper_pink2)
        )

        # 6. 统计符合颜色的像素数量
        cyan_count = cv2.countNonZero(mask_cyan)
        pink_count = cv2.countNonZero(mask_pink)

        # 7. 动态阈值判定
        pixel_threshold = int((w * h) * 0.002) 

        # 判定逻辑和画框
        is_active = (cyan_count > pixel_threshold) and (pink_count > pixel_threshold)
        box.name = f"{cyan_count}, {pink_count}"
        if is_active:
            self.draw_boxes(box.name, [box], color='blue')
            
        return is_active

    def check_avatar_vibrate(self, char_index: int) -> bool:
        """
        (保留的单次检测接口) 检测指定位置(1~4)的角色是否有红蓝锯齿特效
        """
        boxes_coords = self._get_aura_box_coords()
        if char_index not in boxes_coords:
            raise ValueError("角色索引必须在 1 到 4 之间")

        # 若调用单次检测，则依然单独复制一次画面
        x1, y1, x2, y2 = boxes_coords[char_index]
        box = self.box_of_screen(x1, y1, x2, y2)
        cropped = box.crop_frame(self.frame.copy())
        
        return self._detect_aura_feature(cropped, box)

    def get_all_avatar_vibrate(self) -> dict:
        """
        一次性返回所有4个角色的共振状态
        :return: 类似 {1: False, 2: False, 3: True, 4: False} 的字典
        """
        # 1. 获取事先算好的4个坐标点
        boxes_coords = self._get_aura_box_coords()
        
        # 2. 【核心优化】全过程只 copy 这一次游戏画面
        current_frame = self.frame.copy()
        
        status = {}
        # 3. 循环 4 次，复用同一个 current_frame 进行 crop 和特征提取
        if all(item is None for item in self.chars):
            self.load_chars()
        for i, char in enumerate(self.chars):
            if char is None:
                continue
            i += 1
            x1, y1, x2, y2 = boxes_coords[i]
            box = self.box_of_screen(x1, y1, x2, y2)
            
            # 使用唯一画面切图
            cropped = box.crop_frame(current_frame) 
            
            # 获取状态并存入字典
            status[i] = self._detect_aura_feature(cropped, box)
            
        return status

    def is_con_full(self) -> bool:
        ret = False
        chars = []
        status = self.get_all_avatar_vibrate()
        for i, v in status.items():
            if v:
                ret = True
                chars.append(i-1)
        self.vibrate_chars_index = chars
        return ret


white_color = {  # 用于检测UI元素可用状态的白色颜色范围。
    'r': (253, 255),  # Red range
    'g': (253, 255),  # Green range
    'b': (253, 255)  # Blue range
}

cd_white_color = {
    "r": (153, 186),  # Red range
    "g": (158, 192),  # Green range
    "b": (162, 193),  # Blue range
}

def isolate_cd_to_black(cv_image):
    """
    Converts pixels in the cd_white_color range to black,
    and all others to white.
    Args:
        cv_image: Input image (NumPy array, BGR).
    Returns:
        Black and white image (NumPy array), where matches are black.
    """
    lower_bound, upper_bound = color_range_to_bound(cd_white_color)
    
    match_mask = cv2.inRange(cv_image, lower_bound, upper_bound)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)
    output_image = cv2.bitwise_not(output_image)

    return output_image

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
