"""Pushes SuperSkinPro's own ramps into Blender's *native* weight-paint color
pipeline instead of drawing a custom GPU overlay.

The project is moving away from custom GPU shader draw entirely (the old
`core/shaders/shader_manager.py` weight-color path was already retired for
this reason once before — see README.md's "History" section), so this
module never touches `gpu`/`bmesh` draw handlers. It only writes into two
real Blender preferences while the addon's own "Edit Layer Weight" mode is
active:

  - `context.preferences.view.weight_color_range` (a `ColorRamp`) — set to
    `_EDIT_RAMP_STOPS` or `_MASK_RAMP_STOPS` below (whichever applies).
  - `context.preferences.view.use_weight_color_range` (bool) — forced on.
  - `overlay.show_weight` on every VIEW_3D viewport — forced on, so the
    native overlay actually renders the ramp. This is the RNA property
    backing the UI checkbox labeled "Vertex Group Weights" (see
    `multi_color_draw.py`'s module docstring and docs/bug-history/0027 for
    the naming gotcha this was diagnosed from -- there is no separate
    `show_vertex_group_weights` property).

This domain's own two ramps (`_EDIT_RAMP_STOPS` / `_MASK_RAMP_STOPS` below)
are hardcoded constants, not user-editable settings -- there is no longer a
settings UI or JSON persistence for them at all.

Which vertex group the native overlay colors by (the active one) is not
this module's concern — SuperSkinPro's own bone-selection logic already
keeps the real `obj.vertex_groups.active_index` synced to whichever bone
(or the mask's temp VG) is selected in the Deform Bones list; that's how
this display was already able to show *something* meaningful through the
native overlay before this domain owned its own ramps.

**Yields to Multi Color Preview.** `multi_color_draw.py` (this domain's
other overlay-color mode, Alt+3) drives its own, different native mechanism
(`space.shading.type`/`color_type`, not `overlay.show_weight`) — the two
would visually stack if both were live at once, so `_should_be_active()`
below refuses to activate (and the watcher stops itself) whenever
`multi_color_draw.is_enabled()` is True. One-directional dependency only —
`multi_color_draw.py` never imports this module back.

**Non-destructive by construction:** every native value this module writes
is snapshotted the moment the addon's edit mode is entered, and restored
exactly on exit — so a user's own Preferences > Editing > "Custom Weight
Paint Range" configuration is completely unaffected outside an active
SuperSkinPro edit session.
"""

import bpy

from . import _ramp_io
from . import multi_color_draw

# Hardcoded ramp stops -- (position, (r, g, b)), sorted by position. Not
# user-editable; there is no settings UI or JSON persistence for these
# anymore (see this module's docstring).
_EDIT_RAMP_STOPS = [
    (0.0,  (0.0, 0.0, 0.0)),
    (0.0001,  (0.0, 0.0, 1.0)),
    (0.25, (0.0, 1.0, 1.0)),
    (0.5,  (0.0, 1.0, 0.0)),
    (0.75, (1.0, 1.0, 0.0)),
    (0.9999,  (1.0, 0.0, 0.0)),
    (1.0,  (1.0, 1.0, 1.0)),
]
_MASK_RAMP_STOPS = [
    (0.0, (0.0, 0.0, 0.0)),
    (1.0, (1.0, 1.0, 1.0)),
]

_active = False
_watch_timer_registered = False

# Snapshot of the user's own native state, captured on activation and
# restored on deactivation. None while inactive. _orig_show_weight is
# deliberately a single value, not a per-space dict keyed by identity -- see
# multi_color_draw.py's module-level comment above its own _orig_shading for
# why (space.as_pointer() was diagnosed as unreliable across separate calls
# in this codebase; see docs/bug-history).
_orig_use_weight_color_range = None
_orig_stops = None
_orig_show_weight = None  # bool, or None while inactive

_last_pushed_ramp_id = None  # "edit" / "mask" — which ramp is currently live
_last_pushed_stop_signature = None  # detects live edits to the active ramp


# ═══════════════════════════════════════════════════════════════════════════
#  Data access — no core/ imports, no cross-*feature* imports (multi_color_draw
#  is a sibling module in this same domain, not a different feature package)
# ═══════════════════════════════════════════════════════════════════════════

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
    return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'


def _tag_redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ═══════════════════════════════════════════════════════════════════════════
#  Lifecycle
# ═══════════════════════════════════════════════════════════════════════════

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
