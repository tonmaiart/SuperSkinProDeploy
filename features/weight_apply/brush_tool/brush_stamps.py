
import math

import numpy as np

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

_FALLOFF_RING_BANDS = 5


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


def band_strengths(dist, radius: float, shape=FALLOFF_SHAPES[_DEFAULT_FALLOFF]):
    if radius <= 1e-9:
        return np.ones(len(dist))
    band = np.minimum(_FALLOFF_RING_BANDS - 1,
                      (np.minimum(1.0, dist / radius) * _FALLOFF_RING_BANDS).astype(np.int64))
    table = np.array([1.0] + [shape((b + 0.5) / _FALLOFF_RING_BANDS)
                              for b in range(1, _FALLOFF_RING_BANDS)])
    return table[band]


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


def _pow(base, exponent):
    return np.power(base, np.full(base.shape, exponent))


def falloff_weights(falloff, frac):
    if falloff == 'CONSTANT':
        return np.ones_like(frac)
    if falloff == 'LINEAR':
        return 1.0 - frac
    if falloff == 'SMOOTH':
        y = 1.0 - frac
        return _pow(y, 3.0) * (10.0 - 15.0 * y + 6.0 * _pow(y, 2.0))
    shape = FALLOFF_SHAPES[falloff]
    return np.fromiter((shape(x) for x in frac.tolist()), dtype=np.float64, count=len(frac))


def stamp_amount(mode, amount, travel):
    out = np.zeros_like(amount)
    if travel <= 0.0:
        return out
    live = amount > 0.0
    if mode == "sharpen":
        full = live & (amount >= 1.0)
        part = live & ~full
        out[full] = 1.0
        out[part] = 1.0 - _pow(1.0 - amount[part], travel)
    else:
        out[live] = amount[live] * math.sqrt(travel)
    return out


def stamp_totals(mode, ids, amounts):
    if not ids:
        return np.empty(0, dtype=np.int64), np.empty(0)
    ids = np.concatenate(ids)
    amounts = np.concatenate(amounts)
    uniq, first, inverse = np.unique(ids, return_index=True, return_inverse=True)
    order = np.argsort(first, kind="stable")
    if mode == "sharpen":
        rest = np.ones(len(uniq))
        np.multiply.at(rest, inverse, 1.0 - np.minimum(1.0, amounts))
        rest = rest[order]
        keep = rest < 1.0
        return uniq[order][keep], 1.0 - rest[keep]
    total = np.bincount(inverse, weights=amounts * amounts, minlength=len(uniq))[order]
    keep = total > 0.0
    return uniq[order][keep], np.sqrt(total[keep])


def quantize_bands(ids, amounts, levels=_STAMP_LEVELS):
    live = amounts > 0.0
    ids, amounts = ids[live], amounts[live]
    if not len(amounts):
        return []
    top = float(amounts.max())
    steps = np.maximum(1, np.round(amounts / top * levels)).astype(np.int64)
    return [(top * int(q) / levels, ids[steps == q].tolist()) for q in np.unique(steps)]


def _keeps_volume(p):
    if p.connected_only:
        return False
    return p.brush_projection == 'VOLUME' or (p.brush_projection == 'SCREEN' and p.keep_volume)


def stroke_bands(mode, base_intensity, ids, weights, pressure, stroke_strength):
    strength = weights * pressure
    old = stroke_strength[ids]
    lifted = strength > old + 1e-6
    ids, old, strength = ids[lifted], old[lifted], strength[lifted]
    if not len(ids):
        return []
    stroke_strength[ids] = strength
    olds, old_at = np.unique(old, return_inverse=True)
    news, new_at = np.unique(strength, return_inverse=True)
    pair = old_at * len(news) + new_at
    order = np.argsort(pair, kind="stable")
    pair, ids = pair[order], ids[order]
    cuts = (np.flatnonzero(np.diff(pair)) + 1).tolist()
    bands = []
    for start, end in zip([0, *cuts], [*cuts, len(ids)]):
        old_at_band, new_at_band = divmod(int(pair[start]), len(news))
        old, new = float(olds[old_at_band]), float(news[new_at_band])
        if mode == "scale":
            before = _falloff_intensity(mode, base_intensity, old)
            after = _falloff_intensity(mode, base_intensity, new)
            intensity = after / before if before > 1e-9 else after
        else:
            intensity = base_intensity * (new - old)
        bands.append((intensity, ids[start:end].tolist()))
    return bands


def local_brush_radius(obj, radius):
    scale = obj.matrix_world.to_scale()
    return radius / max(1e-9, (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0)


def _slider_intensity(mode):
    from ..weight_apply_feature import get_prefs
    p = get_prefs()
    return {
        "add": p.add_val, "scale": p.scale_val,
        "smooth": p.smooth_val, "sharpen": p.sharpen_val,
    }.get(mode, 0.0)
