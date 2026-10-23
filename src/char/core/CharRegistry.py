import re
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module, invalidate_caches
from importlib.machinery import ModuleSpec
from importlib.util import cache_from_source, module_from_spec, spec_from_file_location
from pathlib import Path, PureWindowsPath
from sys import modules
from threading import RLock
from traceback import StackSummary, walk_tb

from ok import Logger, og

from src.char.BaseChar import BaseChar, Element

logger = Logger.get_logger(__name__)


@dataclass(frozen=True)
class CharImplementation:
    impl_id: str
    source: str
    char_cls: type[BaseChar]
    en_name: str
    cn_name: str
    element: Element
    external_folder_name: str = ""

    def display_name(self, locale_name: str = "") -> str:
        char_name = self.cn_name if locale_name == "zh_CN" else self.en_name
        return (
            f"{self.external_folder_name} - {char_name}" if self.external_folder_name else char_name
        )


class CharRegistry:
    """Discover built-in and external character implementations without a manual mapping."""

    def __init__(self, external_dir: Path | None = None):
        self._lock = RLock()
        self._entries: dict[str, CharImplementation] = {}
        self._builtin_scanned = False
        self._external_scanned = False
        self._external_dir = external_dir
        self._external_packages: dict[str, Path] = {}
        self._external_errors: dict[str, str] = {}

    @staticmethod
    def tr(message: str) -> str:
        app = getattr(og, "app", None)
        return app.tr(message) if app is not None else message

    def get_external_errors(self) -> list[str]:
        """Read cached diagnostics without scanning or executing code again."""
        with self._lock:
            return list(self._external_errors.values())

    def describe_external_error(
        self,
        error: Exception,
        path: Path | None = None,
        *,
        character: str = "",
        combo: str = "",
    ) -> str | None:
        """Describe the current error at its last external frame without loading source text."""
        root = self._get_external_dir().resolve()
        stack = StackSummary.extract(walk_tb(error.__traceback__), lookup_lines=False)
        frames = [(frame.filename, frame.lineno) for frame in reversed(stack)]
        if isinstance(error, SyntaxError) and error.filename:
            frames.insert(0, (error.filename, error.lineno))
        for filename, lineno in frames:
            filename = Path(filename).resolve()
            if filename.is_relative_to(root):
                location = f"{filename.relative_to(root).as_posix()}:{lineno}"
                break
        else:
            if path is None:
                return None
            location = path.relative_to(root).as_posix()

        # Exception messages can contain absolute filenames even without a traceback.
        detail = error.msg if isinstance(error, SyntaxError) else str(error)
        detail = re.sub(
            r"[\"'](?:[A-Za-z]:[\\/]|/|\\{2,})[^\"'\n]*[\"']"
            r"|(?:[A-Za-z]:[\\/]|\\{2,})[^\s\"'()]*"
            r"|(?<![\w:])/(?:[^\s\"'()]+/)*[^\s\"'()]*",
            lambda match: f"<{PureWindowsPath(match.group().strip(chr(34) + chr(39))).name}>",
            detail,
        )
        return self.tr(
            "角色: {character}\n"
            "出招表: {combo}\n"
            "出错位置: {location}\n"
            "{error_type}: {detail}\n"
            "可能与接口或依赖不兼容, 请至工坊更新方案或将此信息反馈给方案作者."
        ).format(
            character=character or "-",
            combo=combo or "-",
            location=location,
            error_type=type(error).__name__,
            detail=detail,
        )

    def _record_external_error(self, path: Path, error: Exception) -> None:
        message = self.describe_external_error(error, path=path.resolve())
        self._external_errors[path.relative_to(self._get_external_dir()).as_posix()] = message
        logger.warning(str(message))

    @staticmethod
    def _builtin_dir() -> Path:
        return Path(__file__).resolve().parent.parent

    def get(self, impl_id: str) -> CharImplementation | None:
        self.ensure_scanned()
        with self._lock:
            return self._entries.get(str(impl_id or ""))

    def get_all(self) -> list[CharImplementation]:
        self.ensure_scanned()
        with self._lock:
            return sorted(self._entries.values(), key=lambda entry: entry.impl_id)

    def rescan_external(self) -> None:
        """Rediscover external character modules without reloading built-ins."""
        with self._lock:
            self._entries = {
                impl_id: entry
                for impl_id, entry in self._entries.items()
                if entry.source != "external"
            }
            self._scan_external()

    def ensure_scanned(self) -> None:
        if self._builtin_scanned and self._external_scanned:
            return
        with self._lock:
            if not self._builtin_scanned:
                for path in sorted(self._builtin_dir().glob("*.py")):
                    self._register_builtin_module(path)
                self._builtin_scanned = True
            if not self._external_scanned:
                self._scan_external()

    def _scan_external(self) -> None:
        self._external_errors.clear()
        for name in tuple(modules):
            if name.partition(".")[0] in self._external_packages:
                modules.pop(name, None)
        self._external_packages.clear()
        invalidate_caches()
        try:
            external_paths = self._get_external_paths()
            # Clear source caches before any import, including later method-local imports.
            for source in self._get_external_dir().rglob("*.py"):
                Path(cache_from_source(str(source))).unlink(missing_ok=True)
        except OSError as error:
            logger.warning(f"Failed to scan external character modules: {error.__class__.__name__}")
            self._record_external_error(self._get_external_dir(), error)
            external_paths = []
        for path in external_paths:
            package_name = self._external_package_name(path.parent)
            if package_name not in self._external_packages:
                spec = ModuleSpec(package_name, loader=None, is_package=True)
                spec.submodule_search_locations = [str(path.parent.resolve())]
                modules[package_name] = module_from_spec(spec)
                self._external_packages[package_name] = path.parent.resolve()
        for path in external_paths:
            self._register_external_module(path)
        self._external_scanned = True

    @staticmethod
    def _external_package_name(directory: Path) -> str:
        suffix = sha256(str(directory.resolve()).casefold().encode("utf-8")).hexdigest()[:16]
        return f"ok_nte_external_{suffix}"

    @classmethod
    def _external_module_name(cls, path: Path) -> str:
        stem = (
            path.stem if path.stem.isidentifier() else sha256(path.name.encode("utf-8")).hexdigest()
        )
        return f"{cls._external_package_name(path.parent)}.{stem}"

    def _get_external_paths(self) -> list[Path]:
        external_dir = self._get_external_dir()
        if not external_dir.is_dir():
            return []
        paths = list(external_dir.glob("*.py"))
        for directory in external_dir.iterdir():
            if directory.is_dir() and not directory.name.startswith("_"):
                paths.extend(directory.glob("*.py"))
        return sorted(paths, key=lambda path: path.relative_to(external_dir).as_posix().lower())

    def _get_external_dir(self) -> Path:
        if self._external_dir is not None:
            return self._external_dir
        from src.char.custom.CustomCharManager import EXTERNAL_CHARS_DIR

        return Path(EXTERNAL_CHARS_DIR)

    def get_external_impl_ids_by_class_name(self, class_name: str) -> list[str]:
        """Return every external implementation declared with this class name."""
        class_name = str(class_name or "").lower()
        self.ensure_scanned()
        with self._lock:
            return [
                entry.impl_id
                for entry in self._entries.values()
                if entry.source == "external" and entry.char_cls.__name__.lower() == class_name
            ]

    def _register_builtin_module(self, path: Path) -> None:
        if path.stem in {"BaseChar", "Support", "__init__"}:
            return
        try:
            module = import_module(f"src.char.{path.stem}")
        except Exception as error:
            logger.warning(f"Failed to import built-in character module {path.name}: {error}")
            return
        candidates = [
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, BaseChar)
            and value is not BaseChar
            and value.__module__ == module.__name__
            and (value.__dict__.get("en_name") or value.__dict__.get("cn_name"))
        ]
        if len(candidates) != 1:
            return
        char_cls = candidates[0]
        impl_id = f"builtin:{path.stem.lower()}"
        self._entries[impl_id] = CharImplementation(
            impl_id=impl_id,
            source="builtin",
            char_cls=char_cls,
            en_name=char_cls.en_name,
            cn_name=char_cls.cn_name,
            element=char_cls.element,
        )

    def _register_external_module(self, path: Path) -> None:
        if path.stem.startswith("_"):
            return
        external_dir = self._get_external_dir()
        relative_path = path.relative_to(external_dir)
        relative_stem = relative_path.with_suffix("").as_posix()
        module_name = self._external_module_name(path)
        try:
            if path.stem.isidentifier():
                module = import_module(module_name)
            else:
                # Keep existing character filenames that cannot be imported as identifiers.
                spec = spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError(self.tr("找不到模块加载器"))
                module = module_from_spec(spec)
                modules[module_name] = module
                spec.loader.exec_module(module)
        except Exception as error:
            modules.pop(module_name, None)
            self._record_external_error(path, error)
            return

        candidates = [
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, BaseChar)
            and value is not BaseChar
            and value.__module__ == module.__name__
        ]
        if len(candidates) != 1:
            self._record_external_error(
                path,
                ValueError(self.tr("外置角色模块必须且只能定义一个 BaseChar 子类")),
            )
            return

        char_cls = candidates[0]
        impl_id = f"external:{relative_stem.lower()}"
        if impl_id in self._entries:
            self._record_external_error(
                path, ValueError(self.tr("角色实现标识重复: {impl_id}").format(impl_id=impl_id))
            )
            return
        self._entries[impl_id] = CharImplementation(
            impl_id=impl_id,
            source="external",
            char_cls=char_cls,
            en_name=char_cls.en_name,
            cn_name=char_cls.cn_name,
            element=char_cls.element,
            external_folder_name=relative_path.parent.name
            if relative_path.parent != Path(".")
            else "",
        )


char_registry = CharRegistry()
