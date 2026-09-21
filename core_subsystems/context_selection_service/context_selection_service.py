"""ContextSelectionService -- stateless viewport context, selection evaluation,
undo-state flag, and per-vertex weight normalisation.

All methods are static or classmethods; no Blender context object is held.
Callers supply the specific bpy.types.* objects needed for each query.

Absorbs three previously scattered subsystems:
  - context_service.py   (get_selected_verts, is_mask_context)
  - undo_manager.py      (is_undo_restore_in_progress, set_undo_restore_in_progress)
  - weight_ops.py        (normalize_weights)

Invariant: this module must not call bpy.context, register handlers, or invoke
bpy.ops. It may accept bpy.types.* parameters and use bpy.types for type hints.
"""

from __future__ import annotations


class ContextSelectionService:
    """Stateless evaluator of viewport selection, scene context, and weight state."""

    # Class-level flag for undo-restore detection.
    # Written exclusively by core/ui_controller/undo_manager.py handlers.
    _undo_restore_in_progress: bool = False

    # =========================================================================
    #  Viewport selection
    # =========================================================================

    @staticmethod
    def get_selected_verts(obj, mesh) -> list[int]:
        """Return selected vertex indices, respecting the current editor mode.

        In EDIT mode, reads live selection from the active BMesh so that
        unsaved edits are visible immediately. In OBJECT/WEIGHT_PAINT mode,
        reads from the mesh-data .select flag.

        Falls back to all vertices when nothing is explicitly selected.

        Args:
            obj:  bpy.types.Object -- used for mode detection.
            mesh: bpy.types.Mesh  -- used for vertex access in non-EDIT modes.

        Returns:
            List of selected (or all, if none selected) vertex indices.
        """
        if obj.mode == 'EDIT':
            import bmesh
            bm = bmesh.from_edit_mesh(mesh)
            bm.verts.ensure_lookup_table()
            sel = [v.index for v in bm.verts if v.select]
            return sel if sel else [v.index for v in bm.verts]
        else:
            selected = [v.index for v in mesh.vertices if v.select]
            return selected if selected else [v.index for v in mesh.vertices]

    # =========================================================================
    #  Mask context detection
    # =========================================================================

    @staticmethod
    def is_mask_context(scene) -> bool:
        """Return True if the UI is currently in a mask-painting context.

        Checks the mask-mode flag (superskin_is_mask_mode), which is written
        as a derived side effect by core/ui_controller/layer_crud.py's
        apply_active_bone() every time the Deform Bones list's active row
        changes (see docs/bug-history/0003 for the tri-state selection
        pattern this flag is derived from).

        Args:
            scene: bpy.types.Scene -- the scene carrying the flag property.
        """
        try:
            return bool(getattr(scene, "superskin_is_mask_mode", False))
        except Exception:
            return False

    # =========================================================================
    #  Undo-restore state flag
    # =========================================================================

    @classmethod
    def is_undo_restore_in_progress(cls) -> bool:
        """Return True if a Blender undo/redo restore is currently in progress."""
        return cls._undo_restore_in_progress

    @classmethod
    def set_undo_restore_in_progress(cls, value: bool) -> None:
        """Set the undo-restore-in-progress flag.

        Called exclusively by core/ui_controller/undo_manager.py handlers.
        """
        cls._undo_restore_in_progress = value

    @classmethod
    def reset_undo_flag(cls) -> None:
        """Reset the undo flag to False. Called during unregister."""
        cls._undo_restore_in_progress = False

    # =========================================================================
    #  Per-vertex weight normalisation
    # =========================================================================

    @staticmethod
    def normalize_weights(
        layer_dict: dict,
        vertex_index: int,
        active_vg_name: str,
        vg_names: list[str],
        bone_locks: dict[str, bool],
        is_mask: bool,
    ) -> dict:
        """Normalise per-vertex weights so the sum of all unlocked bones equals 1.0.

        Operates on a single vertex within *layer_dict*, redistributing the slack
        from the active bone's change proportionally across other unlocked bones
        while respecting bone-lock constraints.

        Args:
            layer_dict: Full layer weight dict ``{v_idx: {bone_name: weight}}``.
                Modified in-place; also returned for chaining.
            vertex_index: Index of the vertex to normalise.
            active_vg_name: Name of the bone/vertex-group that was just painted.
            vg_names: Ordered list of all vertex-group names (stable iteration order).
            bone_locks: ``{bone_name: bool}`` -- locked bones excluded from redistribution.
            is_mask: When ``True``, returns *layer_dict* unchanged (mask weights
                are scalars, not subject to normalisation).

        Returns:
            *layer_dict* with the entry at *vertex_index* normalised.
        """
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
