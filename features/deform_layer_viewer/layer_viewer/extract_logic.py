
from .fill_logic import DEFAULT_REPEAT, DEFAULT_SMOOTH, fill_transparent

_EPS = 1e-9


def split_layer(layer, mask, mask_default, bones, num_verts, is_base):
    picked, rest, picked_mask, emptied = {}, {}, {}, set()
    rest_mask = None if is_base else {}
    for v in range(num_verts):
        m = 1.0 if is_base else max(0.0, min(1.0, mask.get(v, mask_default)))
        row = layer.get(v)
        sel = {b: w for b, w in row.items() if w > 0.0 and b in bones} if row else {}
        oth = {b: w for b, w in row.items() if w > 0.0 and b not in bones} if row else {}
        s = sum(sel.values())
        o = sum(oth.values())
        if s <= 0.0:
            picked_mask[v] = 0.0
            if oth:
                rest[v] = dict(oth)
            if rest_mask is not None:
                rest_mask[v] = m
            continue
        picked[v] = {b: w / s for b, w in sel.items()}
        t = s + o
        c = m * min(t, 1.0)
        picked_mask[v] = c * s / t
        denom = t - c * s
        if oth:
            if is_base and denom > _EPS:
                rest[v] = {b: w * c / denom for b, w in oth.items()}
            else:
                rest[v] = {b: w / o for b, w in oth.items()}
        elif rest_mask is not None or c >= 1.0 - 1e-6:
            emptied.add(v)
        if rest_mask is not None:
            rest_mask[v] = c * (t - s) / denom if denom > _EPS else m
    return picked, rest, picked_mask, rest_mask, emptied


def is_base_layer(meta, index):
    pos = next((i for i, e in enumerate(meta) if int(e.get("index", -1)) == index), None)
    if pos is None:
        return False
    return not any(not e.get("is_group") and e.get("visible", True) for e in meta[pos + 1:])


def new_layer_name(bones, active):
    return active if active in bones else sorted(bones)[0]


def _meta_pos(ctrl, index):
    for i, e in enumerate(ctrl.get_meta_list()):
        if int(e.get("index", -1)) == index:
            return i
    return None


def _place_above(ctrl, new_idx, source, group_id):
    if group_id is not None:
        ctrl.move_layers_to_group([new_idx], group_id)
    for _ in range(len(ctrl.get_meta_list()) * 2):
        new_pos, src_pos = _meta_pos(ctrl, new_idx), _meta_pos(ctrl, source)
        if new_pos is None or src_pos is None or new_pos == src_pos - 1:
            break
        if not ctrl.move_layer(new_idx, 1 if new_pos < src_pos else -1):
            break


def _to_int(rows, bone_to_id):
    return {v: {bone_to_id[b]: w for b, w in row.items() if b in bone_to_id} for v, row in rows.items()}


SOURCE_FILL = 'FILL'
SOURCE_REMOVE = 'REMOVE'
SOURCE_KEEP = 'KEEP'


def extract_to_new_layer(ctrl, source, bones, active_bone, smooth=DEFAULT_SMOOTH, repeat=DEFAULT_REPEAT,
                         source_mode=SOURCE_FILL, name=None):
    meta = ctrl.get_meta_list()
    entry = next(e for e in meta if int(e.get("index", -1)) == source)
    num_verts = ctrl.get_num_verts()
    layer = ctrl.read_active_layer()
    if not any(w > 0.0 and b in bones for row in layer.values() for b, w in row.items()):
        return None

    picked, rest, picked_mask, rest_mask, emptied = split_layer(
        layer, ctrl.get_active_mask_dict(), float(entry.get("mask_default", 1.0)),
        bones, num_verts, is_base_layer(meta, source),
    )
    picked.update(fill_transparent(ctrl, picked, num_verts, smooth, repeat))
    if source_mode == SOURCE_FILL:
        rest_fill = fill_transparent(ctrl, rest, num_verts, smooth, repeat)
        rest.update({v: r for v, r in rest_fill.items() if v in emptied})
    elif source_mode == SOURCE_REMOVE:
        rest = {v: {b: w for b, w in row.items() if w > 0.0 and b not in bones} for v, row in layer.items() if row}
        rest = {v: row for v, row in rest.items() if row}
        rest_mask = None

    new_idx = ctrl.create_layer(name or new_layer_name(bones, active_bone))
    if new_idx is None or new_idx < 0:
        return -1
    _place_above(ctrl, new_idx, source, entry.get("group_id"))

    bone_to_id, id_to_bone = ctrl.get_unified_mapping()
    if source_mode != SOURCE_KEEP:
        ctrl.switch_to_layer(source)
        rest_int = _to_int(rest, bone_to_id)
        ctrl.write_active_layer_int(rest_int, id_to_bone)
        if rest_mask is not None:
            ctrl.write_active_layer_int(rest_int, id_to_bone, rest_mask, is_mask_mode=True)

    ctrl.switch_to_layer(new_idx)
    picked_int = _to_int(picked, bone_to_id)
    ctrl.write_active_layer_int(picked_int, id_to_bone)
    ctrl.write_active_layer_int(picked_int, id_to_bone, picked_mask, is_mask_mode=True)
    if not entry.get("visible", True):
        ctrl.toggle_visible(new_idx)
    ctrl.finish(color_only=False)
    return new_idx
