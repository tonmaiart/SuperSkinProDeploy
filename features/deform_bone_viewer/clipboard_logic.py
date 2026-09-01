# Copyright (c) 2026 Natchapon Srisuk. All rights reserved.
"""Deform Bones List clipboard logic -- "Plane Copy" ported out of the
former features/clipboard Plane Copy tab (see docs/domains/clipboard.md's
bug history) and split into two independent, fixed-target clipboard slots
instead of one context-driven ("auto-detect Bone vs Mask") slot behind a
tab switch:

  - ``bone_weight_clipboard`` always copies/pastes the active Vertex
    Group's weight.
  - ``layer_weight_clipboard`` always copies/pastes the active layer's
    mask.

Because each slot's target is fixed rather than auto-detected, there is no
cross-context (WEIGHT<->MASK) conversion here -- unlike the old Plane Copy
tab, a clip copied by one slot can only ever be pasted by that same slot.

Each capture is dense across every vertex of the mesh (not just vertices
that already carry data) -- required for the paste-time "Pair, N-to-N,
same mesh" resolution case in
core_subsystems/layer_compositor/data_operations.py to identity-map
vertices correctly; see docs/domains/deform_bone_viewer.md's "Why Plane
Copy must be dense" for the full bug history this fixed.

Zero Cross-Imports: this module is deform_bone_viewer's own copy of the
logic, not an import from features/clipboard -- feature domains may not
import each other's modules (see CLAUDE.md's "Zero Cross-Imports" rule).
"""
from __future__ import annotations
from ...core.facade import CoreFacade

data_ops = CoreFacade.get_clipboard_data_ops()


class _PlaneClipboard:
    """One independent copy/paste slot dedicated to a single, fixed target
    (Bone Weight or Layer Weight) -- always one scalar per vertex, dense
    across the whole mesh."""

    def __init__(self, is_mask: bool):
        self._is_mask = is_mask
        self._clip = None  # {"data": {str(v): float}, "source_mesh": str}

    def has_clipboard(self) -> bool:
        return self._clip is not None and bool(self._clip.get("data"))

    def copy(self, ctrl: CoreFacade) -> None:
        mesh = ctrl.get_mesh()
        vertex_count = len(mesh.vertices)

        if self._is_mask:
            mask_dict = ctrl.get_active_mask_dict()
            data = {str(v): float(mask_dict.get(v, mask_dict.get(str(v), 0.0))) for v in range(vertex_count)}
        else:
            active_bone_name = ctrl.get_active_vg_name()
            if not active_bone_name:
                raise ValueError("No active Vertex Group selected")
            layer_dict = ctrl.read_active_layer()
            data = {}
            for v in range(vertex_count):
                weights = layer_dict.get(v, layer_dict.get(str(v), {}))
                data[str(v)] = float(weights.get(active_bone_name, 0.0))

        self._clip = {"data": data, "source_mesh": mesh.name}

    def paste(self, ctrl: CoreFacade, mode: str = 'REPLACE') -> None:
        if not self.has_clipboard():
            raise ValueError("Clipboard is empty — copy first")

        clip = self._clip
        clip_data = clip["data"]
        source_mesh_name = clip["source_mesh"]

        mesh = ctrl.get_mesh()
        all_verts_count = len(mesh.vertices)
        selected_targets = ctrl.get_selected_verts()
        target_verts = selected_targets if len(selected_targets) > 0 else list(range(all_verts_count))
        current_mesh_name = mesh.name

        if self._is_mask:
            resolved = data_ops.resolve_paste_targets_mask(clip_data, target_verts, source_mesh_name, current_mesh_name)
            _merge_mask_paste(ctrl, resolved, mode)
        else:
            active_bone_name = ctrl.get_active_vg_name()
            if not active_bone_name:
                raise ValueError("No active Vertex Group selected")
            # Re-key the flat scalar clip onto whichever VG is active NOW,
            # not the bone that was active at copy time -- lets "copy Bone
            # A, switch to Bone B, paste" work as a starting-point workflow.
            weight_data = {v: {active_bone_name: w} for v, w in clip_data.items()}
            resolved = data_ops.resolve_paste_targets_weight(weight_data, target_verts, source_mesh_name, current_mesh_name)
            _merge_weight_paste(ctrl, resolved, mode)

        ctrl.finish(color_only=False)


def _merge_weight_paste(ctrl: CoreFacade, resolved: dict, mode: str):
    """This clipboard is scoped to a single VG's channel, not the whole
    vertex -- REPLACE only overwrites the one bone present in the pasted
    data, leaving every other bone's weight at that vertex untouched."""
    layer_dict = {int(k): v for k, v in ctrl.read_active_layer().items()}
    for v_int, bone_weights in resolved.items():
        if v_int not in layer_dict:
            layer_dict[v_int] = {}
        for bone_name, w in bone_weights.items():
            if mode == 'REPLACE':
                layer_dict[v_int][bone_name] = float(w)
            else:
                current = layer_dict[v_int].get(bone_name, 0.0)
                if mode == 'ADD':
                    layer_dict[v_int][bone_name] = min(1.0, current + float(w))
                elif mode == 'SUBTRACT':
                    layer_dict[v_int][bone_name] = max(0.0, current - float(w))
    ctrl.write_active_layer(layer_dict, color_only=False)


def _merge_mask_paste(ctrl: CoreFacade, resolved: dict, mode: str):
    mask_dict = {int(k): v for k, v in ctrl.get_active_mask_dict().items()}
    for v_int, val in resolved.items():
        if mode == 'REPLACE':
            mask_dict[v_int] = float(val)
        else:
            current = float(mask_dict.get(v_int, 0.0))
            if mode == 'ADD':
                mask_dict[v_int] = min(1.0, current + float(val))
            elif mode == 'SUBTRACT':
                mask_dict[v_int] = max(0.0, current - float(val))
    ctrl.write_mask_dict(mask_dict)


bone_weight_clipboard = _PlaneClipboard(is_mask=False)
layer_weight_clipboard = _PlaneClipboard(is_mask=True)
