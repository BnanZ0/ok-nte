import numpy as np
from ok import BaseScene, Logger

logger = Logger.get_logger(__name__)


class NTEScene(BaseScene):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_team = None
        self._in_combat = None
        self.cd_refreshed = False
        self._ocr_warm_up = False

    def reset(self):
        self._in_team = None
        self._in_combat = None
        self.cd_refreshed = False
        self.ocr_warm_up()

    def in_combat(self):
        return self._in_combat

    def set_in_combat(self):
        self._in_combat = True
        return True

    def set_not_in_combat(self):
        self._in_combat = False
        return False

    def in_team(self, fun):
        if self._in_team is None:
            self._in_team = fun()
        return self._in_team
    
    def ocr_warm_up(self):
        if not self._ocr_warm_up:
            from ok import og
            self._ocr_warm_up = True
            try:
                all_tasks = og.executor.get_all_tasks()
                if all_tasks and hasattr(all_tasks[0], "ocr"):
                    logger.info("Warming up default OCR...")
                    all_tasks[0].ocr(frame=np.zeros((50, 50, 3)))
                
                self.init_bg_ocr()
                bg_ocr = getattr(og.executor, "_ocr_lib", {}).get("bg_onnx_ocr")
                if bg_ocr:
                    logger.info("Warming up background OCR...")
                    bg_ocr.ocr(np.zeros((50, 50, 3), dtype=np.uint8))

                logger.info("OCR initialization finished.")
            except Exception as e:
                logger.error(f"Failed to initialize OCR in background: {e}")

    def init_bg_ocr(self):
        from ok import og
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        ocr_config = og.executor.config.get("ocr", {})
        bg_config = ocr_config.get("bg_onnx_ocr") or ocr_config.get("default", {})
        config_params = bg_config.get("params", {})

        logger.info(f"Initializing bg onnxocr with params: {config_params}")
        og.executor._ocr_lib["bg_onnx_ocr"] = ONNXPaddleOcr(
            use_angle_cls=False,
            logger=logger,
            use_npu=config_params.get("use_npu", True),
            use_openvino=config_params.get("use_openvino", False),
        )
