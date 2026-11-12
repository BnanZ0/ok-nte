"""Background services started after the application runtime becomes ready."""

import threading
from concurrent.futures import CancelledError, Future, InvalidStateError
from threading import Event

from ok import Logger, get_path_relative_to_exe

logger = Logger.get_logger(__name__)

_OPENVINO_MODELS = {
    "target": (("target", "best.xml"), {"class_names": ["target"]}),
}


def create_openvino_detector(model, detector_type=None):
    if detector_type is None:
        from src.vision.openvino_detector import YOLO26OpenVINOAsyncDetector

        detector_type = YOLO26OpenVINOAsyncDetector
    path, options = _OPENVINO_MODELS[model]
    return detector_type(
        xml_path=get_path_relative_to_exe("assets", "openvino", *path),
        model_name=model,
        **options,
    )


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
        self._openvino_futures: dict[str, Future] = {}
        self._openvino_lock = threading.Lock()
        self._openvino_model_locks = {model: threading.Lock() for model in _OPENVINO_MODELS}

    def start(self, *, preload_models=()) -> None:
        if self._started or self._stop_event.is_set():
            return
        self._started = True
        threading.Thread(
            target=self._init_sound_context,
            daemon=True,
            name="SoundContextInit",
        ).start()
        for model in preload_models:
            self._get_openvino_future(model)

    def stop(self) -> None:
        with self._openvino_lock:
            if self._stop_event.is_set():
                return
            self._stop_event.set()
            for future in self._openvino_futures.values():
                future.cancel()

    def get_openvino_detector(self, model, *, wait=True):
        """Return the detector, or peek at a loaded detector when wait is false."""
        if wait:
            return self._get_openvino_future(model).result()
        with self._openvino_lock:
            future = self._openvino_futures.get(model)
        if future is None or not future.done() or future.cancelled():
            return None
        if future.exception() is None:
            return future.result()
        return None

    def replace_openvino_detector(self, model, detector):
        """Replace a detector and return the previous instance to the caller."""
        with self._openvino_model_locks[model]:
            previous = self.get_openvino_detector(model)
            replacement = Future()
            replacement.set_result(detector)
            with self._openvino_lock:
                self._openvino_futures[model] = replacement
            return previous

    def openvino_detect(
        self,
        image,
        sync=False,
        box=None,
        threshold=0.5,
        force=False,
        mask_regions=None,
        *,
        model,
        label=None,
    ):
        options = {"box": box, "threshold": threshold, "label": label, "mask_regions": mask_regions}
        with self._openvino_model_locks[model]:
            detector = self.get_openvino_detector(model)
            if self._stop_event.is_set():
                raise CancelledError()
            if sync:
                return detector.detect_sync(image, **options)
            return detector.detect(image, force=force, **options)

    def release_openvino_model(self, model) -> None:
        with self._openvino_model_locks[model]:
            with self._openvino_lock:
                future = self._openvino_futures.pop(model, None)
        if future is not None and not future.cancel() and future.exception() is None:
            # A cancelled native request can still be finishing on its worker.
            threading.Thread(
                target=future.result().release, daemon=True, name="OpenVINORelease"
            ).start()

    def openvino_clear_cache(self, model) -> None:
        with self._openvino_model_locks[model]:
            self.get_openvino_detector(model).clear_cache()

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

    def _get_openvino_future(self, model) -> Future:
        if model not in _OPENVINO_MODELS:
            raise KeyError(model)
        with self._openvino_lock:
            future = self._openvino_futures.get(model)
            if future is not None:
                return future
            future = Future()
            if self._stop_event.is_set():
                future.cancel()
                return future
            threading.Thread(
                target=self._init_openvino,
                args=(model, future),
                daemon=True,
                name=f"OpenVINOInit-{model}",
            ).start()
            self._openvino_futures[model] = future
            return future

    def _init_openvino(self, model, future) -> None:
        if future.cancelled():
            return
        try:
            try:
                detector = create_openvino_detector(model)
            except BaseException as error:
                future.set_exception(error)
                logger.error(f"OpenVINO {model} detector initialization failed: {error}")
            else:
                future.set_result(detector)
        except InvalidStateError:
            # stop() cancelled the future while the model was loading.
            return
