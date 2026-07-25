"""RustWeightEngine -- FFI gateway and data-bridge portal to the native Rust core.

Provides two distinct responsibilities through a single class surface:

  FFI Gateway (instantiable):
      ``RustWeightEngine(feature_name)`` locates and loads the compiled
      rust_logic binary for the current platform, then exposes ``call()``
      for dispatching named Rust functions. Raises ``RustUnavailableError``
      immediately if no binary can be found, so callers never silently degrade.

  Data Bridge (static methods):
      ``map_layer_to_int``, ``map_layer_to_string``, and ``prune_zero_bones``
      are thin delegators to the private ``data_bridge`` module. Exposing them
      here lets callers reach both FFI dispatch and key-format conversion from
      a single ``from .rust_weight_engine import RustWeightEngine`` import.

No bpy imports. Operates on plain Python dicts and array types only.
"""

import sys
import os
import platform
import importlib

from .data_bridge import map_layer_to_int, map_layer_to_string, _prune_zero_bones
from . import flat_array_bridge as _fab

_cached_rust_module = None
_has_attempted_load = False

# Tags LicenseGateway itself uses to reach the Rust binary. Exempt from the
# automatic license_key/activation_token injection in call() below, to avoid
# chicken-and-egg recursion and to allow the activation flow to run before
# any license exists. (rust_verify_gumroad_license/rust_check_cached_activation
# have their own license_key/token positional parameters already -- injecting
# a second, different pair as kwargs would collide with those.)
_LICENSE_EXEMPT_FEATURES = frozenset({"license_activation", "license_check"})

# platform.system() returns "Linux"/"Windows"/"Darwin", but
# .github/workflows/release.yml packages binaries into bin/linux/,
# bin/windows/, bin/mac/ (matching its own build-matrix os_name, not
# platform.system()'s raw value) -- "Darwin" must map to "mac" here or the
# loader would look in a bin/darwin/ folder that is never produced by the
# release pipeline, silently failing to find an existing mac binary.
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


def _internal_load_binary():
    """Attempt to locate and import the platform-specific rust_logic binary.

    Search order:
      1. Already in sys.modules (fastest path).
      2. Platform bin directory: <addon_root>/bin/<os_name>/rust_logic.<ext>
         (<os_name> is _PLATFORM_TO_BIN_DIR's mapped name -- "linux"/
         "windows"/"mac" -- matching release.yml's build matrix, not
         platform.system()'s raw value; <ext> is .so on Linux/mac, .pyd on
         Windows -- release.yml always names the file "rust_logic" plus
         the right extension per OS, so a plain importlib.import_module
         resolves it the same way on every platform.)

    Returns the loaded module on success, or None if no binary was found.
    Results are memoised in _cached_rust_module after the first attempt.
    """
    global _cached_rust_module, _has_attempted_load

    if _has_attempted_load:
        return _cached_rust_module

    _has_attempted_load = True

    try:
        import rust_logic
        _cached_rust_module = rust_logic
        return _cached_rust_module
    except ImportError:
        pass

    raw_os = platform.system().lower()
    current_os = _PLATFORM_TO_BIN_DIR.get(raw_os, raw_os)
    # Walk up three levels: rust_weight_engine/ -> core_subsystems/ -> addon root
    pkg_dir = os.path.dirname(__file__)
    subsystems_dir = os.path.dirname(pkg_dir)
    addon_root = os.path.dirname(subsystems_dir)
    bin_path = os.path.join(addon_root, "bin", current_os)

    if os.path.exists(bin_path):
        sys.path.insert(0, bin_path)
        try:
            if "rust_logic" in sys.modules:
                del sys.modules["rust_logic"]
            _cached_rust_module = importlib.import_module("rust_logic")
        except ImportError:
            _cached_rust_module = None
        finally:
            if sys.path and sys.path[0] == bin_path:
                sys.path.pop(0)

    return _cached_rust_module


