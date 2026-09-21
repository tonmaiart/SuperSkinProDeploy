"""Multi-color (rainbow) per-bone weight visualizer for SuperSkinPro."""

import time
import bpy

from ...interface.utils.gpu_utils import BONE_COLORS

_LAYER_NAME = "__ssp_multi_preview"
_HUD_MULTI_LABEL = "Multi Color"
_HUD_MULTI_COLOR = (1.0, 0.8, 0.0, 1.0)  # yellow, matching bone_picker's HUD line
_HUD_SINGLE_LABEL = "Single Color"
_HUD_SINGLE_COLOR = (1.0, 0.8, 0.0, 1.0)  # yellow, matching bone_picker's HUD line
_HUD_OWNER_ID = "overlay_color"
_HUD_SLOT = 1
_last_hud_state = None  # None (no line) | 'MULTI' | 'SINGLE'

# ── Module-level state ──────────────────────────────────────────────────────
_active = False

_user_enabled = False
_suppressed = False
_watch_timer_registered = False

_orig_paint_wire = None
_flat_ineligible_streak = 0

_ineligible_streak = 0
_INELIGIBLE_DEBOUNCE_TICKS = 3  # ~0.3s at _WATCH_INTERVAL -- long enough to
                                 # swallow a one-tick internal bounce, short
                                 # enough that a genuine exit still feels instant

_orig_shading = None                # (shading_type, color_type), or None while inactive
_orig_active_color_index = None     # int, or None if never snapshotted
_orig_show_weight = None            # bool, or None while inactive
_orig_wp_opacity = None             # float, or None while inactive

_orig_view_transform = None
_orig_view_look = None

_color_key = None
_MIN_COLOR_RECOMPUTE_INTERVAL = 0.08  # ~12Hz ceiling
_last_color_compute_time = 0.0

_base_key = None
_base_vert_colors = None    # list[(r, g, b)], pre-highlight
_base_layer_weights = None  # {v_idx: {bone_name: weight}}, reused by the highlight pass
_last_base_compute_time = 0.0



def _active_layer_index(obj) -> int:
    """Live session layer index; the permanent property is not rewritten by
    in-session layer switches."""
    return int(obj.get("__ssp_meta_layer", obj.data.get("ss_active_layer", 0)))


def _read_active_layer(obj) -> dict:
    """Return {v_idx(int): {bone_name(str): weight(float)}} for the active layer."""
    try:
        from ...core.facade import CoreFacade
        data = CoreFacade(bpy.context).get_active_layer_dict()
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
    """Compute per-vertex RGB by blending bone colors weighted by influence."""
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
    """Cheap second pass: blend each vertex's already-computed base color toward white by its
    raw weight on the active bone."""
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



def _get_or_create_color_layer(mesh):
    attr = mesh.color_attributes.get(_LAYER_NAME)
    if attr is None:
        attr = mesh.color_attributes.new(_LAYER_NAME, 'FLOAT_COLOR', 'POINT')
    return attr


def _set_active_color_attribute(obj) -> None:
    try:
        idx = obj.data.color_attributes.find(_LAYER_NAME)
        if idx >= 0:
            obj.data.color_attributes.active_color_index = idx
    except Exception:
        pass


def _write_colors(obj, vert_colors) -> None:
    mesh = obj.data
    attr = _get_or_create_color_layer(mesh)
    flat = []
    for i in range(len(mesh.vertices)):
        c = vert_colors[i] if i < len(vert_colors) else (0.0, 0.0, 0.0)
        flat.extend((c[0], c[1], c[2], 1.0))
    attr.data.foreach_set("color", flat)
    mesh.update()
    _set_active_color_attribute(obj)


def _remove_color_layer(obj) -> None:
    """Remove the temp color attribute from the mesh datablock."""
    if obj is None or obj.type != 'MESH':
        return
    mesh = obj.data
    try:
        attr = mesh.color_attributes.get(_LAYER_NAME)
        if attr is not None:
            mesh.color_attributes.remove(attr)
    except Exception:
        pass


