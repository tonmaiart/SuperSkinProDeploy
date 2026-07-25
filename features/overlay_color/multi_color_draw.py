"""Multi-color (rainbow) per-bone weight visualizer for SuperSkinPro.

Colors are written into a temporary, addon-owned mesh color attribute
(BMesh POINT-domain float-color layer, Edit Mode only) and displayed by
switching the viewport to Blender's own native Solid shading + "Attribute"
color mode -- not a custom GPU shader draw. This replaces an earlier
version that built its own GPU batches (wireframe, selection points, and a
SMOOTH_COLOR triangle batch) and drew them with a SpaceView3D POST_VIEW
handler; that approach needed a hand-rolled multi-tier cache and manual
depth/blend/polygon-offset state management, and was a recurring source of
instability. Letting Blender's own Solid-shading pipeline render the color
removes all of that: no batches, no depth-test/polygon-offset tuning, and
wireframe/vertex-dot selection display is simply whatever Edit Mode already
draws natively (never duplicated here).

Non-destructive by construction: every native viewport/mesh setting this
module changes (``space.shading.type``/``color_type``/``light``, the mesh's
active color attribute, ``overlay.show_weight``, and the scene's
``view_settings.view_transform``/``look`` -- see below) is snapshotted the
moment the preview starts and restored exactly on stop -- the same pattern
used by ``native_sync.py`` for the weight-color ramps.

**IMPORTANT naming gotcha, diagnosed the hard way (see docs/bug-history/0027):**
the viewport checkbox labeled "Vertex Group Weights" (Mesh Edit Mode
Overlays > Shading) is bound to the RNA property ``overlay.show_weight`` --
see Blender's own ``bl_ui/space_view3d.py``: ``col.prop(overlay,
"show_weight", text="Vertex Group Weights")``. There is no separate
``show_vertex_group_weights`` property on ``View3DOverlay`` in this Blender
build. An earlier version of this module (and of ``native_sync.py``) assumed
``show_vertex_group_weights`` was real; every read/write of it raised
``AttributeError`` and was silently swallowed by a surrounding
``except Exception``, which is why the "force this overlay off" logic
appeared to do nothing no matter how it was rewritten. Do not reintroduce
``show_vertex_group_weights`` here.

**Wins over the weight/mask color ramps.** This module and ``native_sync.py``
are this domain's two overlay-color modes, and they'd visually stack if
both were live at once (different native mechanisms: this one drives
``space.shading.color_type``, ``native_sync.py`` drives
``overlay.show_weight``). Two layers of protection, since they close two
different gaps:

1. ``native_sync.py`` checks ``is_enabled()`` below and refuses to
   *activate* while this preview is on -- but that's a reactive, polled
   check (up to its ~0.1s tick interval), and it only ever touches
   ``show_weight`` when *its own* watcher turned it on, not if the user
   enabled it by hand via the Overlays dropdown.
2. ``_start_handlers()`` below also directly snapshots and force-disables
   ``show_weight`` on every ``VIEW_3D`` space the instant this preview
   starts (restored in ``_stop_handlers()``), closing both gaps: no timing
   window, and it doesn't matter who turned the overlay on.

This module itself has no knowledge of ``native_sync.py`` at all (one-way
dependency, no circular import) -- it doesn't need one, since it owns the
overlay-disable directly instead of asking the other module to do it.

All data access uses Blender properties directly so this module has zero
imports from core/ sub-modules.
"""

import json
import time
import bpy
import bmesh
import blf

from ...interface.utils.gpu_utils import BONE_COLORS

_LAYER_NAME = "__ssp_multi_preview"
_HUD_COLOR = (1.0, 0.4, 0.7, 1.0)

# ── Module-level state ──────────────────────────────────────────────────────
_active = False
_hud_handle = None

