"""rust_weight_engine -- encapsulated Rust FFI gateway and data-bridge subsystem.

Public surface: only ``RustWeightEngine`` is exported from this package.
Private modules (data_bridge, flat_array_bridge) are implementation details
and must NOT be imported directly by code outside this directory.

External usage:
    from core_subsystems.rust_weight_engine import RustWeightEngine

    # FFI dispatch
    engine = RustWeightEngine("feature_name")
    result = engine.call("rust_fn_name", *args)

    # Data-bridge helpers
    layer_str = RustWeightEngine.map_layer_to_string(layer_int, id_to_bone)
    RustWeightEngine.prune_zero_bones(layer_str)

    # Raw flat-array bridge (accessed via CoreFacade.get_flat_array_bridge())
    # -- not imported directly from outside this package.
"""

from importlib import reload

from . import data_bridge as _data_bridge
from . import flat_array_bridge as _flat_array_bridge
from . import rust_weight_engine as _rwe

__all__ = ["RustWeightEngine"]

from .rust_weight_engine import RustWeightEngine, RustUnavailableError

# Cascading reload: utilities first (no intra-package deps),
# then the main class module that imports from them.
for _mod in (_data_bridge, _flat_array_bridge, _rwe):
    try:
        reload(_mod)
    except Exception:
        pass
