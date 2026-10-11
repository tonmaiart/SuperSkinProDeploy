
import json

import bpy

from ...core_subsystems.layer_compositor import LayerCompositor as _LC
from . import bone_ids, geometry, live_snapshot, flatten, topology_heal
from .temp_vg_bridge import DEFORM_GEN_KEY, rename_session_bones

BONE_RECORD_KEY = "ss_bone_ids"


class LayerStorageService:
    """Authority portal for reading and writing SuperSkinPro layer and mask
    data directly to Blender's mesh custom properties.
    """

    def __init__(self, mesh: bpy.types.Mesh):
        self.mesh = mesh


    def get_active_layer_index(self) -> int:
        return self.mesh.get("ss_active_layer", 0)

    def set_active_layer_index(self, index: int):
        self.mesh["ss_active_layer"] = index


    def has_layer_system(self) -> bool:
        return "ss_layers_meta" in self.mesh

    def remove_layer_system(self):
        for layer in self.read_meta_list():
            idx = layer["index"]
            self.delete_layer_property(idx)
            self.delete_mask_property(idx)
        for key in ("ss_layers_meta", "ss_active_layer", "ss_bone_uuid_map", BONE_RECORD_KEY):
            if key in self.mesh:
                del self.mesh[key]


    def read_meta_list(self) -> list:
        return json.loads(self.mesh.get("ss_layers_meta", "[]"))

    def write_meta_list(self, meta_list: list):
        self.mesh["ss_layers_meta"] = json.dumps(meta_list)

    def find_layer_meta(self, layer_index: int):
        return next((l for l in self.read_meta_list() if l.get("index") == layer_index), None)


    def read_active_layer_dict(self) -> dict:
        return self.read_layer_dict(self.get_active_layer_index())

    def read_layer_dict(self, layer_index: int) -> dict:
        raw = self.mesh.get(f"ss_layer_{layer_index}")
        return _LC.decode(raw) if raw else {}

    def read_layer_raw(self, layer_index: int) -> str:
        return self.mesh.get(f"ss_layer_{layer_index}", "")

    def read_mask_raw(self, layer_index: int) -> str:
        return self.mesh.get(f"ss_mask_{layer_index}", "")

    def write_layer_dict(self, layer_index: int, layer_dict: dict, *, fast: bool = False):
        if layer_index < 0:
            return
        renames = self.follow_bone_renames()
        if renames:
            self._carry_renamed(layer_index, layer_dict, renames)
        if geometry.mesh_owned_names(self.mesh) is None:
            self._keep_stored_rows(layer_index, layer_dict)
        else:
            self._drop_unowned_rows(layer_dict)
        self.mesh[f"ss_layer_{layer_index}"] = _LC.encode(layer_dict, fast=fast)

    def read_layer_arrays(self, layer_index: int):
        return _LC.blob_arrays(self.read_layer_raw(layer_index))

    def write_layer_arrays(self, layer_index: int, arrays, *, fast: bool = False):
        if layer_index < 0:
            return arrays
        renames = self.follow_bone_renames()
        if renames or geometry.mesh_owned_names(self.mesh) is None:
            layer_dict = _LC.rows_of(arrays, arrays[1].tolist())
            if renames:
                self._carry_renamed(layer_index, layer_dict, renames)
            self.write_layer_dict(layer_index, layer_dict, fast=fast)
            return self.read_layer_arrays(layer_index) or arrays
        arrays = self._drop_unowned_arrays(arrays)
        self.mesh[f"ss_layer_{layer_index}"] = _LC.encode_arrays(arrays, fast=fast)
        return arrays


    def follow_bone_renames(self) -> dict:
        users = geometry.mesh_users(self.mesh)
        arms = []
        for obj in users:
            arms.extend(a for a in geometry.armatures_of(obj) if a not in arms)
        if not arms or any(a.data.is_editmode for a in arms):
            return {}
        try:
            record = json.loads(self.mesh.get(BONE_RECORD_KEY, "{}"))
        except ValueError:
            record = {}
        try:
            current = bone_ids.stamp_and_collect(arms, record)
        except (AttributeError, TypeError, RuntimeError):
            return {}
        renames = bone_ids.renames_between(record, current)
        if renames:
            self._rename_stored_bones(renames)
            for obj in users:
                bone_ids.rename_vertex_groups(obj, renames, geometry.deform_bone_names(obj))
                rename_session_bones(obj, renames)
                obj[DEFORM_GEN_KEY] = obj.get(DEFORM_GEN_KEY, 0) + 1
        if current != record:
            self.mesh[BONE_RECORD_KEY] = json.dumps(current)
        return renames

    def _rename_stored_bones(self, renames: dict) -> None:
        for layer in self.read_meta_list():
            idx = int(layer.get("index", -1))
            arrays = self.read_layer_arrays(idx)
            if arrays is not None and not renames.keys() & set(arrays[0]):
                continue
            layer_dict = self.read_layer_dict(idx)
            if any(n in renames for row in layer_dict.values() for n in row):
                self.mesh[f"ss_layer_{idx}"] = _LC.encode(
                    {v: bone_ids.merge_renamed(row, renames) for v, row in layer_dict.items()})
        meta = self.read_meta_list()
        changed = False
        for layer in meta:
            locks = layer.get("bone_locks") or {}
            if any(n in renames for n in locks):
                layer["bone_locks"] = {renames.get(n, n): v for n, v in locks.items()}
                changed = True
        if changed:
            self.write_meta_list(meta)

    def _carry_renamed(self, layer_index: int, layer_dict: dict, renames: dict) -> None:
        for v, row in list(layer_dict.items()):
            if row and any(n in renames for n in row):
                layer_dict[v] = bone_ids.merge_renamed(row, renames)
        seen = {n for row in layer_dict.values() for n in row}
        unseen = set(renames.values()) - seen
        raw = self.read_layer_raw(layer_index)
        if not unseen or not raw:
            return
        for v, row in _LC.decode(raw).items():
            kept = {n: w for n, w in row.items() if n in unseen}
            if kept:
                v = int(v)
                layer_dict[v] = {**kept, **(layer_dict.get(v) or {})}


    def drop_unmanaged_all_layers(self) -> int:
        self.follow_bone_renames()
        owned = geometry.mesh_owned_names(self.mesh)
        if owned is None:
            return 0
        changed = 0
        for layer in self.read_meta_list():
            idx = int(layer.get("index", -1))
            if idx < 0:
                continue
            arrays = self.read_layer_arrays(idx)
            if arrays is not None:
                names = set(arrays[0])
            else:
                names = {n for row in self.read_layer_dict(idx).values() for n in row}
            if names - owned:
                self.write_layer_dict(idx, self.read_layer_dict(idx))
                changed += 1
        return changed

    def _keep_stored_rows(self, layer_index: int, layer_dict: dict) -> None:
        raw = self.read_layer_raw(layer_index)
        if not raw:
            return
        num_verts = len(self.mesh.vertices)
        for v, row in _LC.decode(raw).items():
            v = int(v)
            if not row or not 0 <= v < num_verts:
                continue
            current = layer_dict.get(v)
            missing = {n: w for n, w in row.items() if not current or n not in current}
            if missing:
                layer_dict[v] = {**missing, **(current or {})}

    def _drop_unowned_rows(self, layer_dict: dict) -> None:
        owned = geometry.mesh_owned_names(self.mesh)
        if owned is None:
            return
        for key, row in list(layer_dict.items()):
            if not row or all(n in owned for n in row):
                continue
            kept = {n: w for n, w in row.items() if n in owned}
            if kept:
                layer_dict[key] = kept
            else:
                del layer_dict[key]

    def _drop_unowned_arrays(self, arrays):
        owned = geometry.mesh_owned_names(self.mesh)
        if owned is None or all(n in owned for n in arrays[0]):
            return arrays
        hit = _LC.entries_of(arrays, frozenset(n for n in arrays[0] if n not in owned))
        rows = _LC.rows_of(arrays, list(hit))
        return _LC.replace_rows(arrays, {v: {n: w for n, w in rows.get(v, {}).items() if n in owned}
                                         for v in hit})

    def migrate_layer_blobs(self) -> int:
        migrated = 0
        for key in [k for k in self.mesh.keys() if k.startswith("ss_layer_")]:
            new = _LC.migrate_blob(self.mesh.get(key))
            if new is not None:
                self.mesh[key] = new
                migrated += 1
        return migrated

    def delete_layer_property(self, layer_index: int):
        key = f"ss_layer_{layer_index}"
        if key in self.mesh:
            del self.mesh[key]

    def clone_layer_properties(self, src_index: int, dst_index: int):
        src_key = f"ss_layer_{src_index}"
        if src_key in self.mesh:
            self.mesh[f"ss_layer_{dst_index}"] = self.mesh[src_key]
        else:
            self.mesh[f"ss_layer_{dst_index}"] = _LC.encode({})

        src_mask = f"ss_mask_{src_index}"
        if src_mask in self.mesh:
            self.mesh[f"ss_mask_{dst_index}"] = self.mesh[src_mask]


    def read_active_mask_dict(self) -> dict:
        return self.read_mask_dict(self.get_active_layer_index())

    def read_mask_dict(self, layer_index: int) -> dict:
        return _LC.decode_mask(self.mesh.get(f"ss_mask_{layer_index}"))

    def write_mask_dict(self, layer_index: int, mask_dict: dict):
        if layer_index < 0:
            return
        self.mesh[f"ss_mask_{layer_index}"] = _LC.encode(mask_dict)

    def delete_mask_property(self, layer_index: int):
        key = f"ss_mask_{layer_index}"
        if key in self.mesh:
            del self.mesh[key]


    def save_active(self, layer_dict: dict, mask_dict: dict = None, *,
                    is_mask_mode: bool = False):
        idx = self.get_active_layer_index()
        if is_mask_mode and mask_dict is not None:
            self.write_mask_dict(idx, mask_dict)
        else:
            self.write_layer_dict(idx, layer_dict)


    def _harvest_raw(self, key_prefix: str) -> dict:
        result = {}
        for layer in self.read_meta_list():
            l_idx = layer["index"]
            raw = self.mesh.get(f"{key_prefix}{l_idx}")
            if raw:
                result[l_idx] = raw
        return result

    def harvest_layer_data_map(self) -> dict:
        return self._harvest_raw("ss_layer_")

    def harvest_mask_data_map(self) -> dict:
        return self._harvest_raw("ss_mask_")


    def get_local_mapping(self, obj) -> tuple[dict[str, int], dict[int, str]]:
        return geometry.get_local_mapping(obj)

    def get_unified_mapping(self, obj) -> tuple[dict[str, int], dict[int, str]]:
        return geometry.get_unified_mapping(obj)

    def managed_vg_names(self, obj) -> frozenset:
        return geometry.managed_vg_names(obj)

    def real_vg_names(self, obj) -> frozenset:
        return geometry.real_vg_names(obj)


    def build_mesh_neighbors(self) -> dict:
        return geometry.build_mesh_neighbors(self.mesh)

    def collect_mesh_weights(self, vert_indices: set) -> dict:
        return geometry.collect_mesh_weights(self.mesh, vert_indices)

    def get_vertex_coordinates(self) -> list:
        return geometry.get_vertex_coordinates(self.mesh)

    def build_bvh_tree(self):
        return geometry.build_bvh_tree(self.mesh)


    def init_layer_0_from_live_weights(self, obj, vg_indices=None):
        layer_dict = live_snapshot.harvest_live_weights(obj, vg_indices)
        self.write_layer_dict(0, layer_dict)


    def flatten_visible_layers_to_mesh(self, obj):
        return flatten.flatten_visible_layers_to_mesh(self, obj)

    def heal_new_vertices(self, obj):
        return topology_heal.heal_new_vertices_storage(self, obj)
