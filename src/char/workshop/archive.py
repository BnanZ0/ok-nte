"""Read and write the deliberately small community ZIP format."""

import ast
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from src.char.workshop.models import PackageSlot, TeamPackage, WorkshopFormatError

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARCHIVE_FILES = 5
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024
_ARCHIVE_TOO_LARGE = "archive is too large"
_MANIFEST_NAME = "team.json"


@dataclass(frozen=True)
class ArchiveContents:
    package: TeamPackage
    sources: dict[str, str]


def default_archive_name(package: TeamPackage) -> str:
    members = "_".join(slot.display["en_US"] for slot in package.slots)
    raw_name = f"{members}_{package.author}_{package.version}"
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", raw_name).strip(" ._")
    return f"{safe_name or 'ok-nte-team'}.zip"


def _read_zip(source: Path | bytes) -> zipfile.ZipFile:
    if isinstance(source, Path):
        if not source.is_file():
            raise WorkshopFormatError("archive file does not exist")
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise WorkshopFormatError(_ARCHIVE_TOO_LARGE)
        return zipfile.ZipFile(source)
    if len(source) > MAX_ARCHIVE_BYTES:
        raise WorkshopFormatError(_ARCHIVE_TOO_LARGE)
    return zipfile.ZipFile(io.BytesIO(source))


def _validate_archive_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    files: dict[str, zipfile.ZipInfo] = {}
    uncompressed_size = 0
    for info in archive.infolist():
        if info.is_dir():
            raise WorkshopFormatError("archive must not contain directory entries")
        if info.flag_bits & 0x1:
            raise WorkshopFormatError("archive must not contain encrypted files")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise WorkshopFormatError("archive must not contain symbolic links")
        name = info.filename
        if (
            not name
            or name.startswith(("/", "\\", "."))
            or "\\" in name
            or "/" in name
            or name in files
        ):
            raise WorkshopFormatError("archive files must be unique root-level files")
        if info.file_size > MAX_SOURCE_BYTES:
            raise WorkshopFormatError(f"archive file is too large: {name}")
        uncompressed_size += info.file_size
        if uncompressed_size > MAX_UNCOMPRESSED_BYTES:
            raise WorkshopFormatError("archive contents are too large")
        files[name] = info
    if len(files) > MAX_ARCHIVE_FILES:
        raise WorkshopFormatError("archive contains too many files")
    return files


def load_archive(source: Path | bytes) -> ArchiveContents:
    """Validate an archive before any external source is installed or imported."""
    try:
        archive = _read_zip(source)
    except (OSError, zipfile.BadZipFile) as error:
        raise WorkshopFormatError(f"cannot open community archive: {error}") from error

    with archive:
        files = _validate_archive_entries(archive)
        if _MANIFEST_NAME not in files:
            raise WorkshopFormatError("archive must contain team.json")
        manifest_info = files[_MANIFEST_NAME]
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise WorkshopFormatError("team.json is too large")
        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkshopFormatError(f"team.json is invalid: {error}") from error
        package = TeamPackage.from_dict(manifest)
        source_names = {slot.file_name for slot in package.slots if slot.kind == "external"}
        if set(files) != {_MANIFEST_NAME, *source_names}:
            raise WorkshopFormatError(
                "archive must contain only team.json and declared Python files"
            )
        sources = {}
        for name in source_names:
            try:
                code = archive.read(files[name]).decode("utf-8")
                ast.parse(code, filename=name)
            except UnicodeDecodeError as error:
                raise WorkshopFormatError(f"{name} must be UTF-8") from error
            except SyntaxError as error:
                raise WorkshopFormatError(
                    f"{name} has invalid Python syntax: {error.msg}"
                ) from error
            sources[name] = code
    return ArchiveContents(package, sources)


def write_archive(path: Path, contents: ArchiveContents) -> None:
    """Write a package after applying the same validation used by import."""
    package = TeamPackage.from_dict(contents.package.to_dict())
    source_names = {slot.file_name for slot in package.slots if slot.kind == "external"}
    if set(contents.sources) != source_names:
        raise WorkshopFormatError("archive sources do not match package slots")
    for name, source in contents.sources.items():
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise WorkshopFormatError(f"source is too large: {name}")
        ast.parse(source, filename=name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            _MANIFEST_NAME, json.dumps(package.to_dict(), ensure_ascii=False, indent=2)
        )
        for name in sorted(contents.sources):
            archive.writestr(name, contents.sources[name])
    archive_bytes = buffer.getvalue()
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise WorkshopFormatError(_ARCHIVE_TOO_LARGE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(archive_bytes)


def remap_external_slot(slot: PackageSlot, directory: str) -> str:
    stem = Path(slot.file_name).stem.lower()
    return f"external:{directory.lower()}/{stem}"
