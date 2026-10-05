import unicodedata
from dataclasses import dataclass
from typing import Tuple

import cv2
import numpy as np
from ok import Box, color_range_to_bound

from src.events import communicate


def trim_right_background(image, threshold=24):
    """Trim trailing background columns while keeping the image's height and left edge."""
    if image is None or image.size == 0:
        return image

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape[:2]
    background_width = max(1, width // 10)
    background = np.median(gray[:, -background_width:], axis=1)
    changed_pixels = np.abs(gray.astype(np.int16) - background[:, None]) > threshold
    active_columns = np.flatnonzero(
        np.count_nonzero(changed_pixels, axis=0) >= max(1, height // 25)
    )
    if not active_columns.size:
        return image

    right_margin = max(2, width // 50)
    right = min(width, active_columns[-1] + right_margin + 1)
    return image[:, :right]


def binarize_bgr_by_brightness(image, threshold=180, to_bgr: bool = True):
    """
    根据亮度阈值对 BGR 图像进行二值化，并返回 BGR 格式的结果。

    参数:
    - image: 输入的 BGR 图像 (MatLike)

    返回:
    - 经过二值化处理的 BGR 图像 (MatLike)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary_mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    if not to_bgr:
        return binary_mask
    binary_bgr = cv2.cvtColor(binary_mask, cv2.COLOR_GRAY2BGR)

    return binary_bgr


def binarize_bgr_by_adaptive_center(image, to_bgr: bool = True):
    """
    根据图像中心 50% 范围的亮度自适应计算阈值，并对全图进行二值化。

    参数:
    - image: 输入的 BGR 图像 (MatLike)

    返回:
    - 经过二值化处理的 BGR 图像 (MatLike)
    """
    # 1. 获取图像尺寸
    h, w = image.shape[:2]

    # 2. 确定中心 50% 的范围 (即长宽各取中间的 1/2 区域)
    y1, y2 = h // 4, 3 * h // 4
    x1, x2 = w // 4, 3 * w // 4

    # 3. 转为灰度图
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 4. 提取中心区域 (ROI)
    roi = gray[y1:y2, x1:x2]

    # 5. 使用 Otsu 算法在 ROI 区域自动计算阈值
    # cv2.THRESH_OTSU 会忽略传入的 0，自动返回最佳阈值 ret
    ret, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 6. 使用计算出的阈值对整张灰度图进行二值化
    _, binary_mask = cv2.threshold(gray, ret, 255, cv2.THRESH_BINARY)
    if not to_bgr:
        return binary_mask

    # 7. 转回 BGR 格式
    binary_bgr = cv2.cvtColor(binary_mask, cv2.COLOR_GRAY2BGR)

    return binary_bgr


def blackout_corners_by_circle(image):
    """
    以方形图像中心为圆心，到边长的距离为半径，将半径以外的四个角区域涂黑。

    参数:
    - image: 输入的 BGR 图像 (MatLike)，通常应为正方形

    返回:
    - 处理后的 BGR 图像
    """
    # 1. 获取图像的尺寸
    h, w = image.shape[:2]
    center = (w // 2, h // 2)

    # 2. 计算半径（中心到边长的距离）
    # 如果是非方形图像，取宽和高中较小的那个的一半作为半径
    radius = min(w, h) // 2

    # 3. 创建一个全黑的遮罩 (与原图大小相同，单通道)
    # 也可以直接创建三通道遮罩，这里用单通道更节省内存
    mask = np.zeros((h, w), dtype=np.uint8)

    # 4. 在遮罩上画一个白色的实心圆 (颜色为 255)
    # 参数：(图像, 圆心坐标, 半径, 颜色, 粗细=-1表示填充)
    cv2.circle(mask, center, radius, 255, thickness=-1)

    # 5. 将遮罩应用到原图上
    # 使用 bitwise_and，只有遮罩中为白色（255）的部分会被保留，黑色部分变为 0
    masked_image = cv2.bitwise_and(image, image, mask=mask)

    return masked_image


def binarize_bgr_by_adaptive_brightness(
    image, ratio_threshold=0.05, offset=20, min_threshold=100, to_bgr: bool = True
):
    """
    根据图像平均亮度动态计算“高亮度”阈值进行二值化。

    参数:
    - image: 输入 BGR 图像
    - ratio_threshold: 高亮度像素占总像素的比例 (0.01 表示 1%)
    - offset: 定义“高亮度”比平均亮度高出多少 (0-255)
    - min_threshold: 允许的最小高亮度阈值，防止在纯黑图像中误触发

    返回:
    - 经过二值化处理的 BGR 图像
    """
    # 1. 转为灰度图
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 2. 计算当前图像的平均亮度
    avg_brightness = np.mean(gray)

    # 3. 计算“候选”高亮度阈值
    # 逻辑：高亮度 = 平均亮度 + 偏移量
    # 使用 np.clip 确保阈值在 0-255 之间，且不低于 min_threshold
    candidate_threshold = np.clip(avg_brightness + offset, min_threshold, 255)

    # 4. 统计超过该候选阈值的像素比例
    high_brightness_pixels = np.sum(gray > candidate_threshold)
    total_pixels = gray.shape[0] * gray.shape[1]
    current_ratio = high_brightness_pixels / total_pixels

    # 5. 判定并设定最终二值化阈值
    if current_ratio >= ratio_threshold:
        final_threshold = candidate_threshold
    else:
        final_threshold = 255

    _, binary_mask = cv2.threshold(gray, int(final_threshold), 255, cv2.THRESH_BINARY)
    if not to_bgr:
        return binary_mask
    binary_bgr = cv2.cvtColor(binary_mask, cv2.COLOR_GRAY2BGR)

    return binary_bgr


def mask_corners(image, ratio_w=0.5555, ratio_h=0.8571, corners=None, to_bgr=True):
    """
    将图像指定角落的三角形区域涂成黑色，其余区域保留为白色（创建掩码）。

    参数:
    - image: 输入图像
    - ratio_w: 宽度比例，用于定义三角形在水平方向的覆盖范围
    - ratio_h: 高度比例，用于定义三角形在垂直方向的覆盖范围
    - corners: 需要涂黑的角落，可选:
      "top_left"/"tl", "top_right"/"tr",
      "bottom_left"/"bl", "bottom_right"/"br"。
      也可以传 "all"/"diamond" 或包含多个角落名称的列表/元组/集合。
      默认涂黑左上角和右下角，保持旧行为。
    - to_bgr: 是否返回 BGR 3通道掩码，False 时返回单通道掩码

    返回:
    - 处理后的掩码图像，指定角落为黑色，其余为白色。
      当 corners="all" 且 ratio_w/ratio_h 合适时，可得到白色菱形 mask。
    """
    h, w = image.shape[:2]

    corner_aliases = {
        "top_left": "top_left",
        "tl": "top_left",
        "top_right": "top_right",
        "tr": "top_right",
        "bottom_left": "bottom_left",
        "bl": "bottom_left",
        "bottom_right": "bottom_right",
        "br": "bottom_right",
    }

    all_corners = ("top_left", "top_right", "bottom_left", "bottom_right")

    if corners is None:
        corners = ("top_left", "bottom_right")
    elif isinstance(corners, str):
        corners = corners.lower()
        if corners in ("all", "diamond"):
            corners = all_corners
        else:
            corners = (corners,)
    selected_corners = set()
    for corner in corners:
        corner_key = corner.lower() if isinstance(corner, str) else corner
        try:
            selected_corners.add(corner_aliases[corner_key])
        except KeyError as exc:
            raise ValueError(f"Unsupported corner: {corner}") from exc

    x_left = int(w * ratio_w)
    x_right = int(w * (1 - ratio_w))
    y_top = int(h * ratio_h)
    y_bottom = int(h * (1 - ratio_h))

    corner_points = {
        "top_left": [[0, 0], [x_left, 0], [0, y_top]],
        "top_right": [[w, 0], [x_right, 0], [w, y_top]],
        "bottom_left": [[0, h], [x_left, h], [0, y_bottom]],
        "bottom_right": [[w, h], [x_right, h], [w, y_bottom]],
    }

    contours = [np.array(corner_points[corner], dtype=np.int32) for corner in selected_corners]

    mask_shape = image.shape if to_bgr else image.shape[:2]
    white = np.ones(mask_shape, dtype=np.uint8) * 255
    if not contours:
        return white

    fill_color = (0, 0, 0) if to_bgr else 0
    result = cv2.fillPoly(white, contours, fill_color)  # 黑色填充

    return result


def mask_outside_white_rect(image):
    """
    找到图像中所有白色像素的最小外接矩形，并将该矩形外部区域涂黑，内部涂白（创建掩码）。

    参数:
    - image: 输入图像

    返回:
    - 处理后的掩码图像，矩形内部区域为白色 (255, 255, 255)，其余区域为黑色 (0, 0, 0)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    x, y, w, h = cv2.boundingRect(gray)

    mask = np.zeros_like(image)
    if w > 0 and h > 0:
        mask[y : y + h, x : x + w] = 255

    return mask


def create_color_mask(
    cv_image: np.ndarray, color_range, invert: bool = False, to_bgr: bool = True
) -> np.ndarray:
    """
    根据指定颜色范围生成3通道BGR掩码图.

    Args:
        cv_image (np.ndarray): 输入的OpenCV图像.
        color_range (Any): 目标颜色范围.
        invert (bool): 是否反转掩码, 默认为False.
        to_bgr (bool): 是否返回3通道BGR图(掩码), 默认为True.

    Returns:
        np.ndarray: 3通道BGR掩码图(匹配区为白, 非匹配区为黑)或单通道二值掩码图.
    """
    lower_bound, upper_bound = color_range_to_bound(color_range)

    match_mask = cv2.inRange(cv_image, lower_bound, upper_bound)
    if invert:
        match_mask = cv2.bitwise_not(match_mask)
    if not to_bgr:
        return match_mask
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)

    return output_image


def _estimate_window_title_width(title: str) -> int:
    title_units = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in title)
    return title_units * 9 + 160


def _pad_image_to_width(image: np.ndarray, width: int) -> np.ndarray:
    image_width = image.shape[1]
    if image_width >= width:
        return image

    pad_width = width - image_width
    if image.ndim == 2:
        pad_shape = ((0, 0), (0, pad_width))
    else:
        pad_shape = ((0, 0), (0, pad_width), (0, 0))
    return np.pad(image, pad_shape, mode="constant")


def show_images(images, names=None, scale=None, wait_key=0):
    if not isinstance(images, list):
        images = [images]
    if names is None or not isinstance(names, list):
        names = ["image"] * len(images)
    if len(images) != len(names):
        raise ValueError("images and names must have the same length")
    if isinstance(scale, float) or isinstance(scale, int):
        images = [
            cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            for image in images
        ]
    for i, image in enumerate(images):
        win_name = f"{names[i]}_{i}"
        display_image = _pad_image_to_width(image, _estimate_window_title_width(win_name))
        cv2.namedWindow(win_name, cv2.WINDOW_AUTOSIZE)
        cv2.moveWindow(win_name, 100 * i, 100 * i)
        cv2.imshow(win_name, display_image)
    cv2.waitKey(wait_key)


@dataclass
class HSVRange:
    """
    HSV 颜色范围容器 (OpenCV 格式)

    取值范围提示:
    - H (Hue): 0 - 179
    - S (Saturation): 0 - 255
    - V (Value): 0 - 255
    """

    lower: np.ndarray
    upper: np.ndarray

    def __init__(self, lower: Tuple[int, int, int], upper: Tuple[int, int, int]):
        """
        初始化 HSV 范围 (输入值若超出范围会自动修正)

        Args:
            lower: 下限 (h: 0-179, s: 0-255, v: 0-255)
            upper: 上限 (h: 0-179, s: 0-255, v: 0-255)
        """
        min_vals = [0, 0, 0]
        max_vals = [179, 255, 255]

        lower_clipped = np.clip(lower, min_vals, max_vals)
        upper_clipped = np.clip(upper, min_vals, max_vals)

        self.lower = np.array(lower_clipped, dtype=np.uint8)
        self.upper = np.array(upper_clipped, dtype=np.uint8)


def filter_by_hsv(image, hsv_range: HSVRange, return_mask: bool = False):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    match_mask = cv2.inRange(hsv, hsv_range.lower, hsv_range.upper)
    if return_mask:
        return match_mask
    return cv2.bitwise_and(image, image, mask=match_mask)


def adjust_lightness_contrast_lab(img, brightness=0, contrast=0):
    """
    基于 Lab 空间的通用亮度对比度调节函数
    参数范围建议: brightness (-100 to 100), contrast (-100 to 100)
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2Lab)
    lightness, a, b = cv2.split(lab)

    if contrast >= 0:
        factor = 1.0 + (contrast / 100.0) * 2.0
    else:
        factor = 1.0 + (contrast / 100.0)

    offset = brightness * 1.28

    lut = np.arange(256).astype(np.float32)
    lut = (lut - 128) * factor + 128 + offset

    lut = np.clip(lut, 0, 255).astype(np.uint8)

    lightness = cv2.LUT(lightness, lut)
    result_lab = cv2.merge((lightness, a, b))
    return cv2.cvtColor(result_lab, cv2.COLOR_Lab2BGR)


def morphology_mask(
    mask: np.ndarray, kernel_size: int = 3, closing: bool = False, to_bgr: bool = True
) -> np.ndarray:
    """
    对遮罩（二值图像）进行形态学处理。

    Args:
        mask (np.ndarray): 输入的二值遮罩图像.
        kernel_size (int): 结构元的大小, 默认为 3.
        closing (bool): 是否进行闭运算 (先膨胀再腐蚀), 默认为 False (仅膨胀).
        to_bgr (bool): 是否将结果转换为 BGR 3通道格式, 默认为 True.

    Returns:
        np.ndarray: 处理后的遮罩图像 (3通道BGR或单通道二值图).
    """
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    if closing:
        result = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    else:
        result = cv2.dilate(mask, kernel, iterations=1)

    if to_bgr and len(result.shape) == 2:
        result = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    return result


def restore_world_brightness(image, percentile=0.99):
    """
    基于直方图百分位数的亮度修复。
    找到图像中前 (1-percentile) 亮度对应的水平，并将其映射到 255。
    能有效避开零星的 UI、文字、伤害数字干扰，还原被滤镜压低的场景亮度。
    """
    if image is None:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])

    # 计算目标像素数（例如前 1% 的像素数）
    total_pixels = image.shape[0] * image.shape[1]
    target_count = total_pixels * (1.0 - percentile)

    current_count = 0
    robust_max = 255
    for i in range(255, 0, -1):
        current_count += hist[i]
        if current_count >= target_count:
            robust_max = i
            break

    # 只有当发现整体亮度不足，且不是纯黑环境时，才进行拉伸
    if robust_max < 254 and robust_max > 100:
        scale = 255.0 / robust_max
        return cv2.convertScaleAbs(image, alpha=scale, beta=0)

    return image


def find_color_enriched_regions(
    color_range, box: Box, image: np.ndarray, min_area: float = 0.01
) -> list[Box]:
    """
    接受 color_range 和 box 传入，找出范围内符合 color_range 的富集区。

    参数:
    - self: 调用者所在任务实例 (需包含 self.frame 属性)
    - color_range: 颜色范围，将使用 color_range_to_bound 解析
    - box: ok.Box 对象
    - min_area: 面积比例 (例如 0.01 表示 1%)，用于根据裁切好的画面动态调整实际最小像素面积

    返回:
    - list[Box]: 包含所有富集区的 Box 对象列表
    """
    box.name = "enriched_regions"
    communicate.emit_draw_box(boxes=box, color="blue", debug=True)
    lower_bound, upper_bound = color_range_to_bound(color_range)

    # 直接使用传入的 box 获取裁切画面 (ROI)
    roi = box.crop_frame(image)
    if roi is None or roi.size == 0:
        return []

    mask = cv2.inRange(roi, lower_bound, upper_bound)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 根据裁切画面的总大小动态计算最小面积阈值
    h, w = roi.shape[:2]
    actual_min_area = h * w * min_area

    result_boxes = []
    i = 1
    for contour in contours:
        area = cv2.contourArea(contour)
        if area >= actual_min_area:
            cx, cy, cw, ch = cv2.boundingRect(contour)
            # 根据 box 的原图坐标还原
            result_boxes.append(
                Box(
                    x=box.x + cx,
                    y=box.y + cy,
                    width=cw,
                    height=ch,
                    name=f"enriched_regions_{i}",
                )
            )
            i += 1
    if result_boxes:
        communicate.emit_draw_box(boxes=result_boxes, color="red", debug=True)
    return result_boxes


def apply_mask(image, mask):
    return cv2.bitwise_and(image, image, mask=mask)
