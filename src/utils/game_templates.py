from collections.abc import Callable, Hashable
from pathlib import Path
from threading import Lock
from typing import cast, overload
from weakref import WeakKeyDictionary

import cv2
import numpy as np
from ok import Logger, get_path_relative_to_exe
from ok.feature.Feature import Feature

logger = Logger.get_logger(__name__)
_MIN_OPAQUE_ALPHA = 250

type MaskedTemplate = tuple[np.ndarray, np.ndarray | None]


class FeatureTemplateCache[T]:
    """Share derived templates for as long as their source feature exists."""

    def __init__(self):
        self._values: WeakKeyDictionary[Feature, dict[Hashable, T]] = WeakKeyDictionary()
        self._lock = Lock()

    def get_or_build(
        self,
        feature: Feature,
        variant: Hashable,
        build: Callable[[np.ndarray], T | None],
    ) -> T | None:
        with self._lock:
            variants = self._values.setdefault(feature, {})
            result = variants.get(variant)
            if result is None:
                result = build(feature.mat)
                if result is not None:
                    variants[variant] = result
            return result


class GameTemplateSet:
    """Share source PNGs and the current size of each template in one asset directory."""

    def __init__(
        self,
        directory: Path,
        *,
        reference_scale: float,
        reference_height: int | None = None,
        interpolation: int = cv2.INTER_AREA,
    ):
        self._directory = directory
        self._reference_scale = reference_scale
        self._reference_height = reference_height
        self._interpolation = interpolation
        self._paths: dict[str, Path] | None = None
        self._originals: dict[str, np.ndarray] = {}
        self._templates: dict[tuple[str, Callable | None], object] = {}
        self._height: int | None = None
        self._lock = Lock()

    def list_names(self) -> tuple[str, ...]:
        """List PNG names in filename order without decoding the images."""
        with self._lock:
            return tuple(path.stem for path in self._catalog().values())

    @overload
    def get_template(self, name: str, height: int) -> MaskedTemplate | None: ...

    @overload
    def get_template[T](
        self,
        name: str,
        height: int,
        *,
        prepare: Callable[[np.ndarray, np.ndarray | None, float], T],
    ) -> T | None: ...

    def get_template[T](
        self,
        name: str,
        height: int,
        *,
        prepare: Callable[[np.ndarray, np.ndarray | None, float], T] | None = None,
    ) -> T | MaskedTemplate | None:
        """Return a shared image and mask cropped to the opaque bounds by default.

        The callback receives the cropped image, mask and height ratio with alpha intact.
        The inputs are read-only. Use a stable, non-reentrant callback.
        Missing or unreadable images return None and are retried on the next lookup.
        """
        if height <= 0:
            raise ValueError("画面高度必须大于0")

        name = name.casefold()
        with self._lock:
            path = self._catalog().get(name)
            if path is None:
                return None
            size_height = height if self._reference_height is not None else None
            if size_height != self._height:
                self._templates.clear()
                self._height = size_height

            key = (name, prepare)
            if key in self._templates:
                return cast(T | MaskedTemplate, self._templates[key])
            original = self._originals.get(name)
            if original is None:
                try:
                    encoded = np.fromfile(path, dtype=np.uint8)
                    original = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED) if encoded.size else None
                except (OSError, cv2.error):
                    return None
                if original is None:
                    return None
                original.setflags(write=False)
                self._originals[name] = original

            height_ratio = height / self._reference_height if self._reference_height else 1.0
            image = self._resize(original, self._reference_scale * height_ratio)
            image.setflags(write=False)
            mask = None
            if image.ndim == 3 and image.shape[2] == 4:
                mask = cv2.compare(image[:, :, 3], _MIN_OPAQUE_ALPHA, cv2.CMP_GE)
                mask.setflags(write=False)
                image, mask = self._crop_alpha_template(image, mask)
            if prepare is None:
                if mask is not None:
                    image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
                    image.setflags(write=False)
                result = (image, mask)
            else:
                result = prepare(image, mask, height_ratio)
            self._templates[key] = result
            return result

    @staticmethod
    def _crop_alpha_template(image: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x, y, width, height = cv2.boundingRect(mask)
        if not width or not height:
            raise ValueError("模板没有可用于匹配的不透明像素")
        return image[y : y + height, x : x + width], mask[y : y + height, x : x + width]

    def _catalog(self) -> dict[str, Path]:
        if self._paths is None:
            try:
                paths = sorted(self._directory.iterdir(), key=lambda path: path.name.casefold())
                self._paths = {
                    path.stem.casefold(): path
                    for path in paths
                    if path.suffix.lower() == ".png" and path.is_file()
                }
            except (FileNotFoundError, NotADirectoryError):
                self._paths = {}
            except OSError:
                logger.warning(f"无法读取模板素材目录: {self._directory.name}")
                self._paths = {}
        return self._paths

    def _resize(self, image: np.ndarray, scale: float) -> np.ndarray:
        if scale == 1:
            return image
        height, width = image.shape[:2]
        size = (
            max(1, round(width * scale)),
            max(1, round(height * scale)),
        )
        return cv2.resize(image, size, interpolation=self._interpolation)


ESPER_ICON = GameTemplateSet(
    Path(get_path_relative_to_exe("assets", "game", "esper_icon")),
    reference_scale=0.5,
    reference_height=1440,
    interpolation=cv2.INTER_NEAREST,
)
WEEKLYBOSS_ITEM = GameTemplateSet(
    Path(get_path_relative_to_exe("assets", "game", "weeklyboss_item")),
    reference_scale=0.38672,
    reference_height=1440,
    interpolation=cv2.INTER_AREA,
)
