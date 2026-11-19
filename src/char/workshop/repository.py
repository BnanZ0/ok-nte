"""Remote static catalog client with bounded downloads and a local catalog cache."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
from ok import get_path_relative_to_exe

from src.char.workshop.archive import MAX_ARCHIVE_BYTES
from src.char.workshop.models import CatalogEntry, parse_catalog

MAX_CATALOG_BYTES = 5 * 1024 * 1024
CATALOG_CACHE_TTL = timedelta(hours=24)
CATALOG_CACHE_PATH = Path(get_path_relative_to_exe("configs", "workshop_catalog.json"))


class WorkshopRepositoryError(RuntimeError):
    """Raised when neither public workshop source can serve a valid catalog."""


@dataclass(frozen=True)
class IndexSource:
    name: str
    index_url: str
    archive_base_url: str


class WorkshopRepository:
    def __init__(
        self,
        github: IndexSource,
        cnb: IndexSource,
        session=requests,
        cache_path: Path = CATALOG_CACHE_PATH,
        now=datetime.now,
    ):
        self.github = github
        self.cnb = cnb
        self.session = session
        self.cache_path = Path(cache_path)
        self.now = now

    def ordered_sources(self, is_chinese: bool) -> tuple[IndexSource, IndexSource]:
        return (self.cnb, self.github) if is_chinese else (self.github, self.cnb)

    def fetch_catalog(
        self, is_chinese: bool, force_refresh: bool = False
    ) -> tuple[list[CatalogEntry], IndexSource]:
        sources = self.ordered_sources(is_chinese)
        if not force_refresh:
            cached = self._load_cached_catalog(sources[0])
            if cached is not None:
                return cached, sources[0]

        errors = []
        for source in sources:
            try:
                payload = self._read_json(source.index_url)
                catalog = parse_catalog(payload)
                self._save_cached_catalog(source, payload)
                return catalog, source
            except (
                requests.RequestException,
                ValueError,
                WorkshopRepositoryError,
            ) as error:
                errors.append(f"{source.name}: {error}")
        raise WorkshopRepositoryError("; ".join(errors) or "workshop catalog is unavailable")

    def download_archive(self, source: IndexSource, entry: CatalogEntry) -> bytes:
        errors = []
        fallback = self.github if source == self.cnb else self.cnb
        for candidate in (source, fallback):
            archive_url = (
                f"{candidate.archive_base_url.rstrip('/')}/{quote(entry.archive, safe='/')}"
            )
            try:
                return self._read_bytes(archive_url, timeout=30, max_bytes=MAX_ARCHIVE_BYTES)
            except (requests.RequestException, ValueError, WorkshopRepositoryError) as error:
                errors.append(f"{candidate.name}: {error}")
        raise WorkshopRepositoryError(f"could not download {entry.filename}: {'; '.join(errors)}")

    def _read_json(self, url: str) -> object:
        content = self._read_bytes(url, timeout=15, max_bytes=MAX_CATALOG_BYTES)
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkshopRepositoryError("workshop catalog is invalid") from error

    def _read_bytes(self, url: str, timeout: int, max_bytes: int) -> bytes:
        response = self.session.get(url, timeout=timeout, stream=True)
        try:
            response.raise_for_status()
            content_length = self._content_length(response)
            if content_length is not None and content_length > max_bytes:
                raise WorkshopRepositoryError("workshop response is too large")

            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise WorkshopRepositoryError("workshop response is too large")
            return bytes(content)
        finally:
            response.close()

    @staticmethod
    def _content_length(response) -> int | None:
        try:
            value = response.headers.get("Content-Length")
            return int(value) if value is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _load_cached_catalog(self, source: IndexSource) -> list[CatalogEntry] | None:
        try:
            cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            item = cache["sources"][source.name]
            fetched_at = datetime.fromisoformat(item["fetched_at"])
            if fetched_at.tzinfo is None:
                return None
            if self._utc_now() - fetched_at.astimezone(UTC) >= CATALOG_CACHE_TTL:
                return None
            return parse_catalog(item["catalog"])
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def _save_cached_catalog(self, source: IndexSource, catalog: object) -> None:
        try:
            cache = {"format_version": 1, "sources": {}}
            if self.cache_path.is_file():
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("sources"), dict):
                    cache = loaded
            cache["format_version"] = 1
            cache["sources"][source.name] = {
                "fetched_at": self._utc_now().isoformat(),
                "catalog": catalog,
            }
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.cache_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
            temporary_path.replace(self.cache_path)
        except (OSError, TypeError, ValueError):
            return

    def _utc_now(self) -> datetime:
        value = self.now(UTC)
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
