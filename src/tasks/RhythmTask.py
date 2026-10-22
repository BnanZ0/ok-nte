import threading
from collections import deque

from ok import TaskDisabledException

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.ui.task_icons import Icon
from src.utils.clock import TimePoint

# ─────────────────────────── 常量 ───────────────────────────

DETECT_POINTS = {
    "d": (0.2301, 0.7715),
    "f": (0.4055, 0.7715),
    "j": (0.5941, 0.7715),
    "k": (0.7699, 0.7715),
}

# 亮度阈值：低于此值认为鼓点经过（背景≈245，鼓点≈28）
BRIGHTNESS_THRESHOLD = 100

# 结算界面
FINISH_CLOSE_POS = (0.5402, 0.0437)  # 结算界面×关闭按钮

# 选歌界面
SONG_START_POS = (0.8313, 0.9313)  # 开始演奏按钮（实测）

FINISH_CHECK_INTERVAL = 2.0  # 结算检测间隔（秒），避免每帧跑OCR
DETECT_RADIUS_X = 5
DETECT_RADIUS_Y = 10
DARK_RATIO_THRESHOLD = 0.06
RETRIGGER_INTERVAL = 0.085
KEY_DOWN_TIME = 0.005


class RhythmTask(NTEOneTimeTask, BaseNTETask):
    CONF_TIMEOUT_SECONDS = "超时秒数"
    CONF_DEBUG_LOG = "调试日志"
    CONF_TRACK_KEYS = "键位"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动音游"
        self.description = "异环鼓组音游自动打击与重试"
        self.group_name = "都市闲趣"
        self.group_icon = Icon.GAME
        self.add_rounds_config()
        self.default_config.update(
            {
                self.CONF_TIMEOUT_SECONDS: 180,
                self.CONF_DEBUG_LOG: False,
                self.CONF_TRACK_KEYS: "d, f, j, k",
            }
        )
        self.config_description.update(
            {
                self.CONF_TIMEOUT_SECONDS: "单曲超时时间(秒)",
                self.CONF_DEBUG_LOG: "输出调试日志",
                self.CONF_TRACK_KEYS: "4列对应的键盘按键",
            }
        )

        self._prev_state: dict[str, bool] = dict.fromkeys(DETECT_POINTS, False)
        self._last_press_time: dict[str, TimePoint] = dict.fromkeys(
            DETECT_POINTS, self.clock.UNSET
        )
        self._last_finish_check = self.clock.UNSET
        self._key_queue: deque = deque()
        self._key_queue_cv = threading.Condition()
        self._key_worker: threading.Thread | None = None
        self._key_worker_stop = False
        self._px_cache: dict | None = None
        self._cache_shape: tuple | None = None

    # =========================================================================
    # 入口
    # =========================================================================

    def run(self):
        super().run()
        try:
            return self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.log_error("RhythmTask error", e)
            raise

    def do_run(self):
        self.start_rounds()
        while self.begin_round():
            # 点击开始演奏
            self.log_round_info("点击开始演奏")
            self.operate_click(SONG_START_POS[0], SONG_START_POS[1])

            # 等待离开选歌界面（最多15秒），确认进入音游后再开始检测
            self.log_info("等待进入音游界面")
            load_timer = self.clock.timer(timeout=15)
            while not load_timer.expired():
                self.sleep(0.3)
                if not self._is_song_select():
                    break
            else:
                self.log_error("15 秒内未进入音游界面，停止任务")
                raise TaskDisabledException()
            self.sleep(1.0)  # 额外等待界面稳定

            # 重置每曲状态
            self._prev_state = dict.fromkeys(DETECT_POINTS, False)
            self._last_press_time = dict.fromkeys(DETECT_POINTS, self.clock.UNSET)
            self._last_finish_check = self.clock.UNSET
            self._start_key_worker()

            # 单曲主循环
            self._run_single()

            self._handle_finish()
            self.add_success()

            # 是否继续
            if self.has_remaining_rounds():
                # 等待回到选歌界面后再点
                self.log_info("等待回到选歌界面")
                self.sleep(1.0)
                # 确认在选歌界面再点（防止界面未就绪）
                timer = self.clock.timer(timeout=10)
                while not timer.expired():
                    if self._is_song_select():
                        break
                    self.sleep(0.5)
                else:
                    self.log_error("10 秒内未返回选歌界面，停止任务")
                    raise TaskDisabledException()

        self.finish_rounds()

    def _run_single(self):
        """单曲打击主循环"""
        timeout = float(self.config.get(self.CONF_TIMEOUT_SECONDS, 180))
        timer = self.clock.timer(timeout=timeout)
        self.log_info("音游开始，按键 D/F/J/K")

        try:
            while not timer.expired():
                now = self.clock.now()
                if now - self._last_finish_check >= FINISH_CHECK_INTERVAL:
                    self._last_finish_check = now
                    if self._is_finished():
                        self.log_info("检测到结算界面")
                        return
                self.tick()
                self.next_frame()
            self.log_error(f"Song time out for {timeout}s, RhythmTask disabled")
            raise TaskDisabledException()
        finally:
            self._stop_key_worker()

    # =========================================================================
    # 结算 & 重打
    # =========================================================================

    def _is_finished(self) -> bool:
        """检测是否出现结算界面（OCR 识别"演奏结果"）"""
        box = self.box_of_screen(0.9383, 0.0576, 0.9621, 0.0986)
        # yellow_box = self.box_of_screen(0.2211, 0.6625, 0.3156, 0.6965, name="finish_yellow")
        # red_box = self.box_of_screen(0.4555, 0.6625, 0.5445, 0.6965, name="finish_red")
        # yellow_pct = self.calculate_color_percentage(finish_yellow_color, yellow_box)
        # red_pct = self.calculate_color_percentage(finish_red_color, red_box)
        # self.log_debug(f"_is_finished: yellow_pct {yellow_pct} red_pct {red_pct}")
        return self.find_one(Labels.close_button, box=box)

    def _handle_finish(self):
        """关闭结算界面"""
        self.log_info("关闭结算界面")
        while self._is_finished():
            self.sleep(1.5)
            self.operate_click(FINISH_CLOSE_POS[0], FINISH_CLOSE_POS[1])
            self.sleep(1.0)

    def _is_song_select(self) -> bool:
        """检测当前是否在选歌界面（右下角有"开始演奏"按钮）"""
        pink_box = self.box_of_screen(0.725, 0.854, 0.939, 0.872, name="song_select_pink")
        pink_pct = self.calculate_color_percentage(song_select_pink_color, pink_box)
        # self.log_debug(f"_is_song_select: pink_pct {pink_pct}")
        return pink_pct > 0.9

    # =========================================================================
    # 每帧逻辑
    # =========================================================================

    def tick(self):
        state = self.detect_notes()
        key_map = self._get_key_map()
        col_name = {"d": "第1列", "f": "第2列", "j": "第3列", "k": "第4列"}

        now = self.clock.now()
        for track, has_note in state.items():
            prev = self._prev_state[track]
            can_retrigger = (
                has_note and prev and now - self._last_press_time[track] >= RETRIGGER_INTERVAL
            )
            if has_note and (not prev or can_retrigger):
                actual_key = key_map[track]
                self._queue_press(actual_key, col_name[track])
                self._last_press_time[track] = now
            self._prev_state[track] = has_note

    def _get_key_map(self) -> dict[str, str]:
        raw_keys = str(self.config.get(self.CONF_TRACK_KEYS, "d, f, j, k"))
        keys = [key.strip() for key in raw_keys.split(",")]
        defaults = ["d", "f", "j", "k"]
        keys = [(keys[i] if i < len(keys) and keys[i] else defaults[i]) for i in range(4)]
        return dict(zip(DETECT_POINTS, keys))

    # =========================================================================
    # 异步按键
    # =========================================================================

    def _start_key_worker(self):
        if self._key_worker and self._key_worker.is_alive():
            return
        with self._key_queue_cv:
            self._key_queue.clear()
            self._key_worker_stop = False
        self._key_worker = threading.Thread(target=self._key_worker_loop, daemon=True)
        self._key_worker.start()

    def _stop_key_worker(self, timeout: float = 1.0):
        with self._key_queue_cv:
            self._key_worker_stop = True
            self._key_queue.clear()
            self._key_queue_cv.notify_all()
        if self._key_worker:
            self._key_worker.join(timeout=timeout)
            if not self._key_worker.is_alive():
                self._key_worker = None

    def _queue_press(self, key: str, col: str = ""):
        with self._key_queue_cv:
            self._key_queue.append((key, col))
            self._key_queue_cv.notify()

    def _key_worker_loop(self):
        while True:
            with self._key_queue_cv:
                while not self._key_queue and not self._key_worker_stop:
                    self._key_queue_cv.wait(timeout=0.05)
                if self._key_worker_stop and not self._key_queue:
                    return
                key, col = self._key_queue.popleft()

            self.send_key(key, interval=0, down_time=KEY_DOWN_TIME)
            self.log_info(f"按键 {key.upper()} ({col})")

    # =========================================================================
    # 鼓点检测：命中线附近窗口亮度
    # =========================================================================

    def detect_notes(self) -> dict[str, bool]:
        frame = self.frame
        if frame is None:
            return dict.fromkeys(DETECT_POINTS, False)

        fh, fw = frame.shape[:2]
        if self._cache_shape != (fh, fw):
            self._px_cache = {k: (int(x * fw), int(y * fh)) for k, (x, y) in DETECT_POINTS.items()}
            self._cache_shape = (fh, fw)

        debug = bool(self.config.get(self.CONF_DEBUG_LOG, False))
        result = {}
        debug_parts = [] if debug else None

        for key, (px, py) in self._px_cache.items():
            x1 = max(0, px - DETECT_RADIUS_X)
            x2 = min(fw, px + DETECT_RADIUS_X + 1)
            y1 = max(0, py - DETECT_RADIUS_Y)
            y2 = min(fh, py + DETECT_RADIUS_Y + 1)
            roi = frame[y1:y2, x1:x2]
            pixel_brightness = roi.mean(axis=2) if roi.ndim == 3 else roi
            dark_ratio = float((pixel_brightness < BRIGHTNESS_THRESHOLD).mean())
            brightness = int(pixel_brightness.mean())
            has_note = dark_ratio >= DARK_RATIO_THRESHOLD
            result[key] = has_note
            if debug:
                debug_parts.append(
                    f"{key.upper()}:{brightness}/{dark_ratio:.2f}{'✓' if has_note else '✗'}"
                )

        if debug:
            self.log_info("检测 | " + " | ".join(debug_parts))

        return result


finish_yellow_color = {
    "r": (220, 230),
    "g": (170, 180),
    "b": (85, 90),
}

finish_red_color = {
    "r": (220, 230),
    "g": (90, 100),
    "b": (85, 90),
}

song_select_pink_color = {
    "r": (160, 210),
    "g": (30, 50),
    "b": (85, 120),
}