# _user_enabled: the user's own toggle intent, independent of temporary
# suppression. _suppressed: True only when _active is False specifically
# because the watcher auto-stopped it -- either the active VG is the mask
# row, or the object is no longer a mesh in Edit Mode at all (left Edit
# Mode via Tab, "Save Weights and Exit", switched objects, etc.) -- not
# because the user turned it off with Alt+3. See start()/stop()/_watcher_tick().
_user_enabled = False
_suppressed = False
_watch_timer_registered = False

# Consecutive watcher ticks the active object has been "not eligible"
# (not a mesh in Edit Mode) for -- see _watcher_tick()'s debounce, added
# after diagnosing a rapid EDIT<->OBJECT flicker (docs/bug-history/0013 is
# the same class of bug: Blender's own undo/memfile restore, and this
# addon's own internal selection-restore round trips, transiently bounce
# through Object Mode as an implementation detail with no user-visible mode
# change -- reacting to every single blip caused Multi Color Mode to
# stop/restart several times a second).
_ineligible_streak = 0
_INELIGIBLE_DEBOUNCE_TICKS = 3  # ~0.3s at _WATCH_INTERVAL -- long enough to
                                 # swallow a one-tick internal bounce, short
                                 # enough that a genuine exit still feels instant

# Snapshot of the user's own native viewport/mesh state, captured on
# _start_handlers() and restored on _stop_handlers(). None while inactive.
# Deliberately a single global value, not a per-space dict keyed by identity
# (space.as_pointer()/id(space)) -- diagnosed as unreliable in this codebase:
# the same conceptual viewport's space struct did not compare equal to
# itself between the snapshot call and the restore call moments later (see
# docs/bug-history for the write-up), so a per-space dict silently failed to
# find a match and skipped restoring. Applying one snapshot uniformly to
# every VIEW_3D space found at restore time is less "correct" only in the
# rare case of multiple simultaneous viewports with genuinely different
# pre-existing shading -- but it always actually restores, which the
# per-space version did not.
_orig_shading = None                # (shading_type, color_type, light), or None while inactive
_orig_active_color_index = None     # int, or None if never snapshotted
_orig_show_weight = None            # bool, or None while inactive

# (view_transform, look) on the scene's own color management, snapshotted the
# same way. AgX/Filmic (Blender's default view transforms since 4.0) crush
# contrast and desaturate mid-range values by design, which is exactly what
# turns this preview's vivid BONE_COLORS into the washed-out pastel look --
# forcing 'Standard'/'None' while the preview is active (and restoring on
# stop, same pattern as shading above) renders the written vertex colors at
# their real saturation without changing the value written to the mesh.
_orig_view_transform = None
_orig_view_look = None

_color_key = None
_MIN_COLOR_RECOMPUTE_INTERVAL = 0.08  # ~12Hz ceiling
_last_color_compute_time = 0.0

# Cache for the expensive per-influence blend (_compute_base_colors), kept
# separate from _color_key/_last_color_compute_time above so a hover-only
# change (active bone, not the underlying weights) never has to pay for it --
# see _make_base_key/_apply_active_highlight.
_base_key = None
_base_vert_colors = None    # list[(r, g, b)], pre-highlight
_base_layer_weights = None  # {v_idx: {bone_name: weight}}, reused by the highlight pass
_last_base_compute_time = 0.0


# ═══════════════════════════════════════════════════════════════════════════
#  Data access — reads Blender properties directly, no core/ imports
# ═══════════════════════════════════════════════════════════════════════════

