import re

import cv2
import numpy as np
from ok import CannotFindException, TaskDisabledException

from src.gifts.GiftManager import GiftManager
from src.gifts.layout import GIFT_LAYOUT
from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.utils import image_utils as iu


class GiftTask(NTEOneTimeTask, BaseNTETask):
    """Give only the explicitly captured, first-page gifts to configured characters."""

    MAX_TOTAL_GIFTS = 10
    MAX_GIFTS_PER_CHARACTER = 3
    NAME_MATCH_THRESHOLD = 0.82
    GIFT_MATCH_THRESHOLD = 0.80
    NAME_RATIO = GIFT_LAYOUT.name_ratio
    GIFT_FIRST_RATIO = GIFT_LAYOUT.gift_first_ratio
    GIFT_COLUMNS = GIFT_LAYOUT.gift_columns
    GIFT_ROWS = GIFT_LAYOUT.gift_rows
    GIFT_COLUMN_STEP = GIFT_LAYOUT.gift_column_step
    GIFT_ROW_STEP = GIFT_LAYOUT.gift_row_step
    UNLIMIT_ICON_X_OFFSET_RATIO = GIFT_LAYOUT.unlimited_icon_x_offset_ratio
    UNLIMIT_ICON_Y_OFFSET_RATIO = GIFT_LAYOUT.unlimited_icon_y_offset_ratio
    UNLIMIT_ICON_WIDTH_REDUCTION_RATIO = GIFT_LAYOUT.unlimited_icon_width_reduction_ratio
    CHARACTER_SLOT_X = GIFT_LAYOUT.character_slot_x
    CHARACTER_SLOT_YS = GIFT_LAYOUT.character_slot_ys
    SIDEBAR_BOX = GIFT_LAYOUT.sidebar_box
    SIDEBAR_SCROLL_X = GIFT_LAYOUT.sidebar_scroll_x
    SIDEBAR_SCROLL_Y = GIFT_LAYOUT.sidebar_scroll_y
    SIDEBAR_SCROLL_STEP = GIFT_LAYOUT.sidebar_scroll_step
    SIDEBAR_RESET_STEP = GIFT_LAYOUT.sidebar_reset_step
    SIDEBAR_SCROLLS_PER_CHARACTER = GIFT_LAYOUT.sidebar_scrolls_per_character
    MAX_SIDEBAR_PAGES = GIFT_LAYOUT.max_sidebar_pages
    SEND_BUTTON = GIFT_LAYOUT.send_button
    COUNTER_BOX = GIFT_LAYOUT.counter_box
    COUNTER_RE = re.compile(r"(?<!\d)(\d++)\s*+/\s*+3")
    MODE_CAPTURE = "capture"
    MODE_RUN = "run"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "羁遇赠礼"
        self.visible = False
        self.manager = GiftManager()
        self.mode: str | None = None
        self.capture_profile_id: str | None = None
        self.capture_frame = None
        self.capture_name = ""
        self.capture_blocked_slots: tuple[int, ...] = ()
        self.capture_error = ""

    def capture_profile(self, profile_id: str | None) -> None:
        self.mode = self.MODE_CAPTURE
        self.capture_profile_id = profile_id
        self.capture_frame = None
        self.capture_name = ""
        self.capture_blocked_slots = ()
        self.capture_error = ""

    def run_gift_mode(self) -> None:
        self.mode = self.MODE_RUN

    def _capture_profile(self) -> None:

        frame = self.frame
        if frame is None or not getattr(frame, "size", 0):
            self.capture_error = self.tr("没有可用的游戏画面")
            return

        name = self.tr("未命名角色")
        try:
            results = self.ocr(box=self.get_name_box(), frame=frame)
            recognized = "".join(str(result.name).strip() for result in results or [])
            if recognized:
                name = recognized
        except Exception as error:
            self.log_debug(f"gift capture name OCR failed: {type(error).__name__}")

        blocked_slots = []
        try:
            blocked_slots = [
                index
                for index, badge_box in enumerate(self.get_unlimit_gift_boxes())
                if self.find_one("unlimit_gift", box=badge_box, frame=frame)
            ]
        except Exception as error:
            self.log_debug(f"gift capture unlimited-gift detection failed: {type(error).__name__}")
        self.capture_frame = frame
        self.capture_name = name
        self.capture_blocked_slots = tuple(blocked_slots)

    def _report(self, message: str) -> None:
        self.log_info(message)

    def run(self):
        mode = self.mode or self.MODE_RUN
        try:
            super().run()
            if mode == self.MODE_CAPTURE:
                self._capture_profile()
            else:
                self.do_run()
        except TaskDisabledException:
            if mode == self.MODE_CAPTURE:
                self.capture_error = self.tr("任务已停止")
            raise
        except Exception as e:
            if mode == self.MODE_CAPTURE:
                self.capture_error = str(e).strip() or e.__class__.__name__
            else:
                self.screenshot("gift_task_failure")
                self.log_error("GiftTask error", e)
            raise
        finally:
            self.mode = None

    def do_run(self) -> bool:
        summary = self.run_gifts()
        return not summary["failed"]

    def run_gifts(self) -> dict:
        profiles = self.manager.get_enabled_profiles()
        summary = {"success": 0, "skipped": [], "failed": []}

        if not profiles:
            self.log_info("No enabled gift profile has selected gifts")
            return summary

        self._report(f"开始赠礼，共 {len(profiles)} 个已启用角色")
        self.ensure_main()
        try:
            self._enter_gift_page_from_main()
            self._scan_character_list(profiles, summary)
            for profile_id, profile in profiles.items():
                if profile_id not in self._processed_profiles(summary):
                    summary["skipped"].append(f"{profile['display_name']}: 未在角色列表中找到")
            self.ensure_main()
            return summary
        finally:
            self._report(
                f"赠礼结束：成功 {summary['success']} 次，"
                f"跳过 {len(summary['skipped'])} 项，失败 {len(summary['failed'])} 项"
            )
            for reason in summary["skipped"]:
                self._report(f"跳过：{reason}")
            for reason in summary["failed"]:
                self._report(f"失败：{reason}")

    def _enter_gift_page_from_main(self) -> None:
        """User-owned navigation seam. Keep all game-specific route actions here."""

        def action():
            self.openESCpanel()
            self.operate_click(*self.pos.panels.esc.gift)
            self.sleep(0.5)
            return self.wait_panel(Labels.bond_panel)

        result = self.retry_on_action(action, self.ensure_main)
        if not result:
            self.log_error("无法找到赠礼面板")
            raise CannotFindException()
        self.sleep(1)
        self.operate_click(0.802, 0.124)
        self.sleep(1)

    def get_name_box(self):
        return self.box_of_screen(*self.NAME_RATIO, name="gift_character_name")

    def get_gift_boxes(self):
        first_x, first_y, first_to_x, first_to_y = self.GIFT_FIRST_RATIO
        box_width = first_to_x - first_x
        box_height = first_to_y - first_y
        return [
            self.box_of_screen(
                first_x + column * self.GIFT_COLUMN_STEP,
                first_y + row * self.GIFT_ROW_STEP,
                first_x + column * self.GIFT_COLUMN_STEP + box_width,
                first_y + row * self.GIFT_ROW_STEP + box_height,
                name=f"gift_slot_{row * self.GIFT_COLUMNS + column}",
            )
            for row in range(self.GIFT_ROWS)
            for column in range(self.GIFT_COLUMNS)
        ]

    def get_unlimit_gift_box(self, gift_box):
        """Return the small upper-left unlimited-gift badge zone for one gift slot."""
        return gift_box.copy(
            x_offset=-round(gift_box.width * self.UNLIMIT_ICON_X_OFFSET_RATIO),
            y_offset=-round(gift_box.height * self.UNLIMIT_ICON_Y_OFFSET_RATIO),
            width_offset=-round(gift_box.width * self.UNLIMIT_ICON_WIDTH_REDUCTION_RATIO),
            name=f"{gift_box.name}_unlimit_badge",
        )

    def get_unlimit_gift_boxes(self):
        """Return the small upper-left badge zone associated with each visible gift slot."""
        return [self.get_unlimit_gift_box(gift_box) for gift_box in self.get_gift_boxes()]

    def resize_captured_frame(self, frame: np.ndarray) -> np.ndarray:
        """Fit a saved full frame to the current game frame so every layout Box is reusable."""
        current_frame = self.frame
        if frame.shape[:2] == current_frame.shape[:2]:
            return frame
        interpolation = (
            cv2.INTER_AREA if frame.shape[0] > current_frame.shape[0] else cv2.INTER_CUBIC
        )
        return cv2.resize(
            frame, (current_frame.shape[1], current_frame.shape[0]), interpolation=interpolation
        )

    def _sidebar_box(self):
        return self.box_of_screen(*self.SIDEBAR_BOX, name="gift_character_sidebar")

    def _scroll_sidebar_to_top(self) -> None:
        for _ in range(3):
            self.operate(
                lambda: self.scroll_relative(
                    self.SIDEBAR_SCROLL_X, self.SIDEBAR_SCROLL_Y, self.SIDEBAR_RESET_STEP
                ),
                block=True,
            )
            self.sleep(0.25)

    def _scan_character_list(self, profiles: dict[str, dict], summary: dict) -> None:
        remaining = dict(profiles)
        self._scroll_sidebar_to_top()

        # First visit the five characters initially visible from top to bottom.  After that,
        # each small scroll only needs to recheck the fifth slot: five small scrolls move the
        # list roughly one avatar height, while completed profiles are removed from `remaining`.
        for y in self.CHARACTER_SLOT_YS:
            if not self._visit_character_slot(y, remaining, summary):
                return

        max_scrolls = self.MAX_SIDEBAR_PAGES * self.SIDEBAR_SCROLLS_PER_CHARACTER
        for _ in range(max_scrolls):
            if not remaining or summary["success"] >= self.MAX_TOTAL_GIFTS:
                return
            if self.scroll_and_is_end(
                self.SIDEBAR_SCROLL_X,
                self.SIDEBAR_SCROLL_Y,
                self.SIDEBAR_SCROLL_STEP,
                self._sidebar_box(),
                after_sleep=0.4,
            ):
                return
            if not self._visit_character_slot(self.CHARACTER_SLOT_YS[-1], remaining, summary):
                return

    def _visit_character_slot(self, y: float, remaining: dict[str, dict], summary: dict) -> bool:
        """Open one visible avatar and process it when its captured name matches."""
        if not remaining or summary["success"] >= self.MAX_TOTAL_GIFTS:
            return False
        self.operate_click(self.CHARACTER_SLOT_X, y)
        self.sleep(1)
        profile_id = self._match_current_profile(remaining)
        if profile_id:
            profile = remaining.pop(profile_id)
            self._give_profile(profile_id, profile, summary)
        return bool(remaining) and summary["success"] < self.MAX_TOTAL_GIFTS

    @staticmethod
    def _processed_profiles(summary: dict) -> set[str]:
        return set(summary.get("processed_profiles", set()))

    def _match_current_profile(self, profiles: dict[str, dict]) -> str | None:
        current_name_box = self.get_name_box()
        for profile_id, profile in profiles.items():
            frame = self.manager.load_frame(profile)
            if frame is None:
                continue
            frame = self.resize_captured_frame(frame)
            template = iu.trim_right_background(current_name_box.crop_frame(frame))
            if self.find_one(
                f"gift_name_{profile_id}",
                template=template,
                box=current_name_box.scale(1.1),
                threshold=self.NAME_MATCH_THRESHOLD,
            ):
                self._report(f"找到角色 {profile['display_name']}")
                return profile_id
        return None

    def _read_character_gift_remaining(self) -> int | None:
        box = self.box_of_screen(*self.COUNTER_BOX, name="gift_counter")
        results = self.ocr(box=box, match=self.COUNTER_RE)
        for result in results or []:
            match = self.COUNTER_RE.search(result.name)
            if match:
                return min(self.MAX_GIFTS_PER_CHARACTER, max(0, int(match.group(1))))
        return None

    def _find_gift_box(self, profile: dict):
        saved_frame = self.manager.load_frame(profile)
        if saved_frame is None:
            return None
        saved_frame = self.resize_captured_frame(saved_frame)
        current_boxes = self.get_gift_boxes()
        for saved_index in profile["selected_slots"]:
            if saved_index in profile.get("blocked_slots", []):
                continue
            template = current_boxes[saved_index].crop_frame(saved_frame)
            for current_box in current_boxes:
                if self.find_one(
                    f"gift_{profile['frame_id']}_{saved_index}",
                    template=template,
                    box=current_box.scale(1.1),
                    threshold=self.GIFT_MATCH_THRESHOLD,
                ):
                    self.log_debug(
                        f"matched gift slot configured={saved_index} current={current_box.name}"
                    )
                    return current_box
        self.log_info("Not found any selected gift")
        return None

    def _give_profile(self, profile_id: str, profile: dict, summary: dict) -> None:
        summary.setdefault("processed_profiles", set()).add(profile_id)
        remaining = self._read_character_gift_remaining()
        if remaining is None:
            summary["skipped"].append(f"{profile['display_name']}: 无法读取赠送次数")
            self.screenshot("gift_count_unreadable")
            return

        requested = min(profile["target_count"], self.MAX_GIFTS_PER_CHARACTER)
        sent = self.MAX_GIFTS_PER_CHARACTER - remaining
        if sent >= requested:
            summary["skipped"].append(f"{profile['display_name']}: 已达赠送目标 {sent}/{requested}")
            return

        while sent < requested and remaining > 0 and summary["success"] < self.MAX_TOTAL_GIFTS:
            from src.utils.game_filters import isolate_text_to_black

            if self.ocr(
                0.789, 0.188, 0.832, 0.252, match="10", frame_processor=isolate_text_to_black
            ):
                summary["skipped"].append(
                    f"{profile['display_name']}: 检测到好感度满级取消赠送; "
                    f"已赠送 {sent}/{requested}"
                )
                return
            gift_box = self._find_gift_box(profile)
            if gift_box is None:
                summary["skipped"].append(f"{profile['display_name']}: 首屏没有已配置礼物")
                self.screenshot("gift_not_found")
                return
            if not self._give_once(gift_box, remaining):
                summary["failed"].append(f"{profile['display_name']}: 点击后剩余赠送次数未减少")
                self.screenshot("gift_count_unchanged")
                return
            self.sleep(3)
            remaining -= 1
            sent += 1
            summary["success"] += 1
            self._report(f"{profile['display_name']} 已赠送 {sent}/{requested}")

    def _give_once(self, gift_box, previous_count: int) -> bool:
        if self.find_one(Labels.unlimit_gift, box=self.get_unlimit_gift_box(gift_box)):
            self.log_warning(f"检测到无限礼物，取消赠送: {gift_box.name}")
            return False
        self.operate_click(gift_box)
        self.sleep(0.3)
        self.operate_click(*self.SEND_BUTTON)

        def has_sent_gift():
            remaining = self._read_character_gift_remaining()
            return remaining is not None and remaining < previous_count

        return bool(
            self.wait_until(
                has_sent_gift,
                time_out=4,
                raise_if_not_found=False,
                settle_time=0.2,
            )
        )
