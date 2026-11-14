import sys

from ok.core.ui_config import resolve_ui_config

from src.patches.i18n_patch import install_i18n_patch
from src.patches.theme_patch import install_theme_patch


def install_startup_patches(config):
    install_i18n_patch()
    if resolve_ui_config(config) is not None and not {"-h", "--help", "--headless"}.intersection(
        sys.argv[1:]
    ):
        install_theme_patch()
