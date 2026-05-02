import threading
import time

from ok import Logger, TriggerTask
from qfluentwidgets import FluentIcon

from src.sound_trigger.SoundCombatContext import SoundCombatContext

logger = Logger.get_logger(__name__)


class SoundTriggerTask(TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.trigger_interval = 0.1
        self.name = "声音触发器"
        self.description = "基于游戏音效波形识别的自动闪避和反击功能,独立于战斗持续运行"
        self.icon = FluentIcon.VOLUME

        self._sound_context: SoundCombatContext | None = None
        self._listener_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _init_sound_trigger(self):
        if self._sound_context is not None:
            return

        self._sound_context = SoundCombatContext()
        self._sound_context.setup(
            task=self,
            enable_sound_trigger=True,
            sample_path="./闪避波形.wav",
            counter_attack_sample_path="./承轨反击波形.wav",
            threshold=0.13,
            counter_attack_threshold=0.12,
            thread_pool_size=4,
        )
        logger.info("SoundTriggerTask: Sound trigger initialized")

    def _listener_loop(self):
        sound_ctx = self._sound_context
        if sound_ctx is None:
            return

        sound_active = sound_ctx.enter()
        if not sound_active:
            logger.warning("SoundTriggerTask: Failed to start sound monitoring")
            return

        logger.info("SoundTriggerTask: Sound trigger monitoring started in background thread")

        try:
            while not self._stop_event.is_set():
                if self.paused:
                    self._stop_event.wait(timeout=0.2)
                    continue
                self._stop_event.wait(timeout=0.2)
        except Exception as e:
            logger.error(f"SoundTriggerTask: Error in listener loop: {e}")
        finally:
            sound_ctx.exit()
            logger.info("SoundTriggerTask: Sound trigger monitoring stopped")

    def run(self):
        self._init_sound_trigger()

        sound_ctx = self._sound_context
        if sound_ctx is None:
            logger.warning("SoundTriggerTask: Sound context not initialized")
            return False

        if self._listener_thread is not None and self._listener_thread.is_alive():
            logger.debug("SoundTriggerTask: Listener already running")
            return False

        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self._listener_loop,
            daemon=True,
            name="SoundTriggerListener"
        )
        self._listener_thread.start()
        logger.info("SoundTriggerTask: Background listener thread started")

        return False

    def disable(self):
        super().disable()
        self._stop_event.set()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=2.0)
            self._listener_thread = None
        logger.info("SoundTriggerTask: Disabled, listener thread stopped")
