import threading
import time
from dataclasses import dataclass

from ok import Logger, TaskDisabledException

from src.char.core.CharFactory import (
    get_char_by_id,
    get_char_feature_by_pos,
    get_char_implementation_class,
    iter_char_implementations,
)
from src.char.custom.CustomCharManager import CustomCharManager
from src.combat.BaseCombatTask import BaseCombatTask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask

logger = Logger.get_logger(__name__)


@dataclass(frozen=True)
class TeamScanResult:
    index: int
    image: object
    width: int
    height: int
    character_id: str
    confidence: float | None


class DebugCharTask(NTEOneTimeTask, BaseCombatTask):
    """Hidden one-time character tools used by the character-management tabs."""

    MODE_SCAN_TEAM = "scan_team"
    MODE_TEST_COMBO = "test_combo"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "角色工具"
        self.description = "角色特征扫描和出招表测试"
        self.visible = False
        self.char = None
        self.is_char_loaded = False
        self.char_list = [entry.impl_id for entry in iter_char_implementations()]
        self.default_config.update({"char": self.char_list[0], "is_current_char": True})
        self.config_description.update(
            {
                "is_current_char": "开发工具调用角色方法时, 开启表示出场角色; 关闭表示后台角色",
            }
        )
        self.config_type.update(
            {
                "char": {
                    "type": "drop_down",
                    "options": self.char_list,
                },
            }
        )
        self.mode: str | None = None
        self.scan_results: tuple[TeamScanResult, ...] = ()
        self.result_error = ""
        self._combo_character_id = ""
        self._combo_implementation_id = ""
        self._combo_text = ""
        self._character_management_ocr_lock = threading.Lock()

    def scan_team(self) -> None:
        self.mode = self.MODE_SCAN_TEAM
        self.scan_results = ()
        self.result_error = ""

    def test_combo(self, character_id: str, implementation_id: str, combo_text: str) -> None:
        self.mode = self.MODE_TEST_COMBO
        self.result_error = ""
        self._combo_character_id = character_id
        self._combo_implementation_id = implementation_id
        self._combo_text = combo_text

    def run(self):
        mode = self.mode
        try:
            super().run()
            if mode == self.MODE_SCAN_TEAM:
                self._scan_team()
            elif mode == self.MODE_TEST_COMBO:
                self._test_combo()
        except TaskDisabledException:
            if mode:
                self.result_error = self.tr("任务已停止")
            raise
        except Exception as error:
            self.result_error = str(error).strip() or error.__class__.__name__
            logger.error("Character tool failed", error)
        finally:
            self.mode = None

    def _scan_team(self) -> None:
        try:
            in_team, _, count = self.in_team()
            if not in_team or count == 0:
                raise RuntimeError(self.tr("队伍不存在"))
            if count < 2:
                raise RuntimeError(self.tr("队伍人数少于2人"))

            manager = CustomCharManager()
            results = []
            frame = self.frame
            for index in range(count):
                image, width, height = get_char_feature_by_pos(self, index, frame=frame)
                if image is None or image.size <= 0:
                    continue
                _, character_id, confidence = manager.match_feature(self, image)
                results.append(
                    TeamScanResult(index, image, width, height, character_id, confidence)
                )
            self.scan_results = tuple(results)
        except Exception as error:
            self.result_error = str(error).strip() or error.__class__.__name__
            logger.error("Team scan failed", error)

    def _test_combo(self) -> None:
        from src.char.custom.CustomChar import CustomChar

        if self._combo_implementation_id:
            test_char = get_char_by_id(
                self,
                index=0,
                char_id=self._combo_character_id,
                impl_id=self._combo_implementation_id,
            )
        else:
            test_char = CustomChar(self, index=0, char_id=self._combo_character_id)

        original_ocr = self.ocr
        original_chars = self.chars
        original_sleep = self.sleep

        def locked_ocr(*args, **kwargs):
            with self._character_management_ocr_lock:
                return original_ocr(*args, **kwargs)

        def direct_sleep(timeout):
            if timeout > 0:
                time.sleep(timeout)
            return True

        self.ocr = locked_ocr
        self.chars = [test_char]
        self.sleep = direct_sleep
        try:
            test_char.is_current_char = True
            test_char.switch_next_char = lambda *args, **kwargs: None
            if isinstance(test_char, CustomChar):
                test_char.combo_str = self._combo_text
                test_char._compile_combo()
            test_char.perform()
        finally:
            self.sleep = original_sleep
            self.chars = original_chars
            self.ocr = original_ocr

    @staticmethod
    def _normalize_impl_id(impl_id):
        impl_id = str(impl_id)
        if impl_id.startswith("char_"):
            return f"builtin:{impl_id.removeprefix('char_')}"
        return impl_id

    def _selected_impl_id(self, warn=False):
        impl_id = self._normalize_impl_id(self.config["char"])
        if get_char_implementation_class(impl_id) is not None:
            return impl_id

        fallback_id = self.char_list[0]
        if warn:
            self.log_warning(
                f"Unknown character implementation '{impl_id}'; using '{fallback_id}' instead"
            )
        return fallback_id

    def init_char(self):
        self.current_char = self._selected_impl_id(warn=True)
        char_class = get_char_implementation_class(self.current_char)
        self.char = char_class(self, 0, char_id=self.current_char, confidence=1)  # type: ignore

    def __getattr__(self, name):
        try:
            if self.char is None or self.current_char != self._selected_impl_id():
                self.is_char_loaded = False
                self.init_char()
            if hasattr(self.char, name):
                if not self.is_char_loaded:
                    self.is_char_loaded = True
                    self.load_chars()
                self.char.is_current_char = self.config.get("is_current_char", True)
                return getattr(self.char, name)
        except AttributeError:
            raise AttributeError(
                f"'{type(self).__name__}' or its member 'char' has no attribute '{name}'"
            )
        return super().__getattr__(name)
