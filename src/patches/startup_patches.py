from __future__ import annotations

import sys

from ok.core.ui_config import resolve_ui_config

_PATCH_INSTALLED = False


def resolve_runtime_ui_mode(config, argv=None) -> str | None:
    ui_config = resolve_ui_config(config)
    arguments = sys.argv[1:] if argv is None else argv
    if ui_config is None or any(argument in {"-h", "--headless"} for argument in arguments):
        return None
    return ui_config["type"]


def install_startup_patches(config):
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.ui.task_icons import Icon

    ui_mode = resolve_runtime_ui_mode(config)
    Icon.configure(ui_mode)

    from src.patches.i18n_patch import install_i18n_patch

    install_i18n_patch()
    # if ui_mode == "qt":
    #     from src.patches.task_tab_patch import install_task_tab_patch

    #     install_task_tab_patch()
    _PATCH_INSTALLED = True
