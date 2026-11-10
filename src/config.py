# ruff: noqa: E501

import os

from ok import Box, ConfigOption

from src import GAME_EXE
from src.audio.routing import create_background_audio_routing_config_option
from src.interaction.NTEInteraction import NTEInteraction
from src.process_feature import process_feature

if "PATH" not in os.environ:
    os.environ["PATH"] = ""

version = "dev"
# 不需要修改version, Github Action打包会自动修改

# Choose Window 里并列展示的两个 PC 窗口变体.
# 选中某个变体时, src/patches/window_variants_patch.py 会动态覆盖
# config["windows"] 的 exe / hwnd_class / capture_method, 使窗口匹配与截图方式随选择切换.
# 顺序即列表顺序: 本地版在前, 云·异环在后.
#
# 云·异环变体的输入限制: 串流客户端只接收系统输入队列里的真实按键/点击,
# NTEInteraction 会按变体改用 SendInput(wVk) 与真实鼠标事件(含滚轮/相对移动).
# 真实输入要求游戏窗口处于前台, 因此云版不能像本地版那样后台挂机:
#   * 交互层用 ALT + AttachThreadInput 显式激活, 并校验点击落点属于游戏进程,
#     避免点击落到 ok 主窗口上误触任务按钮.
#   * 失焦后的第一次点击会被客户端用于重新捕获鼠标, 交互层自动补 warm-up 点击.
#   * 云版禁用 CursorSync 与物理鼠标拦截钩子, 并在失焦/销毁时释放所有真实按键,
#     防止任务中止后按键或光标卡住.
#   * 音频闪避的 WASAPI 进程回环按变体切到云客户端进程名.
# 云版滚轮的真实注入尚未在真机单独验证, 但已不再使用无效的 PostMessage.
#
# 云版任务前置检查失败时必须快速失败: F1 打开「探索指南」是预期流程, 不是错误
# 状态; 全屏面板会遮住右上角 HUD, 使体力 OCR 读不到数字(是「没读到」而非
# 「体力不足」). 旧逻辑把 -1 当成体力不足, 且 F1 页面超时后仍继续点击, 掩盖了
# 真正的问题: 后续页面跳转/点击没有生效. 现在云版在
# BaseNTETask.open_f1_domain_page()/get_stamina() 里保存诊断截图并明确报错;
# 报错只陈述事实, 不引导用户按 ESC 关掉本来正确的 F1 面板.
# 本地 PC 版这两条失败路径保持原样. 云版点击会记录目标坐标与实际光标位置,
# 用于判断串流客户端是否在相对鼠标模式下把光标锁回中心.
#
# 云版**永不自动启动**客户端: 云游戏按在线时长计费, 程序 bug 误启动/保活会
# 造成真实金钱损失. 用户手动打开游戏; 选中云版时弹使用须知(可勾选不再提示),
# 点开始或启动任务时若检测不到云窗口则弹窗并取消本次启动, 由
# src/patches/cloud_launch_guard.py 统一守卫(另在任务执行前兜底拦截).
WINDOW_VARIANTS = (
    {
        "id": "pc_local",
        "nick": "PC版",
        "exe": [GAME_EXE],
        "hwnd_class": ["UnrealWindow"],
        "capture_method": ["WGC", "BitBlt_RenderFull"],
    },
    {
        "id": "pc_cloud",
        "nick": "PC版云异环",
        "exe": ["NTECloudGame.exe"],
        "hwnd_class": ["Qt51517QWindowIcon"],
        "capture_method": ["WGC"],
    },
)

key_config_option = ConfigOption(
    "Game Hotkey Config",
    {  # 全局配置示例
        "Skill Key": "e",
        "Ultimate Key": "q",
        "Arc Key": "r",
        "Use QWERTY Physical Keys": False,
    },
    description="In Game Hotkey for Skills",
    config_description={
        "Use QWERTY Physical Keys": (
            "All letter/number keys, including every hotkey above, are replaced by US QWERTY\n"
            "physical positions, not your current layout's printed keys."
        ),
    },
)

monthly_card_config_option = ConfigOption(
    "Monthly Card Config",
    {"Check Monthly Card": True, "Monthly Card Time": 5},
    description="Turn on to avoid interruption by monthly card when executing tasks",
    config_description={
        "Check Monthly Card": "Check for monthly card to avoid interruption of tasks",
        "Monthly Card Time": "Your computer's local time when the monthly card will popup, hour in (1-24)",
    },
)

sound_trigger_config_option = ConfigOption(
    "Sound Trigger Config",
    {
        "Enable Sound Trigger": True,
        "Dodge All Attacks": True,
        "Dodge Threshold": 0.13,
        "Counter Attack Threshold": 0.12,
    },
    description="Sound-based dodge and counter trigger settings",
    config_description={
        "Enable Sound Trigger": "Enable sound recognition for automatic dodge and counter attacks",
        "Dodge All Attacks": "Dodge all attacks without performing counter attacks",
        "Dodge Threshold": "Dodge sound recognition threshold (0.0-1.0, lower is more sensitive)",
        "Counter Attack Threshold": "Counter attack sound recognition threshold (0.0-1.0, lower is more sensitive)",
    },
)

