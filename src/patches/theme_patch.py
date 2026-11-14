"""Project overrides for the ok-script dark palette."""

DARK_TOKEN_OVERRIDES: dict[str, str] = {
    "surface": "#1D1D26",
    "surface_alt": "#24242F",
    "control": "#30303D",
    "control_hover": "#3B3B4B",
    "control_pressed": "#282834",
    "border": "#393947",
    "border_strong": "#555568",
    "separator": "#30303D",
    "text_secondary": "#B9B9C7",
    "text_tertiary": "#A4A4B5",
    "switch_off": "#68687A",
}


def install_theme_patch():
    from ok.ui.qt.kit.theme import DARK_TOKENS

    DARK_TOKENS.update(DARK_TOKEN_OVERRIDES)
