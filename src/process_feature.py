from src.tasks.BaseNTETask import isolate_white_text_to_black
from src.Labels import Labels

def process_feature(feature_name, feature):
    if feature_name == Labels.char_1_text:
        feature.mat = isolate_white_text_to_black(feature.mat)
    elif feature_name == Labels.char_2_text:
        feature.mat = isolate_white_text_to_black(feature.mat)
    elif feature_name == Labels.char_3_text:
        feature.mat = isolate_white_text_to_black(feature.mat)
    elif feature_name == Labels.char_4_text:
        feature.mat = isolate_white_text_to_black(feature.mat)
