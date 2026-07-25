"""Composite visible layers onto real Vertex Groups on the mesh.

Bpy-dependent bridge between LayerStorageService's raw storage and the
mesh's actual Vertex Group weights. Every read of layer/mask data goes
through the owning LayerStorageService instance -- never touches mesh
custom properties directly.
"""

from ...core_subsystems.layer_compositor import LayerCompositor
from ...core_subsystems.debug_logging import DebugLogService


def _log_compositor_loss(obj, meta, layer_data_map, result):
    """Debug-only (category 'core_pipeline'), ALWAYS logs one line when the
    category is enabled -- even when nothing looks wrong -- so an absent
    log line unambiguously means the category isn't on, never "nothing to
    report". Compares bone names/layer indices present in the raw stored
    data against what actually came out of LayerCompositor.composite_layers(),
    to localize a "weight doesn't come back" report to one of three places:

    orphaned_layer_slots: a layer index that has a raw weight blob in
        storage (ss_layer_N) but no matching entry in ss_layers_meta --
        _composite_layers() iterates meta, not storage, so this index's
        weight data is never even read, regardless of masking.
    invisible_layers: a layer index that IS in meta but has visible=False
        -- also skipped by _composite_layers()'s `if not layer.get(
        "visible", True): continue`, deliberately (this is normal for a
        hidden layer), but worth seeing when a specific bone was expected
        to show through it.
    dropped: a bone name the storage genuinely holds weight for, but that
        never appears as a key anywhere in the composited result -- lost
        inside compositing itself (only possible via one of the two above,
        or the Rust call itself dropping the key).
    collapsed: a bone name that does appear in the result, but whose total
        composited weight is near-zero while storage held a substantial
        total for it -- present but effectively zeroed by the blend math
        (masking), as opposed to structurally missing.
    """
    if not DebugLogService.is_enabled("core_pipeline"):
        return

    meta_indices = {int(l["index"]) for l in meta}
    storage_indices = set(layer_data_map.keys())
    orphaned_layer_slots = sorted(storage_indices - meta_indices)
    invisible_layers = sorted(
        int(l["index"]) for l in meta if not l.get("visible", True)
    )

    input_totals = {}
    for raw in layer_data_map.values():
        decoded = LayerCompositor.decode(raw)
        for weights in decoded.values():
            for name, w in weights.items():
                input_totals[name] = input_totals.get(name, 0.0) + w

    output_totals = {}
    for bone_weights in result.values():
        for name, w in bone_weights.items():
            output_totals[name] = output_totals.get(name, 0.0) + w

    dropped = sorted(n for n in input_totals if n not in output_totals)
    collapsed = sorted(
        n for n, total in input_totals.items()
        if n in output_totals and total > 0.5 and output_totals[n] < 0.01
    )
    DebugLogService.log(
        "core_pipeline",
        f"flatten_visible_layers_to_mesh(): obj={obj.name!r} "
        f"meta_layer_indices={sorted(meta_indices)!r} "
        f"storage_layer_indices={sorted(storage_indices)!r} "
        f"orphaned_layer_slots(in storage, no meta entry)={orphaned_layer_slots!r} "
        f"invisible_layers={invisible_layers!r} "
        f"dropped_bones(missing from result)={dropped!r} "
        f"collapsed_bones(near-zero result, storage was substantial)={collapsed!r}",
    )


def flatten_visible_layers_to_mesh(storage, obj):
    """Composite every visible layer onto real Vertex Groups on *obj*.

    Standard formula: Result = Base * (1 - Mask) + Layer * Mask
    Delegates pure math to ``layer_manager.compositor.composite_layers``.
    """
    if not obj or obj.type != 'MESH' or not storage.has_layer_system():
        return

    mesh = obj.data
    vg_list = obj.vertex_groups
    num_verts = len(mesh.vertices)

    if num_verts == 0 or len(vg_list) == 0:
        return

    meta = storage.read_meta_list()
    idx_to_name = {vg.index: vg.name for vg in vg_list
                   if not vg.name.startswith("__ssp_")}

    layer_data_map = storage.harvest_layer_data_map()
    mask_data_map = storage.harvest_mask_data_map()

    result = LayerCompositor.composite_layers(meta, layer_data_map, mask_data_map,
                                               idx_to_name, num_verts)

    _log_compositor_loss(obj, meta, layer_data_map, result)

    for vg in vg_list:
        if not vg.name.startswith("__ssp_"):
            vg.remove(range(num_verts))

    name_to_vg = {vg.name: vg for vg in obj.vertex_groups
                  if not vg.name.startswith("__ssp_")}
    for v_idx, bone_weights in result.items():
        for g_name, w in bone_weights.items():
            if g_name in name_to_vg and w > 0.001:
                name_to_vg[g_name].add([v_idx], w, 'REPLACE')
