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

from .layer_compositor import LayerCompositor

# Cascading reload: codec first (no intra-package deps besides rust_weight_engine),
# then healer (imports from codec), then merge (imports from codec),
# then main class (imports from all three).
for _mod in (_codec, _healer, _merge, _data_operations, _lc):
    try:
        reload(_mod)
    except Exception:
        pass
