from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent


def python_modules(directory):
    # Config registrations and character discovery use dynamic imports.
    modules = set()
    for path in directory.rglob("*.py"):
        parts = list(path.relative_to(directory.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts and all(part.isidentifier() for part in parts):
            modules.add(".".join(parts))
    return modules


hiddenimports = python_modules(PROJECT_ROOT / "src")
# pywin32 imports this module from native code when converting COM dates.
hiddenimports.add("win32timezone")
# Keep concrete characters as source for discovery, execution and source copying.
module_collection_mode = {"src": "pyz"}
for path in (PROJECT_ROOT / "src" / "char").glob("*.py"):
    if path.stem not in {"BaseChar", "Support", "__init__"} and path.stem.isidentifier():
        module_collection_mode[f"src.char.{path.stem}"] = "py"
datas = []
binaries = []

# build.ps1 copies application resources next to the EXE after collection.

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(hiddenimports),
    hookspath=[str(PROJECT_ROOT / "packaging" / "hooks")],
    runtime_hooks=[],
    excludes=[],
    module_collection_mode=module_collection_mode,
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [('X utf8', None, 'OPTION')],
    exclude_binaries=True,
    name="ok-nte",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(PROJECT_ROOT / "icons" / "icon.png"),
    uac_admin=True,
    contents_directory="_internal",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ok-nte")
