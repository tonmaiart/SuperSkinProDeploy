
import time

import bpy
import numpy as np

from ...core.facade import CoreFacade
from ...interface.utils.gpu_utils import BONE_COLORS
from . import multi_color_draw as _bone_preview

_NO_HIGHLIGHT = object()

_LAYER_NAME = "__ssp_multi_mask_preview"

_active = False
_bound_obj = None
_user_enabled = False
_watch_timer_registered = False

_eligible_streak = 0
_ELIGIBLE_DEBOUNCE_TICKS = 3

_base_key = None
_base_rgb = None
_MIN_COLOR_RECOMPUTE_INTERVAL = 0.08
_last_color_compute_time = 0.0
_color_key = None


def _is_eligible(obj) -> bool:
    wm = bpy.context.window_manager
    return (CoreFacade.in_session(obj)
            and getattr(wm, "superskin_active_interface", "LAYER") == "SKINNING"
            and "ss_layers_meta" in obj.data
            and not _bone_preview._active)


def _resolve_target_mesh():
    obj = bpy.context.active_object
    return obj if _is_eligible(obj) else None


def _with_target_active(mesh_obj, fn):
    view_layer = bpy.context.view_layer
    original_active = view_layer.objects.active
    if original_active is mesh_obj:
        return fn()
    try:
        view_layer.objects.active = mesh_obj
        return fn()
    finally:
        view_layer.objects.active = original_active


def can_toggle() -> bool:
    return _is_eligible(_resolve_target_mesh())


def _layer_color(index: int):
    return BONE_COLORS[index % len(BONE_COLORS)] if BONE_COLORS else None


def _mask_arrays(mask: dict, num_verts: int):
    verts = np.fromiter(mask.keys(), dtype=np.int64, count=len(mask))
    values = np.fromiter(mask.values(), dtype=np.float64, count=len(mask))
    keep = (verts >= 0) & (verts < num_verts) & (values > 0.0)
    return verts[keep], values[keep]


def _resolve_highlight_index():
    idx = getattr(bpy.context.window_manager, "superskin_layer_picker_hover_index", -2)
    if idx == -2:
        return None
    if idx == -1:
        return _NO_HIGHLIGHT
    return idx


def _get_base_rgb(obj, facade, num_verts) -> np.ndarray:
    global _base_key, _base_rgb
    key = (obj.name, id(obj.data), num_verts, obj.get(CoreFacade.DEFORM_GEN_KEY, 0),
           tuple((item.index, item.visible) for item in obj.superskin_layers_collection))
    if key == _base_key and _base_rgb is not None:
        return _base_rgb
    accum = np.zeros((num_verts, 3))
    total = np.zeros(num_verts)
    for item in obj.superskin_layers_collection:
        color = _layer_color(item.index)
        if not item.visible or color is None:
            continue
        mask = facade.get_layer_mask_dict(item.index)
        if not mask:
            continue
        verts, values = _mask_arrays(mask, num_verts)
        accum[verts] += values[:, None] * np.asarray(color, dtype=np.float64)
        total[verts] += values
    scale = np.where(total > 1.0, 1.0 / np.maximum(total, 1e-12), 1.0)
    _base_key, _base_rgb = key, accum * scale[:, None]
    return _base_rgb


def _compute_rgba(obj) -> np.ndarray:
    num_verts = len(obj.data.vertices)
    facade = CoreFacade(bpy.context)
    rgb = _get_base_rgb(obj, facade, num_verts).copy()
    highlight = _resolve_highlight_index()
    if highlight is not _NO_HIGHLIGHT:
        index = facade.get_active_layer_index() if highlight is None else highlight
        mask = facade.get_layer_mask_dict(index)
        if mask:
            verts, values = _mask_arrays(mask, num_verts)
            lit = values > 0.01
            verts, t = verts[lit], values[lit] * 0.95
            rgb[verts] += (1.0 - rgb[verts]) * t[:, None]
    rgba = np.ones((num_verts, 4), dtype=np.float32)
    rgba[:, :3] = rgb
    return rgba


def _make_key(obj):
    vis_key = tuple((item.index, item.visible) for item in obj.superskin_layers_collection)
    return (obj.name, id(obj.data), len(obj.data.vertices),
            obj.get(CoreFacade.DEFORM_GEN_KEY, 0), vis_key, CoreFacade.temp_layer_index(obj),
            _resolve_highlight_index())


def _recompute_and_write(obj, force: bool = False) -> None:
    global _color_key, _last_color_compute_time
    if not _is_eligible(obj):
        return
    key = _make_key(obj)
    if not force and key == _color_key:
        return
    now = time.monotonic()
    if not force and (now - _last_color_compute_time) < _MIN_COLOR_RECOMPUTE_INTERVAL:
        return
    from ..weight_apply.public_api import set_display_bone_colors
    set_display_bone_colors(obj.name, _compute_rgba(obj))
    _color_key = key
    _last_color_compute_time = now
    _bone_preview._tag_redraw_all()


def _remove_color_layer(obj) -> None:
    if obj is None or obj.type != 'MESH':
        return
    try:
        attr = obj.data.color_attributes.get(_LAYER_NAME)
        if attr is not None:
            obj.data.color_attributes.remove(attr)
    except Exception:
        pass


def _start_handlers():
    global _active, _bound_obj, _color_key, _base_key, _base_rgb
    if _active:
        return
    obj = _resolve_target_mesh()
    if not _is_eligible(obj):
        return
    _color_key = None
    _base_key = None
    _base_rgb = None
    _remove_color_layer(obj)
    _recompute_and_write(obj, force=True)
    _bound_obj = obj
    _active = True
    _bone_preview._tag_redraw_all()


def _stop_handlers():
    global _active, _bound_obj
    if not _active:
        return
    if not _bone_preview._active:
        from ..weight_apply.public_api import set_display_bone_colors
        set_display_bone_colors(None, None)
    _remove_color_layer(_bound_obj)
    _bound_obj = None
    _active = False
    _bone_preview._tag_redraw_all()


def start():
    global _user_enabled, _eligible_streak
    _user_enabled = True
    _eligible_streak = 0
    _start_handlers()


def stop():
    global _user_enabled
    _user_enabled = False
    _stop_handlers()


def toggle():
    stop() if _user_enabled else start()


def is_enabled() -> bool:
    return _user_enabled


_WATCH_INTERVAL = 0.1


def _watcher_tick():
    global _eligible_streak
    if _user_enabled:
        obj = _resolve_target_mesh()
        eligible = _is_eligible(obj)
        if _active and eligible and obj is not _bound_obj:
            _stop_handlers()
        _eligible_streak = _eligible_streak + 1 if eligible else 0
        if not eligible:
            if _active:
                _stop_handlers()
        elif not _active and _eligible_streak >= _ELIGIBLE_DEBOUNCE_TICKS:
            _start_handlers()
    if _active:
        _recompute_and_write(_bound_obj)
    return _WATCH_INTERVAL


def cleanup():
    global _user_enabled
    _user_enabled = False
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
