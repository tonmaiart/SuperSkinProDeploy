
from ...core_subsystems.layer_compositor import LayerCompositor as _LC
from . import armature_ids


def backfill_uuid_map(storage, obj, arm_obj) -> dict:
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
    live_names = {vg.name for vg in obj.vertex_groups}
    live_active_idx, live_layer_dict = live_override if live_override else (None, None)

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

    uuid_map = storage.read_bone_uuid_map()
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
    meta = storage.read_meta_list()
    idx_to_name = storage.get_local_mapping(obj)[1]
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