def _shipped_platforms() -> str:
    """List which bin/<os_name>/ subfolders actually contain a rust_logic
    binary right now, for RustUnavailableError's message.

    Derived from disk contents rather than hardcoded, so this stays accurate
    on its own once Windows/mac binaries are added by a future release build
    -- no manual edit needed here when that happens.
    """
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
            # PreferencesService is imported lazily (not at module scope) for
            # the same reload-cascade-ordering reason documented in
            # core_subsystems/license_gateway/license_gateway.py: a
            # module-level binding here could resolve to a pre-reload class
            # during F3 Reload Scripts, silently pointing at the wrong
            # PreferencesService object.
            from ..preferences.preferences_service import PreferencesService
            self._license_key = PreferencesService.get_license_key()
            self._activation_token = PreferencesService.get_activation_token()

    def call(self, fn_name: str, *args, **kwargs):
        """Call self.module.<fn_name>(*args, **kwargs) and return the result.

        For every feature not in ``_LICENSE_EXEMPT_FEATURES``, the persisted
        license key/token are appended as keyword arguments to every Rust
        call. The Pro-activation decision is made *inside the compiled Rust
        function itself* (see ``license_logic::require_pro()`` in
        ``rust_logic/src/license_logic.rs``, called from every protected
        ``#[pyfunction]`` in ``rust_logic/src/lib.rs``) rather than by a
        Python-side check here. A Python-side check lives in an editable
        ``.py`` file and can simply be deleted by anyone with the source;
        this cannot, short of patching the compiled binary itself.

        Raises the underlying ``ValueError`` unwrapped if the Rust function
        rejects the license (so it still flows through the existing
        ``except ValueError`` handling in
        ``interface/registry/register_api.py`` / ``interface/utils/op_exec.py``),
        or RustUnavailableError (chained from the original exception) for any
        other crash inside the native core.
        """
        if self.feature not in _LICENSE_EXEMPT_FEATURES:
            kwargs.setdefault("license_key", self._license_key)
            kwargs.setdefault("activation_token", self._activation_token)
        try:
            return getattr(self.module, fn_name)(*args, **kwargs)
        except ValueError:
            raise
        except Exception as e:
            raise RustUnavailableError(
                f"[SuperSkinPro] '{self.feature}' crashed inside the native "
                f"Rust core: {e}"
            ) from e

    # ── Data-bridge static interface ──────────────────────────────────────────

    @staticmethod
    def map_layer_to_int(raw_layer_dict: dict, bone_to_id: dict) -> dict:
        """Convert string bone-name keys to integer group-index keys.

        See data_bridge.map_layer_to_int for full parameter documentation.
        """
        return map_layer_to_int(raw_layer_dict, bone_to_id)

    @staticmethod
    def map_layer_to_string(calc_layer_dict: dict, id_to_bone: dict) -> dict:
        """Convert integer group-index keys to string bone-name keys.

        See data_bridge.map_layer_to_string for full parameter documentation.
        """
        return map_layer_to_string(calc_layer_dict, id_to_bone)

    @staticmethod
    def prune_zero_bones(layer_str: dict) -> None:
        """Remove zero-weight bones from a string-keyed layer dict, in-place.

        See data_bridge._prune_zero_bones for full parameter documentation.
        """
        _prune_zero_bones(layer_str)

    # ── Flat-array bridge static interface ────────────────────────────────────

    MASK_SENTINEL = _fab.MASK_SENTINEL

    @staticmethod
    def layer_to_csr(layer_int: dict, num_verts: int, **kwargs):
        """Convert ``{v_idx: {bone_id: weight}}`` to CSR flat arrays.

        See flat_array_bridge.layer_to_csr for full documentation.
        """
        return _fab.layer_to_csr(layer_int, num_verts, **kwargs)

    @staticmethod
    def csr_to_layer(vertex_offsets, bone_ids, weights, num_verts: int) -> dict:
        """Convert CSR flat arrays back to ``{v_idx: {bone_id: weight}}``.

        See flat_array_bridge.csr_to_layer for full documentation.
        """
        return _fab.csr_to_layer(vertex_offsets, bone_ids, weights, num_verts)

    @staticmethod
    def mask_to_flat(mask_dict: dict, num_verts: int, **kwargs):
        """Convert ``{v_idx: float}`` mask dict to a dense flat array.

        See flat_array_bridge.mask_to_flat for full documentation.
        """
        return _fab.mask_to_flat(mask_dict, num_verts, **kwargs)

    @staticmethod
    def flat_to_mask(mask_flat, **kwargs) -> dict:
        """Convert flat mask array back to ``{v_idx: float}`` dict.

        See flat_array_bridge.flat_to_mask for full documentation.
        """
        return _fab.flat_to_mask(mask_flat, **kwargs)

    @staticmethod
    def extract_deformed_coords(obj_eval, num_verts: int):
        """Extract deformed world-space vertex coordinates as a flat array.

        See flat_array_bridge.extract_deformed_coords for full documentation.
        """
        return _fab.extract_deformed_coords(obj_eval, num_verts)
