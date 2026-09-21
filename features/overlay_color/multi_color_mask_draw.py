"""Multi-color (rainbow) per-Layer MASK visualizer for SuperSkinPro."""

import time
import bpy

from ...interface.utils.gpu_utils import BONE_COLORS
from . import multi_color_draw as _bone_preview

_NO_HIGHLIGHT = object()

_LAYER_NAME = "__ssp_multi_mask_preview"
_HUD_LABEL = "Mask Multi Color"
_HUD_COLOR = (1.0, 0.8, 0.0, 1.0)
_HUD_OWNER_ID = "overlay_color_mask"
_HUD_SLOT = 4
_last_hud_state = None  # None (no line) | 'ON'

_active = False
_bound_obj = None
_user_enabled = False
_watch_timer_registered = False

_eligible_streak = 0
_ELIGIBLE_DEBOUNCE_TICKS = 3

_base_key = None
_base_vert_colors = None
_MIN_COLOR_RECOMPUTE_INTERVAL = 0.08  # ~12Hz ceiling
_last_color_compute_time = 0.0
_color_key = None



def _is_eligible(obj) -> bool:
    return (obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
            and "ss_layers_meta" in obj.data
            and not _bone_preview._active)


def _resolve_target_mesh():
    """The Mesh this preview should act on: the active object itself, while it's a Mesh in Edit
    Mode with a layer."""
    obj = bpy.context.active_object
    return obj if _is_eligible(obj) else None


def _with_target_active(mesh_obj, fn):
    """Kept for `ops.py`'s call sites."""
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
    """Whether the current context has a resolvable target Mesh for this preview."""
    return _is_eligible(_resolve_target_mesh())


def _compute_layer_colors_map(obj) -> dict:
    """{layer storage index (int): (r, g, b)}."""
    n_colors = len(BONE_COLORS)
    if n_colors == 0:
        return {}
    return {item.index: BONE_COLORS[item.index % n_colors]
            for item in obj.superskin_layers_collection}


def _get_active_layer_index(obj) -> int:
    """The in-session active Layer's storage index while in Edit Mode."""
    return int(obj.get("__ssp_meta_layer", 0))


def _resolve_highlight_index():
    """Live override for which Layer's mask gets the white highlight in
    ``_apply_active_highlight()`` below."""
    idx = getattr(bpy.context.window_manager, "superskin_layer_picker_hover_index", -2)
    if idx == -2:
        return None
    if idx == -1:
        return _NO_HIGHLIGHT
    return idx


def _read_masks_by_layer(obj):
    """Returns ``{layer storage index (int): {v_idx (int): raw mask value (float)}}`` for every
    *visible* Layer."""
    from ...core.facade import CoreFacade

    facade = CoreFacade(bpy.context)
    original_index = facade.get_active_layer_index()
    result = {}
    try:
        for item in obj.superskin_layers_collection:
            if not item.visible:
                continue
            facade.switch_to_layer(item.index)
            mask_dict = facade.get_active_mask_dict()
            if mask_dict:
                result[item.index] = mask_dict
    finally:
        facade.switch_to_layer(original_index)
    return result


def _read_active_mask(obj, highlight_index=None):
    """Returns ``{v_idx (int): mask_value (float)}`` for whichever single Layer gets the white
    highlight."""
    from ...core.facade import CoreFacade

    if highlight_index is _NO_HIGHLIGHT:
        return {}

    facade = CoreFacade(bpy.context)
    if highlight_index is None:
        return facade.get_active_mask_dict()

    original_index = facade.get_active_layer_index()
    if highlight_index == original_index:
        return facade.get_active_mask_dict()
    facade.switch_to_layer(highlight_index)
    mask = facade.get_active_mask_dict()
    facade.switch_to_layer(original_index)
    return mask



def _apply_active_highlight(vert_colors, active_mask) -> list:
    """Blend each vertex's already-computed color toward white by the active Layer's own mask
    value at that vertex."""
    if not active_mask:
        return vert_colors

    out = list(vert_colors)
    for v_idx, m in active_mask.items():
        if v_idx < 0 or v_idx >= len(out):
            continue
        m = float(m)
        if m <= 0.01:
            continue
        r, g, b = out[v_idx]
        t = m * 0.95
        r += (1.0 - r) * t
        g += (1.0 - g) * t
        b += (1.0 - b) * t
        out[v_idx] = (r, g, b)
    return out


