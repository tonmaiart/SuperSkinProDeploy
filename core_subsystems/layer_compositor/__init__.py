"""layer_compositor -- encapsulated layer metadata, compositing, and healing subsystem.

Public surface: only ``LayerCompositor`` is exported from this package.
Private modules (codec, healer, merge) are implementation details and must
NOT be imported directly by code outside this directory.

External usage:
    from core_subsystems.layer_compositor import LayerCompositor

    # CRUD
    meta, new_idx = LayerCompositor.create_layer(meta_list, "Layer 2")

    # Compositing
    result = LayerCompositor.composite_layers(meta, layer_map, mask_map, idx_to_name, n)

    # Serialisation
    encoded = LayerCompositor.encode(layer_dict)
    decoded = LayerCompositor.decode(raw_blob)

    # Topology healing
    layer_dict, modified = LayerCompositor.heal_layer_dict(layer_dict, neighbours, n)

    # Merge
    result = LayerCompositor.merge_selected(meta, lmap, mmap, indices, target, n)
"""

from importlib import reload

from . import codec as _codec
from . import healer as _healer
from . import merge as _merge
from . import data_operations as _data_operations
from . import layer_compositor as _lc

__all__ = ["LayerCompositor"]

# Cascading reload: codec first (no intra-package deps besides rust_weight_engine),
# then healer (imports from codec), then merge (imports from codec),
# then main class (imports from all three).
#
# The export binding below MUST come AFTER this loop, not before it. On F3
# Reload Scripts, `__init__.py` re-executes from the top, but `from
# .layer_compositor import LayerCompositor` on its own only re-fetches
# whatever `LayerCompositor` attribute is currently cached on the `_lc`
# submodule in `sys.modules` -- it does NOT itself re-run `layer_compositor.py`.
# Binding the export before `reload(_lc)` runs captures the PRE-reload class
# object permanently: `reload(_lc)` then builds a brand-new `LayerCompositor`
# class inside `_lc`'s own namespace, but this package's exported name never
# gets pointed at it, so every external caller (`core/ui_controller/
# pipeline.py`, etc.) keeps calling the stale, pre-reload class until a full
# Blender restart -- silently, with no error. This is the exact "Reload-
# Ordering Footgun" `core_subsystems/profiler/__init__.py`'s own README
# section already documents and fixes for itself; this package had the same
# anti-pattern un-fixed.
for _mod in (_codec, _healer, _merge, _data_operations, _lc):
    try:
        reload(_mod)
    except Exception:
        pass

from .layer_compositor import LayerCompositor
