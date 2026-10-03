"""Collect runtime-loaded OpenVINO plugins and their accompanying data."""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Device plugins and model frontends are loaded by the native runtime.
binaries = collect_dynamic_libs("openvino")
datas = collect_data_files("openvino", includes=["libs/*.json"])
