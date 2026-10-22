# Contributing and Verification

## Change Principles

- Keep changes small and easy to review; do not refactor unrelated files along the way.
- Automation must continue to interact with the game only through the UI, OCR, system audio, and ordinary input.
- Do not commit user logs, screenshots, configuration, account information, secrets, or personal paths.
- For changes involving the combat planner, combat state, audio capture, or user-data compatibility, add the relevant tests and document the risks.

## Imports Between External Characters

Each external character directory has its own Python package namespace. Use relative imports
between teammates without an `__init__.py`, changes to `sys.path`, or manual `sys.modules` caches:

```python
from .zankou import get_rotation, make_rotation_next_action
```

Use valid Python identifiers for imported filenames, such as `zankou.py`.
Importing another character class does not register it twice; each character file still defines
exactly one implementation. Modules are shared within a directory and isolated between directories.
Rescanning reloads external modules, but does not replace existing character instances or reset
custom state stored on the task. Avoid circular imports between character files.
Workshop ZIP rules are unchanged: shared logic can live in a declared character file.
Imports use Python's standard machinery, including lazy imports inside methods. Scanning
clears loaded external modules and the current interpreter's bytecode caches corresponding
to Python sources in the external directory so edited code is refreshed. If cache cleanup
fails, scanning logs a warning and stops loading external characters rather than using stale code.
Relative imports are not a sandbox: scanning external characters still executes their Python code.

## Documentation Contributions

- Put user-facing instructions under "Getting started", "Features", or "Guides".
- Put API, architecture, and implementation notes under "Development".
- Add every new page to the `nav` section in the root `mkdocs.yml`; otherwise it will not appear in the website navigation.
- Use relative Markdown links. Before submitting, run a strict build to check for broken links and configuration errors.

```powershell
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

## Code Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*.py" --top-level-directory .
```

State which checks you ran in the submission description, along with any checks that could not be run because of environment limitations.
