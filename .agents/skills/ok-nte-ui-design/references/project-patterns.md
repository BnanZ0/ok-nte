# ok-nte UI patterns

## Project examples

| Surface | Existing example |
| --- | --- |
| Top-level pages | The app registers pages in [`src/config.py`](../../../../src/config.py). Use the existing `CustomTab` shell for project pages. |
| List and detail | [Daily Routine](../../../../src/ui/DailyRoutineTab.py) puts the task list beside the selected task's details, with page tabs for related views and the main action in the detail header. |
| Preferences | The installed kit Gallery demonstrates `SettingGroup` and `SettingRow` with `Switch` and other inputs. [Daily Routine](../../../../src/ui/DailyRoutineTab.py) uses the shared `ConfigForm` for task settings. |
| Page tabs and action hierarchy | [MIDI Player](../../../../src/ui/MidiPlayerTab.py) uses the kit `TabStrip` for library views and the project `PageTabStrip` for settings views. The player emphasizes Play/Pause and uses default buttons for Previous and Next. |
| Dialogs and feedback | [Shared dialogs](../../../../src/ui/foundation/dialogs.py) and [toast feedback](../../../../src/ui/foundation/info_bar.py) show the project's conventions for focused decisions and brief outcomes. |

For Qt kit APIs, check the version pinned in [`pyproject.toml`](../../../../pyproject.toml) and inspect `ok/ui/qt/kit/` in the local environment. When `.venv` is available on Windows, run the kit Gallery with `.\.venv\Scripts\python.exe -m ok.ui.qt.kit.gallery`.

## Project theme

The [theme patch](../../../../src/patches/theme_patch.py) defines the project's dark surfaces and switch styling. When the configured accent matches the Windows system accent, it uses the theme-specific Windows shade. Text and icons on accent fills follow the project's `on_accent` contrast rule. Check theme-sensitive changes in the running ok-nte app with its overrides applied.

For Windows visual conventions, consult [Microsoft's design guidelines](https://learn.microsoft.com/en-us/windows/apps/design/guidelines-overview) and [WinUI control patterns](https://learn.microsoft.com/en-us/windows/apps/develop/ui/controls/).
