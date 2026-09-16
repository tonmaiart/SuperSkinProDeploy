"""Weight Brush -- hold-to-paint modal operator.

Invoked via the "Weight Brush" WorkSpaceTool's own `bl_keymap`
(`brush_tool.py`) -- select that tool from the 3D-viewport Toolbar, then
hold LMB to start a continuous paint stroke. Which of Add/Scale/Smooth/
Sharpen a stroke performs is read from whichever modifier key is held at
the moment LMB is PRESSED (`invoke()`), not a fixed Mode setting -- matches
the ZBrush/Substance-style "modifier switches brush behavior" convention:

    (no modifier)  Add
    Shift          Smooth
    Ctrl           Scale
    Alt            Sharpen

**Locked for the whole stroke, not re-read per tick (per explicit
request):** an earlier revision called `_resolve_mode(event)` fresh on
every `_dab()` (including every `TIMER` tick), so pressing/releasing Ctrl/
Shift/Alt mid-drag switched the active mode instantly, mid-stroke, without
releasing LMB first -- real-world report: this read as too sensitive, a
stray modifier press while still holding LMB shouldn't silently retarget
an in-progress stroke. `invoke()` now resolves the mode ONCE (`self._mode`)
from the PRESS event and every `_dab()` call for that stroke reuses it
unchanged; the held modifier is only re-read on the NEXT stroke's own
`invoke()`, i.e. after LMB has been released and pressed again.

`brush_tool.py`'s `bl_keymap` claims every modifier combination of LMB so
none of these fall through to Blender's native Edit Mesh selection
bindings (Shift/Ctrl+Click normally extend/subtract selection) or to
`../ops.py`'s own Alt-drag gesture while this tool is the active tool.

Every throttled timer tick, one or more `WeightApplyFeature.apply_action()`
calls are dispatched -- the exact same Rust-backed Add/Scale/Smooth/Sharpen
math the panel buttons and Alt-drag gesture (`../ops.py`) already use, just
driven by "whatever the brush is currently over" instead of the real mesh
selection or a fixed drag value. `brush_radius` (`SSPrefWeightBrush`, a
single 0.0-1.0 slider shared by both projection modes) means something
DIFFERENT per mode, per explicit request -- see `brush_logic.
screen_mode_radius_px()`'s docstring for why:

  Surface (default)  A real raycast onto the mesh is required -- no hit,
                      no dab, no cursor shown at all (see `brush_hover.py`
                      for the equivalent hover-only gate). `brush_radius`
                      is true world-space mesh units, used directly by
                      `brush_logic.gather_brush_vertices()` -- a geodesic
                      BFS from the raycast-hit face, following the mesh
                      surface, never reaching occluded/back-facing
                      geometry. The drawn cursor (`brush_draw.show_surface()`)
                      is a real 3D disc oriented to the hit normal, sized
                      in the same world-space units -- it looks correctly
                      tilted against the surface and changes on-screen size
                      with zoom exactly like real scene geometry would.
  Screen              No raycast at all -- positioned directly at the raw
                      mouse position every tick, always shown regardless of
                      what's under the cursor in 3D. `brush_radius` is
                      reinterpreted as a fraction of
                      `brush_logic.SCREEN_RADIUS_PX_SCALE` (a fixed pixel
                      count, via `screen_mode_radius_px()`) -- CONSTANT
                      on-screen size regardless of view zoom/distance, by
                      design. `brush_logic.gather_brush_vertices_screen()`
                      (`self._get_kdtree()`, built lazily, once per stroke,
                      only if Screen mode is actually used) runs a literal
                      2D SCREEN-space range query against every vertex's
                      on-screen projection at that same fixed pixel radius
                      -- a straight/linear projection through the mesh
                      along the view (an infinite cylinder from the
                      on-screen circle), not a world-space sphere (an
                      earlier revision built exactly that sphere, which is
                      why a small `brush_radius` still painted the whole
                      mesh regardless of the drawn circle's actual
                      on-screen size). Deliberately not limited to the
                      visible surface or geodesic connectivity -- reaches
                      straight through the mesh to whatever's behind the
                      on-screen circle too. The drawn cursor
                      (`brush_draw.show_screen()`) is a flat `POST_PIXEL`
                      circle, unaffected by depth/surface entirely.

`WeightApplyFeature` is imported directly here (not via the registry) --
safe because this file lives inside the SAME `weight_apply` package, so it
is an ordinary same-package import, not a cross-domain import. See
`docs/domains/weight_apply.md`'s "Weight Brush" section.

Unlike the Alt-drag gesture (one fixed baseline snapshot for the whole
drag, so a live preview doesn't compound), a paint stroke is SUPPOSED to
accumulate as the brush passes back over the same area -- each dab folds
`apply_action()`'s own returned `layer_int`/`mask_dict` back into the local
`self._ctx`, so the next dab reads the just-painted state instead of a
stale baseline, without needing a fresh (expensive) `read_active_layer()`
BMesh scan every tick.

One hold = one Blender undo step, via the same mechanism `../ops.py`'s
gesture already relies on: intermediate writes inside `modal()` don't each
push their own undo entry -- only this operator's own `{'REGISTER', 'UNDO'}`
completion (RELEASE/ESC returning `{'FINISHED'}`) does.
"""