def _compute_vert_colors(obj, num_verts) -> list:
    """Per vertex, blend every visible Layer's own palette color weighted by its RAW mask value
    there (see ``_get_base_vert_colors()``), then highlight the active."""
    highlight_idx = _resolve_highlight_index()

    base_vert_colors = _get_base_vert_colors(obj, num_verts)
    active_mask = _read_active_mask(obj, highlight_idx)

    return _apply_active_highlight(base_vert_colors, active_mask)


def _get_base_vert_colors(obj, num_verts) -> list:
    """Every visible Layer's RAW mask, decoded and blended into a per-vertex base color by a
    straight weighted average (normalized only when the weights at a vertex sum above ``1.0``)."""
    global _base_key, _base_vert_colors

    key = (obj.name, id(obj.data), num_verts, obj.get("__ssp_deform_gen", 0),
           tuple((item.index, item.visible) for item in obj.superskin_layers_collection))
    if key == _base_key and _base_vert_colors is not None:
        return _base_vert_colors

    color_map = _compute_layer_colors_map(obj)
    masks_by_layer = _read_masks_by_layer(obj)

    vert_colors = [(0.0, 0.0, 0.0)] * num_verts
    if color_map and masks_by_layer:
        # accum[v] = [r, g, b, total_weight]
        accum = [[0.0, 0.0, 0.0, 0.0] for _ in range(num_verts)]
        for layer_idx, mask_dict in masks_by_layer.items():
            color = color_map.get(layer_idx)
            if color is None:
                continue
            cr, cg, cb = color
            for v_idx, m in mask_dict.items():
                if v_idx < 0 or v_idx >= num_verts:
                    continue
                m = float(m)
                if m <= 0.0:
                    continue
                acc = accum[v_idx]
                acc[0] += cr * m
                acc[1] += cg * m
                acc[2] += cb * m
                acc[3] += m

        for v_idx, acc in enumerate(accum):
            total = acc[3]
            if total <= 0.0:
                continue
            r, g, b = acc[0], acc[1], acc[2]
            if total > 1.0:
                inv = 1.0 / total
                r *= inv
                g *= inv
                b *= inv
            vert_colors[v_idx] = (r, g, b)

    _base_key = key
    _base_vert_colors = vert_colors
    return vert_colors



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


def _make_key(obj):
    vis_key = tuple((item.index, item.visible) for item in obj.superskin_layers_collection)
    active_idx = _get_active_layer_index(obj)
    highlight_idx = _resolve_highlight_index()
    return (obj.name, id(obj.data), len(obj.data.vertices),
            obj.get("__ssp_deform_gen", 0), vis_key, active_idx, highlight_idx)


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

    num_verts = len(obj.data.vertices)
    vert_colors = _compute_vert_colors(obj, num_verts)
    _write_colors(obj, vert_colors)
    _color_key = key
    _last_color_compute_time = now
    _bone_preview._tag_redraw_all()



def _sync_hud() -> None:
    global _last_hud_state
    state = 'ON' if _active else None
    if state == _last_hud_state:
        return

    from ...core.facade import CoreFacade
    if state == 'ON':
        CoreFacade.request_hud_slot(_HUD_OWNER_ID, _HUD_LABEL, slot=_HUD_SLOT, color=_HUD_COLOR)
    else:
        CoreFacade.release_hud_slot(_HUD_OWNER_ID)
    _last_hud_state = state



def _start_handlers():
    global _active, _bound_obj, _color_key, _base_key, _base_vert_colors
    if _active:
        return
    obj = _resolve_target_mesh()
    if not _is_eligible(obj):
        return

    _color_key = None
    _base_key = None
    _base_vert_colors = None
    _bone_preview._snapshot_active_color_index(obj)
    _bone_preview._snapshot_and_apply_shading()
    _bone_preview._snapshot_and_disable_vg_weights_overlay()
    _bone_preview._snapshot_and_force_view_transform()
    _recompute_and_write(obj, force=True)

    _bound_obj = obj
    _active = True
    _sync_hud()
    _bone_preview._tag_redraw_all()


def _stop_handlers():
    global _active, _bound_obj
    if not _active:
        return
    obj = _bound_obj
    _bone_preview._restore_shading()
    _bone_preview._restore_vg_weights_overlay()
    _bone_preview._restore_view_transform()
    _bone_preview._restore_active_color_index(obj)
    _remove_color_layer(obj)

    _bound_obj = None
    _active = False
    _sync_hud()
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
        _bone_preview._enforce_active_overrides()
        _recompute_and_write(_bound_obj)

    return _WATCH_INTERVAL


def cleanup():
    """Remove all handlers and restore native state — called from unregister()."""
    global _user_enabled, _last_hud_state
    _user_enabled = False
    _stop_handlers()
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
