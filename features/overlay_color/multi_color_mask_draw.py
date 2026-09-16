"""Multi-color (rainbow) per-Layer MASK visualizer for SuperSkinPro --
the Edit Mask sub-mode counterpart to ``multi_color_draw.py``'s per-bone
Edit Mode preview, so every visible Layer's mask coverage can be read at a
glance while painting a mask, the same way ``multi_color_draw.py`` already
does for per-bone weights while painting weights.

**Edit Mode only (2026-09-11, per explicit user request) -- Object/Pose
Mode support dropped entirely.** This preview used to run in Object Mode
(and, via the active Armature, Pose Mode) so a posed/deformed mesh's mask
coverage could be read without entering Edit Layer Weight at all --
``_resolve_target_mesh()`` scanned for a Mesh directly or via the active
Armature, and ``_with_target_active()`` temporarily made that Mesh the
active object for every ``CoreFacade`` call. None of that is needed
anymore: the target is always ``bpy.context.active_object`` itself, mirroring
``multi_color_draw.py``'s own simplicity. See ``docs/bug-history/0036`` for
the actual bug (a stuck ``layer_picker`` modal) this session started from,
and ``docs/domains/overlay_color.md``'s "Mask Multi Color moved to Edit
Mode" section for the full rationale of this follow-up change.

**Blends by simple weighted average, not the compositor's alpha-composite
(2026-09-11).** The Object-Mode version used
``CoreFacade.get_flattened_mask_dict()`` -- each visible Layer's mask "cut"
down to the fraction that survives the real compositor's top-down
alpha-blend against every Layer stacked above it -- for a real "over"
composite. That method is documented (``docs/core-interfaces/facade-api.md``)
as always reading Object-Mode ``ss_mask_N`` storage, with no Edit-Mode
``__ssp_*`` temp-VG routing at all -- extending it would mean editing
``core/``, which this feature-side fix avoids per the Core Boundary Rule.
Instead, each visible Layer's RAW mask is read the same Edit-Mode-safe way
``docs/bug-history/0035`` already established for ``layer_picker``'s own
per-Layer scan (``CoreFacade.switch_to_layer()`` + ``get_active_mask_dict()``,
not the raw ``active_layer_index`` setter, which skips Edit Mode's temp-VG
load entirely), then every visible Layer's color is blended by a straight
weighted average -- normalized only when the weights at a vertex sum above
``1.0`` -- exactly the scheme ``multi_color_draw.py``'s
``_compute_base_colors()`` already uses for per-bone weights. This trades
away real occlusion/stacking-order fidelity (two heavily-overlapping Layers
now blend toward gray/brown at their shared boundary, the same failure mode
the Object-Mode compositor scheme was originally built to dodge -- see
that scheme's own history in this file's git log) for staying entirely
inside ``features/`` with no ``core/`` change and no new facade method --
acceptable here since the point of this preview during a `layer_picker`
hold is "which Layer wins at this vertex," not a pixel-accurate render of
the final composited result.

**Dropped: the raw/flattened Middle-Mouse toggle.** The Object-Mode version
let holding Middle Mouse during a hover switch the highlighted Layer's
value between its RAW and FLATTENED/cut mask (``_is_mmb_held()``,
``WindowManager.superskin_layer_picker_show_raw_mask``). Edit Mode has no
flattened value available at all now (see above), so both sides of that
toggle would show the exact same thing -- the distinction was removed
entirely rather than kept as a no-op, and the (since fully removed)
`layer_picker` domain's own MIDDLEMOUSE handling was folded back into its
plain PASS_THROUGH bucket before that domain was deleted outright; the
flag itself is no longer registered on `WindowManager` either.

Colors are written into a temporary, addon-owned BMesh POINT-domain
float-color layer (Edit Mode only, same mechanism `multi_color_draw.py`
uses -- not the ``mesh.color_attributes`` API the old Object-Mode version
used, which only works outside Edit Mode) and displayed via Blender's own
native Solid shading + "Attribute" color mode. Every native
viewport/shading override this preview applies is snapshotted and restored
via `multi_color_draw.py`'s own already-shipped helpers
(`_snapshot_and_apply_shading()`/`_restore_shading()`, etc.) -- a
same-package import (both modules live under `features/overlay_color/`),
not a cross-feature one, so this doesn't fall under the project's Zero
Cross-Imports rule (see `docs/domains/overlay_color.md`).

**Mutually exclusive with `multi_color_draw.py`'s own bone preview.**
`_is_eligible()` also requires `not _bone_preview._active` -- both modules
force the exact same native shading override (Solid + Attribute + Flat)
and fight over which color attribute is "active" if both ever ran their
own recompute loop at once. In practice this rarely matters:
`multi_color_draw.py` already self-suppresses while
`obj.superskin_storage.active_is_mask` is `True` (masking has no bones to
show), which is also the only sub-mode this preview is meaningfully useful
in -- but a `layer_picker` (Alt+1) hold during a WEIGHT sub-mode session
with `multi_color_draw.py`'s own Alt+3 toggle already on would otherwise
hit this exact conflict, hence the explicit guard.
"""

