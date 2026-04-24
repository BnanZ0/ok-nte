import cv2
import numpy as np
from ok import color_range_to_bound

from src import text_white_color

dialog_white_color = {
    "r": (220, 240),  # Red range
    "g": (220, 240),  # Green range
    "b": (220, 240),  # Blue range
}

lv_white_color = {
    'r': (210, 240),  # Red range
    'g': (210, 240),  # Green range
    'b': (210, 240)  # Blue range
}

def isolate_cd_to_black(cv_image):
    return create_color_mask(cv_image, text_white_color, invert=True)

def isolate_lv_to_black(cv_image):
    return create_color_mask(cv_image, lv_white_color, invert=True)

def isolate_dialog_to_white(cv_image):
    return create_color_mask(cv_image, dialog_white_color, invert=False)


def binarize_bgr_by_brightness(image, threshold=180, binary=False):
    """
    根据亮度阈值对 BGR 图像进行二值化，并返回 BGR 格式的结果。

    参数:
    - image: 输入的 BGR 图像 (MatLike)

    返回:
    - 经过二值化处理的 BGR 图像 (MatLike)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary_gray = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    if binary:
        return binary_gray
    binary_bgr = cv2.cvtColor(binary_gray, cv2.COLOR_GRAY2BGR)

    return binary_bgr


def binarize_bgr_by_adaptive_center(image):
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
    _, binary_gray = cv2.threshold(gray, ret, 255, cv2.THRESH_BINARY)

    # 7. 转回 BGR 格式
    binary_bgr = cv2.cvtColor(binary_gray, cv2.COLOR_GRAY2BGR)

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


def binarize_bgr_by_adaptive_brightness(image, ratio_threshold=0.05, offset=20, min_threshold=100):
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

    _, binary_gray = cv2.threshold(gray, int(final_threshold), 255, cv2.THRESH_BINARY)
    binary_bgr = cv2.cvtColor(binary_gray, cv2.COLOR_GRAY2BGR)

    return binary_bgr


def mask_corners(image, ratio_w=0.5555, ratio_h=0.8571):
    """
    将图像的左上角和右下角指定的三角形区域涂成黑色，其余区域保留为白色（创建掩码）。

    参数:
    - image: 输入图像
    - ratio_w: 宽度比例，用于定义三角形在水平方向的覆盖范围
    - ratio_h: 高度比例，用于定义三角形在垂直方向的覆盖范围

    返回:
    - 处理后的掩码图像 (BGR)，三角形区域为黑色 (0, 0, 0)，其余为白色 (255, 255, 255)
    """
    h, w = image.shape[:2]

    # 1. 计算左上角三角区域顶点
    pt1_tl = [0, 0]
    pt2_tl = [int(w * ratio_w), 0]
    pt3_tl = [0, int(h * ratio_h)]

    # 2. 计算右下角三角区域顶点
    pt1_br = [w, h]
    pt2_br = [int(w * (1 - ratio_w)), h]
    pt3_br = [w, int(h * (1 - ratio_h))]

    # 定义多边形点集
    contours = [
        np.array([pt1_tl, pt2_tl, pt3_tl], dtype=np.int32),
        np.array([pt1_br, pt2_br, pt3_br], dtype=np.int32),
    ]

    # 在掩码图上填充白色
    white = np.ones_like(image) * 255
    result = cv2.fillPoly(white, contours, (0, 0, 0))  # 黑色填充

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
    cv_image: np.ndarray, color_range, invert: bool = False, gray: bool = False
) -> np.ndarray:
    """
    根据指定颜色范围生成3通道BGR掩码图.

    Args:
        cv_image (np.ndarray): 输入的OpenCV图像.
        color_range (Any): 目标颜色范围.
        invert (bool): 是否反转掩码, 默认为False.
        gray (bool): 是否返回灰度图, 默认为False.

    Returns:
        np.ndarray: 3通道BGR掩码图(匹配区为白, 非匹配区为黑).
    """
    lower_bound, upper_bound = color_range_to_bound(color_range)

    match_mask = cv2.inRange(cv_image, lower_bound, upper_bound)
    if invert:
        match_mask = cv2.bitwise_not(match_mask)
    if gray:
        return match_mask
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)

    return output_image


def display_image(images, name="image", scale=None, wait_key=0):
    if not isinstance(images, list):
        images = [images]
    if isinstance(scale, float) or isinstance(scale, int):
        images = [
            cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            for image in images
        ]
    for i, image in enumerate(images):
        cv2.imshow(f"{name}_{i}", image)
    cv2.waitKey(wait_key)
