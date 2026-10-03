
import json

import bpy

from ...core_subsystems.layer_compositor import LayerCompositor as _LC
from . import geometry, live_snapshot, flatten, topology_heal


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
        for key in ("ss_layers_meta", "ss_active_layer", "ss_bone_uuid_map"):
            if key in self.mesh:
                del self.mesh[key]


    def read_meta_list(self) -> list:
        return json.loads(self.mesh.get("ss_layers_meta", "[]"))

    def write_meta_list(self, meta_list: list):
        self.mesh["ss_layers_meta"] = json.dumps(meta_list)

    def find_layer_meta(self, layer_index: int):
        return next((l for l in self.read_meta_list() if l.get("index") == layer_index), None)


    def read_bone_uuid_map(self) -> dict:
        return json.loads(self.mesh.get("ss_bone_uuid_map", "{}"))

    def write_bone_uuid_map(self, uuid_map: dict):
        self.mesh["ss_bone_uuid_map"] = json.dumps(uuid_map)


    def read_active_layer_dict(self) -> dict:
        return self.read_layer_dict(self.get_active_layer_index())

    def read_layer_dict(self, layer_index: int) -> dict:
        raw = self.mesh.get(f"ss_layer_{layer_index}")
        return _LC.decode(raw) if raw else {}

    def read_layer_raw(self, layer_index: int) -> str:
        return self.mesh.get(f"ss_layer_{layer_index}", "")

    def read_mask_raw(self, layer_index: int) -> str:
        return self.mesh.get(f"ss_mask_{layer_index}", "")

    def write_layer_dict(self, layer_index: int, layer_dict: dict):
        if layer_index < 0:
            return
        self.mesh[f"ss_layer_{layer_index}"] = _LC.encode(layer_dict)

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

    def real_vg_count(self, obj) -> int:
        return geometry.real_vg_count(obj)

    def known_bone_names(self, id_to_bone: dict, real_count: int) -> set:
        return geometry.known_bone_names(id_to_bone, real_count)

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
