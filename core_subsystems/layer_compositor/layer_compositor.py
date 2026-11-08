
from .codec import (encode_layer_dict, decode_layer_dict, normalize_mask_dict, _composite_layers,
                    _composite_layer_groups, has_group_compositor, layer_blob_arrays,
                    migrate_layer_blob, encode_layer_arrays, replace_layer_rows,
                    layer_arrays_rows, layer_arrays_to_int, layer_arrays_entries)
from .healer import heal_layer_dict, heal_mask_dict
from .merge import merge_selected as _merge_selected


class LayerCompositor:
    """Classmethod-only portal; never instantiated."""


    @classmethod
    def layers(cls, meta_list):
        return meta_list

    @classmethod
    def layer_names(cls, meta_list):
        return [l["name"] for l in meta_list]

    @classmethod
    def active_layer_name(cls, meta_list, active_index):
        for l in meta_list:
            if l["index"] == active_index:
                return l["name"]
        return "Base"

    @classmethod
    def _next_index(cls, meta_list):
        return max((l["index"] for l in meta_list), default=0) + 1


    @classmethod
    def create_layer(cls, meta_list, name):
        meta = list(meta_list)
        new_idx = cls._next_index(meta)
        entry = {
            "name": name,
            "index": new_idx,
            "visible": True,
            "bone_locks": {},
            "mask_default": 0.0,
            "icon": "NONE",
            "group_id": None,
        }
        meta.insert(0, entry)
        return meta, new_idx

    @classmethod
    def create_group(cls, meta_list, name):
        meta = list(meta_list)
        new_idx = cls._next_index(meta)
        entry = {
            "name": name,
            "index": new_idx,
            "visible": True,
            "bone_locks": {},
            "mask_default": 1.0,
            "icon": "NONE",
            "is_group": True,
            "group_id": None,
            "collapsed": False,
        }
        meta.insert(0, entry)
        return meta, new_idx

    @classmethod
    def move_layers_to_group(cls, meta_list, selected_indices, target_group_id):
        meta = list(meta_list)
        selected_set = set(selected_indices)
        moved = [dict(l) for l in meta if l["index"] in selected_set]
        if not moved:
            return meta
        remaining = [l for l in meta if l["index"] not in selected_set]

        if target_group_id is not None:
            insert_at = next((i for i, l in enumerate(remaining) if l["index"] == target_group_id), None)
            if insert_at is None:
                target_group_id = None
            else:
                for l in moved:
                    l["group_id"] = target_group_id
                insert_at += 1
                while insert_at < len(remaining) and remaining[insert_at].get("group_id") == target_group_id:
                    insert_at += 1
                return remaining[:insert_at] + moved + remaining[insert_at:]

        result = list(remaining)
        insertion_cursor = {}
        for l in moved:
            old_gid = l.get("group_id")
            l["group_id"] = None
            if old_gid is None:
                result.append(l)
                continue
            if old_gid not in insertion_cursor:
                pos = next((i for i, r in enumerate(result) if r["index"] == old_gid), None)
                if pos is None:
                    insertion_cursor[old_gid] = len(result)
                else:
                    insert_at = pos + 1
                    while insert_at < len(result) and result[insert_at].get("group_id") == old_gid:
                        insert_at += 1
                    insertion_cursor[old_gid] = insert_at
            insert_at = insertion_cursor[old_gid]
            result.insert(insert_at, l)
            insertion_cursor[old_gid] = insert_at + 1
        return result

    @classmethod
    def remove_layer(cls, meta_list, index):
        removed_is_group = any(l["index"] == index and l.get("is_group") for l in meta_list)
        if removed_is_group:
            return [l for l in meta_list if l["index"] != index and l.get("group_id") != index]
        return [l for l in meta_list if l["index"] != index]

    @classmethod
    def duplicate_layer(cls, meta_list, index):
        meta = list(meta_list)
        pos = next((i for i, l in enumerate(meta) if l["index"] == index), None)
        if pos is None:
            return meta, None

        src = meta[pos]
        new_idx = cls._next_index(meta)
        new_entry = {
            "name": f"{src['name']} Copy",
            "index": new_idx,
            "visible": True,
            "bone_locks": dict(src.get("bone_locks", {})),
            "mask_default": src.get("mask_default", 1.0),
            "icon": src.get("icon", "NONE"),
            "group_id": src.get("group_id"),
        }
        meta.insert(pos, new_entry)
        return meta, new_idx

    @classmethod
    def _chunk_bounds(cls, meta, i):
        entry = meta[i]
        gid = entry["index"] if entry.get("is_group") else entry.get("group_id")
        if gid is None:
            return i, i + 1
        start = next(j for j, l in enumerate(meta) if l.get("index") == gid)
        end = start + 1
        while end < len(meta) and meta[end].get("group_id") == gid:
            end += 1
        return start, end

    @classmethod
    def move_layer(cls, meta_list, index, direction):
        meta = list(meta_list)
        pos = next((i for i, l in enumerate(meta) if l["index"] == index), None)
        if pos is None:
            return meta

        entry = meta[pos]
        if entry.get("group_id") is not None and not entry.get("is_group"):
            target_pos = pos + direction
            if target_pos < 0 or target_pos >= len(meta):
                return meta
            if meta[target_pos].get("group_id") != entry["group_id"]:
                return meta
            meta.insert(target_pos, meta.pop(pos))
            return meta

        start, end = cls._chunk_bounds(meta, pos)
        chunk = meta[start:end]
        if direction < 0:
            if start == 0:
                return meta
            prev_start, _ = cls._chunk_bounds(meta, start - 1)
            return meta[:prev_start] + chunk + meta[prev_start:start] + meta[end:]
        else:
            if end >= len(meta):
                return meta
            _, next_end = cls._chunk_bounds(meta, end)
            return meta[:start] + meta[end:next_end] + chunk + meta[next_end:]

    @classmethod
    def toggle_visible(cls, meta_list, index):
        meta = list(meta_list)
        for l in meta:
            if l["index"] == index:
                l["visible"] = not l.get("visible", True)
                break
        return meta

    @classmethod
    def rename_layer(cls, meta_list, index, new_name):
        meta = list(meta_list)
        for l in meta:
            if l["index"] == index:
                l["name"] = new_name
                break
        return meta


    @classmethod
    def _get_field(cls, meta_list, layer_index, field, default):
        for l in meta_list:
            if l["index"] == layer_index:
                return l.get(field, default)
        return default

    @classmethod
    def _set_field(cls, meta_list, layer_index, field, value):
        meta = list(meta_list)
        for l in meta:
            if l["index"] == layer_index:
                l[field] = value
                break
        return meta


    @classmethod
    def get_icon(cls, meta_list, layer_index):
        return cls._get_field(meta_list, layer_index, "icon", "NONE")

    @classmethod
    def set_icon(cls, meta_list, layer_index, icon):
        return cls._set_field(meta_list, layer_index, "icon", icon)


    @classmethod
    def get_bone_locks(cls, meta_list, layer_index):
        return cls._get_field(meta_list, layer_index, "bone_locks", {})

    @classmethod
    def set_bone_locks(cls, meta_list, layer_index, bone_locks):
        return cls._set_field(meta_list, layer_index, "bone_locks", dict(bone_locks))


    @classmethod
    def _scoped_mask_value(cls, layer, group_meta_by_id, mask_dicts_map, v_idx):
        mask_dict = mask_dicts_map.get(layer["index"], {})
        mask_default = float(layer.get("mask_default", 1.0))
        v_mask = max(0.0, min(1.0, mask_dict.get(v_idx, mask_default)))
        group_meta = group_meta_by_id.get(layer.get("group_id"))
        if group_meta is not None:
            if not group_meta.get("visible", True):
                return 0.0
            group_mask = mask_dicts_map.get(group_meta["index"], {})
            group_default = float(group_meta.get("mask_default", 1.0))
            gv = max(0.0, min(1.0, group_mask.get(v_idx, group_default)))
            v_mask *= gv
        return v_mask

    @classmethod
    def find_mask_gaps(cls, meta_list, mask_dicts_map, num_verts) -> list:
        group_meta_by_id = {l["index"]: l for l in meta_list if l.get("is_group")}
        opacity_leak = [1.0] * num_verts
        for layer in meta_list:
            if layer.get("is_group"):
                continue
            if not layer.get("visible", True):
                continue
            for v_idx in range(num_verts):
                v_mask = cls._scoped_mask_value(layer, group_meta_by_id, mask_dicts_map, v_idx)
                opacity_leak[v_idx] *= (1.0 - v_mask)
        return [v_idx for v_idx, leak in enumerate(opacity_leak) if leak > 0.001]

    @classmethod
    def get_layer_cut_mask(cls, meta_list, mask_dicts_map, target_index, num_verts) -> dict:
        target_meta = next((l for l in meta_list if l["index"] == target_index), None)
        if target_meta is None:
            return {}
        if target_meta.get("is_group"):
            return {}

        group_meta_by_id = {l["index"]: l for l in meta_list if l.get("is_group")}

        leak = [1.0] * num_verts
        for layer in meta_list:
            if layer["index"] == target_index:
                break
            if layer.get("is_group"):
                continue
            if not layer.get("visible", True):
                continue
            for v_idx in range(num_verts):
                v_mask = cls._scoped_mask_value(layer, group_meta_by_id, mask_dicts_map, v_idx)
                leak[v_idx] *= (1.0 - v_mask)

        result = {}
        for v_idx in range(num_verts):
            cut = cls._scoped_mask_value(target_meta, group_meta_by_id, mask_dicts_map, v_idx) * leak[v_idx]
            if cut > 0.001:
                result[v_idx] = cut
        return result


    @classmethod
    def composite_layers(cls, meta_list, layer_data_map, mask_data_map, idx_to_name, num_verts,
                         dirty_verts=None):
        return _composite_layers(meta_list, layer_data_map, mask_data_map, idx_to_name, num_verts,
                                 dirty_verts=dirty_verts)

    @classmethod
    def composite_layer_groups(cls, meta_list, layer_data_map, mask_data_map, name_to_idx,
                               num_verts, dirty_verts=None):
        return _composite_layer_groups(meta_list, layer_data_map, mask_data_map, name_to_idx,
                                       num_verts, dirty_verts=dirty_verts)

    @staticmethod
    def has_group_compositor() -> bool:
        return has_group_compositor()

    @classmethod
    def merge_selected(cls, meta_list, layer_data_map, mask_data_map,
                       selected_indices, target_index, num_verts):
        return _merge_selected(
            meta_list, layer_data_map, mask_data_map,
            selected_indices, target_index, num_verts
        )


    @classmethod
    def encode(cls, layer_dict, *, fast: bool = False) -> str:
        return encode_layer_dict(layer_dict, fast=fast)

    @classmethod
    def decode(cls, raw) -> dict:
        return decode_layer_dict(raw)

    @classmethod
    def blob_arrays(cls, raw):
        return layer_blob_arrays(raw)

    @classmethod
    def encode_arrays(cls, arrays, *, fast: bool = False) -> str:
        return encode_layer_arrays(*arrays, fast=fast)

    @classmethod
    def replace_rows(cls, arrays, rows: dict):
        return replace_layer_rows(arrays, rows)

    @classmethod
    def int_rows(cls, arrays, bone_to_id: dict) -> dict:
        return layer_arrays_to_int(arrays, bone_to_id)

    @classmethod
    def entries_of(cls, arrays, names) -> dict:
        return layer_arrays_entries(arrays, names)

    @classmethod
    def rows_of(cls, arrays, verts) -> dict:
        return layer_arrays_rows(arrays, verts)

    @classmethod
    def migrate_blob(cls, raw):
        return migrate_layer_blob(raw)

    @classmethod
    def decode_mask(cls, raw) -> dict:
        return normalize_mask_dict(decode_layer_dict(raw)) if raw else {}


    @classmethod
    def heal_layer_dict(cls, layer_dict, neighbours, num_verts):
        return heal_layer_dict(layer_dict, neighbours, num_verts)

    @classmethod
    def heal_mask_dict(cls, mask_dict, neighbours, num_verts, mask_default):
        return heal_mask_dict(mask_dict, neighbours, num_verts, mask_default)


    @staticmethod
    def add_vg_selected(obj, name: str) -> bool:
        storage = obj.superskin_storage
        if not storage.selected_names or not storage.selected_names.startswith(","):
            storage.selected_names = ","
        if f",{name}," not in storage.selected_names:
            storage.selected_names += f"{name},"
            return True
        return False

    @staticmethod
    def remove_vg_selected(obj, name: str) -> bool:
        storage = obj.superskin_storage
        if f",{name}," in storage.selected_names:
            storage.selected_names = storage.selected_names.replace(f"{name},", "")
            return True
        return False

    @staticmethod
    def clear_all_selected(obj) -> None:
        obj.superskin_storage.selected_names = ","


    @staticmethod
    def bone_weights_to_deform_state(
        weight_result: dict,
        name_to_idx: dict,
        threshold: float = 0.001,
    ) -> dict:
        new_state: dict = {}
        for v_idx, bone_weights in weight_result.items():
            entry: dict = {}
            for g_name, w in bone_weights.items():
                g_idx = name_to_idx.get(g_name)
                if g_idx is None or w <= threshold:
                    continue
                entry[g_idx] = w
            if entry:
                new_state[v_idx] = entry
        return new_state