import bpy

from ....core.facade import CoreFacade
from .brush_logic import (
    build_bvh, build_screen_kdtree, raycast_under_cursor,
    gather_brush_vertices, gather_brush_vertices_screen,
    screen_mode_radius_px,
)
from . import brush_draw

# Same tick rate as the Alt-drag gesture (`../ops.py`'s
# `_GESTURE_APPLY_INTERVAL`) -- caps expensive apply+flatten calls to a
# fixed budget regardless of raw input rate.
_BRUSH_APPLY_INTERVAL = 1.0 / 60.0

# `brush_falloff` (a free 0.0-1.0 slider) was first replaced with a 4-preset
# "Falloff" EnumProperty (spatial edge blending -- how far in from the
# brush's own boundary a dab's strength eased down to 0), then per a LATER
# explicit request cut to 3 presets and renamed `brush_hardness`. Per a
# STILL LATER explicit request, Hardness was redefined again -- it no
# longer touches spatial falloff at all (there is no more per-vertex
# distance banding inside a dab; every gathered vertex now gets the exact
# same intensity). Instead each preset is a flat INTENSITY MULTIPLIER
# applied to whichever Add/Scale/Smooth/Sharpen slider the held modifier
# resolves to (`_slider_intensity()` below) -- `HARD` uses that slider's
# value UNCHANGED (100%), `MEDIUM` uses half of it (50%), `SOFT` uses only
# a fraction of it (5%). This is a deliberate swap of what the row of
# three buttons controls, not an additive feature -- there is no falloff
# concept left in this file for `_dab()` to blend across a dab's radius.
_HARDNESS_PRESETS = (
    ('HARD', "Hard", "Uses the full Add/Scale/Smooth/Sharpen slider intensity for every dab (100%)", 1.0),
    ('MEDIUM', "Medium", "Uses half the Add/Scale/Smooth/Sharpen slider intensity for every dab (50%)", 0.5),
    ('SOFT', "Soft", "Uses only 5% of the Add/Scale/Smooth/Sharpen slider intensity for every dab", 0.05),
)
_HARDNESS_PRESET_MULTIPLIERS = {ident: value for ident, _label, _desc, value in _HARDNESS_PRESETS}
_HARDNESS_IDENTS = tuple(ident for ident, _label, _desc, _value in _HARDNESS_PRESETS)
_DEFAULT_HARDNESS_PRESET = 'HARD'

# Migrates a `brush_falloff` value saved before the Falloff -> Hardness
# rename/preset-count change (`_coerce_hardness_preset()` below) onto its
# nearest surviving `brush_hardness` identifier -- these are the OLD
# spatial-falloff preset identifiers, unrelated to `_HARDNESS_PRESETS`'
# intensity-multiplier meaning above, so this mapping is purely
# identifier -> identifier and doesn't need updating when the multiplier
# values change.
_LEGACY_FALLOFF_TO_HARDNESS = {
    'SHARP_5': 'HARD',
    'BLEND_50': 'MEDIUM',
    'BLEND_70': 'MEDIUM',
    'FULL': 'SOFT',
}

# For the rare legacy case of an ORIGINAL free-float `brush_falloff` value
# (pre-dating even the 4-preset Falloff EnumProperty) -- `_coerce_hardness_
# preset()`'s numeric fallback below matches it against these OLD falloff
# reference numbers, deliberately decoupled from `_HARDNESS_PRESETS`'
# `multiplier` column above, which now means something else entirely
# (intensity, not spatial falloff).
_LEGACY_FALLOFF_FLOAT_REFERENCE = {'HARD': 0.05, 'MEDIUM': 0.5, 'SOFT': 1.0}


