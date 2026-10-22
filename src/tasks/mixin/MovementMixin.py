from collections import deque

from ok import BaseTask, Logger

from src.utils import clock

logger = Logger.get_logger(__name__)

LATERAL_DIRECTION_CHANGE_LIMIT = 4
LATERAL_DIRECTION_CHANGE_WINDOW = 3.0


class MovementMixin(BaseTask):
    clock = clock

    def walk_to_box(
        self, find_function, time_out=30, end_condition=None, y_offset=0.05, x_threshold=0.07
    ):
        if not find_function:
            return False

        if not self.wait_until(
            lambda: (end_condition and end_condition()) or find_function(),
            raise_if_not_found=False,
            time_out=min(time_out, 0.5),
        ):
            return False

        return self._walk_to_box_loop(
            find_function,
            time_out=time_out,
            end_condition=end_condition,
            y_offset=y_offset,
            x_threshold=x_threshold,
        )

    @staticmethod
    def _resolve_target(result):
        """将 find_function 的返回值统一为单个目标或 None"""
        if isinstance(result, list):
            return result[0] if result else None
        return result

    def _calc_walk_direction(self, last_target, last_direction, y_offset, x_threshold, centered):
        """根据目标位置计算下一步移动方向，返回 (direction, centered)"""
        if last_target is None:
            return self._opposite_direction(last_direction), centered

        x, y = last_target.center()
        y = max(0, y - self.height_of_screen(y_offset))
        x_abs = abs(x - self.width_of_screen(0.5))
        threshold = 0.04 if not last_direction else x_threshold
        centered = x_abs <= self.width_of_screen(threshold)

        if not centered:
            direction = "d" if x > self.width_of_screen(0.5) else "a"
        else:
            if last_direction == "s":
                v_center = 0.45
            elif last_direction == "w":
                v_center = 0.6
            else:
                v_center = 0.5
            direction = "s" if y > self.height_of_screen(v_center) else "w"
        return direction, centered

    @staticmethod
    def _has_frequent_lateral_direction(next_direction, lateral_change_times, now):
        """记录时间窗口内的 a/d 方向决策，并在达到阈值时请求重置视角。"""
        lateral_directions = {"a", "d"}
        oldest_allowed = now - LATERAL_DIRECTION_CHANGE_WINDOW
        while lateral_change_times and lateral_change_times[0] < oldest_allowed:
            lateral_change_times.popleft()
        if next_direction not in lateral_directions:
            return False

        lateral_change_times.append(now)
        return len(lateral_change_times) >= LATERAL_DIRECTION_CHANGE_LIMIT

    def _walk_to_box_loop(
        self, find_function, time_out=30, end_condition=None, y_offset=0.05, x_threshold=0.07
    ):
        last_direction = None
        start = self.clock.now()
        ended = False
        last_target = None
        centered = False
        lateral_change_times = deque()
        try:
            while self.clock.now() - start < time_out:
                self.next_frame()
                if end_condition:
                    ended = end_condition()
                    if ended:
                        logger.info(f"_walk_to_box_loop ended {ended}")
                        break
                target = self._resolve_target(find_function())
                if target:
                    last_target = target
                if last_target is None:
                    self.log_info("find_function not found, change to opposite direction")
                next_direction, centered = self._calc_walk_direction(
                    last_target, last_direction, y_offset, x_threshold, centered
                )
                if next_direction != last_direction:
                    if self._has_frequent_lateral_direction(
                        next_direction,
                        lateral_change_times,
                        self.clock.now(),
                    ):
                        logger.info(
                            "Detected rapid lateral direction changes; "
                            "recentering camera before resuming walk"
                        )
                        if last_direction:
                            self.send_key_up(last_direction)
                            self.sleep(0.001)
                        self.send_key("s", after_sleep=0.3)
                        self.middle_click(after_sleep=1)
                        time_out += 5
                        last_direction = None
                        lateral_change_times.clear()
                        continue
                    if last_direction:
                        self.send_key_up(last_direction)
                        self.sleep(0.001)
                    last_direction = next_direction
                    if next_direction:
                        self.send_key_down(next_direction)
        finally:
            if last_direction:
                self.send_key_up(last_direction)
                self.sleep(0.001)
        return ended if end_condition else last_direction is not None

    @staticmethod
    def _opposite_direction(direction):
        if direction == "w":
            return "s"
        elif direction == "s":
            return "w"
        elif direction == "a":
            return "d"
        elif direction == "d":
            return "a"
        else:
            return "w"
