from ok.core.ui_config import resolve_ui_config
from ok.util.process import parse_arguments_to_map

from src.patches.i18n_patch import install_i18n_patch
from src.patches.plugin_repository_patch import install_plugin_repository_patch
from src.patches.task_group_filter_patch import install_task_group_filter_patch
from src.patches.theme_patch import install_theme_patch


def install_startup_patches(config):
    install_i18n_patch()
    if config.get("debug"):
        run_code_tab = ["src.ui.RunCodeTab", "RunCodeTab"]
        custom_tabs = config.setdefault("custom_tabs", [])
        if run_code_tab not in custom_tabs:
            custom_tabs.append(run_code_tab)

    if resolve_ui_config(config) is not None and not parse_arguments_to_map()["headless"]:
        install_task_group_filter_patch()
        install_theme_patch()
        if config.get("plugin"):
            install_plugin_repository_patch(config.get("plugin_repositories", ()))
