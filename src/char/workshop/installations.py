"""Local workshop origins and stable source-file mappings."""

import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from src.char.custom.CustomCharManager import CustomCharManager
from src.char.workshop.models import TeamPackage, parse_version

INSTALLATIONS_LOCK = RLock()


@dataclass(frozen=True)
class WorkshopInstallation:
    author: str
    name: str
    version: str
    directory: str
    files: dict[str, str]
    hashes: dict[str, str]

    @property
    def identity(self) -> tuple[str, str]:
        return self.author, self.name

    def has_update(self, package: TeamPackage) -> bool:
        return self.identity == package.identity and parse_version(package.version) > parse_version(
            self.version
        )

    def can_update(self, package: TeamPackage) -> bool:
        return self.has_update(package) and set(self.files) == {
            slot.file_name for slot in package.slots if slot.kind == "external"
        }

    def to_dict(self) -> dict:
        return {
            "author": self.author,
            "name": self.name,
            "version": self.version,
            "directory": self.directory,
            "files": dict(self.files),
            "hashes": dict(self.hashes),
        }

    @classmethod
    def from_dict(cls, data: object) -> "WorkshopInstallation":
        if not isinstance(data, dict):
            raise ValueError("invalid workshop installation")
        for key in ("author", "name", "version", "directory"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError("invalid workshop installation metadata")
        parse_version(data["version"])
        CustomCharManager.validate_external_directory(data["directory"])
        files = data.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError("invalid workshop file mapping")
        for remote, local in files.items():
            for filename in (remote, local):
                if (
                    not isinstance(filename, str)
                    or not filename
                    or filename.startswith((".", "_"))
                    or any(char in filename for char in '/\\:<>"|?*')
                    or not filename.lower().endswith(".py")
                    or Path(filename).name != filename
                ):
                    raise ValueError("invalid workshop file mapping")
        if len({name.casefold() for name in files.values()}) != len(files):
            raise ValueError("duplicate workshop local file")
        hashes = data.get("hashes", {})
        if not isinstance(hashes, dict) or any(
            not isinstance(value, str) for value in hashes.values()
        ):
            raise ValueError("invalid workshop source hashes")
        return cls(
            data["author"],
            data["name"],
            data["version"],
            data["directory"],
            dict(files),
            dict(hashes),
        )


class WorkshopInstallationStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[WorkshopInstallation]:
        with INSTALLATIONS_LOCK:
            if not self.path.exists():
                return []
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("format_version") != 1:
                raise ValueError("invalid workshop installation records")
            if not isinstance(data.get("installs"), list):
                raise ValueError("invalid workshop installation records")
            installs = [WorkshopInstallation.from_dict(item) for item in data["installs"]]
            if len({item.directory.casefold() for item in installs}) != len(installs):
                raise ValueError("duplicate workshop installation directory")
            return installs

    def save_installation(self, installation: WorkshopInstallation) -> None:
        with INSTALLATIONS_LOCK:
            installs = self.load()
            installs = [
                item
                for item in installs
                if item.directory.casefold() != installation.directory.casefold()
            ]
            installs.append(installation)
            self.save_installations(installs)

    def save_installations(self, installs: list[WorkshopInstallation]) -> None:
        with INSTALLATIONS_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            try:
                temporary.write_text(
                    json.dumps(
                        {"format_version": 1, "installs": [item.to_dict() for item in installs]},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
