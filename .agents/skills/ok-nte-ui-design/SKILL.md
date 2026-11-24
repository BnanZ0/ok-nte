---
name: ok-nte-ui-design
description: Design, implement, or review ok-nte desktop UI in PySide6. Use for project pages, navigation, settings, dialogs, control styling, and theme work using the ok-script Qt kit and the project's WinUI-inspired conventions.
---

# ok-nte UI Design

Use the existing ok-nte interface as the baseline for visual style and interaction. Refer to Microsoft's WinUI guidance for individual Windows control patterns such as button emphasis, switches, selection, and keyboard focus.

## Choose components

- Inspect the nearby page, shared widgets in `src/ui/foundation/`, and the installed `ok.ui.qt.kit` API before introducing a control. Use the installed kit source and Gallery for widget construction and supported variants. Use existing project pages to judge how those controls should look together.
- Use kit controls directly when they fit. Use standard PySide6 controls and layouts for ordinary Qt needs. Add project widgets for project-specific behavior or styling that needs to be reused.
- Use the existing application shell for top-level destinations. Use `TabStrip` or the project's `PageTabStrip` for a small set of peer views within a page. Group related preferences by user goal and use `SettingRow` with the input that fits the setting.
- For concrete component and page examples, read [Project patterns](references/project-patterns.md).

## Preserve the visual language

- Use the semantic `Label` roles for headings, body text, and supporting text. Align related controls and reuse the nearby page's spacing and margins. Use cards where the grouping clarifies the task.
- Use a `primary` button for the dominant action in the current task when one exists. Use the default button for supporting actions and `subtle` for light contextual actions. Give icon-only buttons a tooltip and an accessible name.
- Keep validation near the affected field. Use the project's toast convention for brief feedback and a dialog for decisions that need the user's attention. Keep ongoing status visible where the action happens.
- Use the kit's theme tokens and existing project overrides for backgrounds, borders, text, and accent colors. Use `on_accent` for foregrounds on accent fills. Keep component-specific QSS local and update token-derived styling when the theme changes.

## Verify the affected UI

Check light, dark, and system theme behavior when appearance changes. Check tab order, keyboard activation, visible focus, and the relevant selected, disabled, loading, empty, or error states. For layout changes, check a narrow window, high DPI, and longer translated labels. Use Qt layouts and size policies, with scrolling for content that can exceed its available space.
