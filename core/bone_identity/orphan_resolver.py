"""Orphan scanning and resolution — pure data operations over
LayerStorageService weight dicts, keyed by bone NAME exactly like the rest
of layer storage. The only thing "stable UUID" adds is the ability to
classify an orphaned name as likely-renamed (a live bone still carries the
UUID last associated with that name) vs. truly deleted (no bone does).

No automatic remap/delete ever happens here — every function is a single
explicit action invoked by the user through the resolve operator.
"""

from ...core_subsystems.layer_compositor import LayerCompositor as _LC
from . import armature_ids


def backfill_uuid_map(storage, obj, arm_obj) -> dict:
    """Stamp UUIDs onto every live deform bone and refresh the mesh-side
    ``{uuid: last_known_vg_name}`` snapshot. Purely additive."""
    uuid_map = storage.read_bone_uuid_map()
    if arm_obj:
        for vg in obj.vertex_groups:
            bone = arm_obj.data.bones.get(vg.name)
            if bone:
                bone_uuid = armature_ids.get_or_create_bone_id(bone)
                uuid_map[bone_uuid] = vg.name
    storage.write_bone_uuid_map(uuid_map)
    return uuid_map


def scan_orphans(storage, obj, arm_obj, *, live_override: tuple = None) -> list:
    """Return one entry per orphaned bone NAME (not per layer):
    ``{"name", "classification", "suggested_target", "layer_indices"}``.

    A bone is reported exactly as long as at least one layer's weight data
    still references its name — the instant every layer's data for that
    name is gone (resolved via remap/delete, or simply painted away to
    zero and pruned), it stops appearing here on the next scan with no
    extra bookkeeping needed.

    ``classification`` is ``"RENAMED"`` when a live bone still carries the
    UUID last associated with that name (a target is suggested, never
    auto-applied), otherwise ``"ORPHANED"``. ``layer_indices`` lists every
    layer slot index whose weight data references this name, so resolve
    actions can act across all of them at once.

    live_override: optional ``(active_layer_index, layer_dict)`` pair.
    While Edit Mode temp VGs are loaded, the active layer's true current
    weights live there, not in storage (persistence is deferred to Save
    Weights) -- passing the live-read layer_dict here in place of that one
    layer's stored blob lets a bone painted away to zero mid-session drop
    out of the result immediately, instead of only after baking back to
    ss_layer_N. ``None`` (default) uses storage for every layer, unchanged.
    """
    live_names = {vg.name for vg in obj.vertex_groups}
    live_active_idx, live_layer_dict = live_override if live_override else (None, None)

    # {bone_name: [layer_index, ...]}
    name_to_layers: dict = {}
    for layer_idx, raw in storage.harvest_layer_data_map().items():
        if live_layer_dict is not None and layer_idx == live_active_idx:
            decoded = live_layer_dict
        else:
            decoded = _LC.decode(raw)
        names_in_layer = set()
        for weights in decoded.values():
            names_in_layer.update(weights.keys())
        for name in names_in_layer:
            name_to_layers.setdefault(name, []).append(layer_idx)

    orphan_names = sorted(set(name_to_layers.keys()) - live_names)
    if not orphan_names:
        return []

    uuid_map = storage.read_bone_uuid_map()  # {uuid: last_known_name}
    name_to_uuid = {name: u for u, name in uuid_map.items()}

    results = []
    for name in orphan_names:
        suggestion = None
        bone_uuid = name_to_uuid.get(name)
        if bone_uuid and arm_obj:
            bone = armature_ids.resolve_bone_by_uuid(arm_obj, bone_uuid)
            if bone and bone.name in live_names:
                suggestion = bone.name
        results.append({
            "name": name,
            "classification": "RENAMED" if suggestion else "ORPHANED",
            "suggested_target": suggestion,
            "layer_indices": name_to_layers[name],
        })
    return results


def composite_orphan_weight(storage, obj, orphan_name: str) -> dict:
    """Composite *orphan_name*'s weight across all visible layers exactly
    like flatten.flatten_visible_layers_to_mesh() composites a real bone's
    weight -- an orphan has no real VertexGroup to write the result into,
    but the caller (BoneIdentityService.preview_orphan_weight()) uses this
    to populate a temporary preview VG so the native Weight Overlay can
    still show it while the row is selected.

    Returns {v_idx (int): weight (float)}, vertices with weight <= 0.001
    omitted (mirrors flatten's own write threshold).
    """
    meta = storage.read_meta_list()
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups
                   if not vg.name.startswith("__ssp_")}
    layer_data_map = storage.harvest_layer_data_map()
    mask_data_map = storage.harvest_mask_data_map()
    num_verts = len(obj.data.vertices)

    result = _LC.composite_layers(meta, layer_data_map, mask_data_map, idx_to_name, num_verts)

    weights = {}
    for v_idx, bone_weights in result.items():
        w = bone_weights.get(orphan_name)
        if w and w > 0.001:
            weights[int(v_idx)] = w
    return weights


def delete_bone_weights(storage, meta_list, source_name: str, layer_index: int = None):
    """Remove *source_name*'s weight entries entirely.

    Scoped to *layer_index* when given, otherwise every layer in *meta_list*."""
    layers = meta_list if layer_index is None else [
        l for l in meta_list if l["index"] == layer_index
    ]
    for layer in layers:
        idx = layer["index"]
        layer_dict = storage.read_layer_dict(idx)
        changed = False
        for weights in layer_dict.values():
            if source_name in weights:
                del weights[source_name]
                changed = True
        if changed:
            storage.write_layer_dict(idx, layer_dict)