def _make_base_key(obj, frame):
    """Key for the expensive per-influence blend."""
    layer_idx = _active_layer_index(obj)
    raw = obj.data.get(f"ss_layer_{layer_idx}", "")
    return (obj.name, id(obj.data), len(obj.data.vertices),
            layer_idx, frame, obj.get("__ssp_deform_gen", 0),
            len(raw), hash(raw))


def _recompute_and_write(obj, force: bool = False) -> None:
    global _color_key, _last_color_compute_time
    global _base_key, _base_vert_colors, _base_layer_weights, _last_base_compute_time
    if obj is None or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return

    frame = bpy.context.scene.frame_current
    base_key = _make_base_key(obj, frame)
    active_vg_name = _get_active_bone_name(obj)
    key = (base_key, active_vg_name)
    if not force and key == _color_key:
        return

    now = time.monotonic()
    base_dirty = force or base_key != _base_key
    if base_dirty:
        if not force and (now - _last_base_compute_time) < _MIN_COLOR_RECOMPUTE_INTERVAL:
            return
        num_verts = len(obj.data.vertices)
        _base_vert_colors, _base_layer_weights = _compute_base_colors(obj, num_verts)
        _base_key = base_key
        _last_base_compute_time = now

    vert_colors = _apply_active_highlight(_base_vert_colors, _base_layer_weights, active_vg_name)
    _write_colors(obj, vert_colors)
    _color_key = key
    _last_color_compute_time = now
    _tag_redraw_all()



def _snapshot_and_apply_shading() -> None:
    """Force Solid + Attribute (Vertex) color; lighting is left to the user."""
    global _orig_shading
    _orig_shading = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if _orig_shading is None:
                        _orig_shading = (space.shading.type, space.shading.color_type)
                    space.shading.type = 'SOLID'
                    space.shading.color_type = 'VERTEX'
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
                        space.shading.type, space.shading.color_type = _orig_shading
                    except Exception:
                        pass
    _orig_shading = None


def _snapshot_and_force_view_transform() -> None:
    """Force the scene's color management to 'Standard'/'None' so the written vertex colors
    render at their real."""
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
    """Force off the native "Vertex Group Weights" overlay (RNA property ``overlay.show_weight``."""
    global _orig_show_weight, _orig_wp_opacity
    _orig_show_weight = None
    _orig_wp_opacity = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if _orig_show_weight is None:
                        _orig_show_weight = space.overlay.show_weight
                        _orig_wp_opacity = space.overlay.weight_paint_mode_opacity
                    space.overlay.show_weight = False
                    space.overlay.weight_paint_mode_opacity = 0.0
                except Exception:
                    pass


def _enforce_active_overrides() -> None:
    """Re-apply the shading + overlay overrides every watcher tick while active, not just once
    at start."""
    changed = False
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if space.overlay.show_weight:
                        space.overlay.show_weight = False
                        changed = True
                    if space.overlay.weight_paint_mode_opacity != 0.0:
                        space.overlay.weight_paint_mode_opacity = 0.0
                        changed = True
                    if (space.shading.type != 'SOLID'
                            or space.shading.color_type != 'VERTEX'):
                        space.shading.type = 'SOLID'
                        space.shading.color_type = 'VERTEX'
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
    global _orig_show_weight, _orig_wp_opacity
    if _orig_show_weight is not None:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    try:
                        space = area.spaces.active
                        space.overlay.show_weight = _orig_show_weight
                        if _orig_wp_opacity is not None:
                            space.overlay.weight_paint_mode_opacity = _orig_wp_opacity
                    except Exception:
                        pass
    _orig_show_weight = None
    _orig_wp_opacity = None


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


def _tag_redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _sync_hud(obj):
    """Keep the shared HUD stack in sync with this preview's mode, reading the
    module-level ``_active`` flag (must already reflect the state being synced."""
    global _last_hud_state
    if _active:
        state = 'MULTI'
    else:
        eligible = obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
        state = 'SINGLE' if eligible else None
    if state == _last_hud_state:
        return

    from ...core.facade import CoreFacade
    if state == 'MULTI':
        CoreFacade.request_hud_slot(_HUD_OWNER_ID, _HUD_MULTI_LABEL, slot=_HUD_SLOT, color=_HUD_MULTI_COLOR)
    elif state == 'SINGLE':
        CoreFacade.request_hud_slot(_HUD_OWNER_ID, _HUD_SINGLE_LABEL, slot=_HUD_SLOT, color=_HUD_SINGLE_COLOR)
    else:
        CoreFacade.release_hud_slot(_HUD_OWNER_ID)
    _last_hud_state = state



