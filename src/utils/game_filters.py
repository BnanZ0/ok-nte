from src import text_white_color
from src.utils.image_utils import HSVRange, create_color_mask, filter_by_hsv

dialog_white_color = {
    "r": (220, 240),  # Red range
    "g": (220, 240),  # Green range
    "b": (220, 240),  # Blue range
}

lv_white_color = {
    "r": (210, 255),  # Red range
    "g": (210, 255),  # Green range
    "b": (210, 255),  # Blue range
}


def isolate_cd_to_black(cv_image):
    return create_color_mask(cv_image, text_white_color, invert=True)


def isolate_lv_to_black(cv_image):
    return create_color_mask(cv_image, lv_white_color, invert=True)


def isolate_dialog_to_white(cv_image):
    return create_color_mask(cv_image, dialog_white_color, invert=False)


def current_char_filter(cv_image):
    return filter_by_hsv(cv_image, HSVRange((150, 170, 165), (179, 255, 255)), return_mask=True)
