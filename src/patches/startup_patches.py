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

    from src.patches.cloud_launch_guard import install_cloud_launch_guard
    from src.patches.i18n_patch import install_i18n_patch
    from src.patches.window_variants_patch import install_window_variants_patch

    install_i18n_patch()
    install_window_variants_patch(config)
    # Must come after install_window_variants_patch: the outer selection hook
    # needs the variant that the inner hook has just applied.
    install_cloud_launch_guard()

    if ui_mode == "qt":
        from src.patches.admin_restart_patch import install_admin_restart_patch

        # The admin prompt must stay visible while UAC is answered; the stock
        # handler quits the app first and leaves the user with no window.
        install_admin_restart_patch()
    # if ui_mode == "qt":
    #     from src.patches.task_tab_patch import install_task_tab_patch

    #     install_task_tab_patch()
    _PATCH_INSTALLED = True