cursor_sync_config_option = ConfigOption(
    "防止 NTE 移动鼠标",
    {"启用": True},
    description=("后台运行时，在 NTE 将鼠标移动到屏幕中心后，自动恢复鼠标位置。"),
)
background_audio_routing_config_option = create_background_audio_routing_config_option()


def blur_area(width, height):
    return Box(width * 0, height * 0.9769, to_x=width * 0.0943, to_y=height * 1)


config = {
    "custom_tasks": True,  # enable creating and editing custom tasks
    "plugin": True,
    "gui": {
        "type": "qt",
    },
    "debug": False,  # Optional, default: False
    "use_gui": True,  # 目前只支持True
    "config_folder": "configs",  # 最好不要修改
    "global_configs": [
        key_config_option,
        monthly_card_config_option,
        sound_trigger_config_option,
        cursor_sync_config_option,
        background_audio_routing_config_option,
    ],
    # "screenshot_processor": make_bottom_left_black,  # 在截图的时候对frame进行修改, 可选
    "blur_area": blur_area,
    "gui_icon": "icons/icon.png",  # 窗口图标, 最好不需要修改文件名
    "wait_until_before_delay": 0,
    "wait_until_check_delay": 0,
    "wait_until_settle_time": 0,  # 调用 wait_until时候, 在第一次满足条件的时候, 会等待再次检测, 以避免某些滑动动画没到预定位置就在动画路径中被检测到
    "ocr": {  # 可选, 使用的OCR库
        "default": {
            "lib": "onnxocr",
            "auto_simplify": True,
            "params": {
                "use_openvino": True,
                "use_npu": False,
            },
        },
    },
    "windows": {  # Windows游戏请填写此设置
        # 这里的值是默认变体(PC版)的配置. 用户在 Choose Window 选中变体后,
        # window_variants_patch 会按 WINDOW_VARIANTS 覆盖 exe / hwnd_class / capture_method.
        "exe": [GAME_EXE],
        "hwnd_class": ["UnrealWindow"],
        "interaction": [NTEInteraction],
        # Genshin:某些操作可以后台, 部分游戏支持 PostMessage:可后台点击, 极少游戏支持 ForegroundPostMessage:前台使用PostMessage Pynput/PyDirect:仅支持前台使用
        # PC版保留原有的 WGC + BitBlt_RenderFull 两种; 云·异环变体只会剩下 WGC 一种,
        # 因为云客户端是 GPU 渲染窗口, BitBlt_RenderFull 会拿到纯黑帧且框架不校验纯色帧.
        "capture_method": [
            "WGC",
            "BitBlt_RenderFull",
        ],
        "check_hdr": False,  # 当用户开启AutoHDR时候提示用户, 但不禁止使用
        "force_no_hdr": False,  # True=当用户开启AutoHDR时候禁止使用
        "require_bg": True,  # 要求使用后台截图
        "start_exe": False,
    },
    # 'adb': {  # Windows游戏请填写此设置, mumu模拟器使用原生截图和input,速度极快. 其他模拟器和真机使用adb,截图速度较慢
    #     'packages': ['com.abc.efg1', 'com.abc.efg1']
    # },
    "start_timeout": 120,  # default 60
    "window_size": {  # ok-script窗口大小
        "width": 1200,
        "height": 800,
        "min_width": 600,
        "min_height": 450,
    },
    "supported_resolution": {
        "ratio": "16:9",  # 支持的游戏分辨率
        "min_size": (1920, 1080),  # 支持的最低游戏分辨率
        "resize_to": [
            (3840, 2160),
            (2560, 1440),
            (1920, 1080),
        ],  # 可选, 如果非16:9自动缩放为 resize_to
    },
    "links": {  # 关于里显示的链接, 可选
        "default": {
            "github": "https://github.com/BnanZ0/ok-nte",
            "discord": "https://discord.gg/vVyCatEBgA",
            "download": "https://ok-script.com/ok-nte",
            "sponsor": "https://github.com/BnanZ0/ok-nte/blob/main/SPONSOR.md",
            "share": "Download OK-NTE https://ok-script.com/ok-nte",
            "faq": "https://ok-script.com/ok-nte",
        },
        "zh_CN": {
            "github": "https://github.com/BnanZ0/ok-nte",
            "discord": "https://discord.gg/vVyCatEBgA",
            "download": "https://ok-script.com/ok-nte",
            "sponsor": "https://cnb.cool/BnanZ0/ok-nte-update/-/blob/main/SPONSOR.md",
            "share": "下载 OK-NTE https://ok-script.com/ok-nte",
            "faq": "https://ok-script.com/ok-nte",
            "qq_group": "https://qm.qq.com/q/ZJaIaEb5iE",
            "qq_channel": "https://pd.qq.com/s/djmm6l44y",
        },
    },
    "about": """
        <p style="color:red;">
        <strong>本软件是免费开源的。</strong> 如果你被收费，请立即退款。请访问 QQ 频道或 GitHub 下载最新的官方版本。<br>
        <strong>This software is free and open-source.</strong> If you were charged for it, please request a refund immediately. Visit the QQ channel or GitHub to download the latest official version.
        </p>

        <p style="color:red;">
            <strong>本软件仅供个人使用，用于学习 Python 编程、计算机视觉、UI 自动化等。</strong> 请勿将其用于任何营利性或商业用途。<br>
            <strong>This software is for personal use only, intended for learning Python programming, computer vision, UI automation, and similar purposes.</strong> Do not use it for any commercial or profit-seeking activities.
        </p>

        <p style="color:red;">
            <strong>使用本软件可能会导致账号被封。</strong> 请在了解风险后再使用。<br>
            <strong>Using this software may result in account bans.</strong> Please proceed only if you fully understand the risks.
        </p>
    """,
    "screenshots_folder": "screenshots",  # 截图存放目录, 每次重新启动会清空目录
    "gui_title": "ok-nte",  # 窗口名
    "template_matching": {  # 可选, 如使用OpenCV的模板匹配
        "coco_feature_json": os.path.join("assets", "coco_annotations.json"),
        "default_horizontal_variance": 0.002,  # 默认x偏移, 查找不传box的时候, 会根据coco坐标, match偏移box内的
        "default_vertical_variance": 0.002,  # 默认y偏移
        "default_threshold": 0.7,  # 默认threshold
        "feature_processor": process_feature,
    },
    "template_tab": {
        # 默认是否生成标签枚举
        "generate_label_enum": True,
        # 默认标签枚举的相对路径
        "label_enum_relative_path": "src/Labels",
    },
    "version": version,  # 版本
    "my_app": [
        "src.globals",
        "Globals",
    ],  # 可选. 全局单例对象, 可以存放加载的模型, 使用og.my_app调用
    "onetime_tasks": [  # 用户点击触发的任务
        ["src.tasks.LauncherTask", "LauncherTask"],
        ["src.tasks.daily.DailyRoutineTask", "DailyRoutineTask"],
        ["src.tasks.FishingTask", "FishingTask"],
        ["src.tasks.AnomalyTask", "AnomalyTask"],
        ["src.tasks.AnomalyHunter", "AnomalyHunter"],
        ["src.tasks.RhythmTask", "RhythmTask"],
        ["src.tasks.OwnerSelectionTask", "OwnerSelectionTask"],
        ["src.tasks.AutoHeistTask", "AutoHeistTask"],
        ["src.tasks.BagelAITools", "BagelAITools"],
        ["src.tasks.WhirlwindTask", "WhirlwindTask"],
        ["src.tasks.DSDFarmTask", "DSDFarmTask"],
        ["src.tasks.VolleyballTask", "VolleyballTask"],
        ["src.tasks.DartTask", "DartTask"],
        # 测试相关
        ["src.tasks.CombatDetectionTestTask", "CombatDetectionTestTask"],
        ["src.tasks.DebugCharTask", "DebugCharTask"],
        ["ok", "DiagnosisTask"],
        # 日常相关
        ["src.tasks.daily.DailyClaimTask", "DailyClaimTask"],
        ["src.tasks.daily.GiftTask", "GiftTask"],
        ["src.tasks.daily.CoffeeTask", "CoffeeTask"],
        ["src.tasks.daily.FountainTask", "FountainTask"],
        ["src.tasks.daily.FurnitureTask", "FurnitureTask"],
        ["src.tasks.daily.CinemaDateTask", "CinemaDateTask"],
        ["src.tasks.daily.StoreTask", "StoreTask"],
    ],
    "trigger_tasks": [  # 不断执行的触发式任务
        ["src.tasks.trigger.AutoCombatTask", "AutoCombatTask"],
        ["src.tasks.trigger.SoundTriggerTask", "SoundTriggerTask"],
        ["src.tasks.trigger.SkipDialogTask", "SkipDialogTask"],
        ["src.tasks.trigger.FastTravelTask", "FastTravelTask"],
        ["src.tasks.trigger.HeistTask", "HeistTask"],
        ["src.tasks.trigger.AutoLoginTask", "AutoLoginTask"],
    ],
    "custom_tabs": [
        ["src.ui.DailyRoutineTab", "DailyRoutineTab"],
        ["src.ui.GiftManagerTab", "GiftManagerTab"],
        ["src.ui.CharHubTab", "CharHubTab"],
        ["src.ui.MidiPlayerTab", "MidiPlayerTab"],
        # ['src.ui.MyTab', 'MyTab'], #可选, 自定义UI, 显示在侧边栏
    ],
    "scene": ["src.scene.NTEScene", "NTEScene"],
    "update_pyappify": {
        "to_version": "1.2.6",
        "zip_url": "https://github.com/BnanZ0/ok-nte/releases/download/v1.5.0/ok-nte-win32.zip",
        "sha256": "2d2a6dee7970e626cc3ed4ec8a3078f0808c3a01b9cf45551a94403a0c1749b2",
    },
}
