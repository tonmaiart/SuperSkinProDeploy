
import math
import time

import bpy
import numpy as np

from ....core.facade import CoreFacade
from .brush_logic import build_bvh, raycast_at, screen_mode_radius_px, vertex_hide_flags
from .. import session_display
from . import brush_draw, brush_hover, brush_prep
from ..gc_pause import collection_paused
from .brush_stamps import (
    DAB_SCALE, FALLOFF_IDS, FALLOFF_SHAPES, STAMP_SPACING, _CIRCLE_SAMPLES, _DAB_MAX_SAMPLES,
    _DAB_MIN_SPACING_PX, _DAB_SPACING, _DEFAULT_FALLOFF, _FALLOFF_ITEMS, _FALLOFF_RING_BANDS,
    _FOLLOW_PER_RADIUS, _ISLAND_MIN_SEEN, _ISLAND_PROBES, _OLD_FALLOFF_NAMES, _STAMP_LEVELS,
    _SURFACE_HIT_KINDS, _SURFACE_RADIUS_PX_FALLBACK, _falloff_intensity, _keeps_volume, _pow,
    _slider_intensity, band_strengths, falloff_weights, follow_share, local_brush_radius,
    path_stamps, quantize_bands, stamp_amount, stamp_totals, stroke_bands,
)
from .brush_prefs import (
    CURSOR_COLOR_DEFAULTS, FALLOFF_DEFAULTS, PRESSURE_DEFAULTS, SCREEN_RADIUS_MAX,
    SSPrefWeightBrush, SURFACE_RADIUS_MAX, _BRUSH_MODE_ITEMS, _CURSOR_COLOR_PROPS,
    _FALLOFF_STORE_ITEMS, _PROJECTION_ITEMS, _falloff_items, _falloff_items_cache, _get_brush_mode,
    _get_connected_only, _get_falloff, _get_pressure_strength, _get_projected, _on_brush_changed,
    _on_keep_volume, _set_brush_mode, _set_connected_only, _set_falloff, _set_pressure_strength,
    _set_projected, _side, cursor_color_prop, get_brush_prefs, mirror_axis, populate_prefs,
    serialize_prefs,
)
from .brush_stroke_lookups import StrokeLookups
from .brush_stroke_apply import StrokeApply
from .brush_stroke_gather import StrokeGather
from .brush_stroke_layers import StrokeLayers

_BRUSH_APPLY_INTERVAL = 1.0 / 60.0

_AFFECTED_ONLY_LABEL = "Smooth Affected Only"
_AFFECTED_ONLY_COLOR = (1.0, 0.3, 0.6, 1.0)

_last_stroke_point = None


def last_stroke_point(obj_name):
    if _last_stroke_point is None or _last_stroke_point[0] != obj_name:
        return None
    return _last_stroke_point[1].copy(), _last_stroke_point[2]


def last_stroke_bone(obj_name):
    if _last_stroke_point is None or _last_stroke_point[0] != obj_name:
        return None
    return _last_stroke_point[3]


def clear_last_stroke_point():
    global _last_stroke_point
    _last_stroke_point = None


