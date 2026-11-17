---
name: use-venv
description: Use the repository's .venv for Python commands when it exists.
---

# Use Repository Virtual Environment

When running Python commands in a repository with a `.venv`, use that environment's interpreter for scripts, tests, and package tools. Use the interpreter directly rather than requiring activation: `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on macOS and Linux. Run tools through the interpreter, for example `python -m pytest` or `python -m pip`, with `python` replaced by the environment's interpreter path.

If the repository has no `.venv`, use the interpreter appropriate to the project and environment.
