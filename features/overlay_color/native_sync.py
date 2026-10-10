
import json

import bpy

from ...interface.utils import viewport_overrides
from . import _ramp_io
from . import multi_color_draw

_EDIT_RAMP_STOPS = [
    (0.0,  (0.0, 0.0, 0.0)),
    (0.0001,  (0.0, 0.0, 1.0)),
    (0.25, (0.0, 1.0, 1.0)),
    (0.5,  (0.0, 1.0, 0.0)),
    (0.75, (1.0, 1.0, 0.0)),
    (0.9999,  (1.0, 0.0, 0.0)),
    (1.0,  (0.85, 0.85, 0.85)),
]
_MASK_RAMP_STOPS = [
    (0.00, (0x00 / 255, 0x00 / 255, 0x00 / 255)),
    (0.30, (0x7A / 255, 0x00 / 255, 0x3C / 255)),
    (0.65, (0xFF / 255, 0x14 / 255, 0x93 / 255)),
    (0.88, (0xFF / 255, 0xB6 / 255, 0xD9 / 255)),
    (1.00, (0.85, 0.85, 0.85)),
]

_active = False
_watch_timer_registered = False

_orig_use_weight_color_range = None
_orig_stops = None

_OVERLAY_OWNER = "overlay_color.ramp_overlay"

_last_pushed_ramp_id = None
_last_pushed_stop_signature = None

_SCENE_SNAPSHOT_KEY = "__ssp_ramp_snapshot"


def _sync_scene_snapshot():
    scene = bpy.context.scene
    if scene is None:
        return
    if not _active:
        if _SCENE_SNAPSHOT_KEY in scene:
            del scene[_SCENE_SNAPSHOT_KEY]
        return
    scene[_SCENE_SNAPSHOT_KEY] = json.dumps({
        "use_weight_color_range": _orig_use_weight_color_range,
        "stops": [[pos, list(rgb)] for pos, rgb in (_orig_stops or [])],
    })


def _read_scene_snapshot(scene):
    raw = scene.get(_SCENE_SNAPSHOT_KEY) if scene is not None else None
    if not isinstance(raw, str):
        return None
    try:
        snap = json.loads(raw)
    except ValueError:
        return None
    return snap if isinstance(snap, dict) else None


def _is_addon_ramp(stops) -> bool:
    if not stops:
        return False
    for own in (_EDIT_RAMP_STOPS, _MASK_RAMP_STOPS):
        if len(stops) != len(own):
            continue
        if all(
            abs(pos - own_pos) < 1e-3 and all(abs(c - o) < 1e-3 for c, o in zip(rgb[:3], own_rgb))
            for (pos, rgb), (own_pos, own_rgb) in zip(stops, own)
        ):
            return True
    return False


def _sanitize_orig_ramp():
    global _orig_use_weight_color_range, _orig_stops
    if _is_addon_ramp(_orig_stops):
        _orig_stops = None
        _orig_use_weight_color_range = False


def _active_obj_is_mask() -> bool:
    obj = bpy.context.active_object
    storage = getattr(obj, "superskin_storage", None) if obj is not None else None
    return bool(storage is not None and storage.active_is_mask)


def _get_ramp_stops(ramp_id: str) -> list:
    return _EDIT_RAMP_STOPS if ramp_id == "edit" else _MASK_RAMP_STOPS


def _should_be_active() -> bool:
    if multi_color_draw.is_drawing():
        return False
    wm = bpy.context.window_manager
    if getattr(wm, "superskin_active_interface", "LAYER") != "SKINNING":
        return False
    obj = bpy.context.active_object
    return obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'


def _tag_redraw_all():
    from ...core.facade import CoreFacade
    CoreFacade.tag_redraw_areas()


def _tag_weight_paint_objects():
    view_layer = bpy.context.view_layer
    if view_layer is None:
        return
    for obj in view_layer.objects:
        if obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT':
            obj.update_tag(refresh={'DATA'})


def _push_ramp(ramp_id: str) -> None:
    global _last_pushed_ramp_id, _last_pushed_stop_signature
    stops = _get_ramp_stops(ramp_id)
    if not stops:
        return
    view_prefs = bpy.context.preferences.view
    _ramp_io.write_stops(view_prefs.weight_color_range, stops)
    _last_pushed_ramp_id = ramp_id
    _last_pushed_stop_signature = tuple(stops)
    _tag_weight_paint_objects()
    _tag_redraw_all()


