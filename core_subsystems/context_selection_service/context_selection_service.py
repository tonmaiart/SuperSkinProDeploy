
from __future__ import annotations


class ContextSelectionService:
    """Stateless evaluator of viewport selection, scene context, and weight state."""

    _undo_restore_in_progress: bool = False


    @staticmethod
    def get_selected_verts(obj, mesh) -> list[int]:
        if obj.mode == 'EDIT':
            import bmesh
            bm = bmesh.from_edit_mesh(mesh)
            bm.verts.ensure_lookup_table()
            sel = [v.index for v in bm.verts if v.select]
            return sel if sel else [v.index for v in bm.verts]
        else:
            import numpy as np
            count = len(mesh.vertices)
            flags = np.zeros(count, dtype=np.bool_)
            attr = mesh.attributes.get(".select_vert")
            if attr is not None:
                if attr.data_type == 'BOOLEAN' and attr.domain == 'POINT':
                    attr.data.foreach_get("value", flags)
                else:
                    mesh.vertices.foreach_get("select", flags)
            selected = np.flatnonzero(flags).tolist()
            return selected if selected else list(range(count))


    @staticmethod
    def is_mask_context(scene) -> bool:
        try:
            return bool(getattr(scene, "superskin_is_mask_mode", False))
        except Exception:
            return False


    @classmethod
    def is_undo_restore_in_progress(cls) -> bool:
        return cls._undo_restore_in_progress

    @classmethod
    def set_undo_restore_in_progress(cls, value: bool) -> None:
        cls._undo_restore_in_progress = value

    @classmethod
    def reset_undo_flag(cls) -> None:
        cls._undo_restore_in_progress = False


    @staticmethod
    def normalize_weights(
        layer_dict: dict,
        vertex_index: int,
        active_vg_name: str,
        vg_names: list[str],
        bone_locks: dict[str, bool],
        is_mask: bool,
    ) -> dict:
        if is_mask:
            return layer_dict

        v_weights = layer_dict.get(vertex_index, {})

        has_other_weights = any(
            w > 0.001 for b_name, w in v_weights.items() if b_name != active_vg_name
        )
        if not has_other_weights:
            v_weights[active_vg_name] = 1.0
            v_weights = {k: v for k, v in v_weights.items() if v >= 0.0001}
            layer_dict[vertex_index] = v_weights
            return layer_dict

        lock_total = 0.0
        other_total = 0.0
        active_weight = v_weights.get(active_vg_name, 0.0)
        unlocked_bones: list[tuple[str, float]] = []

        for b_name in vg_names:
            w = v_weights.get(b_name, 0.0)
            if b_name == active_vg_name:
                continue
            if bone_locks.get(b_name, False):
                lock_total += w
            else:
                unlocked_bones.append((b_name, w))
                other_total += w

        lock_total = min(1.0, lock_total)
        max_allowed_active = max(0.0, 1.0 - lock_total)

        if active_weight > max_allowed_active:
            active_weight = max_allowed_active

        v_weights[active_vg_name] = active_weight
        remaining_weight = max(0.0, 1.0 - lock_total - active_weight)

        if not unlocked_bones:
            v_weights[active_vg_name] = max_allowed_active
            v_weights = {k: v for k, v in v_weights.items() if v >= 0.0001}
            layer_dict[vertex_index] = v_weights
            return layer_dict

        if remaining_weight <= 0.00001:
            for b_name, _ in unlocked_bones:
                v_weights[b_name] = 0.0
            v_weights[active_vg_name] = max_allowed_active
            v_weights = {k: v for k, v in v_weights.items() if v >= 0.0001}
            layer_dict[vertex_index] = v_weights
            return layer_dict

        if other_total > 0:
            inv_other = 1.0 / other_total
            for b_name, w in unlocked_bones:
                v_weights[b_name] = w * inv_other * remaining_weight
        else:
            v_weights[active_vg_name] = max_allowed_active
            for b_name, _ in unlocked_bones:
                v_weights[b_name] = 0.0

        v_weights = {k: v for k, v in v_weights.items() if v >= 0.0001}
        layer_dict[vertex_index] = v_weights
        return layer_dict
