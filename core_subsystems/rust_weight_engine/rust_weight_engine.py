
import sys
import os
import platform
import hashlib
import shutil
import importlib.machinery
import importlib.util

from .data_bridge import map_layer_to_int, map_layer_to_string, _prune_zero_bones
from . import flat_array_bridge as _fab

_cached_rust_module = None
_has_attempted_load = False
_native_cache_dir = None

_EXPECTED_FFI_VERSION = 3

_LICENSE_EXEMPT_FEATURES = frozenset({"license_activation", "license_check"})

_PLATFORM_TO_BIN_DIR = {
    "linux": "linux",
    "windows": "windows",
    "darwin": "mac",
}


class RustUnavailableError(RuntimeError):
    """Raised when SuperSkinPro's native Rust acceleration core cannot be
    loaded for the current platform, or crashes while executing a feature.

    There is no Python fallback -- every weight/layer/visualizer calculation
    requires the compiled rust_logic module. Callers (operators, draw
    callbacks) should let this propagate so Blender's normal error surfacing
    (operator report / console traceback) shows the user a clear reason,
    rather than silently degrading or doing nothing.
    """


def _register_debug_log_callback(module) -> None:
    if module is None or not hasattr(module, "rust_register_debug_log_callback"):
        return
    from ..debug_logging import DebugLogService
    module.rust_register_debug_log_callback(DebugLogService.log)


def _internal_load_binary():
    global _cached_rust_module, _has_attempted_load

    if _has_attempted_load:
        return _cached_rust_module

    _has_attempted_load = True

    if "rust_logic" in sys.modules:
        _cached_rust_module = sys.modules["rust_logic"]
        _warn_if_ffi_mismatch(_cached_rust_module)
        _register_debug_log_callback(_cached_rust_module)
        return _cached_rust_module

    raw_os = platform.system().lower()
    current_os = _PLATFORM_TO_BIN_DIR.get(raw_os, raw_os)
    pkg_dir = os.path.dirname(__file__)
    subsystems_dir = os.path.dirname(pkg_dir)
    addon_root = os.path.dirname(subsystems_dir)
    bin_path = os.path.join(addon_root, "bin", current_os)

    shipped = _find_shipped_binary(bin_path)
    if shipped is not None:
        staged = _stage_into_cache(shipped)
        _cached_rust_module = _import_from_file(staged) if staged else None
        if _cached_rust_module is None:
            _cached_rust_module = _import_from_file(shipped)

    _warn_if_ffi_mismatch(_cached_rust_module)
    _register_debug_log_callback(_cached_rust_module)
    return _cached_rust_module


def set_native_cache_dir(path) -> None:
    global _native_cache_dir
    _native_cache_dir = path


def _find_shipped_binary(bin_path):
    for suffix in importlib.machinery.EXTENSION_SUFFIXES:
        candidate = os.path.join(bin_path, "rust_logic" + suffix)
        if os.path.isfile(candidate):
            return candidate
    return None


