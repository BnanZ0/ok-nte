import cv2
from ok.feature.Feature import Feature

from src.Labels import Labels
from src.utils import image_utils as iu

SET_CHAR_LABELS = {Labels.char_1_text, Labels.char_2_text, Labels.char_3_text, Labels.char_4_text}
SET_ELEMENT_LABELS = {
    Labels.blue_element,
    Labels.green_element,
    Labels.red_element,
    Labels.purple_element,
    Labels.yellow_element,
    Labels.white_element,
}


def process_feature(feature_name, feature: Feature):
    if feature_name in SET_CHAR_LABELS:
        feature.mat = iu.binarize_bgr_by_brightness(feature.mat, threshold=180)
    if feature_name in SET_ELEMENT_LABELS:
        feature.mat = _process_element(feature.mat, 0.47)
    match feature_name:
        case Labels.boss_lv_text:
            feature.mat = iu.binarize_bgr_by_brightness(feature.mat, threshold=180)
        case Labels.mini_map_arrow:
            feature.mat = iu.binarize_bgr_by_brightness(feature.mat, threshold=200)


def _process_element(image, scale):
    resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    resized = iu.binarize_bgr_by_brightness(resized, threshold=60)
    return resized
