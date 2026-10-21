"""Background services started after the application runtime becomes ready."""

import threading
from concurrent.futures import Future, InvalidStateError
from threading import Event

from ok import Logger, get_path_relative_to_exe

logger = Logger.get_logger(__name__)


class RuntimeServices:
    """Own startup and shutdown for the sound and OpenVINO runtime services."""

    def __init__(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("RuntimeServices must be constructed on the main thread")
        # comtypes initializes its importing thread, but its atexit cleanup runs
        # on the main thread. Load it here before any audio worker can import it.
        import comtypes  # noqa: F401

        self._stop_event = Event()
        self._started = False
        self._openvino_model_async = None
        self._openvino_model_future = Future()

    def start(self) -> None:
        if self._started or self._stop_event.is_set():
            return
        self._started = True
        threading.Thread(
            target=self._init_sound_context,
            daemon=True,
            name="SoundContextInit",
        ).start()
        threading.Thread(
            target=self._init_openvino,
            daemon=True,
            name="OpenVINOInit",
        ).start()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._openvino_model_future.cancel()

    @property
    def openvino_model_async(self):
        if self._openvino_model_async is not None:
            return self._openvino_model_async
        return self._openvino_model_future.result()

    @property
    def openvino_latency_async(self):
        return self._openvino_model_async.latency

    @property
    def openvino_latest_image(self):
        return self._openvino_model_async.latest_image if self._openvino_model_async else None

    @property
    def openvino_available(self):
        return self.openvino_model_async._openvino_available

    def openvino_detect(
        self, image, sync=False, box=None, threshold=0.5, force=False, mask_regions=None
    ):
        if not sync:
            return self.openvino_model_async.detect(
                image,
                box=box,
                threshold=threshold,
                label="target",
                force=force,
                mask_regions=mask_regions,
            )
        return self.openvino_model_async.detect_sync(
            image,
            box=box,
            threshold=threshold,
            label="target",
            mask_regions=mask_regions,
        )

    def openvino_clear_cache(self) -> None:
        self.openvino_model_async.clear_cache()

    def _init_sound_context(self) -> None:
        if self._stop_event.is_set():
            return
        context = None
        try:
            from src.sound_trigger.SoundCombatContext import SoundCombatContext

            if self._stop_event.is_set():
                return
            context = SoundCombatContext()
            context.setup(
                task=None,
                sample_path=get_path_relative_to_exe("assets", "sounds", "dodge.wav"),
                counter_attack_sample_path=get_path_relative_to_exe(
                    "assets", "sounds", "counter.wav"
                ),
                initialization_stop_event=self._stop_event,
            )
            if self._stop_event.is_set():
                return
            if context.enter() and not self._stop_event.is_set():
                logger.info("SoundCombatContext initialized globally")
                self._stop_event.wait()
        except Exception:
            if not self._stop_event.is_set():
                logger.exception("Failed to initialize SoundCombatContext")
        finally:
            # Keep setup and teardown on the same worker. Stopping must not
            # import audio dependencies or race an unfinished setup.
            if context is not None:
                try:
                    context.shutdown()
                except Exception:
                    logger.exception("Failed to shut down SoundCombatContext")

    def _init_openvino(self) -> None:
        if self._stop_event.is_set():
            return
        try:
            logger.info("openvino_model_async Using YOLO26OpenVINOAsyncDetector")
            from src.vision.openvino_detector import YOLO26OpenVINOAsyncDetector

            detector = YOLO26OpenVINOAsyncDetector(
                xml_path=get_path_relative_to_exe("assets", "openvino", "best.xml")
            )
        except BaseException as error:
            self._publish_openvino_failure(error)
        else:
            self._publish_openvino_detector(detector)

    def _publish_openvino_failure(self, error: BaseException) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._openvino_model_future.set_exception(error)
        except InvalidStateError:
            return
        logger.error(f"OpenVINO detector initialization failed: {error}")

    def _publish_openvino_detector(self, detector) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._openvino_model_future.set_result(detector)
        except InvalidStateError:
            return
        self._openvino_model_async = detector
