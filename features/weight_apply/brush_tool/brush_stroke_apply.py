
import math

import numpy as np

from .brush_logic import screen_mode_radius_px
from . import brush_hover
from .brush_stamps import (
    DAB_SCALE, STAMP_SPACING, _DAB_MAX_SAMPLES, _DAB_MIN_SPACING_PX, _DAB_SPACING,
    _SURFACE_HIT_KINDS, _SURFACE_RADIUS_PX_FALLBACK, _keeps_volume, falloff_weights, path_stamps,
    stamp_amount, stamp_totals,
)
from .brush_prefs import get_brush_prefs


class StrokeApply:
    """Methods of the Weight Brush operator; they read the stroke state its invoke sets."""

    def _apply_bands(self, mode, bands, p):
        bands = self._mirror_bands(bands)
        if not bands:
            return
        if mode == "sharpen":
            if self._sharpen_target is None:
                self._sharpen_target = self._feature.brush_sharpen_target(self._facade, self._ctx)
            if self._sharpen_target is None:
                return
        changed = {v for _intensity, verts in bands for v in verts}
        keep_volume = _keeps_volume(p)
        follow = (lambda diff, mirror: self._follow_layers(changed, diff, mirror)) if keep_volume else None
        if follow is None and p.connected_only:
            follow = lambda _diff, mirror: self._pin_seam(changed | set(mirror or ()))
        links = None
        shape = None
        if mode == "sharpen":
            if keep_volume:
                welds = self._get_welds()
            else:
                welds = self._dab_welds(changed) if p.connected_only else None
        else:
            welds = None
            if mode == "smooth" and keep_volume:
                links = self._get_bind_links()
                welds = self._get_welds()
                shape = (self._layer_bind, welds)
                follow = None
            elif mode == "smooth" and p.connected_only:
                welds = self._dab_welds(changed)
        if mode == "sharpen":
            result = self._feature.apply_target_bands(
                self._facade, self._ctx, self._sharpen_target, bands, in_place=True, follow=follow,
                welds=welds,
            )
        else:
            result = self._feature.apply_bands(mode, self._facade, self._ctx, bands, in_place=True,
                                               links=links, welds=welds, follow=follow, keep_shape=shape)
        if result.get("status") == "FINISHED":
            self._dabbed = True
            warning = result.get("warning")
            if warning:
                self._stroke_warning = warning
            elif mode == "smooth":
                self._stroke_smoothed = True

    def _mirror_map(self):
        if self._mirror is None:
            from ..mirror_apply import active_map
            self._mirror = active_map(self._facade) or False
        return self._mirror or None

    def _mirror_bands(self, bands):
        mmap = self._mirror_map()
        if mmap is None:
            return bands
        if self._mirror_side is None:
            verts = [v for _amount, vs in bands for v in vs]
            if not verts:
                return bands
            self._mirror_side = mmap.verts_positive(verts)
        is_mask = self._ctx["is_mask"]
        kept = [(amount, mmap.on_side(vs, self._mirror_side, is_mask)) for amount, vs in bands]
        return [(amount, vs) for amount, vs in kept if vs]

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
            return stamp_totals(mode, [], [])
        travel = len(stamps) * used / max(1e-6, radius_px)
        self._follow_travel = None if self._last_sample is None else travel
        radius = self._radius(p)
        falloff = p.falloff
        hits = []
        for x, y, _stamp_pressure in stamps:
            if abs(x - coord[0]) < 1e-6 and abs(y - coord[1]) < 1e-6:
                cursor_hit = self._cursor_hit
            else:
                cursor_hit = self._hit_at(context, (x, y), p)
            if cursor_hit is not None and cursor_hit[0] in _SURFACE_HIT_KINDS:
                self._last_hit_world = cursor_hit[2]
            hits.append(cursor_hit)
        with self._facade.profile_section("weight_brush.gather"):
            walks = self._surface_walks(hits, p) if self._batched_surface(p) else [None] * len(hits)
        stamped_ids, stamped_amounts = [], []
        for (_x, _y, stamp_pressure), cursor_hit, walked in zip(stamps, hits, walks):
            if cursor_hit is None:
                continue
            with self._facade.profile_section("weight_brush.gather"):
                ids, dist = self._gathered_arrays(context, cursor_hit, p, walked)
            if not len(ids):
                continue
            if radius > 1e-9:
                weight = falloff_weights(falloff, np.minimum(1.0, dist / radius))
            else:
                weight = np.ones(len(dist))
            a = stamp_amount(mode, rate * weight * stamp_pressure * self._gather_fade, travel)
            live = a > 0.0
            stamped_ids.append(ids[live])
            stamped_amounts.append(a[live])
        return stamp_totals(mode, stamped_ids, stamped_amounts)
