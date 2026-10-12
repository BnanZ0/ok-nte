if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()

    import os
    import sys

    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))

    from src.config import config
    from src.patches.startup_patches import install_startup_patches

    install_startup_patches(config)

    import ok

    ok_instance = ok.OK(config)
    ok_instance.start()