def get_hardness_multiplier(p=None) -> float:
    """The `[0.0, 1.0]` intensity multiplier for *p*'s current
    `brush_hardness` preset -- `brush_hardness` itself is a preset
    identifier (EnumProperty), not a float. `_dab()` multiplies this
    straight into whichever Add/Scale/Smooth/Sharpen slider value the held
    modifier resolves to (see `_slider_intensity()`), so `HARD` (1.0)
    leaves that slider's value unchanged, `MEDIUM` (0.5) halves it, and
    `SOFT` (0.05) reduces it to 5% of it. Defaults to `p=None` ->
    `get_brush_prefs()` so call sites that already hold a `p` can pass it
    in and skip the extra lookup."""
    if p is None:
        p = get_brush_prefs()
    return _HARDNESS_PRESET_MULTIPLIERS.get(p.brush_hardness, _HARDNESS_PRESET_MULTIPLIERS[_DEFAULT_HARDNESS_PRESET])


def _coerce_hardness_preset(value) -> str:
    """Maps a legacy `brush_falloff` value -- either the original free
    0.0-1.0 float slider, or one of the 4 short-lived `Falloff` preset
    identifiers (`FULL`/`BLEND_70`/`BLEND_50`/`SHARP_5`) -- onto its
    nearest surviving `brush_hardness` preset, so an old saved
    `default_config.json`/user prefs value doesn't fail to apply to the new
    EnumProperty. A value that's already a valid `brush_hardness`
    identifier passes through unchanged. The numeric fallback compares
    against `_LEGACY_FALLOFF_FLOAT_REFERENCE` (the OLD falloff meaning),
    not `_HARDNESS_PRESETS`' current intensity-multiplier values -- an
    ancient raw float was always a falloff fraction, never an intensity."""
    if isinstance(value, str):
        if value in _HARDNESS_PRESET_MULTIPLIERS:
            return value
        if value in _LEGACY_FALLOFF_TO_HARDNESS:
            return _LEGACY_FALLOFF_TO_HARDNESS[value]
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_HARDNESS_PRESET
    return min(_LEGACY_FALLOFF_FLOAT_REFERENCE, key=lambda ident: abs(_LEGACY_FALLOFF_FLOAT_REFERENCE[ident] - f))


def _on_brush_changed(self, context):
    from ....core.facade import CoreFacade
    CoreFacade.save_prefs()


def _slider_intensity(mode):
    """*mode*'s own Add/Scale/Smooth/Sharpen N-panel slider value
    (`SSPrefWeightApply.add_val`/`scale_val`/`smooth_val`/`sharpen_val`) --
    the brush has no independent Strength/intensity control of its own
    (removed per explicit request): a dab always applies at whatever the
    corresponding panel row is currently set to, exactly like a plain
    panel-button click or the Alt-drag gesture would, so there is only ever
    one place to tune each action's intensity, not two. Mirrors
    `WeightApplyFeature.execute()`'s own `action -> slider value` mapping."""
    from ..weight_apply_feature import get_prefs
    p = get_prefs()
    return {
        "add": p.add_val, "scale": p.scale_val,
        "smooth": p.smooth_val, "sharpen": p.sharpen_val,
    }.get(mode, 0.0)


# Shared by `SSPrefWeightBrush.brush_projection`'s `EnumProperty(items=...)`
# below and `SUPERSKIN_OT_cycle_brush_projection` (single source of truth for
# identifier/label/description, same DRY convention as `_HARDNESS_PRESETS`).
_PROJECTION_ITEMS = (
    ('SURFACE', "Surface", "Follows the mesh surface (geodesic BFS from the hit "
                            "point) -- never reaches occluded or back-facing "
                            "geometry, like painting on the visible surface with a "
                            "real brush"),
    ('SCREEN', "Screen", "Projects straight through the mesh along the view -- "
                          "reaches every vertex whose ON-SCREEN position falls "
                          "within the drawn circle, regardless of depth, occlusion, "
                          "facing direction, or surface connectivity (ngSkinTools' "
                          "'Screen' projection convention)"),
)
_PROJECTION_IDENTS = tuple(ident for ident, _label, _desc in _PROJECTION_ITEMS)