def _start_handlers():
    global _active, _bone_color_map, _last_mesh_name, _color_key
    global _base_key, _base_vert_colors, _base_layer_weights
    if _active:
        return
    obj = bpy.context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
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

    _active = True
    _sync_hud(obj)
    _tag_redraw_all()


def _stop_handlers():
    global _active
    if not _active:
        return
    obj = bpy.context.active_object
    _restore_shading()
    _restore_vg_weights_overlay()
    _restore_view_transform()
    _restore_active_color_index(obj)
    _remove_color_layer(obj)

    _active = False
    _sync_hud(obj)
    _tag_redraw_all()


def _active_obj_is_mask() -> bool:
    obj = bpy.context.active_object
    storage = getattr(obj, "superskin_storage", None) if obj is not None else None
    return bool(storage is not None and storage.active_is_mask)


def start():
    """User-facing enable."""
    global _user_enabled, _suppressed
    _user_enabled = True
    if _active_obj_is_mask():
        _suppressed = True
        return
    _suppressed = False
    _start_handlers()


def stop():
    """User-facing disable."""
    global _user_enabled, _suppressed
    _user_enabled = False
    _suppressed = False
    _stop_handlers()


def cycle():
    """Advance the Alt+3 weight-overlay mode one step: Single Color -> Multi Color -> Single Color."""
    if _user_enabled:
        stop()
    else:
        start()


def is_enabled() -> bool:
    """User-facing Multi Color state."""
    return _user_enabled


_WATCH_INTERVAL = 0.1


def _apply_paint_wire() -> None:
    """Keep the Weight Paint overlay's Wireframe on while in weight paint, independent of the
    Alt+3 mode."""
    global _orig_paint_wire
    if _orig_paint_wire is None:
        _orig_paint_wire = {}
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    _orig_paint_wire.setdefault(
                        area.as_pointer(), space.overlay.show_paint_wire)
                    if not space.overlay.show_paint_wire:
                        space.overlay.show_paint_wire = True
                        _tag_redraw_all()
                except Exception:
                    pass


def _restore_paint_wire() -> None:
    global _orig_paint_wire
    if _orig_paint_wire is None:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                orig = _orig_paint_wire.get(area.as_pointer())
                if orig is None:
                    continue
                try:
                    space = area.spaces.active
                    space.overlay.show_paint_wire = orig
                except Exception:
                    pass
    _orig_paint_wire = None
    _tag_redraw_all()


def _watcher_tick():
    """bpy.app.timers watcher: auto-suspends/resumes around the mask row AND around leaving
    Edit Mode entirely (Tab, "Save Weights and Exit", switching to a different object, etc.)."""
    global _suppressed, _ineligible_streak, _flat_ineligible_streak
    if _user_enabled:
        obj = bpy.context.active_object
        eligible = obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
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

    if _active:
        _enforce_active_overrides()
        _recompute_and_write(bpy.context.active_object)

    obj = bpy.context.active_object
    if obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT':
        _flat_ineligible_streak = 0
        _apply_paint_wire()
    elif _orig_paint_wire is not None:
        _flat_ineligible_streak += 1
        if _flat_ineligible_streak >= _INELIGIBLE_DEBOUNCE_TICKS:
            _restore_paint_wire()

    _sync_hud(bpy.context.active_object)

    return _WATCH_INTERVAL


def cleanup():
    """Remove all handlers and restore native state — called from unregister()."""
    global _user_enabled, _suppressed, _last_hud_state
    _user_enabled = False
    _suppressed = False
    _stop_handlers()
    _restore_paint_wire()
    from ...core.facade import CoreFacade
    CoreFacade.release_hud_slot(_HUD_OWNER_ID)
    _last_hud_state = None


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
