"""Pushes SuperSkinPro's own ramps into Blender's *native* weight-paint color pipeline instead
of drawing a custom GPU overlay."""

import bpy

from . import _ramp_io
from . import multi_color_draw

_EDIT_RAMP_STOPS = [
    (0.0,  (0.0, 0.0, 0.0)),
    (0.0001,  (0.0, 0.0, 1.0)),
    (0.25, (0.0, 1.0, 1.0)),
    (0.5,  (0.0, 1.0, 0.0)),
    (0.75, (1.0, 1.0, 0.0)),
    (0.9999,  (1.0, 0.0, 0.0)),
    (1.0,  (0.9, 0.9, 0.9)),
]
_MASK_RAMP_STOPS = [
    (0.00, (0x00 / 255, 0x00 / 255, 0x00 / 255)),  # #000000 black
    (0.30, (0x7A / 255, 0x00 / 255, 0x3C / 255)),  # #7A003C deep berry
    (0.65, (0xFF / 255, 0x14 / 255, 0x93 / 255)),  # #FF1493 neon/hot pink
    (0.88, (0xFF / 255, 0xB6 / 255, 0xD9 / 255)),  # #FFB6D9 pastel pink
    (1.00, (0.9, 0.9, 0.9)),  # light gray
]

_active = False
_watch_timer_registered = False

_orig_use_weight_color_range = None
_orig_stops = None
_orig_show_weight = None  # bool, or None while inactive

_last_pushed_ramp_id = None  # "edit" / "mask" — which ramp is currently live
_last_pushed_stop_signature = None  # detects live edits to the active ramp



def _active_obj_is_mask() -> bool:
    obj = bpy.context.active_object
    storage = getattr(obj, "superskin_storage", None) if obj is not None else None
    return bool(storage is not None and storage.active_is_mask)


def _get_ramp_stops(ramp_id: str) -> list:
    """Return the hardcoded stops for ``"edit"`` or ``"mask"``."""
    return _EDIT_RAMP_STOPS if ramp_id == "edit" else _MASK_RAMP_STOPS


def _should_be_active() -> bool:
    if multi_color_draw.is_enabled():
        return False
    wm = bpy.context.window_manager
    if getattr(wm, "superskin_active_interface", "LAYER") != "SKINNING":
        return False
    obj = bpy.context.active_object
    return obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'


def _tag_redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()



def _push_ramp(ramp_id: str) -> None:
    global _last_pushed_ramp_id, _last_pushed_stop_signature
    stops = _get_ramp_stops(ramp_id)
    if not stops:
        return
    view_prefs = bpy.context.preferences.view
    _ramp_io.write_stops(view_prefs.weight_color_range, stops)
    _last_pushed_ramp_id = ramp_id
    _last_pushed_stop_signature = tuple(stops)
    _tag_redraw_all()


def _start():
    global _active, _orig_use_weight_color_range, _orig_stops, _orig_show_weight
    global _last_pushed_ramp_id, _last_pushed_stop_signature
    if _active:
        return

    view_prefs = bpy.context.preferences.view
    _orig_use_weight_color_range = view_prefs.use_weight_color_range
    _orig_stops = _ramp_io.read_stops(view_prefs.weight_color_range)

    _orig_show_weight = None
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                try:
                    space = area.spaces.active
                    if _orig_show_weight is None:
                        _orig_show_weight = space.overlay.show_weight
                    space.overlay.show_weight = True
                except Exception:
                    pass

    view_prefs.use_weight_color_range = True
    _last_pushed_ramp_id = None
    _last_pushed_stop_signature = None
    _push_ramp("mask" if _active_obj_is_mask() else "edit")

    _active = True


def _stop():
    global _active, _orig_use_weight_color_range, _orig_stops, _orig_show_weight
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

    if _orig_show_weight is not None:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    try:
                        space = area.spaces.active
                        space.overlay.show_weight = _orig_show_weight
                    except Exception:
                        pass

    _orig_use_weight_color_range = None
    _orig_stops = None
    _orig_show_weight = None
    _last_pushed_ramp_id = None
    _last_pushed_stop_signature = None
    _active = False
    _tag_redraw_all()


_WATCH_INTERVAL = 0.1


def _watcher_tick():
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

    return _WATCH_INTERVAL


def cleanup():
    """Restore native state and stop — called from domain unregister()."""
    _stop()


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