class SUPERSKIN_OT_weight_brush(
        StrokeLookups, StrokeApply, StrokeGather, StrokeLayers, bpy.types.Operator):
    """Paint weights with a brush"""
    bl_idname = "superskin.weight_brush"
    bl_label = "Weight Brush"
    bl_options = {'REGISTER', 'UNDO'}

    _stroke_active = False

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_skinning_session(context)

    def invoke(self, context, event):
        with collection_paused():
            return self._invoke(context, event)

    def _invoke(self, context, event):
        from ..weight_apply_feature import WeightApplyFeature

        if not context.active_object.visible_get():
            self.report({'WARNING'}, "The mesh is hidden; show it to paint")
            return {'CANCELLED'}
        stroke_start = CoreFacade.profile_section("weight_brush.stroke_start").__enter__()
        self._tick_gap = None
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
        self._ctx["layer_int"] = self._ctx["layer_int"].copy()
        if self._ctx["is_mask"]:
            self._ctx["mask_dict"] = dict(self._ctx["mask_dict"])
        self._visible_restrict = brush_prep.visible_vertex_set(self._bm)
        self._visible_flags = None
        self._posed_box = None
        self._gather_fade = 1.0
        self._screen_points = None
        self._world_points = None
        self._screen_island = None
        self._layer_links = None
        self._reach_failed = False
        self._reach_graphs = {}
        self._layer_bind = None
        self._layer_depths = None
        self._bind_links = None
        self._layer_pieces = None
        self._dab_strength = {}
        self._dab_leader = None
        self._dab_pieces = None
        self._follow_travel = None
        self._world_coords_cache = None
        self._posed_normals = None
        self._rest_normals = None
        self._stroke_front = None
        self._off_mesh = False
        self._hidden = None
        self._bind_failed = False
        self._stroke_piece = None
        self._stroke_leader = None
        self._click_face = None
        self._graph = None
        self._graph_failed = False
        self._last_sample = None
        self._last_hit_world = None
        self._stamp_carry = 0.0
        self._stamp_pressure = None
        self._pending_path = []
        self._stroke_strength = None
        self._sharpen_target = None
        self._dabbed = False
        self._stroke_warning = None
        self._stroke_smoothed = False
        self._cursor_hit = None
        self._mirror = None
        self._mirror_side = None
        self._mode = self._resolve_mode(event)
        self._pressure = 1.0
        self._record_pressure(event)

        SUPERSKIN_OT_weight_brush._stroke_active = True
        self._display_follows = False
        try:
            self._display_follows = session_display.begin_stroke(context)
        except Exception as exc:
            print(f"[SuperSkinPro] the display cannot follow this stroke: {exc}")
        self._facade.begin_live_stroke(keep_state=prepared is not None)
        self._timer = context.window_manager.event_timer_add(
            _BRUSH_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        stroke_start.__exit__(None, None, None)
        self._timed_dab(context, event)
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

    def _record_pressure(self, event):
        pressure = getattr(event, "pressure", 1.0)
        if pressure > 0.0:
            self._pressure = min(1.0, pressure)

    def _radius(self, p):
        return p.brush_radius

    def _update_cursor(self, context, event, redraw=True):
        obj = self._facade.get_obj()
        p = get_brush_prefs()
        mode = self._mode
        radius = self._radius(p)
        label = ""
        if mode == "smooth":
            from ..weight_apply_feature import get_prefs
            if get_prefs().smooth_affected_only:
                label = _AFFECTED_ONLY_LABEL

        if p.brush_projection == 'SCREEN':
            center_2d = (event.mouse_region_x, event.mouse_region_y)
            radius_px = screen_mode_radius_px(radius)
            brush_draw.show_screen(center_2d, radius_px, label, _AFFECTED_ONLY_COLOR,
                                   redraw=redraw)
            self._cursor_hit = ('SCREEN', center_2d, radius_px)
        else:
            hit, face_index, hit_world, hit_normal = raycast_at(
                context, (event.mouse_region_x, event.mouse_region_y), obj, self._bvh,
            )
            if not hit:
                air = self._air_hit(context, (event.mouse_region_x, event.mouse_region_y), obj, p)
                if air is not None:
                    center_2d, radius_px = air[1], air[2]
                    brush_draw.show_screen(center_2d, radius_px, label, _AFFECTED_ONLY_COLOR,
                                           color_projection=p.brush_projection, redraw=redraw)
                self._cursor_hit = air
                return
            brush_hover.remember_hit(obj, hit_world)
            brush_draw.show_surface(hit_world, hit_normal, radius, label, _AFFECTED_ONLY_COLOR,
                                    redraw=redraw)
            self._cursor_hit = (p.brush_projection, face_index, hit_world, hit_normal)

    @collection_paused()
    def _timed_dab(self, context, event):
        if self._tick_gap is not None:
            self._tick_gap.__exit__(None, None, None)
        with self._facade.profile_section("weight_brush.dab"):
            self._dab(context, event)
        coord = (event.mouse_region_x, event.mouse_region_y)
        if coord != getattr(self, "_drawn_coord", None):
            self._drawn_coord = coord
            CoreFacade.tag_redraw_areas()
        self._tick_gap = self._facade.profile_section("weight_brush.tick_gap").__enter__()

    @collection_paused()
    def _dab(self, context, event):
        p = get_brush_prefs()
        mode = self._mode
        base_intensity = _slider_intensity(mode)
        if event.type != 'TIMER':
            self._record_pressure(event)
        pressure = self._pressure if p.use_pressure_strength else 1.0

        self._update_cursor(context, event, redraw=False)
        coord = (event.mouse_region_x, event.mouse_region_y)
        if mode in DAB_SCALE:
            ids, amounts = self._stamp_amounts(
                context, coord, p, mode, base_intensity * DAB_SCALE[mode], pressure,
            )
            self._last_sample = coord
            bands = quantize_bands(ids, amounts)
            if bands:
                top = float(amounts.max())
                scale = min(1.0, max(0.0, base_intensity)) / top if top > 0.0 else 0.0
                self._dab_strength = (dict(zip(ids.tolist(), (amounts * scale).tolist()))
                                      if scale > 0.0 and _keeps_volume(p) else {})
                self._apply_dab(context, mode, bands, p)
            return

        samples = self._samples(context, coord, p)
        if self._last_sample is None:
            self._follow_travel = None
        else:
            moved = math.hypot(coord[0] - self._last_sample[0], coord[1] - self._last_sample[1])
            self._follow_travel = moved / max(1e-6, self._radius_px(context, p))
        self._last_sample = coord

        fade = 0.0
        hits = []
        for sample in samples:
            cursor_hit = self._cursor_hit if sample == coord else self._hit_at(context, sample, p)
            if cursor_hit is not None and cursor_hit[0] in _SURFACE_HIT_KINDS:
                self._last_hit_world = cursor_hit[2]
            hits.append(cursor_hit)
        batched = self._batched_surface(p)
        with self._facade.profile_section("weight_brush.gather"):
            walks = self._surface_walks(hits, p) if batched else [None] * len(hits)
        found_ids, found_dist = [], []
        for cursor_hit, walked in zip(hits, walks):
            if cursor_hit is None:
                continue
            with self._facade.profile_section("weight_brush.gather"):
                ids, dist = self._gathered_arrays(context, cursor_hit, p, walked, visible_only=False)
            if len(ids):
                fade = max(fade, self._gather_fade)
                found_ids.append(ids)
                found_dist.append(dist)
        if not found_ids:
            return
        pressure *= fade

        ids = np.concatenate(found_ids)
        dist = np.concatenate(found_dist).astype(np.float64, copy=False)
        order = np.lexsort((dist, ids))
        ids, dist = ids[order], dist[order]
        nearest = np.ones(len(ids), dtype=bool)
        nearest[1:] = ids[1:] != ids[:-1]
        ids, dist = ids[nearest], dist[nearest]
        if self._visible_restrict is not None:
            if self._visible_flags is None:
                self._visible_flags = ~vertex_hide_flags(self._bm)
            shown = self._visible_flags[ids]
            ids, dist = ids[shown], dist[shown]
        weights = band_strengths(dist, self._radius(p), FALLOFF_SHAPES[p.falloff])
        reached = weights > 0.0
        ids, weights = ids[reached], weights[reached]
        if not len(ids):
            return

        if self._stroke_strength is None:
            self._stroke_strength = np.zeros(len(self._bm.vertices))
        bands = stroke_bands(mode, base_intensity, ids, weights, pressure, self._stroke_strength)
        if bands:
            self._dab_strength = dict(zip(ids.tolist(), weights.tolist())) if _keeps_volume(p) else {}
            self._apply_dab(context, mode, bands, p)

    def _apply_dab(self, context, mode, bands, p):
        context.scene.superskin_internal_transaction = True
        try:
            with self._facade.profile_section("weight_brush.apply", size=sum(len(vs) for _a, vs in bands)):
                self._apply_bands(mode, bands, p)
            if self._display_follows:
                rows = self._facade.take_live_rows()
                if rows is not None:
                    with self._facade.profile_section("weight_brush.display"):
                        session_display.move_dab(rows)
        finally:
            context.scene.superskin_internal_transaction = False

    @collection_paused()
    def _remove_timer(self, context):
        if self._timer is None:
            return
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        brush_draw.hide()
        SUPERSKIN_OT_weight_brush._stroke_active = False
        try:
            self._facade.end_live_stroke(keep_state=True)
        except Exception as exc:
            self._facade.drop_live_state(self._facade.get_obj().name)
            print(f"[SuperSkinPro] end_live_stroke failed: {exc}")
            session_display.end_stroke(committed=False)
            return
        session_display.end_stroke()
        CoreFacade.tag_redraw_areas()
        try:
            if self._dabbed:
                brush_prep.store(self._facade, self._ctx, None, None)
            else:
                brush_prep.store(self._facade, self._ctx, self._bvh, self._posed_coords)
        except Exception:
            self._facade.drop_live_state(self._facade.get_obj().name)

    def modal(self, context, event):
        if event.type == 'TIMER':
            self._timed_dab(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            self._record_pressure(event)
            self._note_path(event)
            self._update_cursor(context, event, redraw=False)
            return {'PASS_THROUGH'}

        if event.type == 'INBETWEEN_MOUSEMOVE':
            self._record_pressure(event)
            self._note_path(event)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._dab(context, event)
            self._remove_timer(context)
            return self._end_stroke()

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._remove_timer(context)
            return self._end_stroke()

        return {'PASS_THROUGH'}

    def _end_stroke(self):
        global _last_stroke_point
        if self._dabbed and self._last_hit_world is not None:
            obj = self._facade.get_obj()
            _last_stroke_point = (obj.name, self._last_hit_world.copy(), time.monotonic(),
                                  CoreFacade.active_vg_name_of(obj))
        if self._stroke_warning and not self._stroke_smoothed:
            self.report({'WARNING'}, self._stroke_warning)
        return {'FINISHED'} if self._dabbed else {'CANCELLED'}

    def cancel(self, context):
        try:
            self._remove_timer(context)
        except Exception:
            SUPERSKIN_OT_weight_brush._stroke_active = False
            brush_draw.hide()
            session_display.end_stroke(committed=False)



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
