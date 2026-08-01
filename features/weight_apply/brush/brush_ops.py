"""Weight Brush -- hold-to-paint modal operator.

Invoked via the "Weight Brush" WorkSpaceTool's own `bl_keymap`
(`brush_tool.py`) -- select that tool from the 3D-viewport Toolbar, then
hold LMB to start a continuous paint stroke. Which of Add/Scale/Smooth/
Sharpen a dab performs is read live from the held modifier key every tick
(see `_dab()`), not a fixed Mode setting -- matches the ZBrush/Substance-
style "modifier switches brush behavior" convention:

    (no modifier)  Add
    Shift          Smooth
    Ctrl           Scale
    Alt            Sharpen

`brush_tool.py`'s `bl_keymap` claims every modifier combination of LMB so
none of these fall through to Blender's native Edit Mesh selection
bindings (Shift/Ctrl+Click normally extend/subtract selection) or to
`../ops.py`'s own Alt-drag gesture while this tool is the active tool.

Every throttled timer tick, the cursor is raycast onto the mesh
(`brush_logic.raycast_under_cursor()`), the vertices within `brush_radius`
of the hit point are gathered with their distance from the hit point, and
one or more `WeightApplyFeature.apply_action()` calls are dispatched -- the
exact same Rust-backed Add/Scale/Smooth/Sharpen math the panel buttons and
Alt-drag gesture (`../ops.py`) already use, just driven by "whatever the
brush is currently over" instead of the real mesh selection or a fixed
drag value. `brush_radius` always means the same thing (world-space mesh
units) regardless of `brush_projection` -- Screen mode does NOT mean
"radius in screen pixels," it means "reach through the mesh," matching
ngSkinTools' own Surface/Screen projection convention. Which gatherer runs:

  Surface (default)  `brush_logic.gather_brush_vertices()` -- geodesic BFS
                      from the raycast-hit face, following the mesh
                      surface. Never reaches occluded/back-facing geometry.
  Screen              `brush_logic.gather_brush_vertices_screen()` -- a
                      world-space spherical KDTree range query
                      (`self._get_kdtree()`, built lazily, once per stroke,
                      only if Screen mode is actually used) centered on the
                      hit point, radius `brush_radius` -- same number as
                      Surface mode. Reaches straight through the mesh to
                      whatever's behind the hit point too, deliberately not
                      limited to the visible surface or geodesic
                      connectivity.

`WeightApplyFeature` is imported directly here (not via the registry) --
safe because this file lives inside the SAME `weight_apply` package, so it
is an ordinary same-package import, not a violation of the project's
Zero-Cross-Imports rule between sibling `features/*` packages. See
`features/weight_apply/README.md`'s "Weight Brush" section.

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
    build_bvh, build_vertex_kdtree, raycast_under_cursor,
    gather_brush_vertices, gather_brush_vertices_screen,
    world_radius_to_screen_px, world_to_screen,
)
from . import brush_draw

# Same tick rate as the Alt-drag gesture (`../ops.py`'s
# `_GESTURE_APPLY_INTERVAL`) -- caps expensive apply+flatten calls to a
# fixed budget regardless of raw input rate.
_BRUSH_APPLY_INTERVAL = 1.0 / 60.0

# Number of concentric distance bands `_dab()` splits a gathered vertex set
# into when `brush_falloff > 0.0` -- each band is its own `apply_action()`
# call at its own falloff-scaled intensity (apply_action() itself only
# takes one flat intensity per call, so a graduated falloff means several
# calls per dab). Higher = smoother falloff, more FFI/BMesh-write cost per
# tick. When `brush_falloff <= 0.0` (the default), banding is skipped
# entirely and every vertex is painted in one call at full strength --
# same cost as before falloff existed.
_FALLOFF_BANDS = 5


def _falloff_weight(t, falloff):
    """1.0 at the brush center, easing down to 0.0 at the brush edge over
    the outer `falloff` fraction of the radius (`t` = distance/radius,
    both in [0, 1]). `falloff=0` -> hard edge, every vertex inside the
    radius gets full weight (matches the original constant-falloff
    behavior). `falloff=1` -> soft across the entire radius. Plain
    smoothstep ease, not a full custom falloff curve -- good enough for a
    first pass; swap for a curve-mapping property later if needed."""
    if falloff <= 0.0:
        return 1.0
    edge_start = max(0.0, 1.0 - falloff)
    if t <= edge_start:
        return 1.0
    if t >= 1.0:
        return 0.0
    x = (t - edge_start) / (1.0 - edge_start)
    return 1.0 - (3.0 * x * x - 2.0 * x * x * x)


def _on_brush_changed(self, context):
    from ....core.facade import CoreFacade
    CoreFacade.save_prefs()


class SSPrefWeightBrush(bpy.types.PropertyGroup):
    """Weight Brush settings (per-machine) -- independent of Weight Apply's
    own Add/Scale/Smooth/Sharpen sliders (`SSPrefWeightApply` in
    `../weight_apply_feature.py`), so painting never mutates (and never
    disk-saves-on-every-dab) those panel values.

    No Mode property here -- which action a dab performs is read live from
    the held modifier key (see this module's docstring), not a stored
    setting."""
    brush_projection: bpy.props.EnumProperty(
        name="Projection",
        description="Which vertices Radius can reach -- does not change what Radius means",
        items=[
            ('SURFACE', "Surface", "Follows the mesh surface (geodesic BFS from the hit "
                                    "point) -- never reaches occluded or back-facing "
                                    "geometry, like painting on the visible surface with a "
                                    "real brush"),
            ('SCREEN', "Screen", "Passes through the mesh -- reaches every vertex within "
                                  "Radius of the hit point regardless of occlusion, facing "
                                  "direction, or surface connectivity (ngSkinTools' "
                                  "'Screen' projection convention)"),
        ],
        default='SURFACE',
        update=_on_brush_changed,
    )
    brush_radius: bpy.props.FloatProperty(
        name="Radius",
        description=(
            "Brush footprint size, world-space mesh units -- same meaning in "
            "both Surface and Screen projection. F to adjust interactively"
        ),
        default=10.0, min=1.0, max=100.0,
        update=_on_brush_changed,
    )
    brush_falloff: bpy.props.FloatProperty(
        name="Falloff",
        description=(
            "0 = hard edge, every vertex inside Radius gets full Strength. "
            "1 = soft falloff across the whole brush, fading to 0 at the "
            "edge. Shift+F to adjust interactively"
        ),
        default=0.0, min=0.0, max=1.0,
        update=_on_brush_changed,
    )
    brush_strength: bpy.props.FloatProperty(
        name="Strength",
        description="Ctrl+F to adjust interactively",
        default=0.5, min=0.0, max=1.0,
        update=_on_brush_changed,
    )


def get_brush_prefs() -> "SSPrefWeightBrush":
    return bpy.context.window_manager.superskin_weight_brush_prefs


def populate_prefs(data: dict) -> None:
    """Write the `weight_apply.brush` JSON sub-section into live prefs."""
    p = get_brush_prefs()
    p.brush_projection = data.get("brush_projection", "SURFACE")
    p.brush_radius = float(data.get("brush_radius", 10.0))
    p.brush_falloff = float(data.get("brush_falloff", 0.0))
    p.brush_strength = float(data.get("brush_strength", 0.5))


def serialize_prefs() -> dict:
    """Current brush prefs, for nesting under `weight_apply.brush` on save."""
    p = get_brush_prefs()
    return {
        "brush_projection": p.brush_projection,
        "brush_radius": p.brush_radius,
        "brush_falloff": p.brush_falloff,
        "brush_strength": p.brush_strength,
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
        self._bm, self._bvh = build_bvh(self._facade.get_obj())
        self._kd = None  # lazily built -- only Screen projection needs it, see _get_kdtree()
        self._last_dab_key = None
        self._dabbed = False

        SUPERSKIN_OT_weight_brush._stroke_active = True
        self._timer = context.window_manager.event_timer_add(
            _BRUSH_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        brush_draw.show()
        self._dab(context, event)
        return {'RUNNING_MODAL'}

    @staticmethod
    def _resolve_mode(event):
        """Which Weight Apply action this dab performs, read live from the
        held modifier key -- see this module's docstring for the mapping.
        Checked fresh every tick, so switching modifiers mid-stroke (even
        without moving the mouse) takes effect on the very next dab."""
        if event.alt:
            return "sharpen"
        if event.ctrl:
            return "scale"
        if event.shift:
            return "smooth"
        return "add"

    def _get_kdtree(self):
        """Lazily build (once per stroke, not once per dab) the vertex
        KDTree `gather_brush_vertices_screen()` needs -- most strokes never
        use Screen projection, so building it unconditionally in invoke()
        would waste the O(n log n) cost on every Surface-projection stroke
        too."""
        if self._kd is None:
            self._kd = build_vertex_kdtree(self._facade, self._facade.get_obj().matrix_world)
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

    def _dab(self, context, event):
        obj = self._facade.get_obj()
        hit, face_index, hit_world = raycast_under_cursor(context, event, obj, self._bvh)
        if not hit:
            return
        p = get_brush_prefs()

        # brush_radius is always world-space mesh units -- brush_projection
        # only picks which gatherer runs, not what the radius means. Both
        # return {v_idx: distance} in world units, so the falloff-banding
        # code below always divides by brush_radius directly with no
        # per-mode unit tracking.
        if p.brush_projection == 'SCREEN':
            dists = gather_brush_vertices_screen(self._get_kdtree(), hit_world, p.brush_radius)
        else:
            dists = gather_brush_vertices(
                self._facade, self._bm, face_index, hit_world, obj.matrix_world, p.brush_radius,
            )

        # Cursor circle -- world_radius_to_screen_px() is purely a display
        # conversion here, same for both modes, since brush_radius itself
        # never varies by projection.
        center_2d = world_to_screen(context, hit_world)
        if center_2d is not None:
            radius_px = world_radius_to_screen_px(context, hit_world, p.brush_radius)
            falloff_edge_px = radius_px * max(0.0, 1.0 - p.brush_falloff)
            brush_draw.update(
                (center_2d.x, center_2d.y), radius_px, falloff_edge_px,
                f"{p.brush_projection.title()}  R:{p.brush_radius:.1f}  "
                f"F:{p.brush_falloff:.2f}  S:{p.brush_strength:.2f}",
            )

        if not dists:
            return

        mode = self._resolve_mode(event)

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
            if p.brush_falloff <= 0.0:
                # Fast path: every vertex gets full strength, one call --
                # identical cost to before falloff banding existed.
                self._apply_one(context, mode, list(dists.keys()), p.brush_strength)
            else:
                radius = max(p.brush_radius, 1e-6)
                bands = [[] for _ in range(_FALLOFF_BANDS)]
                for v_idx, dist in dists.items():
                    t = min(dist / radius, 1.0)
                    band_idx = min(int(t * _FALLOFF_BANDS), _FALLOFF_BANDS - 1)
                    bands[band_idx].append(v_idx)
                for band_idx, band_verts in enumerate(bands):
                    if not band_verts:
                        continue
                    band_t = (band_idx + 0.5) / _FALLOFF_BANDS
                    weight = _falloff_weight(band_t, p.brush_falloff)
                    if weight <= 0.0:
                        continue
                    self._apply_one(context, mode, band_verts, p.brush_strength * weight)
        finally:
            context.scene.superskin_internal_transaction = False

        if self._dabbed:
            context.area.header_text_set(
                f"Weight Brush [{mode}] {p.brush_projection.title()} radius={p.brush_radius:.3f} "
                f"falloff={p.brush_falloff:.2f} strength={p.brush_strength:.2f}"
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

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._dab(context, event)
            self._remove_timer(context)
            return {'FINISHED'} if self._dabbed else {'CANCELLED'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._remove_timer(context)
            return {'FINISHED'} if self._dabbed else {'CANCELLED'}

        return {'RUNNING_MODAL'}


# ── Registration ─────────────────────────────────────────────────────────

def register():
    bpy.utils.register_class(SSPrefWeightBrush)
    bpy.types.WindowManager.superskin_weight_brush_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightBrush, options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush)
    try:
        del bpy.types.WindowManager.superskin_weight_brush_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightBrush)
