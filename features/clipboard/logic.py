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

    def set_clipboard(self, kind: str, data: dict, source_mesh: str, origin: str = 'VERTEX'):
        """*origin* is ``'PLANE'`` (whole-VG/mask, one scalar per vertex,
        always exactly one bone key when ``kind == 'WEIGHT'``) or
        ``'VERTEX'`` (a single vertex's full multi-bone influence
        snapshot). Not passed to ``validate_bone_compatibility``/
        ``resolve_paste_targets_*`` (data_operations.py only knows
        ``'WEIGHT'``/``'MASK'`` for *kind*) -- purely local metadata
        ``_paste_impl`` uses to decide whether a WEIGHT clip is allowed to
        convert into a MASK target."""
        self._clip = {"kind": kind, "data": data, "source_mesh": source_mesh, "origin": origin}

    def clear_clipboard(self):
        self._clip = None

_clipboard_manager = ClipboardManager()


# ═════════════════════════════════════════════════════════════════════════
#  Shared implementation functions
# ═════════════════════════════════════════════════════════════════════════

def _copy_plane_impl(ctrl, is_mask: bool) -> dict:
    """"Plane Copy" — capture ONE scalar per vertex, densely across every
    vertex of the mesh (not just vertices that happen to already carry
    data), ignoring the current selection entirely.

    Dense (full ``range(vertex_count)``) rather than sparse is required for
    correctness, not just faithfulness: pasting back onto the whole mesh
    with nothing selected resolves via data_operations.py's "Pair — N to N,
    same mesh" case, which needs ``len(clip_data) == len(target_verts)`` to
    identity-map correctly. A sparse capture (only vertices with existing
    weight/mask data) almost never equals the full mesh vertex count, so
    that case fell through to "N-to-M mismatch" and silently canceled --
    the reported "copy/paste plane not sticking" bug.

    WEIGHT clips are still wrapped as ``{v: {bone_name: w}}`` (single key)
    rather than a bare ``{v: w}`` -- data_operations.py's
    ``validate_bone_compatibility``/``resolve_paste_targets_weight``/
    ``_merge_weight_paste`` below all already expect that shape for
    ``kind == 'WEIGHT'`` and handle a single-bone-key dict correctly
    (they only ever touch bones actually present in the pasted data), so
    this reuses that existing, working machinery instead of adding a new
    clipboard kind.
    """
    clip_mgr = _clipboard_manager
    vertex_count = len(ctrl.mesh.vertices)

    if is_mask:
        mask_dict = ctrl.get_active_mask_dict()
        subset = {str(v): float(mask_dict.get(v, mask_dict.get(str(v), 0.0))) for v in range(vertex_count)}
        kind = 'MASK'
    else:
        active_vg_id = ctrl._active_vg_id()
        if active_vg_id is None:
            raise ValueError("No active Vertex Group selected")
        active_bone_name = ctrl._idx_to_name().get(active_vg_id, "")
        layer_dict = ctrl.read_active_layer()
        subset = {}
        for v in range(vertex_count):
            weights = layer_dict.get(v, layer_dict.get(str(v), {}))
            subset[str(v)] = {active_bone_name: float(weights.get(active_bone_name, 0.0))}
        kind = 'WEIGHT'

    clip_mgr.set_clipboard(kind, subset, ctrl.mesh.name, origin='PLANE')
    return clip_mgr.get_clipboard()


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
    clip_mgr.set_clipboard(kind, subset, ctrl.mesh.name, origin='VERTEX')
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
    """Paste clipboard data into the active layer or mask, using the given mode."""
    clip_mgr = _clipboard_manager
    if not clip_mgr.has_clipboard():
        raise ValueError("Clipboard is empty — copy or cut first")

    clip = clip_mgr.get_clipboard()
    clip_data = clip["data"]
    clip_kind = clip["kind"]
    clip_origin = clip.get("origin", "VERTEX")
    source_mesh_name = clip["source_mesh"]

    all_verts_count = len(ctrl.mesh.vertices)
    selected_targets = ctrl.get_selected_verts()
    target_verts = selected_targets if len(selected_targets) > 0 else list(range(all_verts_count))

    from ...core.facade import CoreFacade as _DebugFacade
    _debug_active_bone = ctrl._idx_to_name().get(ctrl._active_vg_id())
    _debug_clip_bones = sorted({b for w in clip_data.values() for b in w.keys()}) if clip_kind == 'WEIGHT' else 'n/a'
    _DebugFacade.debug_log(
        "adhoc:clipboard_plane",
        "_paste_impl() ENTRY: mode=%r is_mask_target=%r clip_kind=%r clip_origin=%r "
        "active_bone_at_paste_time=%r bone_names_in_clip=%r" % (
            mode, is_mask_target, clip_kind, clip_origin, _debug_active_bone, _debug_clip_bones,
        ),
    )

    is_plane_weight = (clip_kind == 'WEIGHT' and clip_origin == 'PLANE')

    target_vg_names = {vg.name for vg in ctrl.obj.vertex_groups}
    if not is_plane_weight:
        # Plane Copy's own bone name (baked in at copy time) is about to be
        # discarded and re-targeted onto whichever VG is active now (below)
        # regardless of what it's named, so checking it against the target
        # mesh's VG names here would be checking the wrong thing -- e.g. it
        # would wrongly block "copy Bone A, paste onto Bone B" whenever the
        # target mesh doesn't happen to also have a VG literally named
        # "Bone A".
        ok, reason = data_ops.validate_bone_compatibility(clip_data, target_vg_names, clip_kind)
        if not ok:
            raise ValueError(reason)

    paste_kind = clip_kind
    paste_data = clip_data

    # Only a genuine multi-bone Vertex Influence snapshot should wipe a
    # target vertex's other bones on REPLACE -- a mask-derived weight dict
    # (converted below) is always single-channel regardless of clip_origin,
    # so it must merge in place like Plane Copy does. Computed here, before
    # paste_kind/clip_kind possibly get converted below.
    full_vertex_replace = (clip_kind == 'WEIGHT' and clip_origin == 'VERTEX')

    if clip_kind == 'WEIGHT' and is_mask_target:
        if clip_origin == 'VERTEX':
            # A Vertex Influence Copy snapshot can hold several bones'
            # weights for that one vertex -- collapsing that down to a
            # single mask scalar by picking "whichever bone happens to be
            # active right now" would silently discard/misrepresent data.
            # Only a mask-sourced clip may paste onto a mask.
            raise ValueError(
                "Cannot paste a Vertex Influence Copy of bone weights onto a "
                "mask — copy from the Mask row instead."
            )
        paste_kind = 'MASK'
        paste_data = _convert_plane_weight_to_mask(paste_data)
    elif clip_kind == 'MASK' and not is_mask_target:
        paste_kind = 'WEIGHT'
        paste_data = _convert_mask_to_weight(paste_data, ctrl)
    elif is_plane_weight and not is_mask_target:
        # THE FIX: staying in weight context, a Plane Copy clip must still
        # be re-targeted onto whichever VG is active NOW -- without this,
        # the clip's bone name from copy time was used verbatim, so
        # "copy Bone A, switch to Bone B, paste" silently wrote back into
        # Bone A instead of Bone B (looked exactly like "paste does
        # nothing" whenever Bone A's weights already matched).
        paste_data = _retarget_plane_weight_to_active_bone(paste_data, ctrl)

    _debug_paste_bones = sorted({b for w in paste_data.values() for b in w.keys()}) if paste_kind == 'WEIGHT' else 'n/a'
    _DebugFacade.debug_log(
        "adhoc:clipboard_plane",
        "_paste_impl() AFTER retarget/convert: paste_kind=%r bone_names_being_written=%r" % (
            paste_kind, _debug_paste_bones,
        ),
    )

    current_mesh_name = ctrl.mesh.name
    if paste_kind == 'WEIGHT':
        resolved = data_ops.resolve_paste_targets_weight(paste_data, target_verts, source_mesh_name, current_mesh_name)
        _merge_weight_paste(ctrl, resolved, mode, full_vertex_replace=full_vertex_replace)
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