def _next_hardness(current: str) -> str:
    """The identifier one step after *current* in `_HARDNESS_IDENTS`,
    wrapping back to the first after the last -- the cycle logic behind the
    single Hardness button (`SUPERSKIN_OT_cycle_brush_hardness` below),
    factored out the same way `_next_projection()` is."""
    try:
        idx = _HARDNESS_IDENTS.index(current)
    except ValueError:
        return _HARDNESS_IDENTS[0]
    return _HARDNESS_IDENTS[(idx + 1) % len(_HARDNESS_IDENTS)]


def _next_projection(current: str) -> str:
    """The identifier one step after *current* in `_PROJECTION_IDENTS`,
    wrapping back to the first after the last -- the actual cycle logic
    behind the single Projection button (`SUPERSKIN_OT_cycle_brush_
    projection` below), factored out so both `execute()` and `description()`
    reuse the same wrap-around math instead of duplicating it."""
    try:
        idx = _PROJECTION_IDENTS.index(current)
    except ValueError:
        return _PROJECTION_IDENTS[0]
    return _PROJECTION_IDENTS[(idx + 1) % len(_PROJECTION_IDENTS)]


class SSPrefWeightBrush(bpy.types.PropertyGroup):
    """Weight Brush settings (per-machine) -- independent of Weight Apply's
    own Add/Scale/Smooth/Sharpen sliders (`SSPrefWeightApply` in
    `../weight_apply_feature.py`), so painting never mutates (and never
    disk-saves-on-every-dab) those panel values.

    No Mode property here -- which action a dab performs is read live from
    the held modifier key (see this module's docstring), not a stored
    setting."""
    brush_projection: bpy.props.EnumProperty(
        # `name=""` (not "Projection") per explicit request -- was relevant
        # while this was drawn as a dropdown (an EnumProperty's `name` is
        # what Blender prints as a header line at the top of that dropdown's
        # OWN popup menu). Now drawn as a single cycle button
        # (`SUPERSKIN_OT_cycle_brush_projection` below, no popup menu at
        # all), but kept empty regardless -- there is no dropdown left for
        # it to matter to, and the property still needs a `name` for RNA's
        # sake even if nothing displays it.
        name="",
        description="Which vertices Radius can reach -- does not change what Radius means",
        items=[(ident, label, desc) for ident, label, desc in _PROJECTION_ITEMS],
        default='SURFACE',
        update=_on_brush_changed,
    )
    brush_radius: bpy.props.FloatProperty(
        name="Radius",
        description=(
            "Brush footprint size, world-space mesh units -- same meaning in "
            "both Surface and Screen projection. F to adjust interactively"
        ),
        # Range lowered from min=1.0/max=100.0/default=10.0 per explicit
        # request -- the old hard min=1.0 floor was already far too large
        # on this project's mesh scale (world-space units, not normalized),
        # and being a hard min (not soft_min) meant it couldn't be typed
        # around either. 0.0-1.0 matches this codebase's usual small-slider
        # convention (see SUPERSKIN_OT_weight_gesture's `intensity` property).
        default=0.1, min=0.0, max=1.0,
        update=_on_brush_changed,
    )
    brush_hardness: bpy.props.EnumProperty(
        name="Hardness",
        description=(
            "How strongly a dab applies, as a fraction of the Add/Scale/"
            "Smooth/Sharpen slider's value -- Hard uses 100%, Medium 50%, "
            "Soft 5%"
        ),
        items=[(ident, label, desc) for ident, label, desc, _value in _HARDNESS_PRESETS],
        default=_DEFAULT_HARDNESS_PRESET,
        update=_on_brush_changed,
    )


def get_brush_prefs() -> "SSPrefWeightBrush":
    return bpy.context.window_manager.superskin_weight_brush_prefs


class SUPERSKIN_OT_set_brush_hardness(bpy.types.Operator):
    """Set the Weight Brush's Hardness preset directly to `value`
    (`'HARD'`/`'MEDIUM'`/`'SOFT'`) -- reintroduced (2026-09-15, per explicit
    user request) as the three-button attached toggle row this project used
    before `SUPERSKIN_OT_cycle_brush_hardness` below replaced it (see
    `docs/domains/weight_apply.md`'s "Hardness" section for the full
    history). Only `ui_weight_apply.py`'s N-panel column
    (`brush_ui.py::draw_hardness_buttons()`, plural) draws these three
    buttons now -- `brush_tool.py`'s viewport header still uses the single
    cycle button below, unchanged, so the two surfaces intentionally
    diverge on this one control."""
    bl_idname = "superskin.set_brush_hardness"
    bl_label = "Set Brush Hardness"
    bl_options = {'INTERNAL'}

    value: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        for ident, label, desc, _value in _HARDNESS_PRESETS:
            if ident == properties.value:
                return f"{label} hardness -- {desc}"
        return cls.__doc__

    def execute(self, context):
        get_brush_prefs().brush_hardness = self.value
        return {'FINISHED'}


