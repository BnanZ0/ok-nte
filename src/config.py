import os

import numpy as np
from ok import ConfigOption
from src.process_feature import process_feature

version = "dev"
#不需要修改version, Github Action打包会自动修改

key_config_option = ConfigOption('Game Hotkey Config', { #全局配置示例
    'Skill Key': 'e',
    'Ultimate Key': 'q',
}, description='In Game Hotkey for Skills')


def make_bottom_left_black(frame): #可选. 某些游戏截图时遮挡UID使用
    """
    Changes a portion of the frame's pixels at the bottom left to black.

    Args:
        frame: The input frame (NumPy array) from OpenCV.

    Returns:
        The modified frame with the bottom-left corner blackened.  Returns the original frame
        if there's an error (e.g., invalid frame).
    """
    try:
        height, width = frame.shape[:2]  # Get height and width

        # Calculate the size of the black rectangle
        black_width = int(0.13 * width)
        black_height = int(0.025 * height)

        # Calculate the starting coordinates of the rectangle
        start_x = 0
        start_y = height - black_height

        # Create a black rectangle (NumPy array of zeros)
        black_rect = np.zeros((black_height, black_width, frame.shape[2]), dtype=frame.dtype)  # Ensure same dtype

        # Replace the bottom-right portion of the frame with the black rectangle
        frame[start_y:height, start_x:black_width] = black_rect

        return frame
    except Exception as e:
        print(f"Error processing frame: {e}")
        return frame

config = {
    'debug': False,  # Optional, default: False
    'use_gui': True, # 目前只支持True
    'config_folder': 'configs', #最好不要修改
    'global_configs': [key_config_option],
    'screenshot_processor': make_bottom_left_black, # 在截图的时候对frame进行修改, 可选
    'gui_icon': 'icons/icon.png', #窗口图标, 最好不需要修改文件名
    'wait_until_before_delay': 0,
    'wait_until_check_delay': 0,
    'wait_until_settle_time': 0, #调用 wait_until时候, 在第一次满足条件的时候, 会等待再次检测, 以避免某些滑动动画没到预定位置就在动画路径中被检测到
    'ocr': { #可选, 使用的OCR库
        'lib': 'onnxocr',
        'params': {
            'use_openvino': True,
        }
    },
    'windows': {  # Windows游戏请填写此设置
        'exe': ['HTGame.exe'],
        'hwnd_class': 'UnrealWindow', #增加重名检查准确度
        'interaction': 'PostMessage', # Genshin:某些操作可以后台, 部分游戏支持 PostMessage:可后台点击, 极少游戏支持 ForegroundPostMessage:前台使用PostMessage Pynput/PyDirect:仅支持前台使用
        'capture_method': ['WGC', 'BitBlt_RenderFull'],  # Windows版本支持的话, 优先使用WGC, 否则使用BitBlt_Full. 支持的capture有 BitBlt, WGC, BitBlt_RenderFull, DXGI
        'check_hdr': False, #当用户开启AutoHDR时候提示用户, 但不禁止使用
        'force_no_hdr': False, #True=当用户开启AutoHDR时候禁止使用
        'require_bg': True # 要求使用后台截图
    },
    # 'adb': {  # Windows游戏请填写此设置, mumu模拟器使用原生截图和input,速度极快. 其他模拟器和真机使用adb,截图速度较慢
    #     'packages': ['com.abc.efg1', 'com.abc.efg1']
    # },
    'start_timeout': 120,  # default 60
    'window_size': { #ok-script窗口大小
        'width': 1200,
        'height': 800,
        'min_width': 600,
        'min_height': 450,
    },
    'supported_resolution': {
        'ratio': '16:9', #支持的游戏分辨率
        'min_size': (1920, 1080), #支持的最低游戏分辨率
        'resize_to': [(2560, 1440), (1920, 1080)], #可选, 如果非16:9自动缩放为 resize_to
    },
    'links': { # 关于里显示的链接, 可选
            'default': {
                'github': 'https://github.com/BnanZ0/ok-neverness-to-everness',
                'discord': 'https://discord.gg/vVyCatEBgA',
                'sponsor': 'https://ko-fi.com/bnanz',
                'share': 'Download from https://github.com/BnanZ0/ok-neverness-to-everness',
                'faq': 'https://github.com/BnanZ0/ok-neverness-to-everness'
            }
        },
    'screenshots_folder': "screenshots", #截图存放目录, 每次重新启动会清空目录
    'gui_title': 'ok-nte',  #窗口名
    'template_matching': { # 可选, 如使用OpenCV的模板匹配
        'coco_feature_json': os.path.join('assets', 'coco_detection.json'),
        'default_horizontal_variance': 0.002, #默认x偏移, 查找不传box的时候, 会根据coco坐标, match偏移box内的
        'default_vertical_variance': 0.002, #默认y偏移
        'default_threshold': 0.8, #默认threshold
        'feature_processor': process_feature,
    },
    'version': version, #版本
    'my_app': ['src.globals', 'Globals'], #可选. 全局单例对象, 可以存放加载的模型, 使用og.my_app调用
    'onetime_tasks': [  # 用户点击触发的任务
        # ["src.tasks.MyOneTimeTask", "MyOneTimeTask"],
        # ["src.tasks.MyOneTimeWithAGroup", "MyOneTimeWithAGroup"],
        # ["src.tasks.MyOneTimeWithAGroup2", "MyOneTimeWithAGroup2"],
        # ["src.tasks.MyOneTimeWithBGroup", "MyOneTimeWithBGroup"],
        ["ok", "DiagnosisTask"],
        # ["src.tasks.custom.TeamScannerTask", "TeamScannerTask"],
    ],
    'trigger_tasks':[ # 不断执行的触发式任务
        ["src.tasks.trigger.AutoCombatTask", "AutoCombatTask"],
        # ["src.tasks.MyTriggerTask", "MyTriggerTask"],
    ],
    'custom_tabs': [
        ['src.ui.TeamScannerTab', 'TeamScannerTab'],
        ['src.ui.CharManagerTab', 'CharManagerTab'],
        # ['src.ui.MyTab', 'MyTab'], #可选, 自定义UI, 显示在侧边栏
    ],
    'scene': ["src.scene.NTEScene", "NTEScene"],
}