def copy_whole(ctrl) -> dict:
    """"Plane Copy" — capture the current VG/mask's weight densely across
    every vertex of the mesh, ignoring the current selection entirely
    (unlike ``copy()``, which only takes the whole-mesh branch when nothing
    is selected). See ``_copy_plane_impl()`` for why this must be dense."""
    return _copy_plane_impl(ctrl, ctrl._is_mask_context())


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

def _convert_plane_weight_to_mask(weight_data: dict) -> dict:
    """Unwrap a Plane Copy WEIGHT clip (``{v: {bone_name: w}}``, always
    exactly one key per vertex -- the bone that was active at copy time)
    into a mask scalar dict. Takes the sole value directly rather than
    looking up "the currently active bone" -- the active bone at paste time
    may differ from (or no longer exist as) the one recorded at copy time,
    and the single value is already unambiguous regardless."""
    result: dict = {}
    for v, weights in weight_data.items():
        result[v] = next(iter(weights.values()), 0.0) if weights else 0.0
    return result

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

def _retarget_plane_weight_to_active_bone(weight_data: dict, ctrl) -> dict:
    """Re-key a Plane Copy WEIGHT clip's single bone entry onto whichever VG
    is active NOW at paste time, discarding whatever bone name was recorded
    at copy time. Without this, staying in weight context (not crossing to
    mask) left the clip's original bone name untouched, so "copy Bone A,
    switch to Bone B, paste" silently wrote back into Bone A instead of
    Bone B -- this is what makes cross-bone Plane Copy pastes actually
    reach the intended VG."""
    active_vg_id = ctrl._active_vg_id()
    if active_vg_id is None:
        raise ValueError("No active Vertex Group selected")
    active_bone_name = ctrl._idx_to_name().get(active_vg_id, "")
    result: dict = {}
    for v, weights in weight_data.items():
        value = next(iter(weights.values()), 0.0) if weights else 0.0
        result[v] = {active_bone_name: value}
    return result


