"""资产目录解析：插件安装时优先用插件自带的 assets，否则回退宿主 exe 目录。"""

from pathlib import Path

from ok import get_path_relative_to_exe

# 兼容两种安装形态：
# 宿主源码/打包：模块在 src/tasks/cultivation_goal/，资产在 exe 目录 assets/game/<name>
# 插件：模块在 ok_import/<file_name>/cultivation_goal/，资产在插件根目录 assets/game/<name>


def resolve_asset_dir(name: str) -> Path:
    plugin_local = Path(__file__).resolve().parents[1] / "assets" / "game" / name
    if plugin_local.is_dir():
        return plugin_local
    return Path(get_path_relative_to_exe("assets", "game", name))