def _read_active_layer(obj) -> dict:
    """Return {v_idx(int): {bone_name(str): weight(float)}} for the active layer.

    In Edit Mode reads from __ssp_* temp VGs via bmesh.
    In other modes decodes the ss_layer_N JSON property.
    """
    if obj.mode == 'EDIT':
        try:
            bm = bmesh.from_edit_mesh(obj.data)
            deform_layer = bm.verts.layers.deform.active
            if not deform_layer:
                return {}
            map_raw = obj.get("__ssp_meta_map", "{}")
            id_to_bone = {int(k): v for k, v in json.loads(map_raw).items()}
            if not id_to_bone:
                return {}
            vg_map = {vg.name: vg.index for vg in obj.vertex_groups}
            bone_to_vg = {}
            for bone_id, bone_name in id_to_bone.items():
                temp_name = f"__ssp_{bone_id}"
                if temp_name in vg_map:
                    bone_to_vg[bone_name] = vg_map[temp_name]
            result = {}
            for vert in bm.verts:
                vw = vert[deform_layer]
                weights = {bname: vw[vgi] for bname, vgi in bone_to_vg.items()
                           if vgi in vw and vw[vgi] > 0.0}
                if weights:
                    result[vert.index] = weights
            return result
        except Exception:
            return {}
    else:
        try:
            active_idx = obj.data.get("ss_active_layer", 0)
            raw = obj.data.get(f"ss_layer_{active_idx}", "")
            if not raw:
                return {}
            data = json.loads(raw)
            return {int(k): v for k, v in data.items() if v}
        except Exception:
            return {}


def _get_active_bone_name(obj) -> str:
    try:
        storage = obj.superskin_storage
        if storage.active_orphan_name:
            return storage.active_orphan_name
        idx = storage.last_clicked_index
        if 0 <= idx < len(obj.vertex_groups):
            return obj.vertex_groups[idx].name
    except Exception:
        pass
    return ""


# ═══════════════════════════════════════════════════════════════════════════
#  Color computation
# ═══════════════════════════════════════════════════════════════════════════

_bone_color_map = None
_last_mesh_name = ""


def _compute_bone_colors_map(obj) -> dict:
    """Assign palette colors to bones via BFS of the armature hierarchy."""
    color_map = {}
    palette = BONE_COLORS
    n_colors = len(palette)
    if n_colors == 0:
        return color_map

    for vgroup in obj.vertex_groups:
        color_map[vgroup.name] = palette[vgroup.index % n_colors]

    arm_obj = obj.find_armature()
    if not arm_obj or not arm_obj.data:
        return color_map

    bones = arm_obj.data.bones
    visited = set()
    root_bones = [b for b in bones if b.parent is None]
    queue = []
    for i, root in enumerate(root_bones):
        queue.append((root, i % n_colors))
        visited.add(root.name)

    while queue:
        bone, color_idx = queue.pop(0)
        if bone.name in obj.vertex_groups:
            color_map[bone.name] = palette[color_idx]
        for child_idx, child in enumerate(bone.children):
            if child.name not in visited:
                visited.add(child.name)
                next_idx = (color_idx + child_idx + 1) % n_colors
                queue.append((child, next_idx))

    for item in getattr(obj, 'superskin_bones_collection', ()):
        if item.is_orphan and item.name not in color_map:
            color_map[item.name] = (1.0, 0.5, 0.0)

    return color_map


def _compute_base_colors(obj, num_verts):
    """Compute per-vertex RGB by blending bone colors weighted by influence --
    deliberately independent of which bone is active. Split out from the
    active-bone highlight (``_apply_active_highlight`` below) so that a hover
    change from ``bone_picker`` (which live-applies the hovered bone as the
    real active bone on every ``MOUSEMOVE``, see
    ``features/bone_picker/ops.py``) doesn't force this full per-influence
    summation to re-run for every vertex -- only the cheap highlight pass
    needs to. Returns ``(vert_colors, layer_weights)``; ``layer_weights`` is
    handed back so the highlight pass can look up each vertex's raw weight on
    the active bone without re-reading the bmesh deform layer."""
    global _bone_color_map, _last_mesh_name

    layer_weights = _read_active_layer(obj)

    if _bone_color_map is None or _last_mesh_name != obj.name:
        _bone_color_map = _compute_bone_colors_map(obj)
        _last_mesh_name = obj.name

    vert_colors = [None] * num_verts
    for v_idx in range(num_verts):
        dv = layer_weights.get(v_idx, {})
        r = g = b = total = 0.0

        for g_name, w in dv.items():
            w = float(w)
            if w <= 0.0 or g_name not in _bone_color_map:
                continue
            cr, cg, cb = _bone_color_map[g_name]
            r += cr * w
            g += cg * w
            b += cb * w
            total += w

        if total > 1.0:
            inv = 1.0 / total
            r *= inv
            g *= inv
            b *= inv

        vert_colors[v_idx] = (r, g, b)
    return vert_colors, layer_weights


