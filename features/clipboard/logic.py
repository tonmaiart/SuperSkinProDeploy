# Copyright (c) 2026 Natchapon Srisuk. All rights reserved.
from __future__ import annotations
from ...core.facade import CoreFacade as _CoreFacade
data_ops = _CoreFacade.get_clipboard_data_ops()


# ═════════════════════════════════════════════════════════════════════════
#  Shared Clipboard Manager
# ═════════════════════════════════════════════════════════════════════════

class ClipboardManager:
    def __init__(self):
        self._clip = None

    def has_clipboard(self) -> bool:
        return self._clip is not None and "data" in self._clip and bool(self._clip["data"])

    def get_clipboard(self) -> dict:
        if not self.has_clipboard():
            raise ValueError("Clipboard is empty — copy or cut first")
        return self._clip

    def set_clipboard(self, kind: str, data: dict, source_mesh: str):
        self._clip = {"kind": kind, "data": data, "source_mesh": source_mesh}

    def clear_clipboard(self):
        self._clip = None

_clipboard_manager = ClipboardManager()


# ═════════════════════════════════════════════════════════════════════════
#  Shared implementation functions
# ═════════════════════════════════════════════════════════════════════════

def _copy_impl(ctrl, is_mask: bool) -> dict:
    """Copy the active layer or mask to the clipboard, returning the
    captured data. Whole-mesh fallback (Rule 0) when nothing is selected;
    otherwise a subset of just the selected vertices' full per-vertex data
    (all bones' weights for WEIGHT, matching "Vertex Influence Copy"'s
    single-vertex full-snapshot semantics -- see ``copy_single_vertex()``).
    """
    clip_mgr = _clipboard_manager
    selected = ctrl.get_selected_verts()
    all_verts_count = len(ctrl.mesh.vertices)

    if len(selected) == 0 or len(selected) == all_verts_count:
        if is_mask:
            mask_dict = ctrl.get_active_mask_dict()
            subset = {str(k): v for k, v in mask_dict.items()}
        else:
            layer_dict = ctrl.read_active_layer()
            subset = {str(k): dict(w) for k, w in layer_dict.items()}
    else:
        if is_mask:
            mask_dict = ctrl.get_active_mask_dict()
            subset = data_ops.extract_mask_subset(mask_dict, selected)
        else:
            layer_dict = ctrl.read_active_layer()
            subset = data_ops.extract_weight_subset(layer_dict, selected)

    if not subset:
        raise ValueError("Nothing to copy — selected vertices have no data.")

    kind = 'MASK' if is_mask else 'WEIGHT'
    clip_mgr.set_clipboard(kind, subset, ctrl.mesh.name)
    return clip_mgr.get_clipboard()


def _cut_impl(ctrl, is_mask: bool) -> dict:
    """Copy to clipboard, then clear source data from the active layer/mask."""
    clip = _copy_impl(ctrl, is_mask)
    selected = ctrl.get_selected_verts()
    all_verts_count = len(ctrl.mesh.vertices)

    if len(selected) == 0 or len(selected) == all_verts_count:
        if is_mask:
            ctrl.write_mask_dict({})
        else:
            ctrl.write_active_layer({}, color_only=False)
    else:
        sel_set = {int(v) for v in selected}
        if is_mask:
            mask_dict = ctrl.get_active_mask_dict()
            remaining = {int(k): w for k, w in mask_dict.items() if int(k) not in sel_set}
            ctrl.write_mask_dict(remaining)
        else:
            layer_dict = ctrl.read_active_layer()
            remaining = {int(k): dict(w) for k, w in layer_dict.items() if int(k) not in sel_set}
            ctrl.write_active_layer(remaining, color_only=False)

    ctrl._finish(color_only=False)
    return clip


def _paste_impl(ctrl, mode: str, is_mask_target: bool) -> dict:
    """Paste clipboard data into the active layer or mask, using the given
    mode. Every clip this domain produces is a Vertex Influence Copy (one
    vertex's full multi-bone snapshot) -- the former Plane Copy clip kind
    has moved to features/deform_bone_viewer, see
    docs/domains/deform_bone_viewer.md."""
    clip_mgr = _clipboard_manager
    if not clip_mgr.has_clipboard():
        raise ValueError("Clipboard is empty — copy or cut first")

    clip = clip_mgr.get_clipboard()
    clip_data = clip["data"]
    clip_kind = clip["kind"]
    source_mesh_name = clip["source_mesh"]

    all_verts_count = len(ctrl.mesh.vertices)
    selected_targets = ctrl.get_selected_verts()
    target_verts = selected_targets if len(selected_targets) > 0 else list(range(all_verts_count))

    target_vg_names = {vg.name for vg in ctrl.obj.vertex_groups}
    ok, reason = data_ops.validate_bone_compatibility(clip_data, target_vg_names, clip_kind)
    if not ok:
        raise ValueError(reason)

    paste_kind = clip_kind
    paste_data = clip_data

    if clip_kind == 'WEIGHT' and is_mask_target:
        # A Vertex Influence Copy snapshot can hold several bones' weights
        # for that one vertex -- collapsing that down to a single mask
        # scalar by picking "whichever bone happens to be active right
        # now" would silently discard/misrepresent data. Only a
        # mask-sourced clip may paste onto a mask.
        raise ValueError(
            "Cannot paste a Vertex Influence Copy of bone weights onto a "
            "mask — copy from the Mask row instead."
        )
    elif clip_kind == 'MASK' and not is_mask_target:
        paste_kind = 'WEIGHT'
        paste_data = _convert_mask_to_weight(paste_data, ctrl)

    current_mesh_name = ctrl.mesh.name
    if paste_kind == 'WEIGHT':
        resolved = data_ops.resolve_paste_targets_weight(paste_data, target_verts, source_mesh_name, current_mesh_name)
        _merge_weight_paste(ctrl, resolved, mode)
    else:
        resolved = data_ops.resolve_paste_targets_mask(paste_data, target_verts, source_mesh_name, current_mesh_name)
        _merge_mask_paste(ctrl, resolved, mode)

    ctrl._finish(color_only=False)
    return {"status": "FINISHED"}


