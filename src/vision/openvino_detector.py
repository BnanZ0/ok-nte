import ctypes
import threading

import numpy as np
from ok import Box, Logger, og
from ok.ui.qt.Communicate import communicate

from src.utils import clock

logger = Logger.get_logger(__name__)


class YOLO26OpenVINOAsyncDetector:
    _PF_AVX2_INSTRUCTIONS_AVAILABLE = 40
    _MAX_ACTIVE_RETIRED_INFER_REQUESTS = 3
    _MAX_RETIRED_INFER_REQUESTS = 10
    _RETIRED_REQUEST_KEEP_SECONDS = 3.0
    _SYNC_WAIT_TIMEOUT = 1.5

    def __init__(self, xml_path, num_requests=1):
        self._openvino_available = self._supports_avx2()
        self._openvino_unavailable_reason = None
        if not self._openvino_available:
            message = og.app.tr(
                "当前 CPU 不支持 AVX2, 无法运行 OpenVINO 2026 CPU 推理；"
                "自动战斗的目标检测将不可用，交战流畅度将受到影响。"
            )
            self._openvino_unavailable_reason = message
            logger.error(message)
            communicate.notification.emit(message, "OpenVINO", True, True, None, None, None)
            self.latest_results = False
            self.latest_image = None
            self.latency = 0.0
            return

        from openvino import Core, Layout, PartialShape, Type
        from openvino.preprocess import ColorFormat, PrePostProcessor, ResizeAlgorithm

        self.core = Core()
        model = self.core.read_model(model=xml_path)

        # 1. 配置预处理 (PPP) - 支持动态输入分辨率
        ppp = PrePostProcessor(model)

        # 声明输入的 Tensor 信息
        ppp.input().tensor().set_shape(PartialShape([1, -1, -1, 3])).set_element_type(
            Type.u8
        ).set_color_format(ColorFormat.BGR).set_layout(Layout("NHWC"))

        # 在预处理步骤中进行转换
        ppp.input().preprocess().convert_element_type(Type.f32).convert_color(
            ColorFormat.RGB
        ).resize(ResizeAlgorithm.RESIZE_LINEAR).scale([255.0, 255.0, 255.0])

        ppp.input().model().set_layout(Layout("NCHW"))
        model = ppp.build()

        # 2. 编译模型 (针对 AMD CPU 优化)
        config = {
            "PERFORMANCE_HINT": "LATENCY",
            "INFERENCE_NUM_THREADS": "2",  # 限制线程至 2，降低 CPU 峰值负载
        }
        self.compiled_model = self.core.compile_model(model, "CPU", config)

        self.model_h = 896
        self.model_w = 1536
        self.model_ratio = self.model_w / self.model_h

        # 3. 直接管理 request。force 可以安全取消当前 request 后立即提交新帧。
        self.num_requests = num_requests
        self._state_lock = threading.RLock()
        self._retired_infer_requests = []
        self._active_request_jobs = {}
        self._active_requests = {}

        # 内部状态
        self.latest_results = None
        self.latest_image = None
        self.class_names = ["target"]  # 可根据 data.yaml 修改
        self.latency = 0.0  # 单次推理总耗时 (秒)
        self.job_id = 0
        self._force_next_submit = False
        self.infer_request = self._create_infer_request()

    @classmethod
    def _supports_avx2(cls) -> bool:
        """Return whether the current process can use AVX2 instructions."""

        return cls._windows_supports_avx2() or cls._numpy_supports_avx2()

    @classmethod
    def _windows_supports_avx2(cls) -> bool:
        """Use the Windows AVX2 feature flag when the OS exposes it."""
        try:
            is_feature_available = ctypes.windll.kernel32.IsProcessorFeaturePresent
        except AttributeError:
            return False
        return bool(is_feature_available(cls._PF_AVX2_INSTRUCTIONS_AVAILABLE))

    @staticmethod
    def _numpy_supports_avx2() -> bool:
        """Fall back to NumPy's process-level CPU feature detection.

        Windows only started reporting ``PF_AVX2_INSTRUCTIONS_AVAILABLE`` in
        Windows 10 version 2004. Older LTSC releases otherwise falsely report
        that supported CPUs lack AVX2.
        """

        try:
            return bool(np._core._multiarray_umath.__cpu_features__.get("AVX2"))
        except AttributeError:
            return False

    def _create_infer_request(self):
        return self.compiled_model.create_infer_request()

    def _get_active_retired_count(self):
        with self._state_lock:
            return sum(
                1
                for record in self._retired_infer_requests
                if self._active_request_jobs.get(id(record["request"]), 0) > 0
            )

    def _cleanup_retired_infer_requests(self):
        with self._state_lock:
            # 必须延迟销毁 request！如果因为 active_jobs 降为 0 就立刻从列表中移除对象，
            # 此时 C++ 回调线程可能尚未完全退出。Python GC 此时调用析构函数，
            # 将导致 GIL 锁死，造成软件完全卡死且无法恢复。
            now = clock.now()
            active_records = []
            inactive_records = []
            for record in self._retired_infer_requests:
                request_id = id(record["request"])
                active = self._active_request_jobs.get(request_id, 0) > 0
                still_warm = now - record["retired_at"] < self._RETIRED_REQUEST_KEEP_SECONDS
                if active or still_warm:
                    active_records.append(record)
                else:
                    inactive_records.append(record)

            keep_slots = max(0, self._MAX_RETIRED_INFER_REQUESTS - len(active_records))
            inactive_to_keep = inactive_records[-keep_slots:] if keep_slots > 0 else []
            self._retired_infer_requests = active_records + inactive_to_keep

    def _mark_request_job_started(self, infer_request):
        with self._state_lock:
            request_id = id(infer_request)
            self._active_request_jobs[request_id] = self._active_request_jobs.get(request_id, 0) + 1
            self._active_requests[request_id] = infer_request
            return request_id

    def _mark_request_job_finished(self, request_id):
        with self._state_lock:
            pending_jobs = self._active_request_jobs.get(request_id, 0) - 1
            if pending_jobs > 0:
                self._active_request_jobs[request_id] = pending_jobs
            else:
                self._active_request_jobs.pop(request_id, None)
                self._active_requests.pop(request_id, None)

    def _request_has_active_jobs(self, infer_request):
        with self._state_lock:
            return self._active_request_jobs.get(id(infer_request), 0) > 0

    def _retire_request(self, infer_request, cancel=True):
        retired_at = clock.now()
        self._retired_infer_requests.append(
            {
                "request": infer_request,
                "retired_at": retired_at,
            }
        )
        if cancel:
            try:
                infer_request.cancel()
            except Exception:
                logger.exception("openvino cancel infer request failed")
            finally:
                # OpenVINO 2026.3 does not invoke the Python completion callback
                # when cancel() aborts a request.  Do not leave the request marked
                # active forever, or repeated sync timeouts will exhaust request
                # rotation and make every later detection appear stuck.
                self._mark_request_job_finished(id(infer_request))

    def _try_rotate_busy_request(self):
        with self._state_lock:
            self._cleanup_retired_infer_requests()
            if not self._request_has_active_jobs(self.infer_request):
                return True
            if self._get_active_retired_count() >= self._MAX_ACTIVE_RETIRED_INFER_REQUESTS:
                return False

            # Cancel the directly-owned request so the force frame starts immediately.
            self.job_id += 1
            self._retire_request(self.infer_request, cancel=True)
            self.infer_request = self._create_infer_request()
            return True

    def _callback(self, user_data):
        """异步推理完成后的回调函数"""
        request_id = user_data.get("request_id")
        try:
            with self._state_lock:
                infer_request = self._active_requests.get(request_id)
            if infer_request is None:
                logger.warning("openvino callback ignored unknown request")
                return

            job_id = user_data.get("job_id", 0)
            if job_id < self.job_id:
                return

            start_time = user_data["start_time"]
            self.latency = clock.now() - start_time

            detections = infer_request.get_output_tensor().data[0]

            box = user_data["box"]
            threshold = user_data["threshold"]
            target_label = user_data["label"]
            pad_x = user_data["pad_x"]
            pad_y = user_data["pad_y"]

            # 1. 画布相较于模型的缩放比例
            scale = user_data["target_w"] / self.model_w

            tmp_results = []
            for x1, y1, x2, y2, conf, cls_id in detections:
                if conf < threshold:
                    continue

                name = (
                    self.class_names[int(cls_id)]
                    if int(cls_id) < len(self.class_names)
                    else "unknown"
                )
                if target_label and name != target_label:
                    continue

                # 2. 从 AI 的坐标还原到带灰边的 Canvas 坐标
                canvas_x1 = x1 * scale
                canvas_y1 = y1 * scale
                canvas_w = (x2 - x1) * scale
                canvas_h = (y2 - y1) * scale

                # 3. 减去灰边的偏移量，得到在输入 input_crop 中的坐标
                # 再加上外面传进来的 Box 原图坐标，直接映射到全屏
                abs_x = int(canvas_x1 - pad_x + box.x)
                abs_y = int(canvas_y1 - pad_y + box.y)

                tmp_results.append(
                    Box(
                        x=abs_x,
                        y=abs_y,
                        width=int(canvas_w),
                        height=int(canvas_h),
                        confidence=float(conf),
                        name=name,
                    )
                )

            self.latest_results = tmp_results
            self.latest_image = user_data.get("image")
            result_holder = user_data.get("result_holder")
            if result_holder is not None:
                result_holder["results"] = tmp_results
        except Exception as e:
            logger.error("openvino callback ignored failed/cancelled task", e)
        finally:
            if request_id is not None:
                self._mark_request_job_finished(request_id)
            done_event = user_data.get("done_event")
            if done_event is not None:
                done_event.set()

    def debug_state(self):
        if not self._openvino_available:
            reason = self._openvino_unavailable_reason or "disabled"
            return f"openvino(unavailable: {reason})"
        with self._state_lock:
            active_jobs = sum(self._active_request_jobs.values())
            active_retired = self._get_active_retired_count()
            retired = len(self._retired_infer_requests)
            request_active = self._request_has_active_jobs(self.infer_request)
            latest_count = None if self.latest_results is None else len(self.latest_results)
            return (
                f"openvino(request_active={request_active}, active_jobs={active_jobs}, "
                f"retired={retired}, active_retired={active_retired}, "
                f"latest_count={latest_count}, latency={self.latency:.3f}, "
                f"force_next={self._force_next_submit}, job_id={self.job_id})"
            )

    def _detect(
        self,
        image,
        box: Box = None,
        threshold=0.5,
        label="target",
        force=False,
        mask_regions=None,
        done_event=None,
        result_holder=None,
    ):
        """
        发起异步检测
        :param image: 全图 (numpy array)
        :param box: 指定检测区域的 Box 实例。如果为 None, 则检测全图。
        :param threshold: 置信度阈值
        :param label: 指定检测的类别名称
        :param force: 如果为 True, 取消忙碌的当前 request 并立刻提交新任务
        :param mask_regions: 需要屏蔽的全图归一化区域列表，格式为
            [(x1, y1, x2, y2), ...]。屏蔽会应用到推理画布，不修改原图。
        :return: list[Box] (返回的是上一帧或最近一次完成的结果)
        """

        if not self._openvino_available:
            return False, False

        submitted = False
        self._cleanup_retired_infer_requests()
        force_submit = force or self._force_next_submit
        if self._request_has_active_jobs(self.infer_request):
            if not force_submit or not self._try_rotate_busy_request():
                return self.latest_results, submitted

        h, w = image.shape[:2]

        if box is None:
            box = Box(x=0, y=0, width=w, height=h)

        # 1. 切片提取原始 ROI
        input_crop = image[
            max(0, box.y) : min(h, box.y + box.height),
            max(0, box.x) : min(w, box.x + box.width),
        ]

        crop_h, crop_w = input_crop.shape[:2]
        if crop_h == 0 or crop_w == 0:
            return self.latest_results, submitted  # 防止出界错误

        # 2. 补边逻辑：算出需要补多少灰边，让比例等于 model_ratio
        crop_ratio = crop_w / crop_h
        pad_x, pad_y = 0, 0

        if crop_ratio < self.model_ratio:
            # 框太瘦高了，左右补边
            target_h = crop_h
            target_w = int(crop_h * self.model_ratio)
            pad_x = (target_w - crop_w) // 2
        else:
            # 框太扁宽了，上下补边
            target_w = crop_w
            target_h = int(crop_w / self.model_ratio)
            pad_y = (target_h - crop_h) // 2

        # 3. 创建灰底画布并贴图 (耗时极短，保留 PPP 优势)
        canvas = np.full((target_h, target_w, 3), 114, dtype=np.uint8)
        canvas[pad_y : pad_y + crop_h, pad_x : pad_x + crop_w] = input_crop
        self._apply_canvas_mask(
            canvas,
            mask_regions,
            image_shape=(h, w),
            box=box,
            pad_x=pad_x,
            pad_y=pad_y,
        )

        input_tensor = np.expand_dims(canvas, axis=0)

        with self._state_lock:
            # 在准备输入张量期间，另一个调用可能已提交任务；重新确认后再启动，
            # 防止两个线程同时操作同一个 request。
            if self._request_has_active_jobs(self.infer_request):
                if not force_submit or not self._try_rotate_busy_request():
                    return self.latest_results, submitted

            self.job_id += 1
            current_job_id = self.job_id
            infer_request = self.infer_request
            request_id = self._mark_request_job_started(infer_request)
            self._force_next_submit = False
            try:
                user_data = {
                    "box": box,
                    "threshold": threshold,
                    "label": label,
                    "start_time": clock.now(),
                    # 传给回调函数，用于减去补边的偏移
                    "pad_x": pad_x,
                    "pad_y": pad_y,
                    "target_w": target_w,  # 记录画布的总宽用于还原缩放
                    "job_id": current_job_id,
                    "request_id": request_id,
                    "image": image,
                    "done_event": done_event,
                    "result_holder": result_holder,
                }
                infer_request.set_callback(self._callback, user_data)
                infer_request.start_async({0: input_tensor})
                submitted = True
            except Exception:
                self._mark_request_job_finished(request_id)
                if done_event is not None:
                    done_event.set()
                raise

        return self.latest_results, submitted

    def detect(
        self,
        image,
        box: Box = None,
        threshold=0.5,
        label="target",
        force=False,
        mask_regions=None,
    ):
        """
        发起异步检测，返回上一帧或最近一次完成的结果。
        """

        results, _ = self._detect(
            image,
            box=box,
            threshold=threshold,
            label=label,
            force=force,
            mask_regions=mask_regions,
        )
        return results

    def wait(self, include_retired=False):
        """强制阻塞主线程，默认只等待当前推理 request 完成。"""
        if not self._openvino_available:
            return
        with self._state_lock:
            requests = [self.infer_request]
            if include_retired:
                requests.extend(record["request"] for record in self._retired_infer_requests)
        for infer_request in requests:
            infer_request.wait()
        self._cleanup_retired_infer_requests()

    def _apply_canvas_mask(self, canvas, mask_regions, image_shape, box, pad_x, pad_y):
        if not mask_regions:
            return

        image_h, image_w = image_shape
        canvas_h, canvas_w = canvas.shape[:2]
        crop_x1 = max(0, box.x)
        crop_y1 = max(0, box.y)
        crop_x2 = min(image_w, box.x + box.width)
        crop_y2 = min(image_h, box.y + box.height)

        for x1_ratio, y1_ratio, x2_ratio, y2_ratio in mask_regions:
            x1 = max(crop_x1, min(crop_x2, int(x1_ratio * image_w)))
            y1 = max(crop_y1, min(crop_y2, int(y1_ratio * image_h)))
            x2 = max(crop_x1, min(crop_x2, int(x2_ratio * image_w)))
            y2 = max(crop_y1, min(crop_y2, int(y2_ratio * image_h)))
            if x1 >= x2 or y1 >= y2:
                continue

            canvas_x1 = max(0, min(canvas_w, x1 - crop_x1 + pad_x))
            canvas_y1 = max(0, min(canvas_h, y1 - crop_y1 + pad_y))
            canvas_x2 = max(0, min(canvas_w, x2 - crop_x1 + pad_x))
            canvas_y2 = max(0, min(canvas_h, y2 - crop_y1 + pad_y))
            if canvas_x1 < canvas_x2 and canvas_y1 < canvas_y2:
                canvas[canvas_y1:canvas_y2, canvas_x1:canvas_x2] = 114

    def detect_sync(self, image, box=None, threshold=0.5, label="target", mask_regions=None):
        """同步检测版本：发起请求后立即堵住，直到拿到本次请求的结果。"""
        if not self._openvino_available:
            return False
        done_event = threading.Event()
        result_holder = {"results": None}
        # 同步调用通常用于确认当前帧状态。若等待旧 async 请求，回来的可能是旧帧、
        # 旧 ROI 或旧 mask 的结果，因此这里始终允许抢占忙 request 并提交本次请求。
        _, submitted = self._detect(
            image,
            box=box,
            threshold=threshold,
            label=label,
            force=True,
            mask_regions=mask_regions,
            done_event=done_event,
            result_holder=result_holder,
        )
        if not submitted:
            return None
        if not done_event.wait(timeout=self._SYNC_WAIT_TIMEOUT):
            with self._state_lock:
                active_jobs = sum(self._active_request_jobs.values())
                retired_count = self._get_active_retired_count()
                request_active = self._request_has_active_jobs(self.infer_request)

            message = og.app.tr(
                "OpenVINO 单次推理超时，已自动关闭目标检测；重启程序后会重新尝试启用。"
            )
            logger.error(
                f"{message} timeout={self._SYNC_WAIT_TIMEOUT:.1f}s, "
                f"request_active={request_active}, retired_requests={retired_count}, "
                f"active_jobs={active_jobs}"
            )
            communicate.notification.emit(message, "OpenVINO", True, True, None, None, None)

            with self._state_lock:
                self.job_id += 1
                if self._request_has_active_jobs(self.infer_request):
                    self._retire_request(self.infer_request, cancel=True)
                self._force_next_submit = False
                self._openvino_available = False
                self._openvino_unavailable_reason = message
            return False
        return result_holder["results"]

    def clear_cache(self):
        """清空缓存"""
        if not self._openvino_available:
            return
        with self._state_lock:
            self.latest_results = None
            self.latest_image = None
            self.job_id += 1  # 增加 epoch，所有正在运行的旧任务的回调都会失效
            self._force_next_submit = True
            if (
                self._request_has_active_jobs(self.infer_request)
                and self._get_active_retired_count() < self._MAX_ACTIVE_RETIRED_INFER_REQUESTS
            ):
                self._retire_request(self.infer_request, cancel=True)
                self.infer_request = self._create_infer_request()
        self._cleanup_retired_infer_requests()