def _apply_active_highlight(base_colors, layer_weights, active_vg_name) -> list:
    """Cheap second pass: blend each vertex's already-computed base color
    toward white by its raw weight on the active bone. This is the only part
    of the pipeline that depends on which bone is active, so it's the only
    part that needs to re-run on every hover change -- a plain per-vertex
    lookup (no per-influence summation), safe to run every watcher tick
    regardless of how fast the mouse is sweeping across bones."""
    if not active_vg_name:
        return list(base_colors)

    out = [None] * len(base_colors)
    for v_idx, (r, g, b) in enumerate(base_colors):
        active_weight = layer_weights.get(v_idx, {}).get(active_vg_name, 0.0)
        if active_weight > 0.01:
            t = active_weight * 0.95
            r += (1.0 - r) * t
            g += (1.0 - g) * t
            b += (1.0 - b) * t
        out[v_idx] = (r, g, b)
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  Temp color attribute write
# ═══════════════════════════════════════════════════════════════════════════

def _get_or_create_color_layer(bm):
    layer = bm.verts.layers.float_color.get(_LAYER_NAME)
    if layer is None:
        layer = bm.verts.layers.float_color.new(_LAYER_NAME)
    return layer


def _set_active_color_attribute(obj) -> None:
    try:
        idx = obj.data.color_attributes.find(_LAYER_NAME)
        if idx >= 0:
            obj.data.color_attributes.active_color_index = idx
    except Exception:
        pass


def _write_colors(obj, bm, vert_colors) -> None:
    layer = _get_or_create_color_layer(bm)
    for v in bm.verts:
        c = vert_colors[v.index] if v.index < len(vert_colors) else (0.0, 0.0, 0.0)
        v[layer] = (c[0], c[1], c[2], 1.0)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    _set_active_color_attribute(obj)


def _remove_color_layer(obj) -> None:
    """Remove the temp color attribute — works whether *obj* is still in
    Edit Mode (via bmesh) or has already left it (directly on the mesh
    datablock), so a stop() that races a mode change never leaves the temp
    attribute behind."""
    if obj is None or obj.type != 'MESH':
        return
    mesh = obj.data
    try:
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(mesh)
            layer = bm.verts.layers.float_color.get(_LAYER_NAME)
            if layer is not None:
                bm.verts.layers.float_color.remove(layer)
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        else:
            attr = mesh.color_attributes.get(_LAYER_NAME)
            if attr is not None:
                mesh.color_attributes.remove(attr)
    except Exception:
        pass


def _make_base_key(obj, bm, frame):
    """Key for the expensive per-influence blend -- deliberately excludes the
    active bone, since a hover-driven active-bone change alone must not force
    this to be treated as dirty (see ``_apply_active_highlight``)."""
    return (obj.name, id(obj.data), len(bm.verts),
            obj.data.get("ss_active_layer", 0), frame,
            obj.get("__ssp_deform_gen", 0))


def _recompute_and_write(obj, force: bool = False) -> None:
    global _color_key, _last_color_compute_time
    global _base_key, _base_vert_colors, _base_layer_weights, _last_base_compute_time
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return

    bm = bmesh.from_edit_mesh(obj.data)
    frame = bpy.context.scene.frame_current
    base_key = _make_base_key(obj, bm, frame)
    active_vg_name = _get_active_bone_name(obj)
    key = (base_key, active_vg_name)
    if not force and key == _color_key:
        return

    now = time.monotonic()
    base_dirty = force or base_key != _base_key
    if base_dirty:
        # Only the expensive full-mesh weighted blend is throttled -- the
        # active-bone highlight below is a cheap per-vertex lookup and can
        # run every watcher tick without a rate ceiling.
        if not force and (now - _last_base_compute_time) < _MIN_COLOR_RECOMPUTE_INTERVAL:
            return
        num_verts = len(bm.verts)
        _base_vert_colors, _base_layer_weights = _compute_base_colors(obj, num_verts)
        _base_key = base_key
        _last_base_compute_time = now

    vert_colors = _apply_active_highlight(_base_vert_colors, _base_layer_weights, active_vg_name)
    _write_colors(obj, bm, vert_colors)
    _color_key = key
    _last_color_compute_time = now
    _tag_redraw_all()


