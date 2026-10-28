import re
import time

from ok import TaskDisabledException

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask, interac_pink_color
from src.tasks.NTEOneTimeTask import NTEOneTimeTask
from src.tasks.trigger.SkipDialogTask import SkipDialogTask
from src.utils import image_utils as iu


class FountainTask(NTEOneTimeTask, BaseNTETask):
    CONF_SIGN_MODE = "签到方式"
    SIGN_MODE_SIGN = "签到"
    SIGN_MODE_COIN = "捞币"
    DOMAIN_ENTRY_POS = (0.668, 0.150)
    DOMAIN_CONFIRM_POS = (0.917, 0.335)
    PHONE_BOOTH_BOX = (0.300, 0.420, 0.400, 0.545)
    ICECAR_LIGHT_BOX = (0.650, 0.350, 0.885, 0.600)
    FOUNTAIN_SIGN_COUNT_BOX = (0.695, 0.492, 0.771, 0.650)
    FOUNTAIN_SIGN_BTN_BOX = (0.655, 0.570, 0.790, 0.645)
    BOOKSHOP_ICON_TIMEOUT = 15
    INTERAC_TIMEOUT = 30
    SIGN_SKIP_TIMEOUT = 20
    TASK_TIMEOUT = 180
    TASK_RETRY_COUNT = 1
    FOUNTAIN_SIGN_COUNT_RE = re.compile(r"(\d+)/(\d+)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._check_confirm_timer = 0
        self.name = "喷泉签到"
        self.visible = False
        self.default_config.update({self.CONF_SIGN_MODE: self.SIGN_MODE_SIGN})
        self.config_type.update(
            {
                self.CONF_SIGN_MODE: {
                    "type": "drop_down",
                    "options": [self.SIGN_MODE_SIGN, self.SIGN_MODE_COIN],
                }
            }
        )

    def run(self):
        super().run()
        try:
            self.do_run()
        except TaskDisabledException:
            raise
        except Exception as e:
            self.log_error("FountainTask Error", e, notify=True)
            raise

    def do_run(self) -> bool:
        # try init ocr
        self.ocr(box=self.pos.screen.center.to_box())

        sign_mode = self.config.get(self.CONF_SIGN_MODE, self.SIGN_MODE_SIGN)
        last_error = None
        for attempt in range(1, self.TASK_RETRY_COUNT + 2):
            self._fountain_task_start = self.clock.now()
            self.log_info(f"attempt {attempt}/{self.TASK_RETRY_COUNT + 1}")
            try:
                if self.run_fountain_flow(sign_mode):
                    return True
            except TaskDisabledException:
                raise
            except Exception as e:
                last_error = e
                self.log_warning(f"attempt {attempt} failed: {e}")
            finally:
                self._fountain_task_start = None
                self.release_fountain_move_keys()

            if attempt <= self.TASK_RETRY_COUNT:
                self.log_info("retry once")

        if last_error is not None:
            raise last_error
        raise RuntimeError("failed after retries")

    def run_fountain_flow(self, sign_mode):
        self.transport_to_fountain_teleport()
        self.check_fountain_task_timeout()
        self.run_to_fountain()
        self.check_fountain_task_timeout()
        result = self.fountain_sign_in(sign_mode)
        self.check_fountain_task_timeout()
        return result

    def check_fountain_task_timeout(self):
        start = getattr(self, "_fountain_task_start", None)
        if start is not None and self.clock.now() - start >= self.TASK_TIMEOUT:
            raise TimeoutError(f"timed out after {self.TASK_TIMEOUT}s")

    def release_fountain_move_keys(self):
        self.send_key_up("w")
        self.send_key_up("a")
        self.send_key_up("d")

    def transport_to_fountain_teleport(self):
        self.ensure_main(time_out=30)
        self.open_f1_domain_page()
        self.operate_click(*self.DOMAIN_ENTRY_POS, after_sleep=1)
        self.operate_click(*self.DOMAIN_CONFIRM_POS, after_sleep=2)
        self.click_traval_button()
        self.wait_in_team(time_out=300, settle_time=0.25)
        self.sleep(0.5)
        box = self.box_of_screen(*self.PHONE_BOOTH_BOX, name="fountain_phone_booth")
        self.click_map_teleport(box)
        self.wait_in_team(time_out=300, settle_time=0.25)
        self.sleep(1)

    def run_to_fountain(self):
        self.sleep(0.2)
        self.repeat_mid_click()
        try:
            self.send_key_down("a", after_sleep=0.4)
            self.send_key("lshift", after_sleep=0.4)
            self.wait_until(
                lambda: self.find_bookshop_icon(
                    box=self.box_of_screen(0.0930, 0.1720, 0.1066, 0.1986, hcenter=True)
                ),
                time_out=self.BOOKSHOP_ICON_TIMEOUT,
                raise_if_not_found=True,
            )
        finally:
            self.send_key_up("a")
        self.sleep(0.2)
        self.repeat_mid_click()
        try:
            self.send_key_down("a", after_sleep=0.2)
            self.wait_until(
                lambda: self.find_bookshop_icon(
                    box=self.box_of_screen(0.080, 0.180, 0.096, 0.210, hcenter=True)
                ),
                time_out=self.BOOKSHOP_ICON_TIMEOUT,
                raise_if_not_found=True,
            )
        finally:
            self.send_key_up("a")

        try:
            self.send_key_down("w", after_sleep=0.4)
            self.send_key("lshift", after_sleep=0.4)
            self.sleep(5)
            self.wait_until(
                lambda: self.find_bookshop_icon(
                    box=self.box_of_screen(0.048, 0.051, 0.063, 0.080, hcenter=True)
                ),
                time_out=40,
                raise_if_not_found=True,
            )
        finally:
            self.send_key_up("w")

        self.send_key("d", down_time=0.4)
        self.sleep(0.2)
        self.repeat_mid_click()

        def find_sign():
            ret = self.find_interac() and self.ocr(
                *self.FOUNTAIN_SIGN_COUNT_BOX, match=self.FOUNTAIN_SIGN_COUNT_RE
            )
            return bool(ret)

        try:
            self.send_key_down("w", after_sleep=0.2)
            self.send_key("lshift", after_sleep=0.2)
            self.wait_until(
                lambda: self.find_fountain_icon(
                    box=self.box_of_screen(0.059, 0.168, 0.075, 0.199, hcenter=True)
                ),
                time_out=40,
                raise_if_not_found=True,
            )
        finally:
            self.send_key_up("w")

        self.sleep(0.1)

        try:
            self.send_key_down("a", after_sleep=0.2)
            self.send_key("lshift", after_sleep=0.2)
            self.wait_until(
                lambda: self.find_fountain_icon(
                    box=self.box_of_screen(0.048, 0.144, 0.064, 0.172, hcenter=True)
                ),
                time_out=10,
                raise_if_not_found=True,
            )
        finally:
            self.send_key_up("a")

        self.sleep(0.1)

        try:
            self.send_key_down("w", after_sleep=0.2)
            self.wait_until(
                find_sign,
                time_out=self.INTERAC_TIMEOUT,
                raise_if_not_found=True,
            )
        finally:
            self.send_key_up("w")

        def action():
            if find_sign():
                return True
            self.send_key("w", down_time=0.5)
            self.sleep(1)

        self.retry_on_action(action=action, attempt=5, raise_if_failed=True)

    def repeat_mid_click(self):
        self.middle_click(after_sleep=0.2)
        self.middle_click(after_sleep=0.2)
        self.middle_click(after_sleep=1)

    def find_bookshop_icon(self, box):
        return self.find_one(Labels.bookshop_icon, box=box)

    def find_fountain_icon(self, box):
        return self.find_one(Labels.fountain_icon, box=box)

    def fountain_sign_in(self, sign_mode):
        sign_count, _ = self.read_fountain_sign_count()
        if sign_count == -1:
            self.log_warning("喷泉签到OCR识别次数失败")
            return False
        if sign_count == 0:
            self.log_info("当日已经完成喷泉签到")
            return True
        if sign_count != 1:
            self.log_warning(f"识别到未知喷泉签到次数 {sign_count}, 喷泉签到失败")
            return False

        def merged_action(click):
            self.send_key_down("lalt")
            time.sleep(0.1)
            self.click(click, move=True)
            time.sleep(0.1)
            self.send_key_up("lalt")

        def pre_action():
            if box := self.read_fountain_sign_count()[1]:
                self.run_with_interval(
                    lambda: self.operate(lambda: merged_action(box), block=True), interval=2
                )

        sign_btn = self.wait_until(
            self.find_sign_in_btn,
            pre_action=pre_action,
            time_out=self.INTERAC_TIMEOUT,
            raise_if_not_found=True,
        )
        self.click_sign_action(sign_btn, sign_mode)
        if not self.wait_skip_dialog_until_world(self.SIGN_SKIP_TIMEOUT):
            self.log_warning("对话异常，无法返回大世界")
            return False
        signed_count, _ = self.read_fountain_sign_count()
        if signed_count == 0:
            self.click_nearest_map_teleport()
            self.log_info("喷泉签到完成")
            return True

        self.log_warning(f"喷泉签到失败, 当前可签到次数={signed_count}")
        return False

    def find_sign_in_btn(self):
        box = self.box_of_screen(*self.FOUNTAIN_SIGN_BTN_BOX, name="fountain_sign_btn_area")
        regions = iu.find_color_enriched_regions(
            interac_pink_color,
            box,
            self.frame,
            min_area=0.03,
        )
        if not regions:
            return None
        return max(regions, key=lambda region: region.width * region.height)

    def click_sign_action(self, sign_btn, sign_mode):
        self.log_info("识别确定点击选项")
        target = sign_btn
        if sign_mode == self.SIGN_MODE_COIN:
            target = sign_btn.copy(
                y_offset=self.height_of_screen(0.07),
                name="fountain_sign_coin_target",
            )
        self.operate_click(target, after_sleep=1)

    def find_skip(self):
        return SkipDialogTask.find_skip(self)

    def try_click_skip(self):
        return SkipDialogTask.try_click_skip(self)

    def skip_confirm(self):
        return SkipDialogTask.skip_confirm(self)

    def check_skip(self):
        return SkipDialogTask.check_skip(self)

    def wait_skip_dialog_until_world(self, time_out=10):
        def check_skip_and_world():
            self.check_skip()
            return self.in_team_and_world()

        return self.wait_until(
            check_skip_and_world,
            time_out=time_out,
            raise_if_not_found=False,
        )

    def read_fountain_sign_count(self):
        timer = self.clock.timer(timeout=10)
        while not timer.expired():
            if results := self.wait_ocr(
                *self.FOUNTAIN_SIGN_COUNT_BOX,
                match=self.FOUNTAIN_SIGN_COUNT_RE,
                time_out=2,
                raise_if_not_found=False,
            ):
                break
            else:
                self.send_key("a")
        if not results:
            self.log_warning("fountain sign OCR raw results: []")
            return -1, None

        if match := self.FOUNTAIN_SIGN_COUNT_RE.search(results[0].name):
            sign_count = int(match.group(1))
            self.log_info(f"fountain sign OCR parsed digit: {sign_count}")
            return sign_count, results[0]
        return -1, None
