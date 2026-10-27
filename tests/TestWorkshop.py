import io
import json
import os
import shutil
import stat
import tempfile
import unittest
import uuid
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

from src.char.core.CharFactory import get_char_by_impl_id
from src.char.core.CharRegistry import char_registry
from src.char.custom.CustomCharDb import CustomCharDb
from src.char.custom.CustomCharManager import CustomCharManager
from src.char.workshop.archive import MAX_ARCHIVE_BYTES, ArchiveContents, load_archive
from src.char.workshop.models import (
    CatalogEntry,
    PackageSlot,
    TeamPackage,
    WorkshopFormatError,
    filter_catalog_entries,
    group_catalog_entries,
    parse_catalog,
    parse_version,
)
from src.char.workshop.repository import IndexSource, WorkshopRepository, WorkshopRepositoryError
from src.char.workshop.service import (
    WorkshopInstallError,
    WorkshopInstallErrorCode,
    WorkshopPackageService,
)
from src.ui.features.characters.workshop_dialog import (
    BackgroundCall,
    PackageImportDialog,
    PackageMetadataDialog,
    WorkshopDialog,
)

SOURCE = (
    "from src.char.BaseChar import BaseChar, Element\n\n"
    "class SampleExternal(BaseChar):\n"
    "    cn_name = '示例角色'\n"
    "    en_name = 'SampleExternal'\n"
    "    element = Element.PURPLE\n"
)


def make_package(external=True):
    slots = [
        PackageSlot(
            0,
            "builtin",
            {"zh_CN": "零", "en_US": "Zero"},
            impl_id="builtin:zero",
        )
    ]
    if external:
        slots.append(
            PackageSlot(
                1,
                "external",
                {"zh_CN": "示例角色", "en_US": "SampleExternal"},
                file_name="sample.py",
                class_name="SampleExternal",
            )
        )
    return TeamPackage("测试方案", "说明", "作者", "1.0.0", tuple(slots))


