
import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from ....core.facade import CoreFacade
from .brush_logic import (
    build_bvh, build_screen_points, raycast_at, vertex_hide_flags,
    gather_brush_vertices, gather_brush_vertices_screen, build_brush_graph,
    screen_mode_radius_px,
)
from . import brush_draw, brush_prep

_BRUSH_APPLY_INTERVAL = 1.0 / 60.0

_DAB_SPACING = 0.25
_DAB_MIN_SPACING_PX = 3.0
_DAB_MAX_SAMPLES = 24
_SURFACE_RADIUS_PX_FALLBACK = 20.0

_DEFAULT_HARDNESS_VALUE = 1.0

_AFFECTED_ONLY_LABEL = "Smooth Affected Only"
_AFFECTED_ONLY_COLOR = (1.0, 0.3, 0.6, 1.0)

_LEGACY_HARDNESS_VALUES = {
    'HARD': 1.0, 'MEDIUM': 0.5, 'SOFT': 0.0,
    'SHARP_5': 1.0, 'BLEND_50': 0.5, 'BLEND_70': 0.3, 'FULL': 0.0,
}

_FALLOFF_RING_BANDS = 5


def get_falloff_start_fraction(p=None) -> float:
    if p is None:
        p = get_brush_prefs()
    return max(0.0, min(1.0, p.brush_hardness))


def _bucket_by_falloff(dists: dict, radius: float, falloff_start: float):
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


def _falloff_intensity(mode, base_intensity, weight):
    if mode == "scale":
        return 1.0 - weight * (1.0 - base_intensity)
    return base_intensity * weight


def _slider_intensity(mode):
    from ..weight_apply_feature import get_prefs
    p = get_prefs()
    return {
        "add": p.add_val, "scale": p.scale_val,
        "smooth": p.smooth_val, "sharpen": p.sharpen_val,
    }.get(mode, 0.0)


_PROJECTION_ITEMS = (
    ('SURFACE', "Surface", "Paint only the visible surface under the brush"),
    ('SCREEN', "Projected", "Paint every vertex inside the circle on screen, including hidden and back-facing ones"),
)


class SSPrefWeightBrush(bpy.types.PropertyGroup):
    """Weight Brush settings (per-machine)."""
    brush_projection: bpy.props.EnumProperty(
        name="Projection",
        description="How the brush reaches vertices (Alt+F to switch)",
        items=[(ident, label, desc) for ident, label, desc in _PROJECTION_ITEMS],
        default='SURFACE',
        update=_on_brush_changed,
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
            "Brush size (F to adjust in the viewport)"
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
            "How sharp the brush edge is: 1 is a hard edge, 0 fades out softly (Shift+F to adjust in the viewport)"
        ),
        default=_DEFAULT_HARDNESS_VALUE, min=0.0, max=1.0, subtype='FACTOR',
        update=_on_brush_changed,
    )


def get_brush_prefs() -> "SSPrefWeightBrush":
    return bpy.context.window_manager.superskin_weight_brush_prefs


def populate_prefs(data: dict) -> None:
    p = get_brush_prefs()
    p.brush_projection = data.get("brush_projection", "SURFACE")
    legacy_radius = float(data.get("brush_radius", 0.1))
    p.brush_radius_surface = float(data.get("brush_radius_surface", legacy_radius))
    p.brush_radius_screen = float(data.get("brush_radius_screen", legacy_radius))
    p.brush_hardness = _coerce_hardness_value(
        data.get("brush_hardness", data.get("brush_falloff", _DEFAULT_HARDNESS_VALUE)),
    )


def serialize_prefs() -> dict:
    p = get_brush_prefs()
    return {
        "brush_projection": p.brush_projection,
        "brush_radius_surface": p.brush_radius_surface,
        "brush_radius_screen": p.brush_radius_screen,
        "brush_hardness": p.brush_hardness,
    }


