import cv2

from src import text_black_color, text_white_color
from src.utils import image_utils as iu
from src.utils.image_utils import HSVRange

dialog_white_color = {
    "r": (220, 240),  # Red range
    "g": (220, 240),  # Green range
    "b": (220, 240),  # Blue range
}

lv_white_color = {
    "r": (235, 255),  # Red range
    "g": (235, 255),  # Green range
    "b": (235, 255),  # Blue range
}

lv_red_color = {
    "r": (235, 255),
    "g": (0, 1),
    "b": (0, 1),
}

lv_gray_hsv = HSVRange((0, 0, 100), (0, 0, 170))

lv_white_hsv = HSVRange((0, 0, 180), (160, 20, 255))

lv_red_hsv = HSVRange((0, 235, 180), (0, 255, 255))


def isolate_text_to_black(cv_image):
    image = iu.create_color_mask(cv_image, text_white_color)
    image = iu.morphology_mask(image, closing=True)
    return cv2.bitwise_not(image)


def isolate_lv_to_white(cv_image):
    cv_image = iu.restore_world_brightness(cv_image)
    # mask_white = iu.create_color_mask(cv_image, lv_white_color, to_bgr=False)
    # mask_red = iu.create_color_mask(cv_image, lv_red_color, to_bgr=False)
    mask_gray = iu.filter_by_hsv(cv_image, lv_gray_hsv, return_mask=True)
    mask_white = iu.filter_by_hsv(cv_image, lv_white_hsv, return_mask=True)
    mask_red = iu.filter_by_hsv(cv_image, lv_red_hsv, return_mask=True)
    mask = mask_white | mask_gray | mask_red
    mask = iu.morphology_mask(mask, to_bgr=False)
    return mask


def isolate_dialog_to_white(cv_image):
    return iu.create_color_mask(cv_image, dialog_white_color, invert=False)


def current_char_filter(cv_image):
    lab = cv2.cvtColor(cv_image, cv2.COLOR_BGR2Lab)
    # Use Lab opponent-color channels instead of HSV thresholds.  The current
    # character arc can become pale under lighting, while its a/b relationship
    # stays closer to the template than similar bright backgrounds do.
    return lab[:, :, 1:3]


def isolate_black_text(cv_image):
    return iu.create_color_mask(cv_image, text_black_color, invert=False)


def ultimate_ready_filter(cv_image):
    return iu.binarize_bgr_by_brightness(cv_image, threshold=128, to_bgr=False)
    # iu.morphology_mask(filtered, kernel_size=2, closing=True, to_bgr=False)