class TestWorkshop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def assert_update_indicator_visible(self, item, expected):
        image = item.icon().pixmap(12, 12).toImage()
        visible = any(
            image.pixelColor(x, y).alpha()
            for y in range(image.height())
            for x in range(image.width())
        )
        self.assertEqual(visible, expected)

    def setUp(self):
        root = Path(os.getcwd()) / "tests" / ".tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temp_dir = root / f"workshop_{uuid.uuid4().hex}"
        self.temp_dir.mkdir()
        self.external_dir = self.temp_dir / "external_chars"
        self.db_path = self.temp_dir / "db.json"
        self.features_dir = self.temp_dir / "features"
        self.patchers = [
            patch("src.char.custom.CustomCharManager.CUSTOM_CHARS_DIR", self.temp_dir),
            patch("src.char.custom.CustomCharManager.DB_PATH", self.db_path),
            patch("src.char.custom.CustomCharManager.FEATURES_DIR", self.features_dir),
            patch("src.char.custom.CustomCharManager.EXTERNAL_CHARS_DIR", self.external_dir),
        ]
        for patcher in self.patchers:
            patcher.start()
        CustomCharManager._instance = None
        CustomCharDb.reset_instance()
        char_registry.rescan_external()
        self.manager = CustomCharManager()
        self.service = WorkshopPackageService(self.manager, char_registry)

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        CustomCharManager._instance = None
        CustomCharDb.reset_instance()
        char_registry.rescan_external()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_and_import_only_selected_implementation_sources(self):
        source_dir = self.external_dir / "original"
        source_dir.mkdir(parents=True)
        (source_dir / "sample.py").write_text(SOURCE, encoding="utf-8")
        (source_dir / "unused.py").write_text(
            SOURCE.replace("SampleExternal", "Unused"), encoding="utf-8"
        )
        char_registry.rescan_external()
        preset = self.manager.create_team_preset("本地方案")
        self.assertTrue(
            self.manager.update_team_preset(
                preset["id"],
                slots=[
                    {"char_id": "", "impl_id": "builtin:zero"},
                    {"char_id": "", "impl_id": "external:original/sample"},
                    {"char_id": "", "impl_id": ""},
                    {"char_id": "", "impl_id": ""},
                ],
            )
        )
        archive_path = self.temp_dir / "out.zip"
        self.service.export_preset(preset["id"], make_package(), archive_path)

        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {"team.json", "sample.py"})
        contents = self.service.load_archive(archive_path)
        imported = self.service.install_contents(contents, "导入方案", "sample_author_1.0.0")
        self.assertEqual(imported.preset_name, "导入方案")
        saved = next(
            item for item in self.manager.get_team_presets() if item["id"] == imported.preset_id
        )
        self.assertEqual(saved["slots"][0], {"char_id": "", "impl_id": "builtin:zero"})
        self.assertEqual(saved["slots"][1]["impl_id"], "external:sample_author_1.0.0/sample")
        self.assertTrue((self.external_dir / "original" / "unused.py").is_file())

    def test_install_rejects_conflict_without_touching_existing_files(self):
        target = self.external_dir / "existing"
        target.mkdir(parents=True)
        marker = target / "keep.py"
        marker.write_text("keep", encoding="utf-8")
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})

        with self.assertRaises(WorkshopInstallError):
            self.service.install_contents(contents, "新方案", "existing")
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse(any(item["name"] == "新方案" for item in self.manager.get_team_presets()))

    def test_update_preserves_all_bindings_and_records_original_directory(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE + "\n    revision = 1\n"})
        imported = self.service.install_contents(contents, "Local name", "original_1.0.0")
        impl_id = "external:original_1.0.0/sample"
        character = self.manager.create_character("Local character", impl_id)
        self.manager.update_team_preset(
            imported.preset_id,
            name="Renamed locally",
            slots=[{"char_id": character, "impl_id": impl_id}],
        )
        self.manager.apply_team_preset(imported.preset_id, fixed=True)
        presets_before = self.manager.get_team_presets()
        characters_before = self.manager.get_all_characters()
        task = Mock(running=True)
        running_char = get_char_by_impl_id(task, 0, impl_id)
        task.chars = [running_char]
        updated = ArchiveContents(
            replace(contents.package, version="1.1.0"),
            {"sample.py": SOURCE.replace("Element.PURPLE", "Element.PURPLE\n    revision = 2")},
        )
        self.service.update_contents(updated, imported.directory)

        self.assertEqual(self.manager.get_team_presets(), presets_before)
        self.assertEqual(self.manager.get_all_characters(), characters_before)
        self.assertEqual(char_registry.get(impl_id).char_cls.revision, 2)
        self.assertIs(task.chars[0], running_char)
        self.assertEqual(running_char.revision, 1)
        self.assertEqual(get_char_by_impl_id(task, 0, impl_id).revision, 2)
        record = self.service.get_installations()[0]
        self.assertEqual(record.directory, "original_1.0.0")
        self.assertEqual(record.version, "1.1.0")
        self.assertEqual(record.files, {"sample.py": "sample.py"})
        restarted = WorkshopPackageService(self.manager, char_registry)
        self.assertEqual(restarted.get_installations(), [record])

    def test_update_rejects_any_python_file_list_change_without_mutation(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        self.service.install_contents(contents, "Local", "original")
        record_before = self.service.installation_store.path.read_bytes()
        new_slot = replace(contents.package.slots[1], file_name="renamed.py")
        variants = [
            ArchiveContents(
                replace(contents.package, version="1.1.0", slots=(new_slot,)),
                {"renamed.py": SOURCE},
            ),
            ArchiveContents(replace(contents.package, version="1.1.0"), {}),
            ArchiveContents(
                replace(contents.package, version="1.1.0"),
                {"sample.py": SOURCE, "added.py": SOURCE},
            ),
        ]
        for update in variants:
            with self.subTest(files=list(update.sources)):
                with self.assertRaises(WorkshopInstallError) as caught:
                    self.service.update_contents(update, "original")
                self.assertEqual(caught.exception.code, WorkshopInstallErrorCode.FILES_CHANGED)
                self.assertEqual(self.service.installation_store.path.read_bytes(), record_before)
                self.assertEqual(
                    self.manager.get_external_impl_source("external:original/sample"), SOURCE
                )

    def test_crlf_archive_sources_can_be_updated_repeatedly_without_local_edits(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE.replace("\n", "\r\n")})
        self.service.install_contents(contents, "Local", "original")
        for version, newline in (("1.1.0", "\r\n"), ("1.2.0", "\n")):
            source = (SOURCE + f"\n    revision = '{version}'\n").replace("\n", newline)
            update = ArchiveContents(
                replace(contents.package, version=version), {"sample.py": source}
            )
            self.service.update_contents(update, "original")
            self.assertEqual(self.service.get_installations()[0].version, version)
            self.assertEqual(
                char_registry.get("external:original/sample").char_cls.revision, version
            )
            self.assertNotIn(b"\r\r\n", (self.external_dir / "original" / "sample.py").read_bytes())

    def test_failed_update_restores_all_files_registry_and_version(self):
        helper_slot = PackageSlot(
            2,
            "external",
            {"zh_CN": "Helper", "en_US": "Helper"},
            file_name="helper.py",
            class_name="Helper",
        )
        package = replace(make_package(), slots=(*make_package().slots, helper_slot))
        helper = SOURCE.replace("SampleExternal", "Helper")
        contents = ArchiveContents(package, {"sample.py": SOURCE, "helper.py": helper})
        self.service.install_contents(contents, "Local", "original")
        record_before = self.service.installation_store.path.read_bytes()
        files_before = {
            name: (self.external_dir / "original" / name).read_bytes() for name in contents.sources
        }
        broken = ArchiveContents(
            replace(package, version="1.1.0"),
            {"sample.py": SOURCE + "\nNEW_REVISION = 2\n", "helper.py": "raise RuntimeError()\n"},
        )
        with self.assertRaises(WorkshopInstallError):
            self.service.update_contents(broken, "original")
        for name, previous in files_before.items():
            self.assertEqual((self.external_dir / "original" / name).read_bytes(), previous)
            entry = char_registry.get(f"external:original/{Path(name).stem}")
            self.assertIsNotNone(entry)
        self.assertEqual(self.service.installation_store.path.read_bytes(), record_before)

    def test_update_loads_cross_file_dependencies_as_one_batch(self):
        helper_slot = PackageSlot(
            2,
            "external",
            {"zh_CN": "Helper", "en_US": "Helper"},
            file_name="helper.py",
            class_name="Helper",
        )
        package = replace(make_package(), slots=(*make_package().slots, helper_slot))
        helper = SOURCE.replace("SampleExternal", "Helper") + "\nVALUE = 1\n"
        sample = "from .helper import VALUE\n" + SOURCE + "\n    revision = VALUE\n"
        contents = ArchiveContents(package, {"sample.py": sample, "helper.py": helper})
        self.service.install_contents(contents, "Local", "original")
        update = ArchiveContents(
            replace(package, version="1.1.0"),
            {
                "sample.py": sample.replace("VALUE", "NEW_VALUE"),
                "helper.py": helper.replace("VALUE = 1", "NEW_VALUE = 2"),
            },
        )
        self.service.update_contents(update, "original")
        self.assertEqual(char_registry.get("external:original/sample").char_cls.revision, 2)

    def test_update_rolls_back_when_installation_record_cannot_be_saved(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        self.service.install_contents(contents, "Local", "original")
        record_before = self.service.installation_store.path.read_bytes()
        update = ArchiveContents(
            replace(contents.package, version="1.1.0"),
            {"sample.py": SOURCE + "\nNEW_REVISION = 2\n"},
        )
        with patch.object(
            self.service.installation_store,
            "save_installation",
            side_effect=OSError("disk unavailable"),
        ):
            with self.assertRaises(OSError):
                self.service.update_contents(update, "original")
        self.assertEqual(self.manager.get_external_impl_source("external:original/sample"), SOURCE)
        self.assertEqual(self.service.installation_store.path.read_bytes(), record_before)

    def test_update_overwrites_local_edits_and_refreshes_hashes(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        self.service.install_contents(contents, "Local", "original")
        update = ArchiveContents(
            replace(contents.package, version="1.1.0"),
            {"sample.py": SOURCE + "\nNEW_REVISION = 2\n"},
        )
        path = self.external_dir / "original" / "sample.py"
        path.write_text(SOURCE + "\nLOCAL_CHANGE = True\n", encoding="utf-8")
        previous_hash = self.service.get_installations()[0].hashes["sample.py"]
        guard = Mock(side_effect=RuntimeError("import or export in progress"))
        with self.assertRaises(RuntimeError):
            self.service.update_contents(update, "original", guard)
        self.assertIn("LOCAL_CHANGE", path.read_text(encoding="utf-8"))
        self.assertEqual(self.service.get_installations()[0].version, "1.0.0")
        self.service.update_contents(update, "original")
        self.assertEqual(path.read_text(encoding="utf-8"), update.sources["sample.py"])
        installs = self.service.installation_store.load()
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0].version, "1.1.0")
        self.assertNotEqual(installs[0].hashes["sample.py"], previous_hash)
        self.assertEqual(
            installs[0].hashes["sample.py"], self.service._source_hash(update.sources["sample.py"])
        )

    def test_record_failure_rolls_back_new_installation(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        with patch.object(
            self.service.installation_store,
            "save_installation",
            side_effect=OSError("disk unavailable"),
        ):
            with self.assertRaises(OSError):
                self.service.install_contents(contents, "Local", "original")
        self.assertFalse((self.external_dir / "original").exists())
        self.assertFalse(self.manager.get_team_presets())

    def test_installation_record_rejects_unsafe_mapping_and_preserves_invalid_json(self):
        path = self.service.installation_store.path
        path.write_text("invalid json", encoding="utf-8")
        with self.assertRaises(WorkshopInstallError) as caught:
            self.service.install_contents(
                ArchiveContents(make_package(), {"sample.py": SOURCE}), "Local", "original"
            )
        self.assertEqual(caught.exception.code, WorkshopInstallErrorCode.INVALID_INSTALLATIONS)
        self.assertEqual(path.read_text(encoding="utf-8"), "invalid json")
        self.assertFalse((self.external_dir / "original").exists())
        for filename in ("../outside.py", "folder/file.py", "C:outside.py"):
            data = {
                "format_version": 1,
                "installs": [
                    {
                        "author": "Author",
                        "name": "Team",
                        "version": "1.0.0",
                        "directory": "original",
                        "files": {"sample.py": filename},
                    }
                ],
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(filename=filename), self.assertRaises(WorkshopInstallError):
                self.service.get_installations()

    def test_bulk_update_continues_other_copies_and_checks_download_identity(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        for directory in ("edited", "original"):
            self.service.install_contents(contents, directory, directory)
        (self.external_dir / "edited" / "sample.py").write_text(
            SOURCE + "\n# local\n", encoding="utf-8"
        )
        update = ArchiveContents(
            replace(contents.package, version="1.1.0"),
            {"sample.py": SOURCE + "\nNEW_REVISION = 2\n"},
        )
        entry = CatalogEntry(update.package, "codes/new.zip", "new.zip", 1, "2026-10-23Z")
        repository = Mock()
        source = IndexSource("GitHub", "index", "base")
        with patch.object(self.service, "load_archive", return_value=update):
            updated, failures = self.service.update_from_catalog(
                [entry], repository, source, Mock()
            )
        self.assertEqual([item.directory for item in updated], ["edited", "original"])
        self.assertFalse(failures)
        entry = replace(entry, package=replace(entry.package, version="1.2.0"))
        with patch.object(self.service, "load_archive", return_value=contents):
            updated, failures = self.service.update_from_catalog(
                [entry], repository, source, Mock()
            )
        self.assertFalse(updated)
        self.assertEqual(failures[0][1].code, WorkshopInstallErrorCode.PACKAGE_MISMATCH)
        repository.reset_mock()
        self.service.update_from_catalog([entry], repository, source, Mock(), lambda: True)
        repository.download_archive.assert_not_called()

    def test_refresh_prunes_renamed_paths_and_shows_import_without_touching_bindings(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        imported = self.service.install_contents(contents, "Local", "original")
        self.manager.create_character("Local character", "external:original/sample")
        presets = self.manager.get_team_presets()
        characters = self.manager.get_all_characters()
        directory = self.external_dir / "original"
        path = directory / "sample.py"
        renamed_path = directory / "renamed.py"
        path.rename(renamed_path)
        latest = CatalogEntry(
            replace(contents.package, version="1.1.0"), "codes/new.zip", "new.zip", 1, "2026-10-24Z"
        )
        source = IndexSource("GitHub", "index", "base")
        repository = Mock()
        repository.fetch_catalog.return_value = ([latest], source)
        parent = QWidget()
        with patch.object(BackgroundCall, "start", lambda worker: worker._run()):
            dialog = WorkshopDialog(repository, parent, service=self.service)
        try:
            self.assertTrue(dialog.update_button.isHidden())
            self.assertFalse(dialog.import_button.isHidden())
            self.assertTrue(dialog.import_button.isEnabled())
            self.assertFalse(dialog.update_all_button.isEnabled())
            self.assertFalse(self.service.installation_store.load())
            self.assertTrue(renamed_path.is_file())
            self.assertEqual(self.manager.get_team_presets(), presets)
            self.assertEqual(self.manager.get_all_characters(), characters)
        finally:
            dialog.deleteLater()
            parent.deleteLater()
        renamed_path.rename(path)
        self.assertFalse(self.service.get_installations())
        record = self.service._installation(
            contents, imported.directory, {"sample.py": "sample.py"}
        )
        self.service.installation_store.save_installation(record)
        renamed_directory = self.external_dir / "renamed"
        self.assertTrue(directory.resolve().is_relative_to(self.temp_dir.resolve()))
        self.assertTrue(renamed_directory.resolve().is_relative_to(self.temp_dir.resolve()))
        directory.rename(renamed_directory)
        self.assertFalse(self.service.get_installations())
        self.assertFalse(self.service.installation_store.load())
        self.assertTrue((renamed_directory / "sample.py").is_file())

    def test_scan_error_preserves_records_even_if_another_installation_is_missing(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        for directory in ("missing", "denied"):
            self.service.install_contents(contents, directory, directory)
        (self.external_dir / "missing" / "sample.py").unlink()
        denied = self.external_dir / "denied" / "sample.py"
        before = self.service.installation_store.path.read_bytes()
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path == denied:
                raise PermissionError("access denied")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", stat):
            with self.assertRaises(PermissionError):
                self.service.get_installations()
        self.assertEqual(self.service.installation_store.path.read_bytes(), before)
        with patch.object(Path, "read_bytes", side_effect=PermissionError("access denied")):
            with self.assertRaises(PermissionError):
                self.service.update_contents(
                    ArchiveContents(replace(contents.package, version="1.1.0"), contents.sources),
                    "denied",
                )
        self.assertEqual(
            [item.directory for item in self.service.installation_store.load()], ["denied"]
        )

    def test_prune_save_failure_preserves_original_json(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        self.service.install_contents(contents, "Local", "original")
        (self.external_dir / "original" / "sample.py").unlink()
        before = self.service.installation_store.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("cannot replace record")):
            with self.assertRaises(OSError):
                self.service.get_installations()
        self.assertEqual(self.service.installation_store.path.read_bytes(), before)
        self.assertEqual(len(self.service.installation_store.load()), 1)
        self.assertFalse(self.service.installation_store.path.with_suffix(".tmp").exists())

    def test_update_rechecks_missing_files_and_prunes_before_overwriting(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        self.service.install_contents(contents, "Local", "original")
        update = ArchiveContents(replace(contents.package, version="1.1.0"), contents.sources)
        path = self.external_dir / "original" / "sample.py"
        with self.assertRaises(WorkshopInstallError) as caught:
            self.service.update_contents(update, "original", path.unlink)
        self.assertEqual(caught.exception.code, WorkshopInstallErrorCode.INSTALLATION_MISSING)
        self.assertFalse(self.service.installation_store.load())
        self.assertFalse(path.exists())

    def test_remote_filename_change_offers_import_and_keeps_existing_installation(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        imported = self.service.install_contents(contents, "Local", "original")
        self.manager.delete_team_preset(imported.preset_id)
        before = self.service.installation_store.path.read_bytes()
        package = replace(
            contents.package,
            version="1.1.0",
            slots=(replace(contents.package.slots[1], file_name="renamed.py"),),
        )
        latest = CatalogEntry(package, "codes/new.zip", "new.zip", 1, "2026-10-24Z")
        source = IndexSource("GitHub", "index", "base")
        repository = Mock()
        repository.fetch_catalog.return_value = ([latest], source)
        parent = QWidget()
        with patch.object(BackgroundCall, "start", lambda worker: worker._run()):
            dialog = WorkshopDialog(repository, parent, service=self.service)
        try:
            self.assertTrue(dialog.update_button.isHidden())
            self.assertTrue(dialog.import_button.isEnabled())
            self.assertFalse(dialog.update_all_button.isEnabled())
            self.assert_update_indicator_visible(dialog.table.item(0, 3), False)
            updated, failures = self.service.update_from_catalog(
                [latest], repository, source, Mock()
            )
            self.assertFalse(updated)
            self.assertFalse(failures)
            repository.download_archive.assert_not_called()
            self.assertEqual(self.service.installation_store.path.read_bytes(), before)
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_workshop_history_import_and_latest_update_follow_selected_version(self):
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})
        self.service.install_contents(contents, "Local", "original")
        update = ArchiveContents(
            replace(contents.package, version="1.1.0"),
            {"sample.py": SOURCE + "\nNEW_REVISION = 2\n"},
        )
        old = CatalogEntry(contents.package, "codes/old.zip", "old.zip", 1, "2026-10-23Z")
        latest = replace(old, package=update.package, archive="codes/new.zip")
        source = IndexSource("GitHub", "index", "base")
        repository = Mock()
        parent = QWidget()
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(repository, parent, service=self.service)
        try:
            dialog._catalog_loaded(([old, latest], source, self.service.get_installations()))
            version_item = dialog.table.item(0, 3)
            self.assert_update_indicator_visible(version_item, True)
            self.assertEqual(version_item.text(), "1.1.0")
            self.assertIn("1.0.0", version_item.toolTip())
            self.assertIn("1.1.0", version_item.toolTip())
            self.assertTrue(dialog.update_button.isEnabled())
            self.assertTrue(dialog.import_button.isHidden())
            self.assertTrue(dialog.update_all_button.isEnabled())
            imported = []
            dialog.import_requested.connect(lambda entry, src: imported.append((entry, src)))
            dialog.import_action.trigger()
            self.assertEqual(imported, [(latest, source)])
            dialog.version_combo.setCurrentIndex(1)
            self.assertTrue(dialog.update_button.isHidden())
            self.assertFalse(dialog.update_button.isEnabled())
            self.assertFalse(dialog.import_button.isHidden())
            self.assertTrue(dialog.update_all_button.isEnabled())
            dialog.import_button.click()
            self.assertEqual(imported, [(latest, source), (old, source)])
            repository.download_archive.assert_not_called()
            dialog.version_combo.setCurrentIndex(0)
            self.assertFalse(dialog.update_button.isHidden())
            self.assertTrue(dialog.import_button.isHidden())
            with (
                patch.object(BackgroundCall, "start", lambda worker: worker._run()),
                patch.object(self.service, "load_archive", return_value=update),
            ):
                dialog.update_button.button.click()
            repository.download_archive.assert_called_once_with(source, latest)
            self.assertFalse(dialog.update_button.isEnabled())
            self.assertTrue(dialog.update_button.isHidden())
            self.assertFalse(dialog.import_button.isHidden())
            self.assertFalse(dialog.update_all_button.isEnabled())
            self.assertEqual(self.service.get_installations()[0].version, "1.1.0")
            self.assert_update_indicator_visible(version_item, False)
            self.assertEqual(version_item.toolTip(), "1.1.0")
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_failed_external_scan_rolls_back_new_directory_and_preset(self):
        source = SOURCE.replace("class SampleExternal(BaseChar):", "class NotACharacter:")
        contents = ArchiveContents(make_package(), {"sample.py": source})
        with self.assertRaises(WorkshopInstallError):
            self.service.install_contents(contents, "失败方案", "broken")
        self.assertFalse((self.external_dir / "broken").exists())
        self.assertFalse(
            any(item["name"] == "失败方案" for item in self.manager.get_team_presets())
        )

    def test_existing_external_directory_has_structured_install_error(self):
        directory = self.external_dir / "existing"
        directory.mkdir(parents=True)
        contents = ArchiveContents(make_package(), {"sample.py": SOURCE})

        with self.assertRaises(WorkshopInstallError) as raised:
            self.service.install_contents(contents, "冲突方案", directory.name)

        self.assertEqual(
            raised.exception.code,
            WorkshopInstallErrorCode.EXTERNAL_DIRECTORY_EXISTS,
        )
        self.assertEqual(raised.exception.details["directory"], directory.name)
        self.assertFalse(
            any(item["name"] == "冲突方案" for item in self.manager.get_team_presets())
        )

    def test_import_dialog_rejects_existing_external_directory(self):
        parent = QWidget()
        parent.resize(800, 600)
        dialog = PackageImportDialog(
            make_package(),
            "existing.zip",
            parent,
            directory_exists=lambda directory: directory == "existing",
        )

        self.assertFalse(dialog.yesButton.isEnabled())
        self.assertIn("existing", dialog.error_label.text())

    def test_workshop_filter_header_stays_anchored_during_collapse(self):
        parent = QWidget()
        parent.resize(1400, 960)
        parent.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(Mock(), parent)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        parent.show()
        dialog.show()
        try:
            for _ in range(3):
                dialog.filter_button.click()
                self.qt_app.processEvents()
                tools = dialog.tools_card
                position = dialog.search_edit.mapTo(tools, QPoint())
                dialog.filter_button.click()
                # Check the layout before its parent has consumed the vacated space.
                tools.layout().activate()
                self.assertEqual(dialog.search_edit.mapTo(tools, QPoint()), position)
                self.qt_app.processEvents()
                self.assertEqual(dialog.search_edit.mapTo(tools, QPoint()), position)
                self.assertFalse(dialog.filter_panel.isVisible())
        finally:
            dialog.hide()
            parent.hide()
            dialog.deleteLater()
            parent.deleteLater()

    def test_workshop_dialog_uses_readable_catalog_table(self):
        entry = CatalogEntry(
            make_package(False), "codes/test.zip", "test.zip", 3041, "2026-08-18T12:09:37Z"
        )
        repository = Mock()
        parent = QWidget()
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(repository, parent)
        dialog._catalog_loaded(([entry], IndexSource("GitHub", "index", "base")))

        self.assertEqual(dialog.table.columnCount(), 4)
        self.assertEqual(dialog.table.item(0, 0).text(), "测试方案")
        self.assertEqual(dialog.table.item(0, 2).text(), "作者")
        self.assertEqual(dialog.table.item(0, 3).text(), "1.0.0")
        self.assertIn("2.97 KB", dialog.detail_meta.text())
        self.assertTrue(dialog.import_button.isEnabled())
        dialog.deleteLater()
        parent.deleteLater()

    def test_workshop_lineup_filters_compose_and_preserve_the_current_preview(self):
        old = CatalogEntry(make_package(), "codes/old.zip", "old.zip", 1, "2026-01-01T00:00:00Z")
        latest = replace(old, package=replace(old.package, version="1.1.0", description="updated"))
        other = replace(
            old,
            package=replace(
                make_package(False), name="Other", author="Other", description="updated"
            ),
            filename="other.zip",
            updated_at="2026-02-01T00:00:00Z",
        )
        parent = QWidget()
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(Mock(), parent)

        def add_keyword(word):
            dialog.keyword_edit.setText(word)
            QTest.keyClick(dialog.keyword_edit, Qt.Key.Key_Return)

        try:
            catalog = ([old, latest, other], IndexSource("GitHub", "index", "base"))
            dialog._catalog_loaded(catalog)
            dialog.table.selectRow(1)
            dialog.version_combo.setCurrentIndex(1)
            self.assertEqual(dialog.selected_package(), old.package)
            add_keyword("zE")
            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertEqual(dialog.selected_package(), old.package)
            add_keyword("sAMPle")
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.selected_package(), old.package)
            self.assertIn("1 / 2", dialog.status_label.text())
            dialog._catalog_loaded(catalog)
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.selected_package(), old.package)
            dialog.search_edit.setText("updated")
            self.assertEqual(dialog.table.rowCount(), 1)
            dialog.author_combo.setCurrentText("Other")
            self.assertEqual(dialog.table.rowCount(), 0)
            self.assertIsNone(dialog.selected_package())
            self.assertFalse(dialog.import_button.isEnabled())
            dialog.clear_filters_button.click()
            self.assertEqual(dialog.search_edit.text(), "updated")
            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertEqual(dialog.keyword_edit.keywords, [])
            dialog.keyword_edit.setText("not yet committed")
            self.assertEqual(dialog.table.rowCount(), 2)
            add_keyword("sample")
            self.assertEqual(dialog.table.rowCount(), 1)
            remove_label = dialog.tr("移除关键词 {}").format("sample")
            next(
                button
                for button in dialog.keyword_edit.chip_view.findChildren(QToolButton)
                if button.accessibleName() == remove_label
            ).click()
            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertEqual(dialog.keyword_edit.keywords, [])
            dialog._toggle_filters(True)
            add_keyword("sample")
            dialog._toggle_filters(False)
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.keyword_edit.keywords, ["sample"])
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_workshop_selects_history_for_preview_and_import(self):
        old = CatalogEntry(
            make_package(False), "codes/old.zip", "old.zip", 1, "2026-09-01T00:00:00Z"
        )
        latest = replace(
            old,
            package=replace(make_package(), version="1.10.0", description="New rotation"),
            archive="codes/latest.zip",
            updated_at="2026-08-01T00:00:00Z",
        )
        source = IndexSource("GitHub", "index", "base")
        parent = QWidget()
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(Mock(), parent)
        imported = []
        dialog.import_requested.connect(lambda entry, src: imported.append((entry, src)))
        try:
            for chinese, role in ((True, "零"), (False, "Zero")):
                dialog.is_chinese = chinese
                dialog._catalog_loaded(([old, latest], source))
                self.assertEqual(dialog.slot_cards[0].title_label.text(), role)
                self.assertEqual(dialog.table.rowCount(), 1)
                self.assertEqual(dialog.table.item(0, 3).text(), "1.10.0")
                self.assertEqual(dialog.detail_body.toPlainText(), "New rotation")
                dialog.import_button.click()
                self.assertEqual(imported[-1], (latest, source))
                dialog.version_combo.setCurrentIndex(1)
                self.assertEqual(dialog.detail_body.toPlainText(), old.package.description)
                self.assertEqual(dialog.slot_cards[1].title_label.text(), "-")
                dialog.import_button.click()
                self.assertEqual(imported[-1], (old, source))
                dialog.search_edit.setText("Zero" if chinese else "零")
                self.assertEqual(dialog.table.rowCount(), 1)
                dialog.search_edit.setText(old.package.description)
                self.assertEqual(dialog.table.rowCount(), 0)
                dialog.search_edit.setText("no matches")
                self.assertEqual(dialog.table.rowCount(), 0)
                self.assertFalse(dialog.import_button.isEnabled())
                self.assertEqual(dialog.version_combo.count(), 0)
                dialog.search_edit.clear()
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_workshop_dialog_preserves_description_text(self):
        package = TeamPackage(
            "多行方案",
            '第一行\n\n目录 C:\\new_team\n代码 print("\\n")\n字面量 \\r\\n',
            "作者",
            "1.0.0",
            make_package(False).slots,
        )
        entry = CatalogEntry(package, "codes/test.zip", "test.zip", 100, "2026-01-01T00:00:00Z")
        parent = QWidget()
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(Mock(), parent)
        try:
            dialog._catalog_loaded(([entry], IndexSource("GitHub", "index", "base")))
            self.assertEqual(dialog.detail_body.toPlainText(), package.description)
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_export_fills_selected_workshop_version_without_installing_or_remembering(self):
        defaults = TeamPackage("Local team", "", "", "1.0.0", make_package(False).slots)
        old = CatalogEntry(
            replace(make_package(), description="旧版说明\nOld rotation"),
            "codes/old.zip",
            "old.zip",
            100,
            "2026-09-01T00:00:00Z",
        )
        latest = replace(old, package=replace(old.package, version="1.1.0", description="New"))
        repository = Mock()
        parent = QWidget()
        dialog = PackageMetadataDialog(defaults, parent, repository=repository)
        imported = []

        def select_history(picker):
            self.assertTrue(picker.metadata_only)
            self.assertFalse(picker.yesButton.isEnabled())
            picker.import_requested.connect(lambda *args: imported.append(args))
            picker._catalog_loaded(([latest, old], IndexSource("GitHub", "index", "base")))
            picker.version_combo.setCurrentIndex(1)
            self.assertTrue(picker.yesButton.isEnabled())
            self.assertTrue(picker.validate())
            picker.import_button.click()
            return 1

        try:
            with (
                patch.object(WorkshopDialog, "reload_catalog"),
                patch.object(WorkshopDialog, "exec", new=select_history),
            ):
                dialog.fill_button.click()
            self.assertEqual(dialog.package(), replace(old.package, slots=()))
            self.assertTrue(dialog.yesButton.isEnabled())
            self.assertEqual(dialog.description_edit.toPlainText(), old.package.description)
            dialog.version_edit.setText("1.0.1")
            self.assertEqual(dialog.package().version, "1.0.1")
            self.assertEqual(imported, [])
            repository.download_archive.assert_not_called()
            self.assertEqual(self.manager.get_team_presets(), [])
            reopened = PackageMetadataDialog(defaults, parent, repository=repository)
            self.assertEqual(reopened.package(), replace(defaults, slots=()))
            reopened.deleteLater()
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_cancel_workshop_fill_preserves_edited_export_fields(self):
        defaults = TeamPackage("Draft", "Draft description", "Me", "2.0.0", ())
        parent = QWidget()
        dialog = PackageMetadataDialog(defaults, parent, repository=Mock())
        dialog.name_edit.setText("Edited draft")
        before = dialog.package()

        def cancel_selection(picker):
            entry = CatalogEntry(
                make_package(), "codes/test.zip", "test.zip", 100, "2026-01-01T00:00:00Z"
            )
            picker._catalog_loaded(([entry], IndexSource("GitHub", "index", "base")))
            return 0

        try:
            with (
                patch.object(WorkshopDialog, "reload_catalog"),
                patch.object(WorkshopDialog, "exec", new=cancel_selection),
            ):
                dialog.fill_button.click()
            self.assertEqual(dialog.package(), before)
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_workshop_fill_requires_selection_after_filter_or_load_failure(self):
        entry = CatalogEntry(
            make_package(), "codes/test.zip", "test.zip", 100, "2026-01-01T00:00:00Z"
        )
        parent = QWidget()
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(Mock(), parent, metadata_only=True)
        try:
            with patch("src.ui.features.characters.workshop_dialog.show_bar") as show_bar:
                dialog._catalog_failed(RuntimeError("offline"))
                self.assertIs(show_bar.call_args.args[0], dialog)
                self.assertEqual(show_bar.call_args.args[2], "offline")
                self.assertFalse(show_bar.call_args.kwargs["success"])
            self.assertNotIn("offline", dialog.status_label.text())
            self.assertFalse(dialog.yesButton.isEnabled())
            self.assertFalse(dialog.validate())
            dialog._catalog_loaded(([entry], IndexSource("GitHub", "index", "base")))
            self.assertTrue(dialog.yesButton.isEnabled())
            dialog.search_edit.setText("no matches")
            self.assertFalse(dialog.yesButton.isEnabled())
            self.assertFalse(dialog.validate())
            self.assertIsNone(dialog.selected_package())
            dialog.search_edit.clear()
            self.assertEqual(dialog.selected_package(), entry.package)
        finally:
            dialog.deleteLater()
            parent.deleteLater()

    def test_workshop_dialog_layout_stability_and_no_shadow(self):
        pkg1 = TeamPackage("A", "Desc 1", "Author A", "1.0.0", make_package(False).slots)
        pkg2 = TeamPackage(
            "B", "Multi\nLine\nDescription\nHere", "Author B", "2.0.0", make_package(False).slots
        )
        e1 = CatalogEntry(pkg1, "codes/a.zip", "a.zip", 100, "2026-01-01T00:00:00Z")
        e2 = CatalogEntry(pkg2, "codes/b.zip", "b.zip", 200, "2026-01-02T00:00:00Z")
        parent = QWidget()
        with patch.object(WorkshopDialog, "reload_catalog"):
            dialog = WorkshopDialog(Mock(), parent)
        try:
            self.assertIsNone(dialog.widget.graphicsEffect())
            self.assertEqual(
                dialog.table.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.assertTrue(dialog.table.horizontalHeader().stretchLastSection())

            dialog._catalog_loaded(([e1, e2], IndexSource("GitHub", "index", "base")))
            dialog.show()
            self.qt_app.processEvents()

            w_geom_0 = dialog.widget.geometry()
            t_geom_0 = dialog.table.geometry()
            dialog.table.selectRow(1)
            self.qt_app.processEvents()
            self.assertEqual(dialog.widget.geometry(), w_geom_0)
            self.assertEqual(dialog.table.geometry(), t_geom_0)
        finally:
            dialog.deleteLater()
            parent.deleteLater()


class TestWorkshopModels(unittest.TestCase):
    def test_manifest_rejects_oversized_fields_and_invalid_class_names(self):
        data = make_package().to_dict()
        data["name"] = "x" * 101
        with self.assertRaises(WorkshopFormatError):
            TeamPackage.from_dict(data)

    def test_external_directory_rejects_reserved_windows_names(self):
        with self.assertRaises(ValueError):
            CustomCharManager.validate_external_directory("CON")
        with self.assertRaises(ValueError):
            CustomCharManager.validate_external_directory("team. ")
        self.assertEqual(
            CustomCharManager.validate_external_directory("team_author_1.0.0"),
            "team_author_1.0.0",
        )

        data = make_package().to_dict()
        data["slots"][1]["class_name"] = "not a class"
        with self.assertRaises(WorkshopFormatError):
            TeamPackage.from_dict(data)

    def test_catalog_filter_searches_names_roles_and_authors(self):
        second = TeamPackage("Second", "strategy", "Other", "2.0.0", make_package(False).slots)
        entries = [
            CatalogEntry(
                make_package(False), "codes/first.zip", "first.zip", 1, "2026-01-01T00:00:00Z"
            ),
            CatalogEntry(second, "codes/second.zip", "second.zip", 1, "2026-02-01T00:00:00Z"),
        ]
        self.assertEqual(
            filter_catalog_entries(entries, keyword="strategy")[0].filename, "second.zip"
        )
        self.assertEqual(filter_catalog_entries(entries, role="Zero"), [entries[1], entries[0]])
        self.assertEqual(filter_catalog_entries(entries, author="作者"), [entries[0]])

    def test_catalog_lineup_keywords_match_substrings_across_roles_and_locales(self):
        base = make_package()
        slots = (
            replace(
                base.slots[0],
                display={"zh_CN": "残虹 (社区版)", "en_US": "Zankou (Community)"},
            ),
            replace(
                base.slots[1],
                display={"zh_CN": "早雾 (固定轴)", "en_US": "Sakiri (Fixed rotation)"},
            ),
        )
        full = CatalogEntry(
            replace(base, slots=slots), "codes/full.zip", "full.zip", 1, "2026-01-01Z"
        )
        single = replace(
            full,
            package=replace(full.package, name="Single", slots=slots[:1]),
            filename="single.zip",
            updated_at="2026-02-01Z",
        )
        entries = [full, single]
        for words, expected in (
            (("残虹",), [single, full]),
            (("残虹", "早雾"), [full]),
            (("zAnK", " sAKiRi "), [full]),
            (("社区版", "固定轴"), [full]),
            (("残虹", "不存在"), []),
            (("残虹 (社区版) 早雾",), []),
            (("", "  "), [single, full]),
        ):
            with self.subTest(words=words):
                self.assertEqual(filter_catalog_entries(entries, role_keywords=words), expected)
        self.assertEqual(full.package.slots[0].display["zh_CN"], "残虹 (社区版)")

    def test_catalog_lineup_keywords_do_not_match_other_package_fields(self):
        base = make_package(False)
        match = CatalogEntry(
            replace(
                base,
                slots=(replace(base.slots[0], display={"zh_CN": "残虹", "en_US": "Zankou"}),),
            ),
            "codes/match.zip",
            "match.zip",
            1,
            "2026-01-01Z",
        )
        decoy = replace(
            match,
            package=replace(base, name="残虹", description="残虹", author="残虹"),
            filename="decoy.zip",
        )
        self.assertEqual(filter_catalog_entries([match, decoy], role_keywords=("残虹",)), [match])
        self.assertEqual(
            filter_catalog_entries(
                [match, decoy], keyword="说明", author="作者", role_keywords=("残虹",)
            ),
            [match],
        )
        self.assertEqual(
            filter_catalog_entries([match, decoy], author="Other", role_keywords=("残虹",)),
            [],
        )
        self.assertEqual(
            filter_catalog_entries([match, decoy], keyword="missing", role_keywords=("残虹",)),
            [],
        )

    def test_version_format_and_numeric_order(self):
        self.assertGreater(parse_version("1.10.0"), parse_version("1.9.9"))
        for version in (
            "1",
            "1.0",
            "v1.0.0",
            "1.0.0-beta",
            "01.0.0",
            "1.00.0",
            "-1.0.0",
            "\uff11.0.0",
            "1.0.0.0",
            "1.0.0\n",
            "1" * 33 + ".0.0",
        ):
            with self.subTest(version=version), self.assertRaises(WorkshopFormatError):
                parse_version(version)
        for version in ("0.0.0", "1.9.0", "1.10.0"):
            data = {**make_package().to_dict(), "version": version}
            self.assertEqual(TeamPackage.from_dict(data).version, version)
        with self.assertRaises(WorkshopFormatError):
            TeamPackage.from_dict({**make_package().to_dict(), "version": "1.0"})

    def test_catalog_groups_by_author_and_name_not_members_or_commit_time(self):
        old = CatalogEntry(
            make_package(False), "codes/old.zip", "old.zip", 1, "2026-09-01T00:00:00Z"
        )
        latest = replace(
            old,
            package=replace(make_package(), version="1.10.0"),
            archive="codes/latest.zip",
            updated_at="2026-08-01T00:00:00Z",
        )
        intermediate = replace(old, package=replace(old.package, version="1.9.0"))
        other_author = replace(old, package=replace(old.package, author="Other"))
        other_name = replace(old, package=replace(old.package, name="Other strategy"))
        groups = group_catalog_entries([old, intermediate, latest, other_author, other_name])
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[old.package.identity], (latest, intermediate, old))

    def test_catalog_rejects_duplicate_named_version_even_with_different_slots(self):
        data = {
            **make_package().to_dict(),
            "archive": "codes/a.zip",
            "filename": "a.zip",
            "size": 1,
            "updated_at": "2026-09-01T00:00:00Z",
        }
        duplicate = {**data, "slots": make_package(False).to_dict()["slots"]}
        with self.assertRaises(WorkshopFormatError):
            parse_catalog({"format_version": 1, "packages": [data, duplicate]})


class TestWorkshopArchive(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp_dir = Path(directory.name)

    def test_invalid_archive_is_rejected_before_install(self):
        invalid = io.BytesIO()
        with zipfile.ZipFile(invalid, "w") as archive:
            archive.writestr("team.json", json.dumps(make_package().to_dict()))
            archive.writestr("folder/sample.py", SOURCE)
        with self.assertRaises(WorkshopFormatError):
            load_archive(invalid.getvalue())

    def test_archive_rejects_directory_entries_and_symbolic_links(self):
        archive_path = self.temp_dir / "invalid.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("folder/", "")
            archive.writestr("team.json", json.dumps(make_package().to_dict()))
            archive.writestr("sample.py", SOURCE)
        with self.assertRaises(WorkshopFormatError):
            load_archive(archive_path)

        link_path = self.temp_dir / "link.zip"
        link = zipfile.ZipInfo("sample.py")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(link_path, "w") as archive:
            archive.writestr("team.json", json.dumps(make_package().to_dict()))
            archive.writestr(link, "sample.py")
        with self.assertRaises(WorkshopFormatError):
            load_archive(link_path)

    def test_archive_syntax_check_does_not_execute_source(self):
        contents = ArchiveContents(
            make_package(), {"sample.py": "raise RuntimeError('must not execute')\n"}
        )
        archive_path = self.temp_dir / "safe_parse.zip"
        from src.char.workshop.archive import write_archive

        write_archive(archive_path, contents)
        self.assertEqual(load_archive(archive_path).sources, contents.sources)


class TestWorkshopRepository(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temp_dir = Path(directory.name)

    def test_repository_uses_chinese_source_then_falls_back(self):
        catalog = {
            "format_version": 1,
            "packages": [
                {
                    **make_package(False).to_dict(),
                    "archive": "codes/test.zip",
                    "filename": "test.zip",
                    "size": 1,
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        cnb_response = Mock()
        cnb_response.raise_for_status.side_effect = requests.RequestException("offline")
        github_response = Mock()
        github_response.raise_for_status.return_value = None
        github_response.headers = {}
        github_response.iter_content.return_value = [
            json.dumps(catalog, ensure_ascii=False).encode("utf-8")
        ]
        session = Mock()
        session.get.side_effect = [cnb_response, github_response]
        repository = WorkshopRepository(
            IndexSource("GitHub", "github-index", "github-base"),
            IndexSource("CNB", "cnb-index", "cnb-base"),
            session,
            self.temp_dir / "catalog.json",
        )
        entries, source = repository.fetch_catalog(True)
        self.assertEqual(source.name, "GitHub")
        self.assertEqual(entries[0].filename, "test.zip")
        self.assertEqual(
            [call.args[0] for call in session.get.call_args_list], ["cnb-index", "github-index"]
        )

    def test_repository_reuses_preferred_source_cache_until_manual_refresh(self):
        catalog = {
            "format_version": 1,
            "packages": [
                {
                    **make_package(False).to_dict(),
                    "archive": "codes/test.zip",
                    "filename": "test.zip",
                    "size": 1,
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {}
        response.iter_content.return_value = [json.dumps(catalog).encode("utf-8")]
        session = Mock()
        session.get.return_value = response
        cache_path = self.temp_dir / "catalog.json"
        repository = WorkshopRepository(
            IndexSource("GitHub", "github-index", "github-base"),
            IndexSource("CNB", "cnb-index", "cnb-base"),
            session,
            cache_path,
        )

        repository.fetch_catalog(True)
        repository.fetch_catalog(True)
        repository.fetch_catalog(True, force_refresh=True)

        self.assertEqual(session.get.call_count, 2)

    def test_repository_ignores_expired_cache(self):
        catalog = {
            "format_version": 1,
            "packages": [
                {
                    **make_package(False).to_dict(),
                    "archive": "codes/test.zip",
                    "filename": "test.zip",
                    "size": 1,
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {}
        response.iter_content.return_value = [json.dumps(catalog).encode("utf-8")]
        session = Mock()
        session.get.return_value = response
        cache_path = self.temp_dir / "catalog.json"
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        sources = (
            IndexSource("GitHub", "github-index", "github-base"),
            IndexSource("CNB", "cnb-index", "cnb-base"),
        )
        WorkshopRepository(*sources, session, cache_path, now=lambda _tz: timestamp).fetch_catalog(
            True
        )

        WorkshopRepository(
            *sources,
            session,
            cache_path,
            now=lambda _tz: timestamp + timedelta(hours=24, seconds=1),
        ).fetch_catalog(True)

        self.assertEqual(session.get.call_count, 2)

    def test_repository_rejects_oversized_archive_before_loading_it(self):
        entry = CatalogEntry(
            make_package(False), "codes/test.zip", "test.zip", 1, "2026-01-01T00:00:00Z"
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.headers = {"Content-Length": str(MAX_ARCHIVE_BYTES + 1)}
        session = Mock()
        session.get.return_value = response
        repository = WorkshopRepository(
            IndexSource("GitHub", "github-index", "github-base"),
            IndexSource("CNB", "cnb-index", "cnb-base"),
            session,
            self.temp_dir / "catalog.json",
        )

        with self.assertRaises(WorkshopRepositoryError):
            repository.download_archive(repository.github, entry)
        self.assertEqual(session.get.call_count, 2)
