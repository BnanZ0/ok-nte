"""Serializable models shared by workshop archives, catalogs, and UI code."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

PACKAGE_FORMAT_VERSION = 1
CATALOG_FORMAT_VERSION = 1
MAX_PACKAGE_NAME_LENGTH = 100
MAX_PACKAGE_DESCRIPTION_LENGTH = 2000
MAX_PACKAGE_AUTHOR_LENGTH = 64
MAX_PACKAGE_VERSION_LENGTH = 32
MAX_DISPLAY_NAME_LENGTH = 100


class WorkshopFormatError(ValueError):
    """Raised when workshop metadata cannot be trusted or understood."""


def parse_version(value: str) -> tuple[int, int, int]:
    """Validate a canonical major.minor.patch version and return its numeric order."""
    if len(value) > MAX_PACKAGE_VERSION_LENGTH or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise WorkshopFormatError("version must use major.minor.patch, for example 1.0.0")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _required_text(value, field: str, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkshopFormatError(f"{field} must be a non-empty string")
    text = value.strip()
    if max_length is not None and len(text) > max_length:
        raise WorkshopFormatError(f"{field} must not exceed {max_length} characters")
    return text


def _display(value, field: str, fallback: str) -> dict[str, str]:
    if value is None:
        return {"zh_CN": fallback, "en_US": fallback}
    if not isinstance(value, dict):
        raise WorkshopFormatError(f"{field} must be an object")
    zh_name = value.get("zh_CN") or value.get("en_US") or fallback
    en_name = value.get("en_US") or value.get("zh_CN") or fallback
    return {
        "zh_CN": _required_text(zh_name, f"{field}.zh_CN", MAX_DISPLAY_NAME_LENGTH),
        "en_US": _required_text(en_name, f"{field}.en_US", MAX_DISPLAY_NAME_LENGTH),
    }


@dataclass(frozen=True)
class PackageSlot:
    index: int
    kind: str
    display: dict[str, str]
    impl_id: str = ""
    file_name: str = ""
    class_name: str = ""

    def display_name(self, locale_name: str) -> str:
        return self.display["zh_CN"] if locale_name == "zh_CN" else self.display["en_US"]

    def to_dict(self) -> dict:
        data = {"index": self.index, "kind": self.kind, "display": self.display}
        if self.kind == "builtin":
            data["impl_id"] = self.impl_id
        else:
            data["file"] = self.file_name
            data["class_name"] = self.class_name
        return data

    @classmethod
    def from_dict(cls, data: object) -> "PackageSlot":
        if not isinstance(data, dict):
            raise WorkshopFormatError("each slot must be an object")
        index = data.get("index")
        if not isinstance(index, int) or not 0 <= index < 4:
            raise WorkshopFormatError("slot index must be an integer from 0 to 3")
        kind = data.get("kind")
        if kind == "builtin":
            impl_id = _required_text(data.get("impl_id"), "builtin impl_id")
            if not impl_id.startswith("builtin:"):
                raise WorkshopFormatError("builtin impl_id must start with 'builtin:'")
            display = _display(data.get("display"), "builtin display", impl_id[8:])
            return cls(index, kind, display, impl_id)
        if kind == "external":
            file_name = _required_text(data.get("file"), "external file").replace("\\", "/")
            if (
                "/" in file_name
                or file_name.startswith(".")
                or not file_name.lower().endswith(".py")
            ):
                raise WorkshopFormatError("external file must be a root-level .py file")
            class_name = _required_text(data.get("class_name"), "external class_name")
            if not class_name.isidentifier():
                raise WorkshopFormatError("external class_name must be a Python identifier")
            return cls(
                index,
                kind,
                _display(data.get("display"), "external display", class_name),
                file_name=file_name,
                class_name=class_name,
            )
        raise WorkshopFormatError("slot kind must be 'builtin' or 'external'")


@dataclass(frozen=True)
class TeamPackage:
    name: str
    description: str
    author: str
    version: str
    slots: tuple[PackageSlot, ...]

    @property
    def identity(self) -> tuple[str, str]:
        return self.author, self.name

    def to_dict(self) -> dict:
        return {
            "format_version": PACKAGE_FORMAT_VERSION,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "slots": [slot.to_dict() for slot in self.slots],
        }

    @classmethod
    def from_dict(cls, data: object) -> "TeamPackage":
        if not isinstance(data, dict):
            raise WorkshopFormatError("team.json must contain an object")
        if data.get("format_version") != PACKAGE_FORMAT_VERSION:
            raise WorkshopFormatError(f"format_version must be {PACKAGE_FORMAT_VERSION}")
        slots_raw = data.get("slots")
        if not isinstance(slots_raw, list) or not 1 <= len(slots_raw) <= 4:
            raise WorkshopFormatError("slots must contain between one and four entries")
        slots = tuple(
            sorted((PackageSlot.from_dict(slot) for slot in slots_raw), key=lambda slot: slot.index)
        )
        if len({slot.index for slot in slots}) != len(slots):
            raise WorkshopFormatError("slot indices must be unique")
        external_files = [slot.file_name for slot in slots if slot.kind == "external"]
        if len(external_files) != len(set(external_files)):
            raise WorkshopFormatError("each external file may be referenced by only one slot")
        description = data.get("description", "")
        if not isinstance(description, str):
            raise WorkshopFormatError("description must be a string")
        description = description.strip()
        if len(description) > MAX_PACKAGE_DESCRIPTION_LENGTH:
            raise WorkshopFormatError(
                f"description must not exceed {MAX_PACKAGE_DESCRIPTION_LENGTH} characters"
            )
        version = _required_text(data.get("version"), "version", MAX_PACKAGE_VERSION_LENGTH)
        parse_version(version)
        return cls(
            name=_required_text(data.get("name"), "name", MAX_PACKAGE_NAME_LENGTH),
            description=description,
            author=_required_text(data.get("author"), "author", MAX_PACKAGE_AUTHOR_LENGTH),
            version=version,
            slots=slots,
        )

    def members(self, locale_name: str) -> list[str]:
        return [slot.display_name(locale_name) for slot in self.slots]


@dataclass(frozen=True)
class CatalogEntry:
    package: TeamPackage
    archive: str
    filename: str
    size: int
    updated_at: str

    @classmethod
    def from_dict(cls, data: object) -> "CatalogEntry":
        if not isinstance(data, dict):
            raise WorkshopFormatError("catalog package entry must be an object")
        archive = _required_text(data.get("archive"), "archive").replace("\\", "/")
        if not archive.startswith("codes/") or ".." in archive.split("/"):
            raise WorkshopFormatError("archive must be a safe path under codes/")
        filename = _required_text(data.get("filename"), "filename")
        size = data.get("size")
        if not isinstance(size, int) or size < 0:
            raise WorkshopFormatError("size must be a non-negative integer")
        updated_at = _required_text(data.get("updated_at"), "updated_at")
        return cls(
            package=TeamPackage.from_dict(data),
            archive=archive,
            filename=filename,
            size=size,
            updated_at=updated_at,
        )


def parse_catalog(data: object) -> list[CatalogEntry]:
    if not isinstance(data, dict) or data.get("format_version") != CATALOG_FORMAT_VERSION:
        raise WorkshopFormatError(f"catalog format_version must be {CATALOG_FORMAT_VERSION}")
    packages = data.get("packages")
    if not isinstance(packages, list):
        raise WorkshopFormatError("catalog packages must be a list")
    entries = [CatalogEntry.from_dict(item) for item in packages]
    identities = {(entry.package.identity, entry.package.version) for entry in entries}
    if len(identities) != len(entries):
        raise WorkshopFormatError("duplicate author, package name, and version")
    return sort_catalog_entries(entries)


def group_catalog_entries(
    entries: Iterable[CatalogEntry],
) -> dict[tuple[str, str], tuple[CatalogEntry, ...]]:
    """Group a catalog by author and name, keeping every version in descending order."""
    groups: dict[tuple[str, str], list[CatalogEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.package.identity, []).append(entry)
    return {
        identity: tuple(
            sorted(versions, key=lambda e: parse_version(e.package.version), reverse=True)
        )
        for identity, versions in groups.items()
    }


def sort_catalog_entries(entries) -> list[CatalogEntry]:
    return sorted(entries, key=lambda entry: (entry.updated_at, entry.filename), reverse=True)


def filter_catalog_entries(
    entries,
    keyword: str = "",
    role: str = "",
    author: str = "",
    *,
    role_keywords: Iterable[str] = (),
) -> list[CatalogEntry]:
    keyword = str(keyword or "").strip().casefold()
    role = str(role or "").strip()
    author = str(author or "").strip()
    role_keywords = tuple(word.strip().casefold() for word in role_keywords if word.strip())
    filtered = []
    for entry in entries:
        package = entry.package
        names = [name for slot in package.slots for name in slot.display.values()]
        search_text = " ".join(
            (package.name, package.description, package.author, *names)
        ).casefold()
        if keyword and keyword not in search_text:
            continue
        if role and role not in names:
            continue
        if role_keywords:
            role_names = [name.casefold() for name in names]
            if not all(any(word in name for name in role_names) for word in role_keywords):
                continue
        if author and author != package.author:
            continue
        filtered.append(entry)
    return sort_catalog_entries(filtered)
