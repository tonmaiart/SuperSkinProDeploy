"""LayerStorageService — single authority for reading and writing
SuperSkinPro layer and mask data to Blender mesh custom properties.

No other module should access mesh custom property keys (``ss_layer_*``,
``ss_mask_*``, ``ss_layers_meta``, ``ss_active_layer``) directly.
All reads and writes MUST route through this class.
"""

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

    # ── Active Layer Index ───────────────────────────────────────────

    def get_active_layer_index(self) -> int:
        return self.mesh.get("ss_active_layer", 0)

    def set_active_layer_index(self, index: int):
        self.mesh["ss_active_layer"] = index

    # ── System Initialisation Detection ──────────────────────────────

    def has_layer_system(self) -> bool:
        """Return True if the mesh has already been initialised."""
        return "ss_layers_meta" in self.mesh

    def remove_layer_system(self):
        """Tear down the layer system entirely: deletes every layer's weight
        and mask blob, the metadata stack, the active-layer pointer, and the
        bone UUID map. Does not touch the mesh's real Vertex Group deform
        weights -- those were already flattened onto the mesh and remain
        exactly as last baked, same as a mesh that never had SuperSkinPro's
        layer system initialised on it."""
        for layer in self.read_meta_list():
            idx = layer["index"]
            self.delete_layer_property(idx)
            self.delete_mask_property(idx)
        for key in ("ss_layers_meta", "ss_active_layer", "ss_bone_uuid_map"):
            if key in self.mesh:
                del self.mesh[key]

    # ── Metadata Stack ───────────────────────────────────────────────

    def read_meta_list(self) -> list:
        return json.loads(self.mesh.get("ss_layers_meta", "[]"))

    def write_meta_list(self, meta_list: list):
        self.mesh["ss_layers_meta"] = json.dumps(meta_list)

    # ── Bone UUID Map (stable identity ↔ last-known vertex-group name) ──

    def read_bone_uuid_map(self) -> dict:
        return json.loads(self.mesh.get("ss_bone_uuid_map", "{}"))

    def write_bone_uuid_map(self, uuid_map: dict):
        self.mesh["ss_bone_uuid_map"] = json.dumps(uuid_map)

    # ── Layer Dict (Weights) CRUD ────────────────────────────────────

    def read_active_layer_dict(self) -> dict:
        idx = self.get_active_layer_index()
        raw = self.mesh.get(f"ss_layer_{idx}")
        return _LC.decode(raw) if raw else {}

    def read_layer_dict(self, layer_index: int) -> dict:
        """Decode the weight dictionary for *layer_index*."""
        raw = self.mesh.get(f"ss_layer_{layer_index}")
        return _LC.decode(raw) if raw else {}

    def read_layer_raw(self, layer_index: int) -> str:
        """Return the raw encoded string for *layer_index* or ``""``."""
        return self.mesh.get(f"ss_layer_{layer_index}", "")

    def read_mask_raw(self, layer_index: int) -> str:
        """Return the raw encoded string for *layer_index*'s mask or ``""``."""
        return self.mesh.get(f"ss_mask_{layer_index}", "")

    def write_layer_dict(self, layer_index: int, layer_dict: dict):
        self.mesh[f"ss_layer_{layer_index}"] = _LC.encode(layer_dict)

    def delete_layer_property(self, layer_index: int):
        key = f"ss_layer_{layer_index}"
        if key in self.mesh:
            del self.mesh[key]

    def clone_layer_properties(self, src_index: int, dst_index: int):
        """Copy weight and mask blobs from *src_index* to *dst_index*."""
        src_key = f"ss_layer_{src_index}"
        if src_key in self.mesh:
            self.mesh[f"ss_layer_{dst_index}"] = self.mesh[src_key]
        else:
            self.mesh[f"ss_layer_{dst_index}"] = _LC.encode({})

        src_mask = f"ss_mask_{src_index}"
        if src_mask in self.mesh:
            self.mesh[f"ss_mask_{dst_index}"] = self.mesh[src_mask]

    # ── Mask Dict CRUD ───────────────────────────────────────────────

    @staticmethod
    def _normalize_mask_entries(raw: dict) -> dict:
        """Normalise legacy mask formats to clean ``{v_idx: float}``.

        Handles legacy ``{v: {bone: weight}}`` dict values, current
        ``{v: float}``, and unknown types (defaults to 0.0).
        """
        norm = {}
        for v, w in raw.items():
            if isinstance(w, dict):
                norm[int(v)] = next(iter(w.values()), 0.0) if w else 0.0
            elif isinstance(w, (int, float)):
                norm[int(v)] = float(w)
            else:
                norm[int(v)] = 0.0
        return norm

    def read_active_mask_dict(self) -> dict:
        idx = self.get_active_layer_index()
        raw = self.mesh.get(f"ss_mask_{idx}")
        mask = _LC.decode(raw) if raw else {}
        return self._normalize_mask_entries(mask)

    def read_mask_dict(self, layer_index: int) -> dict:
        """Decode and normalise the mask for *layer_index* to ``{v_idx: float}``."""
        raw = self.mesh.get(f"ss_mask_{layer_index}")
        mask = _LC.decode(raw) if raw else {}
        return self._normalize_mask_entries(mask)

    def write_mask_dict(self, layer_index: int, mask_dict: dict):
        self.mesh[f"ss_mask_{layer_index}"] = _LC.encode(mask_dict)

    def delete_mask_property(self, layer_index: int):
        key = f"ss_mask_{layer_index}"
        if key in self.mesh:
            del self.mesh[key]

    # ── Save helper (combined layer + mask for active layer) ─────────

    def save_active(self, layer_dict: dict, mask_dict: dict = None, *,
                    is_mask_mode: bool = False):
        """Encode and write to the active layer slot.

        When *is_mask_mode* is True and *mask_dict* is provided, writes
        the mask; otherwise writes the layer dict.
        """
        idx = self.get_active_layer_index()
        if is_mask_mode and mask_dict is not None:
            self.write_mask_dict(idx, mask_dict)
        else:
            self.write_layer_dict(idx, layer_dict)

    # ── Map Harvesting for Renderer / Compositor ─────────────────────

    def harvest_layer_data_map(self) -> dict:
        """Returns ``{layer_index: raw_encoded_str}`` for every existing layer."""
        result = {}
        for layer in self.read_meta_list():
            l_idx = layer["index"]
            raw = self.mesh.get(f"ss_layer_{l_idx}")
            if raw:
                result[l_idx] = raw
        return result

    def harvest_mask_data_map(self) -> dict:
        """Returns ``{layer_index: raw_encoded_str}`` for every existing mask."""
        result = {}
        for layer in self.read_meta_list():
            l_idx = layer["index"]
            raw = self.mesh.get(f"ss_mask_{l_idx}")
            if raw:
                result[l_idx] = raw
        return result

    # ── Geometry helpers (thin wrappers around geometry.py) ──────────

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

    # ── Layer 0 initialisation from live weights ─────────────────────

    def init_layer_0_from_live_weights(self, obj, vg_indices=None):
        """Initialise the base layer (index 0) from the mesh's current
        real Vertex Group weights. Used once, by init_layer_system()."""
        layer_dict = live_snapshot.harvest_live_weights(obj, vg_indices)
        self.write_layer_dict(0, layer_dict)

    # ── Flatten & Heal (thin delegators to sibling modules) ──────────

    def flatten_visible_layers_to_mesh(self, obj):
        return flatten.flatten_visible_layers_to_mesh(self, obj)

    def heal_new_vertices(self, obj):
        return topology_heal.heal_new_vertices_storage(self, obj)
