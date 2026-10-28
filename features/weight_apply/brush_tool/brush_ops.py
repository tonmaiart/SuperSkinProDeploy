
import math
import time

import bpy
import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector

from ....core.facade import CoreFacade
from .brush_logic import (
    build_bvh, build_screen_points, build_world_points, raycast_at, vertex_hide_flags,
    gather_brush_vertices, gather_brush_vertices_screen, gather_brush_vertices_volume,
    build_brush_graph, keep_connected, keep_connected_from, layer_links, screen_mode_radius_px,
    build_reach_graph, keep_reachable, REACH_MAX_DETOUR, build_layer_bind, carry_bound_layers,
    brush_islands, lead_rows, coincident_welds, bind_links, inner_piece, weld_joined_pieces,
    nearest_on_ray,
)
from . import brush_draw, brush_hover, brush_prep

_BRUSH_APPLY_INTERVAL = 1.0 / 60.0
_FOLLOW_PER_RADIUS = 0.5
_ISLAND_PROBES = 16
_ISLAND_MIN_SEEN = 0.5
_CIRCLE_SAMPLES = ((0.0, 0.0),) + tuple(
    (ring / 3.0, 2.0 * math.pi * k / (6 * ring)) for ring in (1, 2, 3) for k in range(6 * ring)
)
_SURFACE_HIT_KINDS = ('SURFACE', 'VOLUME')

_DAB_SPACING = 0.25
_DAB_MIN_SPACING_PX = 3.0
_DAB_MAX_SAMPLES = 24
STAMP_SPACING = 0.1
DAB_SCALE = {"smooth": 1.0, "sharpen": 1.0}
_STAMP_LEVELS = 16
_SURFACE_RADIUS_PX_FALLBACK = 20.0

_AFFECTED_ONLY_LABEL = "Smooth Affected Only"
_AFFECTED_ONLY_COLOR = (1.0, 0.3, 0.6, 1.0)

_FALLOFF_RING_BANDS = 5

_last_stroke_point = None


def last_stroke_point(obj_name):
    if _last_stroke_point is None or _last_stroke_point[0] != obj_name:
        return None
    return _last_stroke_point[1].copy(), _last_stroke_point[2]


def clear_last_stroke_point():
    global _last_stroke_point
    _last_stroke_point = None


FALLOFF_SHAPES = {
    'CONSTANT': lambda x: 1.0,
    'SMOOTH': lambda x: (1.0 - x) ** 3 * (10.0 - 15.0 * (1.0 - x) + 6.0 * (1.0 - x) ** 2),
    'LINEAR': lambda x: 1.0 - x,
}
_FALLOFF_ITEMS = (
    ('CONSTANT', "Constant", "Full strength out to the rim", 'NOCURVE', 0),
    ('SMOOTH', "Smooth", "Fades smoothly from the center to the rim", 'SMOOTHCURVE', 1),
    ('LINEAR', "Linear", "Fades evenly from the center to the rim", 'LINCURVE', 2),
)
FALLOFF_IDS = tuple(ident for ident, *_rest in _FALLOFF_ITEMS)
_DEFAULT_FALLOFF = 'SMOOTH'
_OLD_FALLOFF_NAMES = {'SMOOTHER': 'SMOOTH', 'SHARP': 'LINEAR'}
_falloff_items_cache = [None, []]


def _falloff_items(_self, _context):
    from ....interface.utils.icons import get_falloff_icon_id
    icon_ids = tuple(get_falloff_icon_id(ident) for ident in FALLOFF_IDS)
    if _falloff_items_cache[0] != icon_ids:
        _falloff_items_cache[0] = icon_ids
        _falloff_items_cache[1] = [
            (ident, label, desc, icon_id or icon, number)
            for (ident, label, desc, icon, number), icon_id in zip(_FALLOFF_ITEMS, icon_ids)
        ]
    return _falloff_items_cache[1]


def _bucket_by_falloff(dists: dict, radius: float, shape=FALLOFF_SHAPES[_DEFAULT_FALLOFF]):
    if radius <= 1e-9:
        return [(1.0, list(dists.keys()))]

    ring_buckets = {}
    for v_idx, dist in dists.items():
        frac = min(1.0, dist / radius)
        band = min(_FALLOFF_RING_BANDS - 1, int(frac * _FALLOFF_RING_BANDS))
        ring_buckets.setdefault(band, []).append(v_idx)

    bands = []
    for band, v_idxs in ring_buckets.items():
        weight = shape((band + 0.5) / _FALLOFF_RING_BANDS)
        if weight > 0.0:
            bands.append((weight, v_idxs))
    return bands


def _on_brush_changed(self, context):
    from ....core.facade import CoreFacade
    CoreFacade.save_prefs()
    CoreFacade.tag_redraw_areas()


def _falloff_intensity(mode, base_intensity, weight):
    if mode == "scale":
        return 1.0 - weight * (1.0 - base_intensity)
    return base_intensity * weight


def follow_share(travel):
    return 1.0 - (1.0 - _FOLLOW_PER_RADIUS) ** (1.0 if travel is None else travel)