# ═══════════════════════════════════════════════════════════════════════════
#  Native shading + active color attribute — snapshot/restore
# ═══════════════════════════════════════════════════════════════════════════

def _snapshot_and_apply_shading() -> None:
    """Force Solid + Attribute (Vertex) color + Flat lighting -- Studio/Matcap
    lighting dims and unevenly tints the per-vertex color attribute, so Flat
    is forced here to keep the rainbow blend readable.

    Captures exactly one (type, color_type, light) tuple -- from the first
    VIEW_3D space encountered -- rather than one snapshot per space. See the
    module-level comment above `_orig_shading` for why."""
    global _orig_shading
    _orig_shading = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if _orig_shading is None:
                        _orig_shading = (space.shading.type, space.shading.color_type, space.shading.light)
                    space.shading.type = 'SOLID'
                    space.shading.color_type = 'VERTEX'
                    space.shading.light = 'FLAT'
                except Exception:
                    pass


def _restore_shading() -> None:
    global _orig_shading
    if _orig_shading is not None:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    try:
                        space = area.spaces.active
                        space.shading.type, space.shading.color_type, space.shading.light = _orig_shading
                    except Exception:
                        pass
    _orig_shading = None


def _snapshot_and_force_view_transform() -> None:
    """Force the scene's color management to 'Standard'/'None' so the
    written vertex colors render at their real saturation instead of
    AgX/Filmic's characteristic muted, contrast-crushed look."""
    global _orig_view_transform, _orig_view_look
    try:
        view_settings = bpy.context.scene.view_settings
        _orig_view_transform = view_settings.view_transform
        _orig_view_look = view_settings.look
        view_settings.view_transform = 'Standard'
        view_settings.look = 'None'
    except Exception:
        _orig_view_transform = None
        _orig_view_look = None


def _restore_view_transform() -> None:
    global _orig_view_transform, _orig_view_look
    if _orig_view_transform is not None:
        try:
            view_settings = bpy.context.scene.view_settings
            view_settings.view_transform = _orig_view_transform
            view_settings.look = _orig_view_look
        except Exception:
            pass
    _orig_view_transform = None
    _orig_view_look = None


def _snapshot_and_disable_vg_weights_overlay() -> None:
    """Force off the native "Vertex Group Weights" overlay (RNA property
    ``overlay.show_weight`` -- see this module's docstring for the naming
    gotcha) while this preview is active -- if left on (whether
    native_sync.py had it on for the edit/mask ramp, or the user turned it
    on manually via the Overlays dropdown), it visually stacks on top of
    this preview's Solid+Attribute color. native_sync.py separately refuses
    to *activate* it while this preview is on (see this module's docstring),
    but that's a reactive, polled check -- doing it here too, synchronously
    in _start_handlers(), removes any timing gap and also covers the "user
    turned it on by hand" case native_sync.py never touches at all.

    Captures exactly one bool -- from the first VIEW_3D space encountered --
    rather than one snapshot per space. See the module-level comment above
    `_orig_show_weight` for why."""
    global _orig_show_weight
    _orig_show_weight = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if _orig_show_weight is None:
                        _orig_show_weight = space.overlay.show_weight
                    space.overlay.show_weight = False
                except Exception:
                    pass