class SUPERSKIN_OT_cycle_brush_hardness(bpy.types.Operator):
    """Cycle the Weight Brush's Hardness preset (Hard/Medium/Soft) -- a
    single button standing in for the old three-button attached toggle row
    (`SUPERSKIN_OT_set_brush_hardness`, per explicit request), mirroring
    `SUPERSKIN_OT_cycle_brush_projection` below. The old three-button row
    existed only because `UILayout.prop_enum()` can't carry a custom
    `icon_value` (see that removed operator's history in
    `docs/domains/weight_apply.md`'s "Hardness" section); a single cycling
    `operator()` button has no such restriction and needs only one icon
    (the CURRENT preset's) instead of three. Written as a genuine cycle over
    `_HARDNESS_IDENTS` (`_next_hardness()` above), not a hardcoded 3-way
    swap, so a future preset addition would only need extending that tuple."""
    bl_idname = "superskin.cycle_brush_hardness"
    bl_label = "Cycle Brush Hardness"
    bl_options = {'INTERNAL'}

    @classmethod
    def description(cls, context, properties):
        next_ident = _next_hardness(get_brush_prefs().brush_hardness)
        for ident, label, desc, _value in _HARDNESS_PRESETS:
            if ident == next_ident:
                return f"Switch to {label} hardness -- {desc}"
        return cls.__doc__

    def execute(self, context):
        p = get_brush_prefs()
        p.brush_hardness = _next_hardness(p.brush_hardness)
        return {'FINISHED'}


class SUPERSKIN_OT_cycle_brush_projection(bpy.types.Operator):
    """Cycle the Weight Brush's Projection (Surface/Screen) -- a single
    button standing in for the old two-item dropdown, per explicit request.
    Only two values exist today, so this is effectively a toggle, but
    written as a genuine cycle over `_PROJECTION_IDENTS` (`_next_projection()`
    above) rather than a hardcoded swap, so a possible future third
    projection mode would only need extending that tuple, not new button
    logic."""
    bl_idname = "superskin.cycle_brush_projection"
    bl_label = "Cycle Brush Projection"
    bl_options = {'INTERNAL'}

    @classmethod
    def description(cls, context, properties):
        next_ident = _next_projection(get_brush_prefs().brush_projection)
        for ident, label, desc in _PROJECTION_ITEMS:
            if ident == next_ident:
                return f"Switch to {label} projection -- {desc}"
        return cls.__doc__

    def execute(self, context):
        p = get_brush_prefs()
        p.brush_projection = _next_projection(p.brush_projection)
        return {'FINISHED'}


def populate_prefs(data: dict) -> None:
    """Write the `weight_apply.brush` JSON sub-section into live prefs.

    Reads the legacy `brush_falloff` key as a fallback when `brush_hardness`
    isn't present -- a `default_config.json`/user prefs file saved before
    the Falloff -> Hardness rename only ever wrote the old key."""
    p = get_brush_prefs()
    p.brush_projection = data.get("brush_projection", "SURFACE")
    p.brush_radius = float(data.get("brush_radius", 0.1))
    p.brush_hardness = _coerce_hardness_preset(
        data.get("brush_hardness", data.get("brush_falloff", _DEFAULT_HARDNESS_PRESET)),
    )


def serialize_prefs() -> dict:
    """Current brush prefs, for nesting under `weight_apply.brush` on save."""
    p = get_brush_prefs()
    return {
        "brush_projection": p.brush_projection,
        "brush_radius": p.brush_radius,
        "brush_hardness": p.brush_hardness,
    }


