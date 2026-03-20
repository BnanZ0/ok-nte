from src.tasks.BaseNTETask import binarize_bgr_by_brightness
from src.Labels import Labels

def process_feature(feature_name, feature):
    if feature_name in char_labels:
        feature.mat = binarize_bgr_by_brightness(feature.mat)

char_labels = {
    Labels.char_1_text, 
    Labels.char_2_text, 
    Labels.char_3_text, 
    Labels.char_4_text
}