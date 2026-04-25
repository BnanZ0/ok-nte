# from ok import og
from ok.feature.Feature import Feature

from src.Labels import Labels
from src.utils import image_utils as iu

SET_CHAR_LABELS = {Labels.char_1_text, Labels.char_2_text, Labels.char_3_text, Labels.char_4_text}


def process_feature(feature_name, feature: Feature):
    if feature_name in SET_CHAR_LABELS:
        feature.mat = iu.binarize_bgr_by_brightness(feature.mat, threshold=180)
    match feature_name:
        case Labels.boss_lv_text:
            feature.mat = iu.binarize_bgr_by_brightness(feature.mat, threshold=180)
        case Labels.mini_map_arrow:
            feature.mat = iu.binarize_bgr_by_brightness(feature.mat, threshold=200)
        case Labels.skip_dialog:
            feature.mat = iu.isolate_dialog_to_white(feature.mat)
