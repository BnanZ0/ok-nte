from __future__ import annotations

from functools import wraps

from src.utils.i18n_format import match_i18n_format

_PATCH_INSTALLED = False


def _translate_key(self, key, original_tr):
    if isinstance(key, str) and (matched_format := match_i18n_format(key)):
        rule, match = matched_format
        if self.to_translate is not None:
            self.to_translate.discard(key)
        values = match.groupdict()
        for field in rule.translated_fields:
            values[field] = _translate_key(self, values[field], original_tr)
        template = original_tr(self, rule.template) if rule.translate_template else rule.template
        args = [values[field] for field in rule.positional_fields]
        return template.format(*args, **values)

    translated = original_tr(self, key)
    if self.to_translate is not None and isinstance(key, str) and key.strip().isdigit():
        self.to_translate.discard(key)
    return translated


def _wrap_tr(original_tr, boss_names):
    @wraps(original_tr)
    def tr_with_overrides(self, key):
        if isinstance(key, str) and (names := boss_names.get(key)):
            return names.get(self.locale.name()) or names.get("en_US") or key
        return _translate_key(self, key, original_tr)

    return tr_with_overrides


def install_i18n_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok import App, HeadlessApp, get_path_relative_to_exe
    from ok.util.file import read_json_file

    path = get_path_relative_to_exe("assets", "game", "weeklyboss_item", "boss_names.json")
    boss_names = read_json_file(path) or {}
    App.tr = _wrap_tr(App.tr, boss_names)
    HeadlessApp.tr = _wrap_tr(HeadlessApp.tr, boss_names)
    _PATCH_INSTALLED = True