class SUPERSKIN_OT_weight_brush(bpy.types.Operator):
    """Hold-to-paint Add/Scale/Smooth/Sharpen, constrained to a circular
    (geodesic-radius) footprint that follows the cursor across the mesh
    surface, instead of the currently-selected vertices. See this module's
    docstring for the accumulation/undo contract."""
    bl_idname = "superskin.weight_brush"
    bl_label = "Weight Brush"
    bl_options = {'REGISTER', 'UNDO'}

    # Class-level flag (not instance state) -- checked by brush_hover.py's
    # persistent modal so it steps aside and stops drawing/updating
    # brush_draw itself while an actual stroke is running, instead of the
    # two fighting over the same shared draw state.
    _stroke_active = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (CoreFacade.is_system_activated() and CoreFacade.is_editing_weights() and
                obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT')

    def invoke(self, context, event):
        from ..weight_apply_feature import WeightApplyFeature

        self._facade = CoreFacade(context)
        self._feature = WeightApplyFeature()
        self._ctx = self._feature.snapshot_context(self._facade)
        self._bm, self._bvh, self._posed_coords = build_bvh(context, self._facade.get_obj())
        self._kd = None  # lazily built -- only Screen projection needs it, see _get_kdtree()
        self._last_dab_key = None
        self._dabbed = False
        # Cache of the cheap per-position work (_update_cursor()) that
        # _dab() reuses instead of raycasting a second time -- see
        # _update_cursor()'s own docstring.
        self._cursor_hit = None
        # Resolved ONCE from the press event and reused for every _dab() in
        # this stroke -- see this module's docstring for why the mode is no
        # longer re-read from the live modifier state on every tick.
        self._mode = self._resolve_mode(event)

        SUPERSKIN_OT_weight_brush._stroke_active = True
        self._timer = context.window_manager.event_timer_add(
            _BRUSH_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        # No separate brush_draw.show() call needed -- _dab() below calls
        # show_screen()/show_surface() itself, which both handle showing
        # the relevant draw handler internally.
        self._dab(context, event)
        return {'RUNNING_MODAL'}

    @staticmethod
    def _resolve_mode(event):
        """Which Weight Apply action this stroke performs, from whichever
        modifier key is held on *event* -- see this module's docstring for
        the mapping. Called exactly once, from `invoke()`'s own PRESS event
        -- NOT re-checked per tick, so changing modifiers while LMB is still
        held does not retarget an in-progress stroke (see this module's
        docstring)."""
        if event.alt:
            return "sharpen"
        if event.ctrl:
            return "scale"
        if event.shift:
            return "smooth"
        return "add"

    def _get_kdtree(self, context):
        """Lazily build (once per stroke, not once per dab) the 2D
        screen-space KDTree `gather_brush_vertices_screen()` needs -- most
        strokes never use Screen projection, so building it unconditionally
        in invoke() would waste the O(n log n) cost on every
        Surface-projection stroke too. Assumes the view doesn't change
        mid-stroke -- see `build_screen_kdtree()`'s own docstring for why
        that's an acceptable tradeoff here."""
        if self._kd is None:
            obj = self._facade.get_obj()
            self._kd = build_screen_kdtree(
                obj.matrix_world, context.region, context.region_data, self._posed_coords,
            )
        return self._kd

    def _apply_one(self, context, mode, verts, intensity):
        """One `apply_action()` call over *verts* at *intensity*, folding
        the result back into `self._ctx` so the next call (next band, or
        next tick) reads the just-written state. Must be called with
        `context.scene.superskin_internal_transaction` already set by the
        caller -- shared across every band of one dab, not re-toggled per
        call, to match how a single `apply_action()` call in the rest of
        this domain wraps that guard exactly once per write.

        Resets `_nearest_bones_cache` alongside `selected` -- unlike the
        plain gesture operator (ops.py), this ctx is NOT a frozen baseline:
        `selected` and `layer_int` both change dab to dab (see below), and
        Scale's cached nearest-bone search (weight_apply_feature.py's
        `apply_action()`) is only valid for the exact `selected`/`layer_int`
        it was computed from -- leaving a stale entry here would let a later
        dab's Scale redistribute weight based on an earlier dab's geometry."""
        self._ctx["selected"] = verts
        self._ctx["_nearest_bones_cache"] = {}
        result = self._feature.apply_action(mode, self._facade, self._ctx, intensity)
        if result.get("status") == "FINISHED":
            self._dabbed = True
            if "layer_int" in result:
                self._ctx["layer_int"] = result["layer_int"]
            if "mask_dict" in result:
                self._ctx["mask_dict"] = result["mask_dict"]

    def _update_cursor(self, context, event):
        """Refresh the drawn brush cursor at the CURRENT mouse position, and
        cache whatever positional data `_dab()` needs (`self._cursor_hit`)
        so it doesn't have to recompute it.

        Split out of `_dab()` (which used to do this AND the expensive
        vertex-gather + `apply_action()` call together, both gated to the
        throttled `TIMER` tick) because the combined cost of a real-world
        dab -- BMesh writes, Rust FFI, active-layer flatten -- can comfortably
        exceed `_BRUSH_APPLY_INTERVAL` (`1/60s`) on a busy mesh, which made
        the TICK itself arrive late and the cursor visibly lag behind the
        real mouse position. This method is cheap -- a single BVH raycast, no
        vertex gather -- so `modal()` also calls it on every real
        `MOUSEMOVE` event (Blender's native input rate, not the fixed
        60Hz apply cap), keeping the drawn circle glued to the cursor
        between apply ticks exactly the way `brush_hover.py`'s pre-stroke
        hover cursor already does. The actual paint application still only
        happens at `_BRUSH_APPLY_INTERVAL`, unchanged."""
        obj = self._facade.get_obj()
        p = get_brush_prefs()
        mode = self._mode
        # Hardness now scales INTENSITY, not spatial falloff (per explicit
        # request) -- every vertex the dab gathers gets this exact same
        # value, no per-vertex distance banding. See `_HARDNESS_PRESETS`'
        # own comment for the HARD=100%/MEDIUM=50%/SOFT=5% mapping.
        hardness_mult = get_hardness_multiplier(p)
        intensity = _slider_intensity(mode) * hardness_mult
        # Info text only shows for Smooth/Scale/Sharpen dabs -- per explicit
        # request, a plain Add dab (no modifier held) and the hover-only
        # cursor (`brush_hover.py`) both stay label-free.
        label = (
            f"{p.brush_projection.title()}  R:{p.brush_radius:.2f}  "
            f"H:{hardness_mult:.0%}  [{mode}] I:{intensity:.2f}"
        ) if mode in ("smooth", "scale", "sharpen") else ""

        # No falloff-edge inner ring anymore -- passing the full radius as
        # the "edge" is what makes `brush_draw`'s existing
        # `edge < radius - epsilon` guard skip drawing it (see
        # `brush_draw.py::_draw_callback_pixel()`/`_draw_callback_view()`),
        # without needing to touch that module's signature just for a
        # permanently-unused inner-ring case.
        if p.brush_projection == 'SCREEN':
            # No raycast at all -- see this module's docstring. Positioned
            # at the raw mouse position, sized in constant on-screen
            # pixels regardless of zoom/distance.
            center_2d = (event.mouse_region_x, event.mouse_region_y)
            radius_px = screen_mode_radius_px(p.brush_radius)
            brush_draw.show_screen(center_2d, radius_px, radius_px, label)
            self._cursor_hit = ('SCREEN', center_2d, radius_px)
        else:
            hit, face_index, hit_world, hit_normal = raycast_under_cursor(
                context, event, obj, self._bvh,
            )
            if not hit:
                self._cursor_hit = None
                return
            brush_draw.show_surface(hit_world, hit_normal, p.brush_radius, p.brush_radius, label)
            self._cursor_hit = ('SURFACE', face_index, hit_world, hit_normal)

    def _dab(self, context, event):
        obj = self._facade.get_obj()
        p = get_brush_prefs()
        mode = self._mode
        hardness_mult = get_hardness_multiplier(p)
        intensity = _slider_intensity(mode) * hardness_mult

        self._update_cursor(context, event)
        if self._cursor_hit is None:
            return

        if self._cursor_hit[0] == 'SCREEN':
            _kind, center_2d, radius_px = self._cursor_hit
            dists = gather_brush_vertices_screen(
                self._get_kdtree(context), center_2d, radius_px, p.brush_radius,
            )
        else:
            _kind, face_index, hit_world, hit_normal = self._cursor_hit
            dists = gather_brush_vertices(
                self._facade, self._bm, face_index, hit_world, obj.matrix_world, p.brush_radius,
                self._posed_coords,
            )

        # Ad hoc debug deck -- temporary, to confirm the screen-space Screen
        # gather (brush_logic.py's `gather_brush_vertices_screen()`) scopes
        # to the drawn circle instead of the whole mesh, which is what the
        # previous world-space-sphere design was doing whenever `radius`
        # exceeded the mesh's own extent. Remove alongside the
        # `adhoc:weight_brush_hover` instrumentation once confirmed.
        CoreFacade.debug_log(
            "adhoc:weight_brush_dab",
            f"projection={p.brush_projection} radius={p.brush_radius:.3f} "
            f"gathered={len(dists)} "
            f"mesh_verts={len(self._facade.get_vertex_coordinates())}",
        )

        if not dists:
            return

        # Skip a dab that would be an exact repeat of the last one (cursor
        # briefly still, no modifier change) -- not just "same vertices",
        # since a modifier change at a stationary cursor should still
        # trigger a fresh dab under the new mode.
        dab_key = (frozenset(dists), mode)
        if dab_key == self._last_dab_key:
            return
        self._last_dab_key = dab_key

        context.scene.superskin_internal_transaction = True
        try:
            # One call, every gathered vertex at the same flat `intensity`
            # -- there is no more per-vertex distance banding (Hardness no
            # longer means spatial falloff, see above).
            self._apply_one(context, mode, list(dists.keys()), intensity)
        finally:
            context.scene.superskin_internal_transaction = False

        if self._dabbed:
            context.area.header_text_set(
                f"Weight Brush [{mode}] {p.brush_projection.title()} radius={p.brush_radius:.3f} "
                f"hardness={hardness_mult:.0%} intensity={intensity:.2f}"
            )

    def _remove_timer(self, context):
        context.window_manager.event_timer_remove(self._timer)
        context.area.header_text_set(None)
        brush_draw.hide()
        SUPERSKIN_OT_weight_brush._stroke_active = False

    def modal(self, context, event):
        if event.type == 'TIMER':
            self._dab(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            # Cheap cursor-only refresh at Blender's real input rate -- see
            # _update_cursor()'s docstring for why this is split from the
            # throttled apply-timer tick above. Still PASS_THROUGH
            # afterwards, unchanged -- this operator has never consumed
            # MOUSEMOVE itself.
            self._update_cursor(context, event)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._dab(context, event)
            self._remove_timer(context)
            return {'FINISHED'} if self._dabbed else {'CANCELLED'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._remove_timer(context)
            return {'FINISHED'} if self._dabbed else {'CANCELLED'}

        # Anything else (other mouse buttons, keyboard shortcuts, clicks
        # meant for native Blender UI or the addon's own panels) is NOT this
        # operator's business -- PASS_THROUGH lets it reach whatever
        # would normally handle it. The earlier version of this branch
        # returned RUNNING_MODAL here (swallowing everything not explicitly
        # listed above), so if the paint stroke's own LEFTMOUSE RELEASE ever
        # failed to reach this modal (e.g. the button released after the
        # cursor left the Blender window mid-drag), this operator stayed on
        # the handler stack forever and silently ate every subsequent click
        # anywhere in the window -- reading exactly like "the UI is stuck and
        # unclickable." See `docs/bug-history/0036` and `0037` for the same
        # failure class in sibling brush/picker operators.
        return {'PASS_THROUGH'}

    def cancel(self, context):
        """Blender calls this (not modal()) when it force-terminates this
        operator outside modal()'s own RELEASE/RIGHTMOUSE/ESC branches --
        e.g. a workspace/tool switch while the stroke is still technically
        running. Without this override the timer, header text and brush
        cursor overlay from `_remove_timer()` would never be cleaned up, and
        `_stroke_active` would stay stuck True, permanently blocking
        `brush_hover.py`'s own cursor from ever resuming -- mirrors
        `brush_hover.py`'s and `brush_radius_adjust.py`'s own `cancel()`."""
        try:
            self._remove_timer(context)
        except Exception:
            SUPERSKIN_OT_weight_brush._stroke_active = False
            brush_draw.hide()


# ── Registration ─────────────────────────────────────────────────────────

def register():
    bpy.utils.register_class(SSPrefWeightBrush)
    bpy.types.WindowManager.superskin_weight_brush_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightBrush, options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(SUPERSKIN_OT_set_brush_hardness)
    bpy.utils.register_class(SUPERSKIN_OT_cycle_brush_hardness)
    bpy.utils.register_class(SUPERSKIN_OT_cycle_brush_projection)
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush)
    bpy.utils.unregister_class(SUPERSKIN_OT_cycle_brush_projection)
    bpy.utils.unregister_class(SUPERSKIN_OT_cycle_brush_hardness)
    bpy.utils.unregister_class(SUPERSKIN_OT_set_brush_hardness)
    try:
        del bpy.types.WindowManager.superskin_weight_brush_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightBrush)
