import time

import numpy as np
from ok import Box
from openvino import AsyncInferQueue, Core, Layout, PartialShape, Type
from openvino.preprocess import ColorFormat, PrePostProcessor, ResizeAlgorithm


class YOLO26OpenVINOAsyncDetector:
    def __init__(self, xml_path, num_requests=1):
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
            "INFERENCE_NUM_THREADS": "4",  # 限制线程，防止游戏卡顿
        }
        self.compiled_model = self.core.compile_model(model, "CPU", config)

        self.model_h = 896
        self.model_w = 1536
        self.model_ratio = self.model_w / self.model_h

        # 3. 创建异步队列
        # 对于游戏辅助，jobs 建议设为 1 或 2，以保证最低延迟
        self.infer_queue = AsyncInferQueue(self.compiled_model, jobs=num_requests)
        self.infer_queue.set_callback(self._callback)

        # 内部状态
        self.latest_results = []
        self.class_names = ["target"]  # 可根据 data.yaml 修改
        self.latency = 0.0  # 单次推理总耗时 (秒)

    def _callback(self, infer_request, user_data):
        """异步推理完成后的回调函数"""
        start_time = user_data["start_time"]
        self.latency = time.time() - start_time

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
                self.class_names[int(cls_id)] if int(cls_id) < len(self.class_names) else "unknown"
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

    def detect(self, image, box: Box = None, threshold=0.5, label="target"):
        """
        发起异步检测
        :param image: 全图 (numpy array)
        :param box: 指定检测区域的 Box 实例。如果为 None, 则检测全图。
        :param threshold: 置信度阈值
        :param label: 指定检测的类别名称
        :return: list[Box] (返回的是上一帧或最近一次完成的结果)
        """

        if self.infer_queue.is_ready():
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
                return self.latest_results  # 防止出界错误

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

            input_tensor = np.expand_dims(canvas, axis=0)

            self.infer_queue.start_async(
                {0: input_tensor},
                {
                    "box": box,
                    "threshold": threshold,
                    "label": label,
                    "start_time": time.time(),
                    # 传给回调函数，用于减去补边的偏移
                    "pad_x": pad_x,
                    "pad_y": pad_y,
                    "target_w": target_w,  # 记录画布的总宽用于还原缩放
                },
            )

        return self.latest_results

    def wait(self):
        """强制阻塞主线程，直到所有正在进行的推理任务全部完成"""
        self.infer_queue.wait_all()

    def detect_sync(self, image, box=None, threshold=0.5, label="target"):
        """同步检测版本：发起请求后立即堵住，直到拿到结果"""
        self.detect(image, box, threshold, label)
        self.wait()
        return self.latest_results
