---
name: translate-task-i18n
description: Add, sync, or repair gettext translations for Python task classes in ok-script style projects. Use when Codex is asked to translate a task, sync missing translations, add i18n entries for task name/default_config/description/config_description, or compile i18n .po files into .mo files.
---

# Translate Task I18n

Use this skill for task translation work in repositories that keep gettext catalogs under `i18n/<locale>/LC_MESSAGES/ok.po`.

## Workflow

1. Inspect the target task file and collect user-facing task metadata:
   - `self.name`
   - `self.description`
   - keys and string values in `self.default_config`
   - string values in `self.config_description`
   - dropdown/list option strings if they are shown in config UI
2. Check whether each string already exists in all `i18n/*/LC_MESSAGES/ok.po` catalogs.
3. Add missing `msgid` blocks to every locale. Preserve existing translations unless the user asks to change them.
4. Translate into every locale present under the repo's `i18n` directory. Discover locales by listing `i18n/*/LC_MESSAGES/ok.po`; do not hard-code a fixed language list.
5. Compile every changed `ok.po` into `ok.mo`.
6. Verify with a syntax/format check and search for duplicate `msgid` entries.

## Helper Script

Use `scripts/task_i18n_helper.py` from this skill when helpful:

## Python Environment

When running helper scripts or validation commands, use the project virtual environment if one exists. Prefer these interpreters in order:

1. `.venv\Scripts\python.exe`
2. `venv\Scripts\python.exe`
3. `env\Scripts\python.exe`
4. Global `python`

If no project virtual environment exists, fall back to global `python`.

```powershell
.\.venv\Scripts\python.exe ~\.codex\skills\translate-task-i18n\scripts\task_i18n_helper.py scan --task src\tasks\LauncherTask.py
.\.venv\Scripts\python.exe ~\.codex\skills\translate-task-i18n\scripts\task_i18n_helper.py compile --i18n i18n
```

The scanner is a helper, not a substitute for reading the task. It finds common literal strings but may miss values built through constants or formatting.

## Catalog Rules

- Append new entries directly as plain text to the end of the PO file (safely handling line endings by reading with `newline=""` and writing back with `newline="\n"` to avoid double carriage returns `\r\r\n`), rather than using `polib.POFile.save()` which rewrites the entire file and alters the formatting/wrapping of other entries.
- Do not add log-only strings unless the user explicitly asks; focus on UI strings from task metadata/config.
- Keep `msgid` exactly equal to the source string used by the code.
- Empty `msgstr` is acceptable only when that locale intentionally falls back to the source language.
- After editing `.po`, always compile `.mo`.
