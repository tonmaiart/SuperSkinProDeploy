
import time

import bpy
import numpy as np

from ...core.facade import CoreFacade
from ...interface.utils.gpu_utils import BONE_COLORS

_LAYER_NAME = "__ssp_multi_preview"

_active = False

_user_enabled = False
_suppressed = False
_watch_timer_registered = False

_orig_paint_wire = None
_flat_ineligible_streak = 0

_ineligible_streak = 0
_INELIGIBLE_DEBOUNCE_TICKS = 3

_orig_shading = None
_orig_shading_light = None
_orig_active_color_index = None
_orig_show_weight = None
_orig_wp_opacity = None

_orig_view_transform = None
_orig_view_look = None

_color_key = None
_MIN_COLOR_RECOMPUTE_INTERVAL = 0.08
_last_color_compute_time = 0.0

_base_key = None
_base_payload = None
_last_base_compute_time = 0.0

_last_rgba = None
_live_dirty = False



def _active_layer_index(obj) -> int:
    return CoreFacade.temp_layer_index(obj, obj.data.get("ss_active_layer", 0))


def _read_active_layer(obj) -> dict:
    try:
        from ...core.facade import CoreFacade
        data = CoreFacade(bpy.context).get_active_layer_dict()
        return {int(k): v for k, v in data.items() if v}
    except Exception:
        return {}


def _get_active_bone_name(obj) -> str:
    try:
        return CoreFacade.active_vg_name_of(obj)
    except Exception:
        return ""



_bone_color_map = None
_last_mesh_name = ""


def _compute_bone_colors_map(obj) -> dict:
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


def _coo_payload(rows):
    slots = {}
    vs, ss, ws = [], [], []
    for v_idx, dv in rows:
        for g_name, w in dv.items():
            vs.append(v_idx)
            ss.append(slots.setdefault(g_name, len(slots)))
            ws.append(float(w))
    return (
        list(slots),
        np.asarray(vs, dtype=np.int64),
        np.asarray(ss, dtype=np.int64),
        np.asarray(ws, dtype=np.float64),
    )


def _layer_payload(obj):
    return _coo_payload(_read_active_layer(obj).items())


def _vert_colors(obj, num_verts, payload, active_vg_name):
    global _bone_color_map, _last_mesh_name
    if _bone_color_map is None or _last_mesh_name != obj.name:
        _bone_color_map = _compute_bone_colors_map(obj)
        _last_mesh_name = obj.name

    names, vs, ss, ws = payload
    nan = (float("nan"),) * 3
    palette = np.array([_bone_color_map.get(n, nan) for n in names], dtype=np.float64).reshape(-1)
    active = names.index(active_vg_name) if active_vg_name in names else -1
    out = np.empty(num_verts * 4, dtype=np.float32)

    from ...core.facade import CoreFacade
    CoreFacade.get_rust_gateway("overlay_color").call(
        "rust_overlay_bone_colors", num_verts, vs, ss, ws, palette, active, out,
    )
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


def _write_colors(obj, flat_rgba) -> None:
    mesh = obj.data
    attr = _get_or_create_color_layer(mesh)
    attr.data.foreach_set("color", flat_rgba)
    mesh.update()
    _set_active_color_attribute(obj)


def _remove_color_layer(obj) -> None:
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
    layer_idx = _active_layer_index(obj)
    raw = obj.data.get(f"ss_layer_{layer_idx}", "")
    return (obj.name, id(obj.data), len(obj.data.vertices),
            layer_idx, frame, obj.get(CoreFacade.DEFORM_GEN_KEY, 0),
            len(raw), hash(raw))


def _recompute_and_write(obj, force: bool = False) -> None:
    global _color_key, _last_color_compute_time, _last_rgba
    global _base_key, _base_payload, _last_base_compute_time
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
        _base_payload = _layer_payload(obj)
        _base_key = base_key
        _last_base_compute_time = now

    _last_rgba = _vert_colors(obj, len(obj.data.vertices), _base_payload, active_vg_name)
    _write_colors(obj, _last_rgba)
    _color_key = key
    _last_color_compute_time = now
    _tag_redraw_all()


def _on_live_write(obj, layer_int, id_to_bone, dirty_verts) -> None:
    global _live_dirty
    if not _active or _last_rgba is None or _color_key is None:
        return
    if obj is None or obj != bpy.context.active_object or obj.mode != 'WEIGHT_PAINT':
        return
    num_verts = len(obj.data.vertices)
    active_vg_name = _get_active_bone_name(obj)
    if _last_rgba.size != num_verts * 4 or active_vg_name != _color_key[1]:
        return
    verts = [v for v in dirty_verts if 0 <= v < num_verts]
    if not verts:
        return

    payload = _coo_payload(
        (i, {id_to_bone[b]: w for b, w in (layer_int.get(v) or {}).items() if b in id_to_bone})
        for i, v in enumerate(verts)
    )
    sub = _vert_colors(obj, len(verts), payload, active_vg_name)
    _last_rgba.reshape(-1, 4)[np.asarray(verts, dtype=np.int64)] = sub.reshape(-1, 4)
    _write_colors(obj, _last_rgba)
    _live_dirty = True
    _tag_redraw_all()


def _addon_stroke_active(obj) -> bool:
    try:
        return CoreFacade(bpy.context).is_addon_stroke_active(obj.name)
    except Exception:
        return False