def path_stamps(points, carry, spacing, max_stamps):
    segments = []
    for a, b in zip(points, points[1:]):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length > 0.0:
            segments.append((a, b, length))
    path_length = sum(length for _a, _b, length in segments)
    if path_length <= 0.0 or spacing <= 0.0:
        return [], carry, spacing
    total = carry + path_length
    count = int(total // spacing)
    if count > max_stamps:
        spacing = total / max_stamps
        count = max_stamps
    stamps = []
    next_at = spacing - carry
    walked = 0.0
    for a, b, length in segments:
        while len(stamps) < count and next_at <= walked + length + 1e-9:
            f = min(1.0, max(0.0, (next_at - walked) / length))
            stamps.append(tuple(a[i] + (b[i] - a[i]) * f for i in range(3)))
            next_at += spacing
        walked += length
    while len(stamps) < count:
        stamps.append(tuple(points[-1]))
    return stamps, total - count * spacing, spacing


def stamp_amount(mode, amount, travel):
    if amount <= 0.0 or travel <= 0.0:
        return 0.0
    if mode == "sharpen":
        if amount >= 1.0:
            return 1.0
        return 1.0 - (1.0 - amount) ** travel
    return amount * math.sqrt(travel)


def add_stamp(mode, acc, amounts):
    if mode == "sharpen":
        for v, a in amounts.items():
            acc[v] = acc.get(v, 1.0) * (1.0 - min(1.0, a))
    else:
        for v, a in amounts.items():
            acc[v] = acc.get(v, 0.0) + a * a


def stamp_totals(mode, acc):
    if mode == "sharpen":
        return {v: 1.0 - rest for v, rest in acc.items() if rest < 1.0}
    return {v: math.sqrt(t) for v, t in acc.items() if t > 0.0}


def quantize_bands(amounts, levels=_STAMP_LEVELS):
    top = max(amounts.values(), default=0.0)
    if top <= 0.0:
        return []
    buckets = {}
    for v, a in amounts.items():
        if a > 0.0:
            buckets.setdefault(max(1, round(a / top * levels)), []).append(v)
    return [(top * q / levels, verts) for q, verts in sorted(buckets.items())]


def _keeps_volume(p):
    return p.brush_projection == 'VOLUME' or (p.brush_projection == 'SCREEN' and p.keep_volume)


def stroke_bands(mode, base_intensity, buckets, pressure, stroke_strength):
    grouped = {}
    for weight, v_idxs in buckets:
        strength = weight * pressure
        for v in v_idxs:
            old = stroke_strength.get(v, 0.0)
            if strength <= old + 1e-6:
                continue
            stroke_strength[v] = strength
            grouped.setdefault((old, strength), []).append(v)
    bands = []
    for (old, new), v_idxs in grouped.items():
        if mode == "scale":
            before = _falloff_intensity(mode, base_intensity, old)
            after = _falloff_intensity(mode, base_intensity, new)
            intensity = after / before if before > 1e-9 else after
        else:
            intensity = base_intensity * (new - old)
        bands.append((intensity, v_idxs))
    return bands


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
    ('VOLUME', "Volume", "Paint every vertex within the brush radius in 3D, including stacked layers such as clothing merged over a body, but not parts that only the pose brought close, such as a hand resting on a knee"),
)


def _get_projected(self):
    return self.brush_projection == 'SCREEN'


def _set_projected(self, value):
    if value:
        self.brush_projection = 'SCREEN'
    else:
        self.brush_projection = 'VOLUME' if self.keep_volume else 'SURFACE'


_BRUSH_MODE_ITEMS = (
    ('SURFACE', "Surface", "Paint the surface under the brush (Alt+F to switch)", 0),
    ('SCREEN', "Projected", "Paint every vertex inside the circle on screen, including hidden and back-facing ones (Alt+F to switch)", 1),
)


def _get_brush_mode(self):
    return 1 if _get_projected(self) else 0


def _set_brush_mode(self, value):
    _set_projected(self, value == 1)


def _on_keep_volume(self, context):
    if self.brush_projection != 'SCREEN':
        self.brush_projection = 'VOLUME' if self.keep_volume else 'SURFACE'
    _on_brush_changed(self, context)
    from ..draw import sync_keep_volume_hud
    sync_keep_volume_hud()


SURFACE_RADIUS_MAX = 1000.0
FALLOFF_DEFAULTS = {'SURFACE': 'SMOOTH', 'SCREEN': 'CONSTANT'}
_FALLOFF_STORE_ITEMS = [(ident, label, desc) for ident, label, desc, _icon, _n in _FALLOFF_ITEMS]


def _side(self):
    return 'screen' if self.brush_projection == 'SCREEN' else 'surface'


def _get_connected_only(self):
    return getattr(self, f"connected_only_{_side(self)}")


def _set_connected_only(self, value):
    setattr(self, f"connected_only_{_side(self)}", value)


def _get_falloff(self):
    return FALLOFF_IDS.index(getattr(self, f"falloff_{_side(self)}"))


def _set_falloff(self, value):
    setattr(self, f"falloff_{_side(self)}", FALLOFF_IDS[value])


CURSOR_COLOR_DEFAULTS = {
    "SURFACE": (1.0, 1.0, 0.0, 0.902),
    "SCREEN": (1.0, 0.0039, 0.0118, 0.902),
}
_CURSOR_COLOR_PROPS = {
    "SURFACE": "cursor_surface_color",
    "SCREEN": "cursor_screen_color",
    "VOLUME": "cursor_surface_color",
}
SCREEN_RADIUS_MAX = 1.0


