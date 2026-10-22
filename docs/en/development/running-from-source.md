# Running from Source

## Environment Setup

- Python 3.12.
- [uv](https://docs.astral.sh/uv/) is recommended.
- A Windows development environment.

```bash
git clone https://github.com/BnanZ0/ok-nte.git
cd ok-nte
uv sync
```

## Starting the Application

```bash
# Production entry point
python main.py

# Debug entry point with more detailed logs
python main_debug.py
```

## Tests and Static Checks

The repository uses `unittest`. When a local virtual environment is available, prefer its interpreter:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "*.py" --top-level-directory .
.\.venv\Scripts\python.exe -m py_compile src\flow.py
```

If Ruff is installed, you can run:

```powershell
ruff check .
```

## Building the Documentation Site

Documentation dependencies are separate from application dependencies. After installation, you can generate HTML that can be hosted by any static web server:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

The default output directory is `site/`. To preview the site locally:

```powershell
.\.venv\Scripts\python.exe -m mkdocs serve
```