import time
import bpy
import bmesh

from ...interface.utils.gpu_utils import BONE_COLORS
from . import multi_color_draw as _bone_preview

# Sentinel returned by _resolve_highlight_index() when a layer_picker
# session is active but currently hovering no Layer -- per explicit user
# request, this suppresses the white active-Layer highlight entirely
# (every visible Layer's own color still shows, just none of them
# brightened toward white) rather than falling back to the real active
# Layer. A plain object() identity sentinel, not a magic int, so it can
# never collide with a real Layer storage index.
_NO_HIGHLIGHT = object()

_LAYER_NAME = "__ssp_multi_mask_preview"
_HUD_LABEL = "Mask Multi Color"
_HUD_COLOR = (1.0, 0.8, 0.0, 1.0)  # matches bone_picker's HUD yellow -- see facade-api.md's reserved-slot table
_HUD_OWNER_ID = "overlay_color_mask"
_HUD_SLOT = 4  # next free row -- see docs/core-interfaces/facade-api.md's reserved-slot table
_last_hud_state = None  # None (no line) | 'ON'

_active = False
_bound_obj = None  # the Mesh this preview is currently applied to, or None while inactive
_user_enabled = False
_watch_timer_registered = False

# Debounce before auto-(re)starting after the watcher sees an eligible
# state -- guards against a brief internal mode bounce (see
# docs/bug-history/0013's class of bug) being mistaken for a genuine
# return to Edit Mode. Only the START trigger is debounced; STOP is
# immediate, since leaving this preview's overrides engaged a moment too
# long is worse than a moment's delay turning it on.
_eligible_streak = 0
_ELIGIBLE_DEBOUNCE_TICKS = 3

# Cache for the expensive part of _compute_vert_colors() -- every visible
# Layer's mask decoded and blended together. Keyed on everything that
# actually changes that blend (mask/visibility/topology/deform data),
# deliberately excluding the highlight index -- without this split, every
# hover tick during a layer_picker session (which changes highlight_idx on
# the _make_key() below) forced a full re-decode of every visible Layer's
# mask, even though hovering alone never changes any Layer's mask data --
# only which single Layer gets the white highlight. See
# _get_base_vert_colors() / _read_active_mask() below.
_base_key = None
_base_vert_colors = None
_MIN_COLOR_RECOMPUTE_INTERVAL = 0.08  # ~12Hz ceiling
_last_color_compute_time = 0.0
_color_key = None


# ═══════════════════════════════════════════════════════════════════════════
#  Data access — reads via CoreFacade only for mask decoding; everything
#  else (visibility, palette) reads mirrored Blender properties directly.
# ═══════════════════════════════════════════════════════════════════════════

def _is_eligible(obj) -> bool:
    return (obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'
            and "ss_layers_meta" in obj.data
            and not _bone_preview._active)


def _resolve_target_mesh():
    """The Mesh this preview should act on: the active object itself, while
    it's a Mesh in Edit Mode with a layer system and this doesn't conflict
    with `multi_color_draw.py`'s own bone preview. `None` otherwise. No
    Pose Mode / Armature resolution anymore -- Edit Mode only ever has one
    candidate object, the literal active one."""
    obj = bpy.context.active_object
    return obj if _is_eligible(obj) else None


def _with_target_active(mesh_obj, fn):
    """Kept for `ops.py`'s call sites -- always a no-op passthrough now
    that Pose Mode support is gone, since *mesh_obj* (from
    `_resolve_target_mesh()`) is always already `context.active_object` in
    Edit Mode. Not removed outright so `ops.py`'s toggle/start/stop
    operators (which still wrap their dispatch in this call) don't need
    their own changes."""
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
    """Whether the current context has a resolvable target Mesh for this
    preview -- backs ``SUPERSKIN_OT_toggle_multi_color_mask.poll()``."""
    return _is_eligible(_resolve_target_mesh())


