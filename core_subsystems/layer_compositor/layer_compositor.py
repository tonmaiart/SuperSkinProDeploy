"""LayerCompositor -- unified portal for layer metadata CRUD and compositing.

All public operations are classmethods. No instance state is maintained.
External callers (core/ modules) MUST import only ``LayerCompositor`` from
the package __init__.py, never from this file directly.

Responsibilities:
  - Layer metadata CRUD (create, remove, move, duplicate, rename, toggle)
  - Per-layer field accessors (bone locks, bone selection, active bone)
  - Mask gap detection
  - Composite pipeline entry point (delegates to codec._composite_layers)
  - Topology heal entry points (delegates to healer)
  - Merge entry point (delegates to merge.merge_selected)
  - Encode/decode pass-throughs for callers that only need SerDe
  - Vertex-group selection pool helpers (add_vg_selected etc.)
"""

from .codec import encode_layer_dict, decode_layer_dict, _composite_layers
from .healer import heal_layer_dict, heal_mask_dict
from .merge import merge_selected as _merge_selected


class LayerCompositor:
    """Classmethod-only portal; never instantiated."""

    # =========================================================================
    #  Query helpers
    # =========================================================================

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

    # =========================================================================
    #  CRUD
    # =========================================================================

    @classmethod
    def create_layer(cls, meta_list, name):
        """Insert a new layer at the top of the stack.

        Returns:
            ``(new_meta_list, new_index)``
        """
        meta = list(meta_list)
        new_idx = cls._next_index(meta)
        entry = {
            "name": name,
            "index": new_idx,
            "visible": True,
            "bone_locks": {},
            "mask_default": 0.0,
            "bone_selection": ",",
            "active_bone": "",
        }
        meta.insert(0, entry)
        return meta, new_idx

    @classmethod
    def remove_layer(cls, meta_list, index):
        """Remove a layer by slot *index*. At least one layer is always kept."""
        if len(meta_list) <= 1:
            return list(meta_list)
        return [l for l in meta_list if l["index"] != index]

    @classmethod
    def duplicate_layer(cls, meta_list, index):
        """Clone a layer's metadata, inserting the copy above the source.

        Returns:
            ``(new_meta_list, new_index)`` -- caller clones the data blobs.
        """
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
            "bone_selection": src.get("bone_selection", ","),
            "active_bone": src.get("active_bone", ""),
        }
        meta.insert(pos, new_entry)
        return meta, new_idx

    @classmethod
    def move_layer(cls, meta_list, index, direction):
        """Shift a layer one position in display order.

        Args:
            direction: ``-1`` for up (toward top), ``+1`` for down (toward base).

        Returns:
            ``new_meta_list`` (shallow copy). No-op if already at an edge.
        """
        meta = list(meta_list)
        pos = next((i for i, l in enumerate(meta) if l["index"] == index), None)
        if pos is None:
            return meta
        target_pos = pos + direction
        if target_pos < 0 or target_pos >= len(meta):
            return meta
        meta.insert(target_pos, meta.pop(pos))
        return meta

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

    # =========================================================================
    #  Private field-access helpers
    # =========================================================================

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

    # =========================================================================
    #  Per-layer bone locks
    # =========================================================================

    @classmethod
    def get_bone_locks(cls, meta_list, layer_index):
        return cls._get_field(meta_list, layer_index, "bone_locks", {})

    @classmethod
    def set_bone_locks(cls, meta_list, layer_index, bone_locks):
        return cls._set_field(meta_list, layer_index, "bone_locks", dict(bone_locks))

    # =========================================================================
    #  Per-layer bone selection
    # =========================================================================

    @classmethod
    def get_selected_bones(cls, meta_list, layer_index):
        return cls._get_field(meta_list, layer_index, "bone_selection", ",")

    @classmethod
    def set_selected_bones(cls, meta_list, layer_index, selected_names):
        val = str(selected_names) if selected_names else ","
        if not val.startswith(","):
            val = "," + val
        return cls._set_field(meta_list, layer_index, "bone_selection", val)

    # =========================================================================
    #  Per-layer active bone
    # =========================================================================

    @classmethod
    def get_active_bone_name(cls, meta_list, layer_index):
        return cls._get_field(meta_list, layer_index, "active_bone", "")

    @classmethod
    def set_active_bone_name(cls, meta_list, layer_index, name):
        return cls._set_field(
            meta_list, layer_index, "active_bone", str(name) if name else ""
        )

    # =========================================================================
    #  Mask gap detection
    # =========================================================================

    @classmethod
    def find_mask_gaps(cls, meta_list, mask_dicts_map, num_verts) -> list:
        """Return vertex indices where accumulated mask coverage falls below 1.0."""
        opacity_leak = [1.0] * num_verts
        for layer in meta_list:
            if not layer.get("visible", True):
                continue
            l_idx = layer["index"]
            mask_dict = mask_dicts_map.get(l_idx, {})
            mask_default = float(layer.get("mask_default", 1.0))
            for v_idx in range(num_verts):
                v_mask = mask_dict.get(v_idx, mask_default)
                v_mask = max(0.0, min(1.0, v_mask))
                opacity_leak[v_idx] *= (1.0 - v_mask)
        return [v_idx for v_idx, leak in enumerate(opacity_leak) if leak > 0.001]

    # =========================================================================
    #  Compositing and merge entry points
    # =========================================================================

    @classmethod
    def composite_layers(cls, meta_list, layer_data_map, mask_data_map, idx_to_name, num_verts,
                         dirty_verts=None):
        """Composite all visible layers via the native Rust core.

        Args:
            dirty_verts: optional iterable of vertex indices to restrict
                recomputation to. See codec._composite_layers()'s docstring
                for the full contract and the rust_logic rebuild requirement.
        """
        return _composite_layers(meta_list, layer_data_map, mask_data_map, idx_to_name, num_verts,
                                 dirty_verts=dirty_verts)

    @classmethod
    def merge_selected(cls, meta_list, layer_data_map, mask_data_map,
                       selected_indices, target_index, num_verts):
        """Composite selected layers and return merged result + updated metadata."""
        return _merge_selected(
            meta_list, layer_data_map, mask_data_map,
            selected_indices, target_index, num_verts
        )

    # =========================================================================
    #  SerDe pass-throughs (for callers that only need encode/decode)
    # =========================================================================

    @classmethod
    def encode(cls, layer_dict) -> str:
        return encode_layer_dict(layer_dict)

    @classmethod
    def decode(cls, raw) -> dict:
        return decode_layer_dict(raw)

    # =========================================================================
    #  Topology heal entry points
    # =========================================================================

    @classmethod
    def heal_layer_dict(cls, layer_dict, neighbours, num_verts):
        return heal_layer_dict(layer_dict, neighbours, num_verts)

    @classmethod
    def heal_mask_dict(cls, mask_dict, neighbours, num_verts, mask_default):
        return heal_mask_dict(mask_dict, neighbours, num_verts, mask_default)

    # =========================================================================
    #  Vertex-group selection pool helpers (formerly module-level in LayerManager)
    # =========================================================================

    @staticmethod
    def add_vg_selected(obj, name: str) -> bool:
        """Add *name* to the comma-separated selection pool if not already present."""
        storage = obj.superskin_storage
        if not storage.selected_names or not storage.selected_names.startswith(","):
            storage.selected_names = ","
        if f",{name}," not in storage.selected_names:
            storage.selected_names += f"{name},"
            return True
        return False

    @staticmethod
    def remove_vg_selected(obj, name: str) -> bool:
        """Remove *name* from the comma-separated selection pool."""
        storage = obj.superskin_storage
        if f",{name}," in storage.selected_names:
            storage.selected_names = storage.selected_names.replace(f"{name},", "")
            return True
        return False

    @staticmethod
    def clear_all_selected(obj) -> None:
        """Reset the selection pool to the empty sentinel ``","``."""
        obj.superskin_storage.selected_names = ","

    # =========================================================================
    #  Flatten pipeline helpers
    # =========================================================================

    @staticmethod
    def bone_weights_to_deform_state(
        weight_result: dict,
        name_to_idx: dict,
        threshold: float = 0.001,
    ) -> dict:
        """Convert composite layer weights from bone-name keys to VG-index keys.

        The output of ``composite_layers()`` uses string bone names. BMesh deform
        layers require integer vertex-group indices. This function performs that
        remapping and prunes near-zero weights.

        Args:
            weight_result: ``{v_idx: {bone_name: weight}}`` -- raw compositor output.
            name_to_idx: ``{bone_name: vg_index}`` -- only non-temp VGs (i.e. those
                not starting with ``__ssp_``).
            threshold: Weights at or below this value are omitted from the result.

        Returns:
            ``{v_idx: {vg_index: weight}}`` ready for BMesh deform layer writes.
            Vertices whose mapped weights are all below *threshold* are excluded.
        """
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