class SSPrefWeightBrush(bpy.types.PropertyGroup):
    """Weight Brush settings (per-machine)."""
    brush_projection: bpy.props.EnumProperty(
        name="Projection",
        description="How the brush reaches vertices (Alt+F to switch)",
        items=[(ident, label, desc) for ident, label, desc in _PROJECTION_ITEMS],
        default='SURFACE',
        update=_on_brush_changed,
    )
    brush_mode: bpy.props.EnumProperty(
        name="Projection",
        description="How the brush reaches vertices (Alt+F to switch)",
        items=_BRUSH_MODE_ITEMS,
        get=_get_brush_mode,
        set=_set_brush_mode,
        options={'SKIP_SAVE'},
    )
    projected: bpy.props.BoolProperty(
        name="Projected",
        description="Paint every vertex inside the circle on screen, including hidden and back-facing ones",
        get=_get_projected,
        set=_set_projected,
        options={'SKIP_SAVE'},
    )
    keep_volume: bpy.props.BoolProperty(
        name="Keep Volume",
        description="Maintains gaps between overlapping layers (like clothing and body) so they deform together naturally.",
        default=False,
        update=_on_keep_volume,
    )
    brush_radius_surface: bpy.props.FloatProperty(
        name="Surface Radius", default=0.1, min=0.0, max=SURFACE_RADIUS_MAX,
    )
    brush_radius_screen: bpy.props.FloatProperty(
        name="Screen Radius", default=0.1, min=0.0, max=1.0,
    )
    brush_radius: bpy.props.FloatProperty(
        name="Size",
        description=(
            "Brush size (F to adjust in the viewport)"
        ),
        default=0.1, min=0.0, max=SURFACE_RADIUS_MAX,
        get=lambda self: (
            self.brush_radius_screen if self.brush_projection == 'SCREEN'
            else self.brush_radius_surface
        ),
        set=lambda self, value: (
            setattr(self, "brush_radius_screen", min(value, SCREEN_RADIUS_MAX))
            if self.brush_projection == 'SCREEN'
            else setattr(self, "brush_radius_surface", value)
        ),
        update=_on_brush_changed,
    )
    use_pressure_strength: bpy.props.BoolProperty(
        name="Strength Pressure",
        description="Use pen pressure to control brush strength.",
        default=True,
        update=_on_brush_changed,
    )
    connected_only_surface: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    connected_only_screen: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    connected_only: bpy.props.BoolProperty(
        name="Connected Only",
        description=(
            "Limits painting strictly to the connected mesh piece where the stroke began, avoiding "
            "bleed onto adjacent parts."
        ),
        get=_get_connected_only,
        set=_set_connected_only,
        options={'SKIP_SAVE'},
        update=_on_brush_changed,
    )
    falloff_surface: bpy.props.EnumProperty(
        items=_FALLOFF_STORE_ITEMS, default=FALLOFF_DEFAULTS['SURFACE'], options={'HIDDEN'},
    )
    falloff_screen: bpy.props.EnumProperty(
        items=_FALLOFF_STORE_ITEMS, default=FALLOFF_DEFAULTS['SCREEN'], options={'HIDDEN'},
    )
    falloff: bpy.props.EnumProperty(
        name="", description="Brush falloff for every weight action",
        items=_falloff_items, get=_get_falloff, set=_set_falloff, options={'SKIP_SAVE'},
        update=_on_brush_changed,
    )
    cursor_surface_color: bpy.props.FloatVectorProperty(
        name="Surface Color", description="Brush cursor color in Surface projection",
        subtype='COLOR_GAMMA', size=4, min=0.0, max=1.0,
        default=CURSOR_COLOR_DEFAULTS["SURFACE"], update=_on_brush_changed,
    )
    cursor_screen_color: bpy.props.FloatVectorProperty(
        name="Projected Color", description="Brush cursor color in Projected projection",
        subtype='COLOR_GAMMA', size=4, min=0.0, max=1.0,
        default=CURSOR_COLOR_DEFAULTS["SCREEN"], update=_on_brush_changed,
    )


def cursor_color_prop(projection: str) -> str:
    return _CURSOR_COLOR_PROPS[projection]


def get_brush_prefs() -> "SSPrefWeightBrush":
    return bpy.context.window_manager.superskin_weight_brush_prefs


def populate_prefs(data: dict) -> None:
    p = get_brush_prefs()
    projection = data.get("brush_projection", "SURFACE")
    p.keep_volume = bool(data.get("keep_volume", projection == "VOLUME"))
    p.brush_projection = projection
    legacy_radius = float(data.get("brush_radius", 0.1))
    p.brush_radius_surface = float(data.get("brush_radius_surface", legacy_radius))
    p.brush_radius_screen = float(data.get("brush_radius_screen", legacy_radius))
    p.use_pressure_strength = bool(data.get("use_pressure_strength", True))
    for side, key in (('surface', 'SURFACE'), ('screen', 'SCREEN')):
        setattr(p, f"connected_only_{side}", bool(data.get(f"connected_only_{side}", False)))
        shape = data.get(f"falloff_{side}", FALLOFF_DEFAULTS[key])
        shape = _OLD_FALLOFF_NAMES.get(shape, shape)
        setattr(p, f"falloff_{side}", shape if shape in FALLOFF_SHAPES else FALLOFF_DEFAULTS[key])
    for projection, default in CURSOR_COLOR_DEFAULTS.items():
        prop = _CURSOR_COLOR_PROPS[projection]
        color = data.get(prop)
        valid = isinstance(color, (list, tuple)) and len(color) == 4
        setattr(p, prop, [float(c) for c in color] if valid else default)