# ═════════════════════════════════════════════════════════════════════════
#  Merge helpers
# ═════════════════════════════════════════════════════════════════════════

def _merge_weight_paste(ctrl, resolved: dict[int, dict[str, float]], mode: str = 'REPLACE',
                         full_vertex_replace: bool = True):
    """*full_vertex_replace* controls REPLACE mode's blast radius:

    - ``True`` (Vertex Influence Copy): REPLACE wipes each target vertex's
      ENTIRE bone dict down to just what's in the pasted snapshot -- correct
      for "stamp this vertex's full influence onto other vertices."
    - ``False`` (Plane Copy): REPLACE only overwrites the bone(s) actually
      present in the pasted data (always exactly one, the VG that was
      active at copy time) and leaves every other bone's weight at that
      vertex untouched -- Plane Copy is scoped to a single VG's channel,
      not the whole vertex.

    ADD/SUBTRACT already only ever touch bones present in the pasted data
    regardless of this flag, so it has no effect on those modes.
    """
    layer_dict = {int(k): v for k, v in ctrl.read_active_layer().items()}
    for v_int, bone_weights in resolved.items():
        if mode == 'REPLACE':
            if full_vertex_replace:
                layer_dict[v_int] = {bone_name: float(w) for bone_name, w in bone_weights.items()}
            else:
                if v_int not in layer_dict: layer_dict[v_int] = {}
                for bone_name, w in bone_weights.items():
                    layer_dict[v_int][bone_name] = float(w)
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