def _stage_into_cache(shipped):
    if not _native_cache_dir:
        return None
    try:
        digest = hashlib.sha256()
        with open(shipped, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        key = digest.hexdigest()[:16]
        dest_dir = os.path.join(_native_cache_dir, key)
        dest = os.path.join(dest_dir, os.path.basename(shipped))
        if not (os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(shipped)):
            os.makedirs(dest_dir, exist_ok=True)
            tmp = f"{dest}.{os.getpid()}.tmp"
            shutil.copyfile(shipped, tmp)
            os.replace(tmp, dest)
    except OSError as e:
        _log_ffi(f"Native core staging failed, loading the shipped file directly: {e!r}")
        return None
    _prune_native_cache(keep=key)
    return dest


def _prune_native_cache(keep) -> None:
    try:
        names = os.listdir(_native_cache_dir)
    except OSError:
        return
    for name in names:
        path = os.path.join(_native_cache_dir, name)
        if name != keep and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def _import_from_file(path):
    try:
        spec = importlib.util.spec_from_file_location("rust_logic", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ImportError as e:
        _log_ffi(f"Native core import failed for {path}: {e!r}")
        return None
    sys.modules["rust_logic"] = module
    return module


def _log_ffi(message) -> None:
    from ..debug_logging import DebugLogService
    DebugLogService.log("rust_ffi", message)
    print(f"[SuperSkinPro] {message}")


def _ffi_mismatch_detail(module):
    if module is None:
        return None
    found = getattr(module, "SSP_FFI_VERSION", None)
    if found == _EXPECTED_FFI_VERSION:
        return None
    return (
        f"The loaded native core ({getattr(module, '__file__', '?')}, FFI version "
        f"{found if found is not None else 'unknown'}) does not match this SuperSkinPro version "
        f"(FFI version {_EXPECTED_FFI_VERSION}). Fully close and reopen Blender. If this "
        f"message remains, uninstall SuperSkinPro, restart Blender, and install it again."
    )


def _warn_if_ffi_mismatch(module) -> None:
    detail = _ffi_mismatch_detail(module)
    if detail is not None:
        _log_ffi(detail)


def _shipped_platforms() -> str:
    pkg_dir = os.path.dirname(__file__)
    subsystems_dir = os.path.dirname(pkg_dir)
    addon_root = os.path.dirname(subsystems_dir)
    bin_root = os.path.join(addon_root, "bin")

    shipped = []
    if os.path.isdir(bin_root):
        for os_name in sorted(os.listdir(bin_root)):
            os_dir = os.path.join(bin_root, os_name)
            if os.path.isdir(os_dir) and any(
                f.startswith("rust_logic") for f in os.listdir(os_dir)
            ):
                shipped.append(os_name)

    return ", ".join(shipped) if shipped else "none"


class RustWeightEngine:
    """Required-dependency portal to the compiled rust_logic native module.

    Raises RustUnavailableError immediately if no binary is found for the
    current platform (bin/<os_name>/rust_logic.<ext>, see
    _PLATFORM_TO_BIN_DIR and _internal_load_binary's docstring above for the
    os_name/extension mapping -- SuperSkinPro currently ships a binary for
    Linux only; Windows/mac bin/ folders exist but are empty until a future
    multi-platform release), and from call() if the underlying Rust
    function itself raises.

    Static data-bridge methods (map_layer_to_int, map_layer_to_string,
    prune_zero_bones) are provided as a convenience so callers can reach both
    FFI dispatch and key-format conversion from a single import.

    Usage (FFI dispatch):
        engine = RustWeightEngine("smooth_weights")
        result = engine.call("rust_smooth_weights", layer_int, neighbors, strength)

    Usage (data bridge):
        layer_str = RustWeightEngine.map_layer_to_string(layer_int, id_to_bone)
        RustWeightEngine.prune_zero_bones(layer_str)
    """

    def __init__(self, feature_name: str):
        self.feature = feature_name
        self.module = _internal_load_binary()
        if self.module is None:
            raise RustUnavailableError(
                f"[SuperSkinPro] '{feature_name}' requires the native Rust "
                f"acceleration core, but no compiled binary was found for "
                f"this platform ({platform.system()}). SuperSkinPro currently "
                f"ships a Rust binary for: {_shipped_platforms()}."
            )
        if feature_name in _LICENSE_EXEMPT_FEATURES:
            self._license_key = None
            self._activation_token = None
        else:
            from ..preferences.preferences_service import PreferencesService
            self._license_key = PreferencesService.get_license_key()
            self._activation_token = PreferencesService.get_activation_token()

    def call(self, fn_name: str, *args, **kwargs):
        if self.feature not in _LICENSE_EXEMPT_FEATURES:
            kwargs.setdefault("license_key", self._license_key)
            kwargs.setdefault("activation_token", self._activation_token)
        try:
            return getattr(self.module, fn_name)(*args, **kwargs)
        except ValueError:
            raise
        except Exception as e:
            mismatch = _ffi_mismatch_detail(self.module)
            suffix = f" -- {mismatch}" if mismatch else ""
            raise RustUnavailableError(
                f"[SuperSkinPro] '{self.feature}' crashed inside the native "
                f"Rust core: {e}{suffix}"
            ) from e


    @staticmethod
    def map_layer_to_int(raw_layer_dict: dict, bone_to_id: dict) -> dict:
        return map_layer_to_int(raw_layer_dict, bone_to_id)

    @staticmethod
    def map_layer_to_string(calc_layer_dict: dict, id_to_bone: dict) -> dict:
        return map_layer_to_string(calc_layer_dict, id_to_bone)

    @staticmethod
    def prune_zero_bones(layer_str: dict) -> None:
        _prune_zero_bones(layer_str)


    MASK_SENTINEL = _fab.MASK_SENTINEL

    @staticmethod
    def layer_to_csr(layer_int: dict, num_verts: int, **kwargs):
        return _fab.layer_to_csr(layer_int, num_verts, **kwargs)

    @staticmethod
    def csr_to_layer(vertex_offsets, bone_ids, weights, num_verts: int) -> dict:
        return _fab.csr_to_layer(vertex_offsets, bone_ids, weights, num_verts)

    @staticmethod
    def mask_to_flat(mask_dict: dict, num_verts: int, **kwargs):
        return _fab.mask_to_flat(mask_dict, num_verts, **kwargs)

    @staticmethod
    def flat_to_mask(mask_flat, **kwargs) -> dict:
        return _fab.flat_to_mask(mask_flat, **kwargs)

    @staticmethod
    def extract_deformed_coords(obj_eval, num_verts: int):
        return _fab.extract_deformed_coords(obj_eval, num_verts)