def _start():
    global _active, _orig_use_weight_color_range, _orig_stops
    global _last_pushed_ramp_id, _last_pushed_stop_signature
    if _active:
        return

    view_prefs = bpy.context.preferences.view
    _orig_use_weight_color_range = view_prefs.use_weight_color_range
    _orig_stops = _ramp_io.read_stops(view_prefs.weight_color_range)
    _sanitize_orig_ramp()

    viewport_overrides.apply_view3d(_OVERLAY_OWNER, {
        "overlay.show_weight": True,
        "overlay.weight_paint_mode_opacity": 1.0,
    })

    view_prefs.use_weight_color_range = True
    _last_pushed_ramp_id = None
    _last_pushed_stop_signature = None
    _push_ramp("mask" if _active_obj_is_mask() else "edit")

    _active = True
    _sync_scene_snapshot()


def _stop():
    global _active, _orig_use_weight_color_range, _orig_stops
    global _last_pushed_ramp_id, _last_pushed_stop_signature
    if not _active:
        return

    view_prefs = bpy.context.preferences.view
    try:
        if _orig_stops:
            _ramp_io.write_stops(view_prefs.weight_color_range, _orig_stops)
        view_prefs.use_weight_color_range = _orig_use_weight_color_range
    except Exception:
        pass

    viewport_overrides.restore(_OVERLAY_OWNER)

    _orig_use_weight_color_range = None
    _orig_stops = None
    _last_pushed_ramp_id = None
    _last_pushed_stop_signature = None
    _active = False
    _sync_scene_snapshot()
    _tag_weight_paint_objects()
    _tag_redraw_all()


_WATCH_INTERVAL = 0.4


def _reconcile():
    should = _should_be_active()
    if should and not _active:
        _start()
    elif not should and _active:
        _stop()

    if _active:
        ramp_id = "mask" if _active_obj_is_mask() else "edit"
        stops = tuple(_get_ramp_stops(ramp_id))
        if ramp_id != _last_pushed_ramp_id or stops != _last_pushed_stop_signature:
            _push_ramp(ramp_id)


@bpy.app.handlers.persistent
def _on_depsgraph_update(*_):
    if not _active:
        return
    try:
        ramp_id = "mask" if _active_obj_is_mask() else "edit"
        if ramp_id != _last_pushed_ramp_id:
            _push_ramp(ramp_id)
    except Exception:
        pass


def _watcher_tick():
    _reconcile()
    return _WATCH_INTERVAL


def release():
    _stop()


def sync_now():
    _reconcile()


@bpy.app.handlers.persistent
def _on_undo_redo(*_):
    try:
        _reconcile()
    except Exception:
        pass


def _run_load_post_recovery():
    global _active, _orig_use_weight_color_range, _orig_stops
    scene = bpy.context.scene if bpy.context else None
    if scene is None:
        return None
    snap = _read_scene_snapshot(scene)
    if snap is None:
        if _SCENE_SNAPSHOT_KEY in scene:
            del scene[_SCENE_SNAPSHOT_KEY]
        return None

    _orig_use_weight_color_range = snap.get("use_weight_color_range")
    stops = snap.get("stops")
    _orig_stops = [(pos, tuple(rgb)) for pos, rgb in stops] if stops else None
    _sanitize_orig_ramp()
    _active = True
    _stop()
    return None


@bpy.app.handlers.persistent
def _on_load_post(*_):
    bpy.app.timers.register(_run_load_post_recovery, first_interval=0.0)


def cleanup():
    _stop()


def register():
    global _watch_timer_registered
    if not _watch_timer_registered:
        bpy.app.timers.register(_watcher_tick, first_interval=_WATCH_INTERVAL, persistent=True)
        _watch_timer_registered = True
    bpy.app.handlers.undo_post.append(_on_undo_redo)
    bpy.app.handlers.redo_post.append(_on_undo_redo)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)


def unregister():
    global _watch_timer_registered
    if _watch_timer_registered and bpy.app.timers.is_registered(_watcher_tick):
        bpy.app.timers.unregister(_watcher_tick)
    _watch_timer_registered = False
    try:
        bpy.app.handlers.load_post.remove(_on_load_post)
    except Exception:
        pass
    try:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    except Exception:
        pass
    try:
        bpy.app.handlers.undo_post.remove(_on_undo_redo)
    except Exception:
        pass
    try:
        bpy.app.handlers.redo_post.remove(_on_undo_redo)
    except Exception:
        pass
    cleanup()