def _compute_layer_colors_map(obj) -> dict:
    """{layer storage index (int): (r, g, b)} -- one palette color per
    Layer, assigned by storage slot (not display position) so a Layer's
    color stays stable across Move Up/Down reordering."""
    n_colors = len(BONE_COLORS)
    if n_colors == 0:
        return {}
    return {item.index: BONE_COLORS[item.index % n_colors]
            for item in obj.superskin_layers_collection}


def _get_active_layer_index(obj) -> int:
    """The in-session active Layer's storage index while in Edit Mode.
    ``obj.data.get("ss_active_layer", 0)`` is NOT kept live during an Edit
    Mode session -- a resident-Layer ``switch_to_layer()`` call never
    writes permanent storage (see ``docs/core-interfaces/facade-api.md``'s
    entry) -- so that property can read stale, whatever it was when Edit
    Mode was entered. ``__ssp_meta_layer`` is the same in-session pointer
    ``multi_color_draw.py``'s ``_read_active_layer()`` already relies on
    for the identical reason."""
    return int(obj.get("__ssp_meta_layer", 0))


def _resolve_highlight_index():
    """Live override for which Layer's mask gets the white highlight in
    ``_apply_active_highlight()`` below. Used to be fed by the (since
    removed, per a later explicit user request) `features/layer_picker`
    domain's Alt+1 (or `bone_picker`'s Alt+2 Mask redirect into it) modal
    via a WindowManager flag it wrote on every hover update -- not a direct
    import (Zero Cross-Imports forbids reaching into a sibling
    ``features/*`` package) -- so the highlight live-tracked whatever Layer
    was currently hovered during a picking session instead of staying
    pinned to the real active Layer. Now that nothing writes the flag, the
    ``getattr`` default below always applies and this always resolves to
    ``None`` (highlight the real active Layer) -- kept rather than deleted
    since it's harmless and the flag's shape is still documented here for
    anyone re-adding a similar picker in the future.

    Three possible results, matching the three states the WindowManager
    flag can hold:

    - ``None`` -- no picking session is running right now (flag is
      still at its own -2 default, its only reachable value now). Highlight
      the real active Layer, the ordinary case outside a picking session.
    - `_NO_HIGHLIGHT` -- a session is running but currently hovering no
      Layer (flag is -1). Per explicit user request, this suppresses the
      white highlight entirely instead of falling back to the real active
      Layer -- every visible Layer's mask still shows, none of them
      brightened.
    - an ``int`` (the flag's raw value, >= 0) -- a session is running and
      hovering that Layer. Highlight it instead of the real active Layer.
    """
    idx = getattr(bpy.context.window_manager, "superskin_layer_picker_hover_index", -2)
    if idx == -2:
        return None
    if idx == -1:
        return _NO_HIGHLIGHT
    return idx


def _read_masks_by_layer(obj):
    """Returns ``{layer storage index (int): {v_idx (int): raw mask value
    (float)}}`` for every *visible* Layer -- a hidden Layer contributes no
    color. Each Layer's RAW mask, read the Edit-Mode-safe way
    ``docs/bug-history/0035`` established for `layer_picker`'s own scan:
    ``CoreFacade.switch_to_layer(item.index)`` (loads that Layer's own
    resident temp-VG set on first visit this session, pure bookkeeping on
    every later visit -- see that method's facade-api.md entry) followed by
    ``get_active_mask_dict()``, restoring the original active Layer index
    before returning. NOT the raw ``active_layer_index`` setter, which
    bypasses Edit Mode's temp-VG load entirely and would read every visible
    Layer's mask as whichever Layer's temp VGs already happened to be
    resident (the exact bug 0035 diagnosed).

    Split out from the highlight read (``_read_active_mask()`` below) so
    ``_get_base_vert_colors()`` can cache this decode across hover/active-
    Layer changes -- this is the expensive part (one full-mesh decode per
    *visible* Layer), and mask data cannot change from hovering alone."""
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
    """Returns ``{v_idx (int): mask_value (float)}`` for whichever single
    Layer gets the white highlight -- normally the real active Layer, used
    by ``_apply_active_highlight()`` below. When *highlight_index* is
    `_NO_HIGHLIGHT`, returns an empty dict, which makes
    ``_apply_active_highlight()`` a no-op -- per explicit user request,
    hovering no Layer during a picking session shows every visible Layer's
    mask with no highlight at all, rather than falling back to the real
    active Layer.

    Always the Layer's RAW mask (``get_active_mask_dict()``, Edit-Mode-safe
    via ``switch_to_layer()`` the same way ``_read_masks_by_layer()`` reads
    the base composite) -- there is no raw/flattened distinction to switch
    between anymore (see this module's docstring)."""
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


