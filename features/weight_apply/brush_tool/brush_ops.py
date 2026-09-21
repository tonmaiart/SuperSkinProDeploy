"""Weight Brush -- hold-to-paint modal operator."""

import bpy

from ....core.facade import CoreFacade
from .brush_logic import (
    build_bvh, build_screen_kdtree, raycast_under_cursor, vertex_hide_flags,
    gather_brush_vertices, gather_brush_vertices_screen,
    screen_mode_radius_px,
)
from . import brush_draw, brush_prep

_BRUSH_APPLY_INTERVAL = 1.0 / 60.0

_DEFAULT_HARDNESS_VALUE = 1.0

_LEGACY_HARDNESS_VALUES = {
    'HARD': 1.0, 'MEDIUM': 0.5, 'SOFT': 0.0,
    'SHARP_5': 1.0, 'BLEND_50': 0.5, 'BLEND_70': 0.3, 'FULL': 0.0,
}

_FALLOFF_RING_BANDS = 5


def get_falloff_start_fraction(p=None) -> float:
    """The `[0.0, 1.0]` normalized-radius fraction (0=brush center, 1=brush edge) at which
    *p*'s current."""
    if p is None:
        p = get_brush_prefs()
    return max(0.0, min(1.0, p.brush_hardness))


def _bucket_by_falloff(dists: dict, radius: float, falloff_start: float):
    """Buckets *dists* (`{v_idx: distance}`, same units as *radius*."""
    if radius <= 1e-9 or falloff_start >= 1.0:
        return [(1.0, list(dists.keys()))]

    core = []
    ring_buckets = {}
    ring_span = 1.0 - falloff_start
    for v_idx, dist in dists.items():
        frac = min(1.0, dist / radius)
        if frac <= falloff_start:
            core.append(v_idx)
            continue
        band = min(_FALLOFF_RING_BANDS - 1, int((frac - falloff_start) / ring_span * _FALLOFF_RING_BANDS))
        ring_buckets.setdefault(band, []).append(v_idx)

    bands = []
    if core:
        bands.append((1.0, core))
    for band, v_idxs in ring_buckets.items():
        weight = 1.0 - (band + 0.5) / _FALLOFF_RING_BANDS
        if weight > 0.0:
            bands.append((weight, v_idxs))
    return bands


def _coerce_hardness_value(value) -> float:
    """Maps a legacy `brush_hardness`/`brush_falloff` preset identifier onto its equivalent
    falloff-start fraction."""
    if isinstance(value, str) and value in _LEGACY_HARDNESS_VALUES:
        return _LEGACY_HARDNESS_VALUES[value]
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_HARDNESS_VALUE
    return max(0.0, min(1.0, f))


def _on_brush_changed(self, context):
    from ....core.facade import CoreFacade
    CoreFacade.save_prefs()


def _slider_intensity(mode):
    """*mode*'s own Add/Scale/Smooth/Sharpen N-panel slider value
    (`SSPrefWeightApply.add_val`/`scale_val`/`smooth_val`/`sharpen_val`)."""
    from ..weight_apply_feature import get_prefs
    p = get_prefs()
    return {
        "add": p.add_val, "scale": p.scale_val,
        "smooth": p.smooth_val, "sharpen": p.sharpen_val,
    }.get(mode, 0.0)


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


def _next_projection(current: str) -> str:
    """The identifier one step after *current* in `_PROJECTION_IDENTS`, wrapping back to the
    first after the last."""
    try:
        idx = _PROJECTION_IDENTS.index(current)
    except ValueError:
        return _PROJECTION_IDENTS[0]
    return _PROJECTION_IDENTS[(idx + 1) % len(_PROJECTION_IDENTS)]


