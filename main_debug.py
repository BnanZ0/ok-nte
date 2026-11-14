if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()

    from src.config import config
    from src.patches.i18n_patch import install_i18n_patch

    install_i18n_patch()

    import ok

    config["debug"] = True
    ok_instance = ok.OK(config)
    ok_instance.start()
