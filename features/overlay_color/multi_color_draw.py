
import time

import bpy
import numpy as np

from ...core.facade import CoreFacade
from ...interface.utils.gpu_utils import BONE_COLORS
from ...interface.utils import viewport_overrides

_LAYER_NAME = "__ssp_multi_preview"

_active = False

_user_enabled = False
_suppressed = False
_watch_timer_registered = False

_flat_ineligible_streak = 0

_ineligible_streak = 0
_INELIGIBLE_DEBOUNCE_TICKS = 3

_PAINT_WIRE_OWNER = "overlay_color.paint_wire"

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
    from ..weight_apply.public_api import displayed_bone_name
    try:
        return displayed_bone_name(obj)
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


def _write_colors(obj, flat_rgba) -> None:
    from ..weight_apply.public_api import set_display_bone_colors
    set_display_bone_colors(obj.name, flat_rgba)


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
    if not CoreFacade.in_session(obj):
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
    if obj is None or obj != bpy.context.active_object or not CoreFacade.in_session(obj):
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
    rows = np.asarray(verts, dtype=np.int64)
    _last_rgba.reshape(-1, 4)[rows] = sub.reshape(-1, 4)
    from ..weight_apply.public_api import patch_display_bone_colors
    patch_display_bone_colors(obj.name, rows, sub)
    _live_dirty = True
    _tag_redraw_all()


def _addon_stroke_active(obj) -> bool:
    try:
        return CoreFacade(bpy.context).is_addon_stroke_active(obj.name)
    except Exception:
        return False



def _tag_redraw_all():
    CoreFacade.tag_redraw_areas()


def _start_handlers():
    global _active, _bone_color_map, _last_mesh_name, _color_key
    global _base_key, _base_payload, _last_rgba, _live_dirty
    if _active:
        return
    obj = bpy.context.active_object
    if not CoreFacade.in_session(obj):
        return

    _bone_color_map = None
    _last_mesh_name = ""
    _color_key = None
    _base_key = None
    _base_payload = None
    _last_rgba = None
    _live_dirty = False
    _recompute_and_write(obj, force=True)

    _active = True
    _tag_redraw_all()


def _stop_handlers():
    global _active, _last_rgba, _live_dirty
    if not _active:
        return
    from ..weight_apply.public_api import set_display_bone_colors
    set_display_bone_colors(None, None)
    _remove_color_layer(bpy.context.active_object)

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
    if viewport_overrides.apply_view3d(_PAINT_WIRE_OWNER, {"overlay.show_paint_wire": True}, once=True):
        _tag_redraw_all()


def _restore_paint_wire() -> None:
    if viewport_overrides.restore(_PAINT_WIRE_OWNER):
        _tag_redraw_all()


def _watcher_tick():
    global _suppressed, _ineligible_streak, _flat_ineligible_streak, _live_dirty
    if _user_enabled:
        obj = bpy.context.active_object
        wm = bpy.context.window_manager
        eligible = (
            CoreFacade.in_session(obj)
            and getattr(wm, "superskin_active_interface", "LAYER") == "SKINNING"
        )
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
        obj = bpy.context.active_object
        if not (_live_dirty and obj is not None and _addon_stroke_active(obj)):
            _recompute_and_write(obj, force=_live_dirty)
            _live_dirty = False

    obj = bpy.context.active_object
    wm = bpy.context.window_manager
    eligible_wp = (
        CoreFacade.in_session(obj)
        and getattr(wm, "superskin_active_interface", "LAYER") == "SKINNING"
    )
    if eligible_wp:
        _flat_ineligible_streak = 0
        _apply_paint_wire()
    elif viewport_overrides.is_applied(_PAINT_WIRE_OWNER):
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