def _snapshot_and_apply_shading() -> None:
    global _orig_shading, _orig_shading_light
    _orig_shading = None
    _orig_shading_light = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if _orig_shading is None:
                        _orig_shading = (space.shading.type, space.shading.color_type)
                        _orig_shading_light = space.shading.light
                    space.shading.type = 'SOLID'
                    space.shading.color_type = 'VERTEX'
                    space.shading.light = 'FLAT'
                except Exception:
                    pass


def _restore_shading() -> None:
    global _orig_shading, _orig_shading_light
    if _orig_shading is not None:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    try:
                        space = area.spaces.active
                        space.shading.type, space.shading.color_type = _orig_shading
                        if _orig_shading_light is not None:
                            space.shading.light = _orig_shading_light
                    except Exception:
                        pass
    _orig_shading = None
    _orig_shading_light = None


def _snapshot_and_force_view_transform() -> None:
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
                    if space.shading.light != 'FLAT':
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
    CoreFacade.tag_redraw_areas()


def _start_handlers():
    global _active, _bone_color_map, _last_mesh_name, _color_key
    global _base_key, _base_payload, _last_rgba, _live_dirty
    if _active:
        return
    obj = bpy.context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return

    _bone_color_map = None
    _last_mesh_name = ""
    _color_key = None
    _base_key = None
    _base_payload = None
    _last_rgba = None
    _live_dirty = False

    _snapshot_active_color_index(obj)
    _snapshot_and_apply_shading()
    _snapshot_and_disable_vg_weights_overlay()
    _snapshot_and_force_view_transform()
    _recompute_and_write(obj, force=True)

    _active = True
    _tag_redraw_all()


def _stop_handlers():
    global _active, _last_rgba, _live_dirty
    if not _active:
        return
    obj = bpy.context.active_object
    _restore_shading()
    _restore_vg_weights_overlay()
    _restore_view_transform()
    _restore_active_color_index(obj)
    _remove_color_layer(obj)

    _active = False
    _last_rgba = None
    _live_dirty = False
    _tag_redraw_all()


def _active_obj_is_mask() -> bool:
    obj = bpy.context.active_object
    storage = getattr(obj, "superskin_storage", None) if obj is not None else None
    return bool(storage is not None and storage.active_is_mask)


def start():
    global _user_enabled, _suppressed
    _user_enabled = True
    if _active_obj_is_mask():
        _suppressed = True
        return
    _suppressed = False
    _start_handlers()


def stop():
    global _user_enabled, _suppressed
    _user_enabled = False
    _suppressed = False
    _stop_handlers()


def cycle():
    if _user_enabled:
        stop()
    else:
        start()


def is_enabled() -> bool:
    return _user_enabled


def is_drawing() -> bool:
    return _active


_WATCH_INTERVAL = 0.1


def _apply_paint_wire() -> None:
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
    global _suppressed, _ineligible_streak, _flat_ineligible_streak, _live_dirty
    if _user_enabled:
        obj = bpy.context.active_object
        wm = bpy.context.window_manager
        eligible = (
            obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
            and getattr(wm, "superskin_active_interface", "LAYER") == "SKINNING"
        )
        _ineligible_streak = 0 if eligible else _ineligible_streak + 1
        is_mask = eligible and _active_obj_is_mask()
        left_edit_mode = not eligible and _ineligible_streak >= _INELIGIBLE_DEBOUNCE_TICKS

        from . import native_sync
        if is_mask or left_edit_mode:
            if _active:
                _suppressed = True
                _stop_handlers()
                native_sync.sync_now()
        elif eligible:
            _suppressed = False
            if not _active:
                native_sync.release()
                _start_handlers()

    if _active:
        _enforce_active_overrides()
        obj = bpy.context.active_object
        if not (_live_dirty and obj is not None and _addon_stroke_active(obj)):
            _recompute_and_write(obj, force=_live_dirty)
            _live_dirty = False

    obj = bpy.context.active_object
    wm = bpy.context.window_manager
    eligible_wp = (
        obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
        and getattr(wm, "superskin_active_interface", "LAYER") == "SKINNING"
    )
    if eligible_wp:
        _flat_ineligible_streak = 0
        if _orig_paint_wire is None:
            _apply_paint_wire()
    elif _orig_paint_wire is not None:
        _flat_ineligible_streak += 1
        if _flat_ineligible_streak >= _INELIGIBLE_DEBOUNCE_TICKS:
            _restore_paint_wire()

    return _WATCH_INTERVAL


def cleanup():
    global _user_enabled, _suppressed
    _user_enabled = False
    _suppressed = False
    _stop_handlers()
    _restore_paint_wire()


def register():
    global _watch_timer_registered
    from ..weight_apply.public_api import add_live_write_listener
    add_live_write_listener(_on_live_write)
    if not _watch_timer_registered:
        bpy.app.timers.register(_watcher_tick, first_interval=_WATCH_INTERVAL, persistent=True)
        _watch_timer_registered = True


def unregister():
    global _watch_timer_registered
    try:
        from ..weight_apply.public_api import remove_live_write_listener
        remove_live_write_listener(_on_live_write)
    except Exception:
        pass
    if _watch_timer_registered and bpy.app.timers.is_registered(_watcher_tick):
        bpy.app.timers.unregister(_watcher_tick)
    _watch_timer_registered = False
    cleanup()
