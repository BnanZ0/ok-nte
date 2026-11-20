"""Project overrides for the ok-script theme and widgets."""

import re

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
    from ok.ui.qt.kit import buttons as kit_buttons
    from ok.ui.qt.kit import qss as kit_qss
    from ok.ui.qt.kit.icons import icon_file
    from ok.ui.qt.kit.switch import Switch
    from ok.ui.qt.kit.theme import DARK_TOKENS, ThemeManager, contrast_ratio, theme
    from PySide6.QtCore import QPropertyAnimation, QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPalette, QPen

    from src.ui.icons import resource_icon

    DARK_TOKENS.update(DARK_TOKEN_OVERRIDES)

    def paint_switch(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0.5, 0.5, self.WIDTH - 1, self.HEIGHT - 1)
        radius = track.height() / 2
        enabled = self.isEnabled()
        if self._animation.state() != QPropertyAnimation.State.Running:
            self._position = 1.0 if self.isChecked() else 0.0
        position = self._position

        if self.isChecked():
            color = theme.accent()
            if not enabled:
                color.setAlphaF(0.4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
        else:
            painter.setPen(QPen(theme.color("switch_off" if enabled else "border"), 1))
            painter.setBrush(theme.color("control"))
        painter.drawRoundedRect(track, radius, radius)

        knob = 14
        x = 4 + (self.WIDTH - 8 - knob) * position
        y = (self.HEIGHT - knob) / 2
        if self.isChecked():
            knob_color = theme.color("on_accent")
            if not enabled:
                knob_color.setAlphaF(0.7)
        else:
            knob_color = theme.color("text_secondary" if enabled else "text_disabled")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(x, y, knob, knob))

        if (
            self.hasFocus()
            and self.focusPolicy() != Qt.FocusPolicy.NoFocus
            and self._keyboard_focus
        ):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.accent(), 1.5))
            painter.drawRoundedRect(track.adjusted(-0.5, -0.5, 0.5, 0.5), radius, radius)

    Switch.paintEvent = paint_switch

    # Match the Windows accent shade used by Settings in each theme.
    original_accent = ThemeManager.accent
    system_accent_variant_cache = {}

    def system_accent_variant(base_name: str, dark: bool) -> str | None:
        cache_key = (base_name, dark)
        if cache_key in system_accent_variant_cache:
            return system_accent_variant_cache[cache_key]
        try:
            from ok.rotypes.Windows.UI.ViewManagement import UIColorType, get_color_value

            base = get_color_value(UIColorType.Accent)
            if QColor(base.red, base.green, base.blue).name() != base_name:
                return None
            variant_name = "AccentLight2" if dark else "AccentDark1"
            variant = get_color_value(getattr(UIColorType, variant_name))
        except (ImportError, OSError, TypeError, AttributeError):
            return None
        color = QColor(variant.red, variant.green, variant.blue).name()
        system_accent_variant_cache[cache_key] = color
        return color

    def accent(self) -> QColor:
        if variant := system_accent_variant(self._accent.name(), self.is_dark()):
            return QColor(variant)
        return original_accent(self)

    ThemeManager.accent = accent

    # Keep text and icons legible against the active accent shade.
    original_color = ThemeManager.color

    def color(self, name: str) -> QColor:
        if name == "on_accent":
            accent = self.accent()
            black = QColor("#000000")
            white = QColor("#FFFFFF")
            return (
                black if contrast_ratio(accent, black) >= contrast_ratio(accent, white) else white
            )
        return original_color(self, name)

    ThemeManager.color = color

    original_palette = ThemeManager.palette

    def palette(self) -> QPalette:
        result = original_palette(self)
        result.setColor(QPalette.ColorRole.HighlightedText, self.color("on_accent"))
        return result

    ThemeManager.palette = palette

    # Override the upstream foregrounds that are hardcoded to white.
    original_build_stylesheet = kit_qss.build_stylesheet
    original_button_icon = kit_buttons.kit_icon

    def button_icon(name, color=None, stroke_width: float = 1.75):
        if name == "play":
            return resource_icon("play", color=color or "text")
        return original_button_icon(name, color, stroke_width)

    kit_buttons.kit_icon = button_icon

    def build_stylesheet(manager) -> str:
        # Remove radio-only rules at line starts and radio selectors after commas.
        stylesheet = re.sub(
            r"(?<![^\n])QRadioButton\b[^,{}]*\{[^{}]*\}\n?|,\s*QRadioButton\b[\w:-]*",
            "",
            original_build_stylesheet(manager),
        )
        on_accent = manager.color("on_accent").name()
        check_icon = icon_file("check", on_accent, 2.6)
        return stylesheet + (
            f'\nButton[variant="primary"] QLabel#buttonText:enabled {{ color: {on_accent}; }}'
            f'\nButton[variant="primary"] QLabel[role="kbd"]:enabled {{ color: {on_accent}; }}'
            "\nQLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit "
            f"{{ selection-color: {on_accent}; }}"
            f'\nQCheckBox::indicator:enabled:checked {{ image: url("{check_icon}"); }}'
        )

    kit_qss.build_stylesheet = build_stylesheet