# ═══════════════════════════════════════════════════════════════════════════
#  Color computation
# ═══════════════════════════════════════════════════════════════════════════

def _apply_active_highlight(vert_colors, active_mask) -> list:
    """Blend each vertex's already-computed color toward white by the
    active Layer's own mask value at that vertex -- same shape as
    ``multi_color_draw._apply_active_highlight()``, just keyed off the
    active Layer's mask instead of the active bone's weight, per explicit
    user request that the active Layer always read as white."""
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
    """Per vertex, blend every visible Layer's own palette color weighted
    by its RAW mask value there (see ``_get_base_vert_colors()``), then
    highlight the active -- or, per explicit user request, whichever Layer
    a ``layer_picker`` session is currently hovering (see
    ``_resolve_highlight_index()``) -- Layer toward white on top."""
    highlight_idx = _resolve_highlight_index()

    base_vert_colors = _get_base_vert_colors(obj, num_verts)
    active_mask = _read_active_mask(obj, highlight_idx)

    return _apply_active_highlight(base_vert_colors, active_mask)


def _get_base_vert_colors(obj, num_verts) -> list:
    """Every visible Layer's RAW mask, decoded and blended into a per-vertex
    base color by a straight weighted average (normalized only when the
    weights at a vertex sum above ``1.0``) -- the same scheme
    ``multi_color_draw.py``'s ``_compute_base_colors()`` already uses for
    per-bone weights. See this module's docstring for why this isn't the
    Object-Mode version's real alpha-composite (that needed
    ``get_flattened_mask_dict()``, which has no Edit-Mode routing).

    Cached on ``_base_key``/``_base_vert_colors``, keyed on everything that
    actually invalidates this blend (mask/visibility/topology/deform data)
    but NOT on ``highlight_idx`` -- that only picks which single Layer gets
    white-highlighted afterward (``_read_active_mask()``, applied fresh on
    top of this cached array by ``_apply_active_highlight()``, which never
    mutates its input). Without this split, a `layer_picker` session
    sweeping the mouse across the mesh changed ``highlight_idx`` on nearly
    every ~12Hz recompute tick, forcing a full re-decode-and-reblend here
    even though hovering alone never changes any Layer's mask data."""
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


# ═══════════════════════════════════════════════════════════════════════════
#  Temp color attribute write — BMesh POINT-domain float-color layer, Edit
#  Mode only, same mechanism multi_color_draw.py uses for its own per-bone
#  preview (the old Object-Mode mesh.color_attributes API doesn't apply
#  once this preview only ever runs in Edit Mode).
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
    attribute behind. Mirrors multi_color_draw.py's own
    _remove_color_layer() exactly."""
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


def _make_key(obj):
    vis_key = tuple((item.index, item.visible) for item in obj.superskin_layers_collection)
    active_idx = _get_active_layer_index(obj)
    # Included so a hover change alone (no weight/mask edit, no visibility
    # or active-Layer change) still triggers a recompute -- otherwise the
    # white highlight would stay pinned to whatever it was computed for
    # last, since nothing else about the key would have changed.
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

    bm = bmesh.from_edit_mesh(obj.data)
    num_verts = len(bm.verts)
    vert_colors = _compute_vert_colors(obj, num_verts)
    _write_colors(obj, bm, vert_colors)
    _color_key = key
    _last_color_compute_time = now
    _bone_preview._tag_redraw_all()


# ═══════════════════════════════════════════════════════════════════════════
#  HUD
# ═══════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════
#  Public lifecycle API
# ═══════════════════════════════════════════════════════════════════════════

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

        # The resolved target changed to a *different* Mesh while already
        # active (e.g. the user switched to editing a different character
        # mid-session) -- stop on the old one first instead of silently
        # continuing to write into a Mesh that's no longer the current
        # target.
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
