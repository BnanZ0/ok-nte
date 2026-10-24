"""Application service for exporting and transactionally installing team packages."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from src.char.core.CharRegistry import char_registry
from src.char.custom.CustomCharManager import CustomCharManager
from src.char.workshop.archive import (
    ArchiveContents,
    load_archive,
    write_archive,
)
from src.char.workshop.installations import (
    INSTALLATIONS_LOCK,
    WorkshopInstallation,
    WorkshopInstallationStore,
)
from src.char.workshop.models import CatalogEntry, PackageSlot, TeamPackage


class WorkshopInstallErrorCode(StrEnum):
    INVALID_EXTERNAL_DIRECTORY = "invalid_external_directory"
    EXTERNAL_DIRECTORY_EXISTS = "external_directory_exists"
    FILES_CHANGED = "files_changed"
    LOCAL_SOURCE_CHANGED = "local_source_changed"
    INSTALLATION_MISSING = "installation_missing"
    INVALID_INSTALLATIONS = "invalid_installations"
    PACKAGE_MISMATCH = "package_mismatch"


class WorkshopInstallError(RuntimeError):
    """Raised when a package cannot be safely installed into local character data."""

    def __init__(
        self,
        message: str,
        code: WorkshopInstallErrorCode | None = None,
        **details: str,
    ):
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class ImportedTeamPackage:
    preset_id: str
    preset_name: str
    directory: str
    package: TeamPackage


class WorkshopPackageService:
    def __init__(
        self,
        manager: CustomCharManager | None = None,
        registry=char_registry,
        installation_store: WorkshopInstallationStore | None = None,
    ):
        from src.char.custom.CustomCharManager import EXTERNAL_CHARS_DIR

        self.manager = manager or CustomCharManager()
        self.registry = registry
        self.external_dir = Path(EXTERNAL_CHARS_DIR)
        self.installation_store = installation_store or WorkshopInstallationStore(
            self.external_dir / "workshop_installs.json"
        )

    def get_installations(self) -> list[WorkshopInstallation]:
        with INSTALLATIONS_LOCK:
            try:
                installs = self.installation_store.load()
            except (OSError, ValueError) as error:
                raise WorkshopInstallError(
                    "Could not read workshop installation records",
                    WorkshopInstallErrorCode.INVALID_INSTALLATIONS,
                ) from error
            current = [item for item in installs if self._installation_exists(item)]
            if current != installs:
                self.installation_store.save_installations(current)
            return current

    def _installation_exists(self, installation: WorkshopInstallation) -> bool:
        root = self.external_dir.resolve()
        target = root / installation.directory
        try:
            if not S_ISDIR(target.stat().st_mode):
                return False
            if target.is_symlink():
                raise ValueError("External character directory cannot be a symbolic link")
            for local in installation.files.values():
                path = target / local
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("External character source path is unsafe")
                if not S_ISREG(path.stat().st_mode):
                    return False
        except (FileNotFoundError, NotADirectoryError):
            return False
        return True

    @staticmethod
    def _source_hash(source: str) -> str:
        return sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()

    def _installation(
        self, contents: ArchiveContents, directory: str, files: dict[str, str]
    ) -> WorkshopInstallation:
        return WorkshopInstallation.from_dict(
            {
                "author": contents.package.author,
                "name": contents.package.name,
                "version": contents.package.version,
                "directory": directory,
                "files": files,
                "hashes": {
                    name: self._source_hash(code) for name, code in contents.sources.items()
                },
            }
        )

    def _mapped_slots(
        self, package: TeamPackage, directory: str, files: dict[str, str]
    ) -> list[dict]:
        slots = [{"char_id": "", "impl_id": ""} for _ in range(4)]
        for slot in package.slots:
            impl_id = slot.impl_id
            if slot.kind == "external":
                stem = Path(files[slot.file_name]).stem.lower()
                impl_id = f"external:{directory.lower()}/{stem}"
                entry = self.registry.get(impl_id)
                if (
                    entry is None
                    or entry.source != "external"
                    or entry.char_cls.__name__ != slot.class_name
                ):
                    raise WorkshopInstallError(
                        f"External implementation could not be loaded: {slot.file_name}"
                    )
            slots[slot.index] = {"char_id": "", "impl_id": impl_id}
        return slots

    def update_contents(
        self,
        contents: ArchiveContents,
        directory: str,
        before_replace: Callable[[], None] | None = None,
    ) -> WorkshopInstallation:
        with INSTALLATIONS_LOCK:
            installation = next(
                (item for item in self.get_installations() if item.directory == directory), None
            )
            if installation is None:
                raise WorkshopInstallError(
                    "Workshop installation was not found",
                    WorkshopInstallErrorCode.INSTALLATION_MISSING,
                )
            if not installation.has_update(contents.package):
                raise WorkshopInstallError(
                    "Package identity or version does not match the update",
                    WorkshopInstallErrorCode.PACKAGE_MISMATCH,
                )
            if set(contents.sources) != set(installation.files):
                raise WorkshopInstallError(
                    "Python file names changed; import the package again",
                    WorkshopInstallErrorCode.FILES_CHANGED,
                )
            declared_files = {
                slot.file_name for slot in contents.package.slots if slot.kind == "external"
            }
            if declared_files != set(contents.sources):
                raise WorkshopInstallError("Package sources do not match its manifest")
            root = self.external_dir.resolve()
            target = root / directory
            previous = {}
            replacements = {}
            for remote, local in installation.files.items():
                path = target / local
                if (
                    target.is_symlink()
                    or path.is_symlink()
                    or not path.is_file()
                    or not path.resolve().is_relative_to(root)
                ):
                    self.get_installations()
                    raise WorkshopInstallError(
                        "Installed source was not found",
                        WorkshopInstallErrorCode.INSTALLATION_MISSING,
                    )
                try:
                    previous[path] = path.read_bytes()
                except (FileNotFoundError, NotADirectoryError) as error:
                    self.get_installations()
                    raise WorkshopInstallError(
                        "Installed source was not found",
                        WorkshopInstallErrorCode.INSTALLATION_MISSING,
                    ) from error
                replacements[path] = contents.sources[remote]
                compile(replacements[path], local, "exec")
            updated = self._installation(contents, directory, installation.files)
            if before_replace:
                before_replace()
            if not self._installation_exists(installation):
                self.get_installations()
                raise WorkshopInstallError(
                    "Installed source was not found",
                    WorkshopInstallErrorCode.INSTALLATION_MISSING,
                )
            try:
                for path, code in replacements.items():
                    path.write_text(code, encoding="utf-8", newline="")
                self.registry.rescan_external()
                self._mapped_slots(contents.package, directory, installation.files)
                self.installation_store.save_installation(updated)
            except Exception as error:
                restore_errors = []
                for path, code in previous.items():
                    try:
                        path.write_bytes(code)
                    except OSError:
                        restore_errors.append(path.name)
                        try:
                            path.with_suffix(".workshop-backup").write_bytes(code)
                        except OSError:
                            restore_errors.append(f"{path.stem}.workshop-backup")
                self.registry.rescan_external()
                if restore_errors:
                    raise WorkshopInstallError(
                        "Could not restore sources or save .workshop-backup files: "
                        + ", ".join(restore_errors)
                    ) from error
                raise
            return updated

    def update_from_catalog(
        self,
        entries: list[CatalogEntry],
        repository,
        source,
        before_replace: Callable[[], None],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> tuple[list[WorkshopInstallation], list[tuple[str, Exception]]]:
        """Update each installation independently, continuing after a failed package."""
        updated = []
        failures = []
        installations = self.get_installations()
        for entry in entries:
            if cancelled():
                break
            targets = [item for item in installations if item.can_update(entry.package)]
            if not targets:
                continue
            try:
                contents = self.load_archive(repository.download_archive(source, entry))
                if contents.package.to_dict() != entry.package.to_dict():
                    raise WorkshopInstallError(
                        "Downloaded archive does not match the catalog",
                        WorkshopInstallErrorCode.PACKAGE_MISMATCH,
                    )
            except Exception as error:
                failures.append((entry.package.name, error))
                continue
            for target in targets:
                if cancelled():
                    break
                try:
                    updated.append(self.update_contents(contents, target.directory, before_replace))
                except Exception as error:
                    failures.append((target.directory, error))
        return updated, failures

    def export_preset(self, preset_id: str, package: TeamPackage, path: Path) -> None:
        preset = next(
            (item for item in self.manager.get_team_presets() if item["id"] == preset_id), None
        )
        if preset is None:
            raise WorkshopInstallError("team preset was not found")
        slots: list[PackageSlot] = []
        sources: dict[str, str] = {}
        used_names: set[str] = set()
        for index, raw_slot in enumerate(preset["slots"]):
            impl_id = str(raw_slot.get("impl_id", "")).strip()
            if not impl_id:
                continue
            entry = self.registry.get(impl_id)
            if entry is None:
                raise WorkshopInstallError(f"unknown implementation in slot {index + 1}: {impl_id}")
            display = {"zh_CN": entry.cn_name, "en_US": entry.en_name}
            if entry.source == "builtin":
                slots.append(PackageSlot(index, "builtin", display, impl_id=impl_id))
                continue
            if entry.source != "external":
                raise WorkshopInstallError(
                    f"unsupported implementation in slot {index + 1}: {impl_id}"
                )
            source = self.manager.get_external_impl_source(impl_id)
            if not source:
                raise WorkshopInstallError(f"could not read external source in slot {index + 1}")
            base_name = Path(impl_id.removeprefix("external:")).name
            file_name = f"{base_name}.py"
            if file_name in used_names:
                file_name = f"slot_{index + 1}_{file_name}"
            used_names.add(file_name)
            slots.append(
                PackageSlot(
                    index,
                    "external",
                    display,
                    file_name=file_name,
                    class_name=entry.char_cls.__name__,
                )
            )
            sources[file_name] = source
        if not slots:
            raise WorkshopInstallError("team preset has no exportable implementations")
        exported_package = TeamPackage(
            package.name,
            package.description,
            package.author,
            package.version,
            tuple(slots),
        )
        write_archive(path, ArchiveContents(exported_package, sources))

    def load_archive(self, source: Path | bytes) -> ArchiveContents:
        return load_archive(source)

    def install_contents(
        self,
        contents: ArchiveContents,
        preset_name: str,
        directory: str,
    ) -> ImportedTeamPackage:
        with INSTALLATIONS_LOCK:
            return self._install_contents(contents, preset_name, directory)

    def _install_contents(
        self,
        contents: ArchiveContents,
        preset_name: str,
        directory: str,
    ) -> ImportedTeamPackage:
        try:
            directory = self.manager.validate_external_directory(directory)
        except ValueError as error:
            raise WorkshopInstallError(
                str(error),
                WorkshopInstallErrorCode.INVALID_EXTERNAL_DIRECTORY,
                directory=str(directory or ""),
            ) from error
        preset_name = str(preset_name or "").strip()
        if not preset_name:
            raise WorkshopInstallError("team preset name is required")
        for slot in contents.package.slots:
            if slot.kind == "builtin" and not self.manager.is_builtin_impl(slot.impl_id):
                raise WorkshopInstallError(f"unknown builtin implementation: {slot.impl_id}")

        created_preset_id = ""
        installed_sources = False
        installation = None
        if contents.sources:
            self.get_installations()
            installation = self._installation(
                contents, directory, {name: name for name in contents.sources}
            )
        try:
            if contents.sources:
                if self.manager.external_directory_exists(directory):
                    raise WorkshopInstallError(
                        "External character directory already exists",
                        WorkshopInstallErrorCode.EXTERNAL_DIRECTORY_EXISTS,
                        directory=directory,
                    )
                installed, error = self.manager.install_external_sources(
                    directory,
                    {name: code.replace("\r\n", "\n") for name, code in contents.sources.items()},
                )
                if not installed:
                    if self.manager.external_directory_exists(directory):
                        raise WorkshopInstallError(
                            "External character directory already exists",
                            WorkshopInstallErrorCode.EXTERNAL_DIRECTORY_EXISTS,
                            directory=directory,
                        )
                    raise WorkshopInstallError(error)
                installed_sources = True

            mapped_slots = self._mapped_slots(
                contents.package, directory, {name: name for name in contents.sources}
            )

            preset = self.manager.create_team_preset(preset_name)
            created_preset_id = preset["id"]
            if not self.manager.update_team_preset(created_preset_id, slots=mapped_slots):
                raise WorkshopInstallError("could not save imported team preset")
            saved_preset = next(
                item for item in self.manager.get_team_presets() if item["id"] == created_preset_id
            )
            if installation is not None:
                self.installation_store.save_installation(installation)
            return ImportedTeamPackage(
                created_preset_id,
                saved_preset["name"],
                directory,
                contents.package,
            )
        except Exception:
            if created_preset_id:
                self.manager.delete_team_preset(created_preset_id)
            if installed_sources:
                self.manager.remove_external_sources(directory)
            else:
                self.registry.rescan_external()
            raise

    def import_archive(
        self,
        source: Path | bytes,
        preset_name: str,
        directory: str,
    ) -> ImportedTeamPackage:
        return self.install_contents(self.load_archive(source), preset_name, directory)