# ═════════════════════════════════════════════════════════════════════════
#  Public API — Automatic Pipeline (context-driven)
# ═════════════════════════════════════════════════════════════════════════

def copy(ctrl) -> dict:
    """Copy active layer or mask data based on UI context."""
    return _copy_impl(ctrl, ctrl._is_mask_context())


def cut(ctrl) -> dict:
    """Cut active layer or mask data based on UI context."""
    return _cut_impl(ctrl, ctrl._is_mask_context())


def paste(ctrl, mode: str = 'REPLACE') -> dict:
    """Paste clipboard data into the target determined by UI context."""
    return _paste_impl(ctrl, mode, ctrl._is_mask_context())


def copy_single_vertex(ctrl) -> dict:
    """"Vertex Influence Copy" — capture exactly one selected vertex's
    influence. Refuses (raises) unless the selection is exactly one vertex,
    so a stray multi-vertex or empty selection never silently captures the
    wrong thing."""
    selected = ctrl.get_selected_verts()
    if len(selected) != 1:
        raise ValueError("Select exactly one vertex to copy its influence")
    return _copy_impl(ctrl, ctrl._is_mask_context())


# ═════════════════════════════════════════════════════════════════════════
#  Selection helpers
# ═════════════════════════════════════════════════════════════════════════

def select_affected(ctrl) -> set:
    if ctrl._is_mask_context():
        mask_dict = ctrl.get_active_mask_dict()
        return data_ops.vertices_with_mask_override(mask_dict)
    active_id = ctrl._active_vg_id()
    if active_id is None:
        raise ValueError("No active Vertex Group selected")
    active_name = ctrl.obj.vertex_groups[active_id].name
    layer_dict = ctrl.read_active_layer()
    return data_ops.vertices_with_weight(layer_dict, active_name)


# ═════════════════════════════════════════════════════════════════════════
#  Conversion helpers
# ═════════════════════════════════════════════════════════════════════════

def _convert_mask_to_weight(mask_data: dict, ctrl) -> dict:
    active_vg_id = ctrl._active_vg_id()
    if active_vg_id is None:
        raise ValueError("No active Vertex Group selected for conversion")
    id_to_name = ctrl._idx_to_name()
    active_bone_name = id_to_name.get(active_vg_id, "")
    result: dict = {}
    for v, val in mask_data.items():
        result[v] = {active_bone_name: float(val)}
    return result


# ═════════════════════════════════════════════════════════════════════════
#  Merge helpers
# ═════════════════════════════════════════════════════════════════════════

def _merge_weight_paste(ctrl, resolved: dict[int, dict[str, float]], mode: str = 'REPLACE'):
    """REPLACE wipes each target vertex's ENTIRE bone dict down to just
    what's in the pasted snapshot -- correct for "stamp this vertex's full
    influence onto other vertices" (the only clip kind this domain
    produces now; the former Plane Copy's narrower single-VG-channel
    REPLACE semantics moved to features/deform_bone_viewer along with it).
    """
    layer_dict = {int(k): v for k, v in ctrl.read_active_layer().items()}
    for v_int, bone_weights in resolved.items():
        if mode == 'REPLACE':
            layer_dict[v_int] = {bone_name: float(w) for bone_name, w in bone_weights.items()}
        else:
            if v_int not in layer_dict: layer_dict[v_int] = {}
            existing = layer_dict[v_int]
            for bone_name, w in bone_weights.items():
                current = existing.get(bone_name, 0.0)
                if mode == 'ADD': existing[bone_name] = min(1.0, current + float(w))
                elif mode == 'SUBTRACT': existing[bone_name] = max(0.0, current - float(w))
    ctrl.write_active_layer(layer_dict, color_only=False)

def _merge_mask_paste(ctrl, resolved: dict[int, float], mode: str = 'REPLACE'):
    mask_dict = {int(k): v for k, v in ctrl.get_active_mask_dict().items()}
    for v_int, val in resolved.items():
        if mode == 'REPLACE':
            mask_dict[v_int] = float(val)
        else:
            current = float(mask_dict.get(v_int, 0.0))
            if mode == 'ADD': mask_dict[v_int] = min(1.0, current + float(val))
            elif mode == 'SUBTRACT': mask_dict[v_int] = max(0.0, current - float(val))
    ctrl.write_mask_dict(mask_dict)