class SUPERSKIN_OT_weight_brush(bpy.types.Operator):
    """Paint weights with a brush"""
    bl_idname = "superskin.weight_brush"
    bl_label = "Weight Brush"
    bl_options = {'REGISTER', 'UNDO'}

    _stroke_active = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (CoreFacade.is_editing_weights() and
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
        shared_layer = self._ctx["layer_int"]
        self._ctx["layer_int"] = dict(shared_layer)
        self._ctx["mask_dict"] = dict(self._ctx["mask_dict"])
        store_entry = self._ctx.get("_layer_store")
        if store_entry is not None and store_entry[0] is shared_layer:
            self._ctx["_layer_store"] = (self._ctx["layer_int"], store_entry[1].derive([], [], []))
        self._selection_restrict = brush_prep.selected_vertex_set(self._bm)
        self._screen_points = None
        self._graph = None
        self._graph_failed = False
        self._last_sample = None
        self._last_hit_world = None
        self._last_dab_key = None
        self._painted_verts = set()
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
        if event.alt:
            return "sharpen"
        if event.ctrl:
            return "scale"
        if event.shift:
            return "smooth"
        return "add"

    def _get_screen_points(self, context):
        if self._screen_points is None:
            obj = self._facade.get_obj()
            self._screen_points = build_screen_points(
                obj.matrix_world, context.region, context.region_data, self._posed_coords,
                hidden_vertices=vertex_hide_flags(obj.data),
            )
        return self._screen_points

    def _get_graph(self):
        if self._graph is None and not self._graph_failed:
            try:
                self._graph = build_brush_graph(self._facade, self._posed_coords)
            except ValueError:
                raise
            except Exception as exc:
                self._facade.debug_log("feature_domains", f"weight brush graph build failed: {exc!r}")
                self._graph = None
            self._graph_failed = self._graph is None
        return self._graph

    def _apply_bands(self, mode, bands):
        result = self._feature.apply_bands(mode, self._facade, self._ctx, bands, in_place=True)
        if result.get("status") == "FINISHED":
            self._dabbed = True

    def _radius_px(self, context, p):
        if p.brush_projection == 'SCREEN':
            return screen_mode_radius_px(p.brush_radius)
        region, rv3d = context.region, context.region_data
        if self._last_hit_world is None or region is None or rv3d is None:
            return _SURFACE_RADIUS_PX_FALLBACK
        right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
        a = view3d_utils.location_3d_to_region_2d(region, rv3d, self._last_hit_world)
        b = view3d_utils.location_3d_to_region_2d(
            region, rv3d, self._last_hit_world + right * p.brush_radius,
        )
        if a is None or b is None:
            return _SURFACE_RADIUS_PX_FALLBACK
        return (b - a).length

    def _samples(self, context, coord, p):
        last = self._last_sample
        if last is None:
            return [coord]
        dx, dy = coord[0] - last[0], coord[1] - last[1]
        length = math.hypot(dx, dy)
        spacing = max(_DAB_MIN_SPACING_PX, self._radius_px(context, p) * _DAB_SPACING)
        if length <= spacing:
            return [coord]
        count = min(_DAB_MAX_SAMPLES, math.ceil(length / spacing))
        return [(last[0] + dx * i / count, last[1] + dy * i / count) for i in range(1, count)] + [coord]

    def _hit_at(self, context, coord, p):
        if p.brush_projection == 'SCREEN':
            return ('SCREEN', coord, screen_mode_radius_px(p.brush_radius))
        hit, face_index, hit_world, hit_normal = raycast_at(
            context, coord, self._facade.get_obj(), self._bvh,
        )
        if not hit:
            return None
        return ('SURFACE', face_index, hit_world, hit_normal)

    def _gather(self, context, cursor_hit, p):
        if cursor_hit[0] == 'SCREEN':
            _kind, center_2d, radius_px = cursor_hit
            return gather_brush_vertices_screen(
                self._get_screen_points(context), center_2d, radius_px, p.brush_radius,
            )
        _kind, face_index, hit_world, _hit_normal = cursor_hit
        obj = self._facade.get_obj()
        return gather_brush_vertices(
            self._facade, self._bm, face_index, hit_world, obj.matrix_world, p.brush_radius,
            self._posed_coords, graph=self._get_graph(),
        )

    def _update_cursor(self, context, event):
        obj = self._facade.get_obj()
        p = get_brush_prefs()
        mode = self._mode
        falloff_start = get_falloff_start_fraction(p)
        label = ""
        if mode == "smooth":
            from ..weight_apply_feature import get_prefs
            if get_prefs().smooth_affected_only:
                label = _AFFECTED_ONLY_LABEL

        if p.brush_projection == 'SCREEN':
            center_2d = (event.mouse_region_x, event.mouse_region_y)
            radius_px = screen_mode_radius_px(p.brush_radius)
            brush_draw.show_screen(
                center_2d, radius_px, radius_px * falloff_start, label, _AFFECTED_ONLY_COLOR,
            )
            self._cursor_hit = ('SCREEN', center_2d, radius_px)
        else:
            hit, face_index, hit_world, hit_normal = raycast_at(
                context, (event.mouse_region_x, event.mouse_region_y), obj, self._bvh,
            )
            if not hit:
                self._cursor_hit = None
                return
            brush_draw.show_surface(
                hit_world, hit_normal, p.brush_radius, p.brush_radius * falloff_start, label,
                _AFFECTED_ONLY_COLOR,
            )
            self._cursor_hit = ('SURFACE', face_index, hit_world, hit_normal)

    def _dab(self, context, event):
        p = get_brush_prefs()
        mode = self._mode
        base_intensity = _slider_intensity(mode)

        self._update_cursor(context, event)
        coord = (event.mouse_region_x, event.mouse_region_y)
        samples = self._samples(context, coord, p)
        self._last_sample = coord

        dists = {}
        for sample in samples:
            cursor_hit = self._cursor_hit if sample == coord else self._hit_at(context, sample, p)
            if cursor_hit is None:
                continue
            if cursor_hit[0] == 'SURFACE':
                self._last_hit_world = cursor_hit[2]
            for v_idx, d in self._gather(context, cursor_hit, p).items():
                if d < dists.get(v_idx, math.inf):
                    dists[v_idx] = d

        if self._selection_restrict is not None:
            dists = {v_idx: d for v_idx, d in dists.items() if v_idx in self._selection_restrict}

        if mode in ("add", "scale"):
            dists = {v_idx: d for v_idx, d in dists.items() if v_idx not in self._painted_verts}

        if not dists:
            return

        dab_key = (frozenset(dists), mode)
        if dab_key == self._last_dab_key:
            return
        self._last_dab_key = dab_key

        if mode in ("add", "scale"):
            self._painted_verts.update(dists)

        bands = [
            (_falloff_intensity(mode, base_intensity, weight), v_idxs)
            for weight, v_idxs in _bucket_by_falloff(dists, p.brush_radius, get_falloff_start_fraction(p))
        ]

        context.scene.superskin_internal_transaction = True
        try:
            self._apply_bands(mode, bands)
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
        try:
            self._remove_timer(context)
        except Exception:
            SUPERSKIN_OT_weight_brush._stroke_active = False
            brush_draw.hide()



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