def _enforce_active_overrides() -> None:
    """Re-apply the shading + overlay overrides every watcher tick while
    active, not just once at start -- the one-shot snapshot in
    _start_handlers()/_snapshot_and_disable_vg_weights_overlay() doesn't
    stick if the user re-enables "Vertex Group Weights" (or switches
    shading mode) by hand from the Overlays/Shading dropdowns after Multi
    Color Preview is already running; that re-enabled overlay then draws
    on top of the attribute color again."""
    changed = False
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if space.overlay.show_weight:
                        space.overlay.show_weight = False
                        changed = True
                    if (space.shading.type != 'SOLID'
                            or space.shading.color_type != 'VERTEX'
                            or space.shading.light != 'FLAT'):
                        space.shading.type = 'SOLID'
                        space.shading.color_type = 'VERTEX'
                        space.shading.light = 'FLAT'
                        changed = True
                except Exception:
                    pass
    try:
        view_settings = bpy.context.scene.view_settings
        if view_settings.view_transform != 'Standard' or view_settings.look != 'None':
            view_settings.view_transform = 'Standard'
            view_settings.look = 'None'
            changed = True
    except Exception:
        pass
    if changed:
        _tag_redraw_all()


def _restore_vg_weights_overlay() -> None:
    global _orig_show_weight
    if _orig_show_weight is not None:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    try:
                        space = area.spaces.active
                        space.overlay.show_weight = _orig_show_weight
                    except Exception:
                        pass
    _orig_show_weight = None


def _snapshot_active_color_index(obj) -> None:
    global _orig_active_color_index
    try:
        _orig_active_color_index = obj.data.color_attributes.active_color_index
    except Exception:
        _orig_active_color_index = None


def _restore_active_color_index(obj) -> None:
    global _orig_active_color_index
    if _orig_active_color_index is not None and obj is not None and obj.type == 'MESH':
        try:
            n = len(obj.data.color_attributes)
            if 0 <= _orig_active_color_index < n:
                obj.data.color_attributes.active_color_index = _orig_active_color_index
        except Exception:
            pass
    _orig_active_color_index = None


# ═══════════════════════════════════════════════════════════════════════════
#  HUD — unchanged, plain blf text, not a shader draw
# ═══════════════════════════════════════════════════════════════════════════

def _draw_hud():
    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return
    font_id = 0
    label = "MULTI COLOR MODE"
    blf.size(font_id, 24)
    text_w, _ = blf.dimensions(font_id, label)
    region_width = context.region.width
    center_x = max(0, region_width // 2 - int(text_w) // 2)
    y_offset = 25
    blf.position(font_id, center_x + 2, y_offset + 2, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.85)
    blf.draw(font_id, label)
    blf.position(font_id, center_x, y_offset, 0)
    blf.color(font_id, *_HUD_COLOR)
    blf.draw(font_id, label)


def _tag_redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ═══════════════════════════════════════════════════════════════════════════
#  Public lifecycle API
# ═══════════════════════════════════════════════════════════════════════════

def _start_handlers():
    global _active, _hud_handle, _bone_color_map, _last_mesh_name, _color_key
    global _base_key, _base_vert_colors, _base_layer_weights
    if _active:
        return
    obj = bpy.context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return

    _bone_color_map = None
    _last_mesh_name = ""
    _color_key = None
    _base_key = None
    _base_vert_colors = None
    _base_layer_weights = None

    _snapshot_active_color_index(obj)
    _snapshot_and_apply_shading()
    _snapshot_and_disable_vg_weights_overlay()
    _snapshot_and_force_view_transform()
    _recompute_and_write(obj, force=True)

    _hud_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_hud, (), 'WINDOW', 'POST_PIXEL')
    _active = True
    _tag_redraw_all()


def _stop_handlers():
    global _active, _hud_handle
    if not _active:
        return
    if _hud_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_hud_handle, 'WINDOW')
        _hud_handle = None

    obj = bpy.context.active_object
    _restore_shading()
    _restore_vg_weights_overlay()
    _restore_view_transform()
    _restore_active_color_index(obj)
    _remove_color_layer(obj)

    _active = False
    _tag_redraw_all()


def _active_obj_is_mask() -> bool:
    obj = bpy.context.active_object
    storage = getattr(obj, "superskin_storage", None) if obj is not None else None
    return bool(storage is not None and storage.active_is_mask)