def serialize_prefs() -> dict:
    p = get_brush_prefs()
    return {
        "brush_projection": p.brush_projection,
        "keep_volume": p.keep_volume,
        "brush_radius_surface": p.brush_radius_surface,
        "brush_radius_screen": p.brush_radius_screen,
        "use_pressure_strength": p.use_pressure_strength,
        "connected_only_surface": p.connected_only_surface,
        "connected_only_screen": p.connected_only_screen,
        "falloff_surface": p.falloff_surface,
        "falloff_screen": p.falloff_screen,
        **{_CURSOR_COLOR_PROPS[proj]: list(getattr(p, _CURSOR_COLOR_PROPS[proj])) for proj in CURSOR_COLOR_DEFAULTS},
    }


class SUPERSKIN_OT_weight_brush(bpy.types.Operator):
    """Paint weights with a brush"""
    bl_idname = "superskin.weight_brush"
    bl_label = "Weight Brush"
    bl_options = {'REGISTER', 'UNDO'}

    _stroke_active = False

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_skinning_session(context)

    def invoke(self, context, event):
        from ..weight_apply_feature import WeightApplyFeature

        if not context.active_object.visible_get():
            self.report({'WARNING'}, "The mesh is hidden; show it to paint")
            return {'CANCELLED'}
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
        self._ctx["layer_int"] = dict(self._ctx["layer_int"])
        self._ctx["mask_dict"] = dict(self._ctx["mask_dict"])
        self._visible_restrict = brush_prep.visible_vertex_set(self._bm)
        self._posed_box = None
        self._gather_fade = 1.0
        self._screen_points = None
        self._world_points = None
        self._screen_island = None
        self._layer_links = None
        self._reach_failed = False
        self._layer_bind = None
        self._bind_links = None
        self._leader_links = None
        self._layer_pieces = None
        self._dab_strength = {}
        self._dab_leader = None
        self._dab_pieces = None
        self._follow_travel = None
        self._world_coords_cache = None
        self._hidden = None
        self._bind_failed = False
        self._stroke_piece = None
        self._stroke_leader = None
        self._stroke_inner = None
        self._rest_points = None
        self._rest_normals = None
        self._graph = None
        self._graph_failed = False
        self._last_sample = None
        self._last_hit_world = None
        self._stamp_carry = 0.0
        self._stamp_pressure = None
        self._pending_path = []
        self._stroke_strength = {}
        self._sharpen_target = None
        self._dabbed = False
        self._stroke_warning = None
        self._stroke_smoothed = False
        self._cursor_hit = None
        self._mode = self._resolve_mode(event)
        self._pressure = 1.0
        self._record_pressure(event)

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

    def _record_pressure(self, event):
        pressure = getattr(event, "pressure", 1.0)
        if pressure > 0.0:
            self._pressure = min(1.0, pressure)

    def _radius(self, p):
        return p.brush_radius

    def _get_screen_points(self, context):
        if self._screen_points is None:
            obj = self._facade.get_obj()
            self._screen_points = build_screen_points(
                obj.matrix_world, context.region, context.region_data, self._posed_coords,
                hidden_vertices=vertex_hide_flags(obj.data),
            )
        return self._screen_points

    def _get_world_points(self):
        if self._world_points is None:
            obj = self._facade.get_obj()
            self._world_points = build_world_points(
                obj.matrix_world, self._posed_coords, hidden_vertices=vertex_hide_flags(obj.data),
            )
        return self._world_points

    def _world_coords(self):
        if self._world_coords_cache is None:
            m = np.array(self._facade.get_obj().matrix_world, dtype=np.float64)
            co = np.asarray(self._posed_coords, dtype=np.float64).reshape(-1, 3)
            self._world_coords_cache = co @ m[:3, :3].T + m[:3, 3]
        return self._world_coords_cache

    def _rest_coords(self):
        coords = self._ctx.get("_coords_cache")
        if coords is None:
            coords = self._ctx["_coords_cache"] = self._facade.get_vertex_coordinates()
        return coords

    def _get_bind_links(self):
        if not self._layer_bind:
            return None
        if self._bind_links is None:
            self._bind_links = bind_links(self._layer_bind, self._rest_coords())
        leader, graph = self._dab_leader, self._dab_pieces
        if leader is None or graph is None or not self._bind_links:
            return self._bind_links or None
        cached = self._leader_links
        if cached is None or cached[0] != leader:
            piece_of = self._layer_piece_of(graph)[0]
            kept = {}
            for v, partners in self._bind_links.items():
                if piece_of(v) == leader:
                    continue
                others = [u for u in partners if piece_of(u) != leader]
                if others:
                    kept[v] = others
            cached = self._leader_links = (leader, kept)
        return cached[1] or None

    def _get_welds(self):
        try:
            return coincident_welds(self._facade, self._rest_coords()) or None
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush seam welds failed: {exc!r}")
            return None

    def _get_layer_links(self, p):
        if self._layer_links is None:
            scale = self._facade.get_obj().matrix_world.to_scale()
            local_radius = p.brush_radius / max(1e-9, (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0)
            try:
                self._layer_links = layer_links(self._facade, self._rest_coords(), local_radius)
            except Exception as exc:
                self._facade.debug_log("feature_domains", f"weight brush layer links failed: {exc!r}")
                self._layer_links = {}
        return self._layer_links

    def _local_radius(self, p):
        scale = self._facade.get_obj().matrix_world.to_scale()
        return self._radius(p) / max(1e-9, (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0)

    def _get_reach_graph(self, p, with_links):
        if self._reach_failed:
            return None
        try:
            links = (self._get_layer_links(p) or None) if with_links else None
            return build_reach_graph(self._facade, self._rest_coords(), links)
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush reach graph failed: {exc!r}")
            self._reach_failed = True
            return None

    def _on_stroke_piece(self, graph, v):
        piece = graph.piece_of(v)
        if self._stroke_piece is None:
            self._stroke_piece = piece
        return piece == self._stroke_piece

    def _rest_origin(self, face_verts, hit_world):
        matrix = self._facade.get_obj().matrix_world
        rest = self._rest_coords()
        weights = [
            1.0 / max(1e-9, (matrix @ Vector(self._posed_coords[v]) - hit_world).length)
            for v in face_verts
        ]
        total = sum(weights)
        return tuple(
            sum(rest[v][i] * w for v, w in zip(face_verts, weights)) / total for i in range(3)
        )

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

    def _apply_bands(self, mode, bands, p):
        if mode == "sharpen":
            if self._sharpen_target is None:
                self._sharpen_target = self._feature.brush_sharpen_target(self._facade, self._ctx)
            if self._sharpen_target is None:
                return
        changed = {v for _intensity, verts in bands for v in verts}
        keep_volume = _keeps_volume(p)
        follow = (lambda diff: self._follow_layers(changed, diff)) if keep_volume else None
        if mode == "sharpen":
            result = self._feature.apply_target_bands(
                self._facade, self._ctx, self._sharpen_target, bands, in_place=True, follow=follow,
            )
        else:
            links = welds = None
            if mode == "smooth" and keep_volume:
                links = self._get_bind_links()
                welds = self._get_welds()
            result = self._feature.apply_bands(mode, self._facade, self._ctx, bands, in_place=True,
                                               links=links, welds=welds, follow=follow)
        if result.get("status") == "FINISHED":
            self._dabbed = True
            warning = result.get("warning")
            if warning:
                self._stroke_warning = warning
            elif mode == "smooth":
                self._stroke_smoothed = True

    def _radius_px(self, context, p):
        if p.brush_projection == 'SCREEN':
            return screen_mode_radius_px(self._radius(p))
        if self._last_hit_world is None:
            return _SURFACE_RADIUS_PX_FALLBACK
        radius_px = brush_hover.off_mesh_radius_px(context, self._last_hit_world, self._radius(p))
        return _SURFACE_RADIUS_PX_FALLBACK if radius_px is None else radius_px

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

    def _note_path(self, event):
        if self._mode in DAB_SCALE and self._last_sample is not None:
            pressure = self._pressure if get_brush_prefs().use_pressure_strength else 1.0
            self._pending_path.append((event.mouse_region_x, event.mouse_region_y, pressure))

    def _stamp_amounts(self, context, coord, p, mode, rate, pressure):
        radius_px = self._radius_px(context, p)
        spacing = max(_DAB_MIN_SPACING_PX, radius_px * STAMP_SPACING)
        end = (coord[0], coord[1], pressure)
        if self._last_sample is None:
            stamps, used = [end], spacing
            self._stamp_carry = 0.0
        else:
            start = (self._last_sample[0], self._last_sample[1], self._stamp_pressure)
            stamps, self._stamp_carry, used = path_stamps(
                [start, *self._pending_path, end], self._stamp_carry, spacing, _DAB_MAX_SAMPLES,
            )
        self._pending_path = []
        self._stamp_pressure = pressure
        if not stamps:
            return {}
        travel = len(stamps) * used / max(1e-6, radius_px)
        self._follow_travel = None if self._last_sample is None else travel
        radius = self._radius(p)
        shape = FALLOFF_SHAPES[p.falloff]
        acc = {}
        for x, y, stamp_pressure in stamps:
            if abs(x - coord[0]) < 1e-6 and abs(y - coord[1]) < 1e-6:
                cursor_hit = self._cursor_hit
            else:
                cursor_hit = self._hit_at(context, (x, y), p)
            if cursor_hit is None:
                continue
            if cursor_hit[0] in _SURFACE_HIT_KINDS:
                self._last_hit_world = cursor_hit[2]
            gathered = self._gather(context, cursor_hit, p)
            if self._visible_restrict is not None:
                gathered = {v: d for v, d in gathered.items() if v in self._visible_restrict}
            amounts = {}
            for v, d in gathered.items():
                weight = shape(min(1.0, d / radius)) if radius > 1e-9 else 1.0
                a = stamp_amount(mode, rate * weight * stamp_pressure * self._gather_fade, travel)
                if a > 0.0:
                    amounts[v] = a
            add_stamp(mode, acc, amounts)
        return stamp_totals(mode, acc)

    def _hit_at(self, context, coord, p):
        if p.brush_projection == 'SCREEN':
            return ('SCREEN', coord, screen_mode_radius_px(self._radius(p)))
        obj = self._facade.get_obj()
        hit, face_index, hit_world, hit_normal = raycast_at(context, coord, obj, self._bvh)
        if hit:
            return (p.brush_projection, face_index, hit_world, hit_normal)
        return self._air_hit(context, coord, obj, p)

    def _air_hit(self, context, coord, obj, p):
        anchor = brush_hover.off_mesh_cursor(context, coord, obj)
        if anchor is None:
            return None
        radius = self._radius(p)
        radius_px = brush_hover.off_mesh_radius_px(context, anchor[0], radius)
        if radius_px is None:
            return None
        return ('AIR', coord, radius_px, anchor[0], anchor[1], self._nearest_surface(context, coord, obj, radius))

    def _nearest_surface(self, context, coord, obj, radius):
        region, rv3d = context.region, context.region_data
        if region is None or rv3d is None or self._bvh is None:
            return None
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        if origin is None or direction is None:
            return None
        inv = obj.matrix_world.inverted()
        scale = max(1e-9, sum(obj.matrix_world.to_scale()) / 3.0)
        if self._posed_box is None:
            co = np.asarray(self._posed_coords, dtype=np.float64).reshape(-1, 3)
            self._posed_box = (co.min(axis=0), co.max(axis=0))
        pad = radius / scale
        found = nearest_on_ray(self._bvh, inv @ origin, (inv.to_3x3() @ direction).normalized(),
                               self._posed_box[0] - pad, self._posed_box[1] + pad)
        if found is None:
            return None
        location, normal, face_index, gap = found
        fade = 1.0 - gap * scale / max(1e-9, radius)
        if fade <= 0.0 or face_index is None:
            return None
        world_normal = (obj.matrix_world.to_3x3().inverted_safe().transposed() @ normal).normalized()
        return face_index, obj.matrix_world @ location, world_normal, fade

    def _carry_layers(self, gathered, p, distance_of):
        if self._bind_failed:
            return gathered
        try:
            obj = self._facade.get_obj()
            if self._layer_bind is None:
                self._layer_bind = build_layer_bind(self._facade, self._rest_coords(), obj.data)
                self._hidden = vertex_hide_flags(obj.data)
                normals = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
                obj.data.vertices.foreach_get("normal", normals)
                self._rest_normals = normals.reshape(-1, 3)
                self._rest_points = np.asarray(self._rest_coords(), dtype=np.float64).reshape(-1, 3)
            graph = self._get_reach_graph(p, with_links=True)
            self._dab_pieces = graph
            piece_of = home = None
            if graph is not None:
                piece_of, piece_root = self._layer_piece_of(graph)
                if gathered:
                    home = piece_root(self._home_piece(graph, gathered))
            carried = carry_bound_layers(
                self._layer_bind, gathered, self._radius(p), distance_of, self._world_coords(),
                self._hidden, piece_of=piece_of, home_piece=home,
            )
            self._dab_leader = None if home is None else self._inner_leader(piece_of, carried, home)
            return carried
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush layer bind failed: {exc!r}")
            self._bind_failed = True
            return gathered

    def _screen_distance(self, context, center_2d, radius_px, p):
        persp = np.array(context.region_data.perspective_matrix, dtype=np.float64)
        half_w, half_h = context.region.width / 2.0, context.region.height / 2.0
        scale = self._radius(p) / max(1e-9, radius_px)

        def distance_of(point):
            x, y, _z, w = persp @ np.append(point, 1.0)
            if w <= 0.0:
                return math.inf
            return math.hypot(half_w + half_w * x / w - center_2d[0],
                              half_h + half_h * y / w - center_2d[1]) * scale
        return distance_of

    def _follow_layers(self, changed, diff):
        bind, leader, graph = self._layer_bind, self._dab_leader, self._dab_pieces
        if not bind or leader is None or graph is None:            return None
        strength = dict(self._dab_strength)
        travel_share = follow_share(self._follow_travel)

        def progress(v, corners):
            reach = max([strength.get(v, 0.0)] + [strength.get(c, 0.0) for c in corners])
            strength[v] = reach
            return reach * travel_share

        ctx = self._ctx
        piece_of = self._layer_piece_of(graph)[0]
        try:
            if ctx["is_mask"]:
                mask, default = ctx["mask_dict"], ctx["mask_default"]
                base = lambda v: mask.get(v, default)
                rows = lead_rows(bind, changed, lambda v: diff[v] if v in diff else base(v),
                                 piece_of, leader, progress, before=base)
            else:
                layer = ctx["layer_int"]
                base = lambda v: layer.get(v, {})
                rows = lead_rows(bind, changed, lambda v: diff[v] if v in diff else base(v),
                                 piece_of, leader, progress, locks=ctx["locks_id"], before=base)
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush layer follow failed: {exc!r}")
            return None
        return {v: row for v, row in rows.items() if not self._hidden[v]}

    def _keep_seen_islands(self, context, gathered, p):
        volume = p.brush_projection == 'VOLUME'
        links = (self._get_layer_links(p) or None) if volume else None
        islands = brush_islands(gathered, self._facade.get_cached_mesh_neighbors(), links)
        if len(islands) < 2:
            return gathered
        seen = [self._island_seen(context, island) for island in islands]
        best = max(seen)
        if best <= 0.0:
            return gathered
        keep = set().union(*(island for island, share in zip(islands, seen)
                             if share >= _ISLAND_MIN_SEEN * best))
        return {v: d for v, d in gathered.items() if v in keep}

    def _island_seen(self, context, island):
        region, rv3d = context.region, context.region_data
        world = self._world_coords()
        ids = np.fromiter(island, dtype=np.int64, count=len(island))
        ids.sort()
        probes = ids[np.unique(np.linspace(0, len(ids) - 1, _ISLAND_PROBES).astype(np.int64))]
        obj = self._facade.get_obj()
        faces = self._bm.polygons
        seen = tried = 0
        for v in probes:
            coord = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(world[v]))
            if coord is None:
                continue
            tried += 1
            hit, face_index, _hit_world, _normal = raycast_at(context, coord, obj, self._bvh)
            if hit and face_index is not None and 0 <= face_index < len(faces) and any(
                    u in island for u in faces[face_index].vertices):
                seen += 1
        return seen / tried if tried else 0.0

    def _home_piece(self, graph, gathered):
        if self._stroke_piece is not None:
            return self._stroke_piece
        if self._stroke_leader is None:
            self._stroke_leader = graph.piece_of(min(gathered, key=gathered.get))
        return self._stroke_leader

    def _layer_piece_of(self, graph):
        if self._layer_pieces is None or self._layer_pieces[0] is not graph:
            self._layer_pieces = (graph, *weld_joined_pieces(graph.piece_of, self._get_welds(),
                                                             len(self._rest_coords())))
        return self._layer_pieces[1], self._layer_pieces[2]

    def _inner_leader(self, piece_of, carried, home):
        if self._stroke_inner is None:
            inner = inner_piece(self._layer_bind, carried, piece_of, self._rest_points,
                                self._rest_normals, home)
            if inner == home:
                return home
            self._stroke_inner = inner
        return self._stroke_inner

    def _keep_connected(self, gathered, face_index):
        faces = self._bm.polygons
        if face_index is not None and 0 <= face_index < len(faces):
            seeds = faces[face_index].vertices
        else:
            seeds = (min(gathered, key=gathered.get),)
        return keep_connected(gathered, seeds, self._facade.get_cached_mesh_neighbors())

    def _volume_reach(self, gathered, face_index, hit_world, p):
        faces = self._bm.polygons
        if face_index is None or not 0 <= face_index < len(faces):
            return {}
        face_verts = list(faces[face_index].vertices)
        graph = self._get_reach_graph(p, with_links=True)
        if graph is None:
            return self._keep_connected(gathered, face_index)
        if not self._on_stroke_piece(graph, face_verts[0]):
            return {}
        return keep_reachable(
            graph, gathered, face_verts, self._rest_origin(face_verts, hit_world),
            max_reach=REACH_MAX_DETOUR * self._local_radius(p),
        )

    def _surface_reach(self, gathered, face_index, hit_world, p):
        faces = self._bm.polygons
        if face_index is None or not 0 <= face_index < len(faces):
            return {}
        face_verts = list(faces[face_index].vertices)
        graph = self._get_reach_graph(p, with_links=False)
        if graph is None:
            return gathered
        if not self._on_stroke_piece(graph, face_verts[0]):
            return {}
        return keep_reachable(
            graph, gathered, face_verts, self._rest_origin(face_verts, hit_world),
            max_reach=REACH_MAX_DETOUR * self._local_radius(p),
        )

    def _projected_reach(self, context, center_2d, gathered, p, radius_px):
        graph = self._get_reach_graph(p, with_links=False)
        if graph is None:
            return self._projected_connected_fallback(context, center_2d, gathered)
        hits = self._circle_hits(context, center_2d, radius_px, graph,
                                 center_only=self._stroke_piece is not None)
        if self._stroke_piece is not None and not any(h[1] == self._stroke_piece for h in hits):
            hits = self._circle_hits(context, center_2d, radius_px, graph)
        if self._stroke_piece is None and hits:
            self._stroke_piece = self._dominant_piece(hits)
        on_piece = [h for h in hits if self._stroke_piece is None or h[1] == self._stroke_piece]
        if on_piece:
            _off, piece, face_index, hit_world, _depth = min(on_piece, key=lambda h: h[0])
            seeds = list(self._bm.polygons[face_index].vertices)
            self._on_stroke_piece(graph, seeds[0])
            return keep_reachable(
                graph, gathered, seeds, self._rest_origin(seeds, hit_world), piece=self._stroke_piece,
            )
        seed = next(
            (v for v in sorted(gathered, key=gathered.get)
             if self._stroke_piece is None or graph.piece_of(v) == self._stroke_piece),
            None,
        )
        if seed is None:
            return {}
        self._on_stroke_piece(graph, seed)
        return keep_reachable(
            graph, gathered, [seed], self._rest_coords()[seed], piece=self._stroke_piece,
        )

    def _circle_hits(self, context, center_2d, radius_px, graph, center_only=False):
        obj = self._facade.get_obj()
        faces = self._bm.polygons
        origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, center_2d)
        hits = []
        for offset, angle in _CIRCLE_SAMPLES[:1] if center_only else _CIRCLE_SAMPLES:
            coord = (center_2d[0] + offset * radius_px * math.cos(angle),
                     center_2d[1] + offset * radius_px * math.sin(angle))
            hit, face_index, hit_world, _normal = raycast_at(context, coord, obj, self._bvh)
            if not hit or face_index is None or not 0 <= face_index < len(faces):
                continue
            piece = graph.piece_of(faces[face_index].vertices[0])
            hits.append((offset, piece, face_index, hit_world, (hit_world - origin).length))
        return hits

    @staticmethod
    def _dominant_piece(hits):
        nearest = min(h[4] for h in hits)
        score = {}
        for offset, piece, _face, _hit, depth in hits:
            weight = (1.0 - 0.75 * offset * offset) * (nearest / max(depth, 1e-9))
            score[piece] = score.get(piece, 0.0) + weight
        return max(score, key=score.get)

    def _projected_connected_fallback(self, context, center_2d, gathered):
        if self._screen_island is not None:
            return gathered
        hit, face_index, _hit_world, _normal = raycast_at(
            context, center_2d, self._facade.get_obj(), self._bvh,
        )
        return self._keep_connected(gathered, face_index if hit else None)

    def _gather(self, context, cursor_hit, p):
        self._gather_fade = 1.0
        if cursor_hit[0] == 'AIR':
            nearest = cursor_hit[5]
            if nearest is None:
                return {}
            face_index, point, normal, fade = nearest
            gathered = self._gather(context, (p.brush_projection, face_index, point, normal), p)
            self._gather_fade = fade
            return gathered
        if cursor_hit[0] == 'SCREEN':
            _kind, center_2d, radius_px = cursor_hit
            gathered = gather_brush_vertices_screen(
                self._get_screen_points(context), center_2d, radius_px, self._radius(p),
            )
            if p.connected_only and gathered:
                gathered = self._keep_seen_islands(context, gathered, p)
                if gathered:
                    gathered = self._projected_reach(context, center_2d, gathered, p, radius_px)
                if gathered and self._screen_island is not None:
                    gathered = keep_connected_from(
                        gathered, self._screen_island, self._facade.get_cached_mesh_neighbors(),
                    )
                if gathered:
                    self._screen_island = set(gathered)
            if gathered and p.keep_volume:
                gathered = self._carry_layers(
                    gathered, p, self._screen_distance(context, center_2d, radius_px, p),
                )
            return gathered
        kind, face_index, hit_world, _hit_normal = cursor_hit
        if kind == 'VOLUME':
            gathered = gather_brush_vertices_volume(self._get_world_points(), hit_world, self._radius(p))
            if gathered and p.connected_only:
                gathered = self._volume_reach(gathered, face_index, hit_world, p)
                if gathered:
                    gathered = self._keep_seen_islands(context, gathered, p)
            if gathered:
                center = np.asarray(hit_world, dtype=np.float64)
                gathered = self._carry_layers(
                    gathered, p, lambda point: float(np.linalg.norm(point - center)),
                )
            return gathered
        obj = self._facade.get_obj()
        gathered = gather_brush_vertices(
            self._facade, self._bm, face_index, hit_world, obj.matrix_world, self._radius(p),
            self._posed_coords, graph=self._get_graph(),
        )
        if gathered and p.connected_only:
            gathered = self._surface_reach(gathered, face_index, hit_world, p)
        return gathered

    def _update_cursor(self, context, event):
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
            brush_draw.show_screen(center_2d, radius_px, label, _AFFECTED_ONLY_COLOR)
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
                                           color_projection=p.brush_projection)
                self._cursor_hit = air
                return
            brush_hover.remember_hit(obj, hit_world)
            brush_draw.show_surface(hit_world, hit_normal, radius, label, _AFFECTED_ONLY_COLOR)
            self._cursor_hit = (p.brush_projection, face_index, hit_world, hit_normal)

    def _dab(self, context, event):
        p = get_brush_prefs()
        mode = self._mode
        base_intensity = _slider_intensity(mode)
        if event.type != 'TIMER':
            self._record_pressure(event)
        pressure = self._pressure if p.use_pressure_strength else 1.0

        self._update_cursor(context, event)
        coord = (event.mouse_region_x, event.mouse_region_y)
        if mode in DAB_SCALE:
            amounts = self._stamp_amounts(
                context, coord, p, mode, base_intensity * DAB_SCALE[mode], pressure,
            )
            self._last_sample = coord
            bands = quantize_bands(amounts)
            if bands:
                top = max(amounts.values())
                scale = min(1.0, max(0.0, base_intensity)) / top if top > 0.0 else 0.0
                self._dab_strength = {v: a * scale for v, a in amounts.items()} if scale > 0.0 else {}
                self._apply_dab(context, mode, bands, p)
            return

        samples = self._samples(context, coord, p)
        if self._last_sample is None:
            self._follow_travel = None
        else:
            moved = math.hypot(coord[0] - self._last_sample[0], coord[1] - self._last_sample[1])
            self._follow_travel = moved / max(1e-6, self._radius_px(context, p))
        self._last_sample = coord

        dists = {}
        fade = 0.0
        for sample in samples:
            cursor_hit = self._cursor_hit if sample == coord else self._hit_at(context, sample, p)
            if cursor_hit is None:
                continue
            if cursor_hit[0] in _SURFACE_HIT_KINDS:
                self._last_hit_world = cursor_hit[2]
            gathered = self._gather(context, cursor_hit, p)
            if gathered:
                fade = max(fade, self._gather_fade)
            for v_idx, d in gathered.items():
                if d < dists.get(v_idx, math.inf):
                    dists[v_idx] = d
        pressure *= fade

        if self._visible_restrict is not None:
            dists = {v_idx: d for v_idx, d in dists.items() if v_idx in self._visible_restrict}

        if not dists:
            return

        buckets = _bucket_by_falloff(
            dists, self._radius(p),
            shape=FALLOFF_SHAPES[p.falloff],
        )
        bands = stroke_bands(mode, base_intensity, buckets, pressure, self._stroke_strength)
        if bands:
            self._dab_strength = {v: weight for weight, verts in buckets for v in verts}
            self._apply_dab(context, mode, bands, p)

    def _apply_dab(self, context, mode, bands, p):
        context.scene.superskin_internal_transaction = True
        try:
            self._apply_bands(mode, bands, p)
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
            self._dab(context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            self._record_pressure(event)
            self._note_path(event)
            self._update_cursor(context, event)
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
            _last_stroke_point = (
                self._facade.get_obj().name, self._last_hit_world.copy(), time.monotonic())
        if self._stroke_warning and not self._stroke_smoothed:
            self.report({'WARNING'}, self._stroke_warning)
        return {'FINISHED'} if self._dabbed else {'CANCELLED'}

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