class SSPrefWeightBrush(bpy.types.PropertyGroup):
    """Weight Brush settings (per-machine)."""
    brush_projection: bpy.props.EnumProperty(
        name="",
        description="Which vertices Radius can reach -- does not change what Radius means",
        items=[(ident, label, desc) for ident, label, desc in _PROJECTION_ITEMS],
        default='SURFACE',
        update=_on_brush_changed,
    )
    brush_projected: bpy.props.BoolProperty(
        name="Projected",
        description=(
            "Reach every vertex under the brush circle on screen, regardless of facing "
            "direction or surface connectivity (Screen projection). Off follows the surface "
            "(Surface projection)"
        ),
        get=lambda self: self.brush_projection == 'SCREEN',
        set=lambda self, value: setattr(
            self, "brush_projection", 'SCREEN' if value else 'SURFACE',
        ),
    )
    brush_radius_surface: bpy.props.FloatProperty(
        name="Surface Radius", default=0.1, min=0.0, max=1.0,
    )
    brush_radius_screen: bpy.props.FloatProperty(
        name="Screen Radius", default=0.1, min=0.0, max=1.0,
    )
    brush_radius: bpy.props.FloatProperty(
        name="Size",
        description=(
            "Brush footprint size, world-space mesh units. Surface and Screen "
            "projection each keep their own value. F to adjust interactively"
        ),
        default=0.1, min=0.0, max=1.0,
        get=lambda self: (
            self.brush_radius_screen if self.brush_projection == 'SCREEN'
            else self.brush_radius_surface
        ),
        set=lambda self, value: setattr(
            self,
            "brush_radius_screen" if self.brush_projection == 'SCREEN'
            else "brush_radius_surface",
            value,
        ),
        update=_on_brush_changed,
    )
    brush_hardness: bpy.props.FloatProperty(
        name="Hardness",
        description=(
            "How gradually a dab fades out from the brush center to its edge -- "
            "1.0 stays full strength across the whole radius (a hard edge), 0.0 "
            "fades across the entire radius (a soft brush). Never affects the "
            "Add/Scale/Smooth/Sharpen slider's own intensity. Shift+F to adjust "
            "interactively"
        ),
        default=_DEFAULT_HARDNESS_VALUE, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_brush_changed,
    )


def get_brush_prefs() -> "SSPrefWeightBrush":
    return bpy.context.window_manager.superskin_weight_brush_prefs


class SUPERSKIN_OT_cycle_brush_projection(bpy.types.Operator):
    """Cycle the Weight Brush's Projection (Surface/Screen)."""
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
    """Write the `weight_apply.brush` JSON sub-section into live prefs."""
    p = get_brush_prefs()
    p.brush_projection = data.get("brush_projection", "SURFACE")
    legacy_radius = float(data.get("brush_radius", 0.1))
    p.brush_radius_surface = float(data.get("brush_radius_surface", legacy_radius))
    p.brush_radius_screen = float(data.get("brush_radius_screen", legacy_radius))
    p.brush_hardness = _coerce_hardness_value(
        data.get("brush_hardness", data.get("brush_falloff", _DEFAULT_HARDNESS_VALUE)),
    )


def serialize_prefs() -> dict:
    """Current brush prefs, for nesting under `weight_apply.brush` on save."""
    p = get_brush_prefs()
    return {
        "brush_projection": p.brush_projection,
        "brush_radius_surface": p.brush_radius_surface,
        "brush_radius_screen": p.brush_radius_screen,
        "brush_hardness": p.brush_hardness,
    }


