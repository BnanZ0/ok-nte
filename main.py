if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()

    import os
    import sys

    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

    from src.config import config
    from src.patches.i18n_patch import install_i18n_patch

    install_i18n_patch()

    import ok

    ok_instance = ok.OK(config)
    ok_instance.start()