def start():
    """User-facing enable. Suppressed (but remembered) while the active VG
    is the mask row, or the active object isn't a mesh in Edit Mode yet —
    see _watcher_tick()."""
    global _user_enabled, _suppressed
    _user_enabled = True
    if _active_obj_is_mask():
        _suppressed = True
        return
    _suppressed = False
    _start_handlers()


def stop():
    """User-facing disable. Clears the "want it on" intent entirely, so
    re-entering Edit Mode (or switching away from the mask row) later does
    not auto-resume it."""
    global _user_enabled, _suppressed
    _user_enabled = False
    _suppressed = False
    _stop_handlers()


def toggle():
    stop() if _user_enabled else start()


def is_enabled() -> bool:
    """User-facing on/off state — True even while temporarily suppressed,
    since the user's own intent is still "on". Also the signal
    `native_sync.py` checks to yield the viewport to this preview instead
    of pushing the weight/mask ramp — see this module's docstring."""
    return _user_enabled


_WATCH_INTERVAL = 0.1


def _watcher_tick():
    """bpy.app.timers watcher: auto-suspends/resumes around the mask row AND
    around leaving Edit Mode entirely (Tab, "Save Weights and Exit",
    switching to a different object, etc.) -- either condition restores the
    snapshotted shading/overlay state via _stop_handlers() exactly like an
    explicit Alt+3 toggle-off would, so flat lighting / a disabled "Vertex
    Group Weights" overlay never leaks past this preview's own lifetime no
    matter how Edit Mode was left.

    The "left Edit Mode" side is debounced (_ineligible_streak /
    _INELIGIBLE_DEBOUNCE_TICKS) -- a single-tick EDIT->OBJECT->EDIT blip
    (Blender's own undo/memfile restore, or this addon's own internal
    selection-restore round trips -- see docs/bug-history/0013 for the same
    class of bug in a different subsystem) must not stop/restart this
    preview, only a mode change that actually persists. The mask-row switch
    is a real, intentional user action and stays undebounced.

    Auto-(re)starts whenever the user's own "on" intent is unmet for any
    other reason (e.g. Alt+3 was pressed before entering Edit Mode, or a
    controller-domain operator like "Enter Edit Mode" bounces the object
    OBJECT->EDIT internally and this preview's _active flag never got a
    chance to be set) -- and periodically recomputes + rewrites the color
    attribute, replacing the old draw-callback-driven recompute now that
    there's no SpaceView3D POST_VIEW handler doing that per-redraw."""
    global _suppressed, _ineligible_streak
    if _user_enabled:
        obj = bpy.context.active_object
        eligible = obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'
        _ineligible_streak = 0 if eligible else _ineligible_streak + 1
        is_mask = eligible and _active_obj_is_mask()
        left_edit_mode = not eligible and _ineligible_streak >= _INELIGIBLE_DEBOUNCE_TICKS

        if is_mask or left_edit_mode:
            if _active:
                _suppressed = True
                _stop_handlers()
        elif eligible:
            _suppressed = False
            if not _active:
                _start_handlers()
        # else: not eligible, but still within the debounce grace period --
        # leave _active as-is and wait for the next tick to resolve either
        # way, rather than reacting to what may be a transient internal blip.

    if _active:
        _enforce_active_overrides()
        _recompute_and_write(bpy.context.active_object)

    return _WATCH_INTERVAL


def cleanup():
    """Remove all handlers and restore native state — called from unregister()."""
    global _user_enabled, _suppressed
    _user_enabled = False
    _suppressed = False
    _stop_handlers()


def register():
    global _watch_timer_registered
    if not _watch_timer_registered:
        bpy.app.timers.register(_watcher_tick, first_interval=_WATCH_INTERVAL, persistent=True)
        _watch_timer_registered = True


def unregister():
    global _watch_timer_registered
    if _watch_timer_registered and bpy.app.timers.is_registered(_watcher_tick):
        bpy.app.timers.unregister(_watcher_tick)
    _watch_timer_registered = False
    cleanup()
