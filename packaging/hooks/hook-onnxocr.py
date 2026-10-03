"""Collect OCR models and dictionaries; Python imports are analyzed normally."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("onnxocr", includes=["models/**"])
