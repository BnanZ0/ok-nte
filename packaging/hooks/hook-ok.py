"""Collect ok-script's lazy public imports and runtime UI resources."""

from PyInstaller.utils.hooks import collect_data_files, copy_metadata, get_module_attribute

# Inspect the lazy export registry in an isolated subprocess.
lazy_imports = get_module_attribute("ok", "_LAZY_IMPORTS")
hiddenimports = sorted({module_name for module_name, _ in lazy_imports.values()})
datas = collect_data_files("ok") + copy_metadata("ok-script")