class SUPERSKIN_OT_weight_brush(bpy.types.Operator):
    """Hold-to-paint Add/Scale/Smooth/Sharpen, constrained to a circular (geodesic-radius)
    footprint that follows the cursor across the mesh surface."""
    bl_idname = "superskin.weight_brush"
    bl_label = "Weight Brush"
    bl_options = {'REGISTER', 'UNDO'}

    _stroke_active = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (CoreFacade.is_system_activated() and CoreFacade.is_editing_weights() and
                obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT')

    def invoke(self, context, event):
        from ..weight_apply_feature import WeightApplyFeature

        self._facade = CoreFacade(context)
        self._feature = WeightApplyFeature()
        prepared = brush_prep.take(self._facade)
        if prepared is not None:
            self._ctx = prepared.ctx
            self._bvh = prepared.bvh
            self._posed_coords = prepared.posed_coords
            self._bm = self._facade.get_obj().data
            brush_prep.refresh_cheap_fields(self._ctx, self._facade)
            if prepared.pose_stale:
                self._bm, self._bvh, self._posed_coords = build_bvh(context, self._facade.get_obj())
        else:
            self._ctx = self._feature.snapshot_context(self._facade, need_selected=False)
            self._bm, self._bvh, self._posed_coords = build_bvh(context, self._facade.get_obj())
        self._selection_restrict = brush_prep.selected_vertex_set(self._bm)
        self._kd = None
        self._last_dab_key = None
        self._dabbed = False
        self._cursor_hit = None
        self._mode = self._resolve_mode(event)

        SUPERSKIN_OT_weight_brush._stroke_active = True
        self._facade.begin_live_stroke(keep_state=prepared is not None)
        self._timer = context.window_manager.event_timer_add(
            _BRUSH_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        self._dab(context, event)
        return {'RUNNING_MODAL'}

    @staticmethod
    def _resolve_mode(event):
        """Which Weight Apply action this stroke performs, from whichever modifier key is held
        on *event*."""
        if event.alt:
            return "sharpen"
        if event.ctrl:
            return "scale"
        if event.shift:
            return "smooth"
        return "add"

    def _get_kdtree(self, context):
        """Lazily build (once per stroke, not once per dab) the 2D screen-space KDTree
        `gather_brush_vertices_screen()` needs."""
        if self._kd is None:
            obj = self._facade.get_obj()
            self._kd = build_screen_kdtree(
                obj.matrix_world, context.region, context.region_data, self._posed_coords,
                hidden_vertices=vertex_hide_flags(obj.data),
            )
        return self._kd

    def _apply_one(self, context, mode, verts, intensity):
        """One `apply_action()` call over *verts* at *intensity*, folding the result back into
        `self._ctx` so the next call reads the just-written state."""
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
        """Refresh the drawn brush cursor at the current mouse position, and cache whatever
        positional data `_dab()` needs (`self._cursor_hit`) so it doesn't have to recompute it."""
        obj = self._facade.get_obj()
        p = get_brush_prefs()
        mode = self._mode
        falloff_start = get_falloff_start_fraction(p)
        label = ""

        if p.brush_projection == 'SCREEN':
            # No raycast at all -- see this module's docstring. Positioned
            # at the raw mouse position, sized in constant on-screen pixels.
            center_2d = (event.mouse_region_x, event.mouse_region_y)
            radius_px = screen_mode_radius_px(p.brush_radius)
            brush_draw.show_screen(center_2d, radius_px, radius_px * falloff_start, label)
            self._cursor_hit = ('SCREEN', center_2d, radius_px)
        else:
            hit, face_index, hit_world, hit_normal = raycast_under_cursor(
                context, event, obj, self._bvh,
            )
            if not hit:
                self._cursor_hit = None
                return
            brush_draw.show_surface(
                hit_world, hit_normal, p.brush_radius, p.brush_radius * falloff_start, label,
            )
            self._cursor_hit = ('SURFACE', face_index, hit_world, hit_normal)

    def _dab(self, context, event):
        obj = self._facade.get_obj()
        p = get_brush_prefs()
        mode = self._mode
        base_intensity = _slider_intensity(mode)

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

        if self._selection_restrict is not None:
            dists = {v_idx: d for v_idx, d in dists.items() if v_idx in self._selection_restrict}

        if not dists:
            return

        dab_key = (frozenset(dists), mode)
        if dab_key == self._last_dab_key:
            return
        self._last_dab_key = dab_key

        bands = _bucket_by_falloff(dists, p.brush_radius, get_falloff_start_fraction(p))

        context.scene.superskin_internal_transaction = True
        try:
            for weight, v_idxs in bands:
                self._apply_one(context, mode, v_idxs, base_intensity * weight)
        finally:
            context.scene.superskin_internal_transaction = False

    def _remove_timer(self, context):
        context.window_manager.event_timer_remove(self._timer)
        brush_draw.hide()
        SUPERSKIN_OT_weight_brush._stroke_active = False
        try:
            self._facade.end_live_stroke(keep_state=True)
        except Exception as exc:
            self._facade.drop_live_state(self._facade.get_obj().name)
            print(f"[SuperSkinPro] end_live_stroke failed: {exc}")
            return
        try:
            brush_prep.store(self._facade, self._ctx, self._bvh, self._posed_coords)
        except Exception:
            self._facade.drop_live_state(self._facade.get_obj().name)

    def modal(self, context, event):
        if event.type == 'TIMER':
            self._dab(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            self._update_cursor(context, event)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._dab(context, event)
            self._remove_timer(context)
            return {'FINISHED'} if self._dabbed else {'CANCELLED'}

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._remove_timer(context)
            return {'FINISHED'} if self._dabbed else {'CANCELLED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        """Blender calls this (not modal()) when it force-terminates this operator outside
        modal()'s own RELEASE/RIGHTMOUSE/ESC branches."""
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
    bpy.utils.register_class(SUPERSKIN_OT_cycle_brush_projection)
    bpy.utils.register_class(SUPERSKIN_OT_weight_brush)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_weight_brush)
    bpy.utils.unregister_class(SUPERSKIN_OT_cycle_brush_projection)
    try:
        del bpy.types.WindowManager.superskin_weight_brush_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightBrush)
