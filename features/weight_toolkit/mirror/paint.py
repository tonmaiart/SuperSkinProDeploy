
import numpy as np

from .. import mesh_arrays
from . import detect
from .logic import (_centered_on_real_plane, _matches, _raw_target_verts, _symmetrize_center_vertices,
                    classify_sides)

_DIRECTIONS = ('POS_NEG', 'NEG_POS')


class _Channel:
    """One direction's matches: each target with its three source corners and their weights."""

    def __init__(self, matches, num_verts):
        self.targets = matches.targets
        self.corners = matches.corners.reshape(-1, 3)
        self.bary = matches.bary.reshape(-1, 3)


class MirrorPaintMap:
    """Partner lookup across one symmetry axis, for both directions and both channels."""

    def __init__(self, core_facade, axis_idx):
        mesh = core_facade.get_mesh()
        n = len(mesh.vertices)
        self.axis_idx = axis_idx
        self.num_verts = n
        co_flat, self.plane_offset = _centered_on_real_plane(core_facade, mesh_arrays.local_co(mesh), axis_idx)
        self._side_offset = co_flat[axis_idx::3].copy()
        triangles = mesh_arrays.loop_triangles(mesh)
        self.id_pairs = _bone_pairs(core_facade, axis_idx)

        self._weight = {}
        self._weight_target = {}
        self._mask = {}
        self._mask_target = {}
        self._partner_cache = {}
        self.center = set()
        for direction in _DIRECTIONS:
            if self.id_pairs:
                sides, center = classify_sides(core_facade, co_flat, axis_idx, direction)
                self.center |= center
                is_target = np.asarray(sides, dtype=bool)
                self._weight_target[direction] = is_target
                self._weight[direction] = _Channel(
                    _matches(core_facade, co_flat, triangles, np.flatnonzero(is_target), axis_idx), n)
            raw = _raw_target_verts(co_flat, axis_idx, direction)
            is_target = np.zeros(n, dtype=bool)
            is_target[raw] = True
            self._mask_target[direction] = is_target
            self._mask[direction] = _Channel(_matches(core_facade, co_flat, triangles, raw, axis_idx), n)

    def source_positive(self, local_point):
        return float(local_point[self.axis_idx]) - self.plane_offset >= 0.0

    def verts_positive(self, verts):
        ids = np.fromiter(verts, dtype=np.int64, count=len(verts))
        return float(self._side_offset[ids].sum()) >= 0.0

    def on_side(self, verts, positive, is_mask=False):
        ids = np.fromiter(verts, dtype=np.int64, count=len(verts))
        direction = _DIRECTIONS[0] if positive else _DIRECTIONS[1]
        targets = None if is_mask else self._weight_target.get(direction)
        if targets is None:
            targets = self._mask_target[direction]
        return ids[~targets[ids]].tolist()

    def bones_positive(self, core_facade, bone_ids):
        sided = {b for b in bone_ids if self.id_pairs.get(b, b) != b}
        if not sided:
            return None
        _bone_to_id, id_to_bone = core_facade.get_unified_mapping()
        segs = detect.bone_segments(core_facade)
        sides = set()
        for b in sided:
            seg = segs.get(id_to_bone.get(b))
            if seg is not None:
                offset = float(seg[0][self.axis_idx] + seg[1][self.axis_idx]) * 0.5 - self.plane_offset
                if abs(offset) > 1e-6:
                    sides.add(offset > 0.0)
        return sides.pop() if len(sides) == 1 else None

    def partners(self, verts, is_mask=False):
        src_sorted, tgt_sorted, center = self._partner_index(is_mask)
        ids = np.unique(np.fromiter(verts, dtype=np.int64, count=len(verts)))
        ids = ids[(ids >= 0) & (ids < self.num_verts)]
        lo = np.searchsorted(src_sorted, ids, side='left')
        counts = np.searchsorted(src_sorted, ids, side='right') - lo
        starts = np.repeat(lo - (np.cumsum(counts) - counts), counts)
        take = starts + np.arange(int(counts.sum()), dtype=np.int64)
        seam = ids[center[ids]]
        return (np.concatenate((tgt_sorted[take], seam)),
                np.concatenate((np.repeat(ids, counts), seam)))

    def symmetrize_seam(self, rows, verts, locks):
        if verts and self.id_pairs:
            _symmetrize_center_vertices(rows, self.id_pairs, set(verts) & set(rows), locks)

    def _partner_index(self, is_mask):
        key = "mask" if is_mask or not self._weight else "weight"
        cached = self._partner_cache.get(key)
        if cached is not None:
            return cached
        channels = self._mask if key == "mask" else self._weight
        sources, targets = [], []
        for channel in channels.values():
            if len(channel.targets):
                rows = np.arange(len(channel.targets))
                sources.append(channel.corners[rows, np.argmax(channel.bary, axis=1)])
                targets.append(channel.targets)
        src = np.concatenate(sources) if sources else np.empty(0, dtype=np.int64)
        tgt = np.concatenate(targets) if targets else np.empty(0, dtype=np.int64)
        order = np.argsort(src, kind='stable')
        if key == "weight":
            center = np.zeros(self.num_verts, dtype=bool)
            center[list(self.center)] = True
        else:
            center = ~(self._mask_target[_DIRECTIONS[0]] | self._mask_target[_DIRECTIONS[1]])
        cached = self._partner_cache[key] = (src[order].astype(np.int64), tgt[order].astype(np.int64), center)
        return cached

    def mirror_point(self, local_point):
        p = [float(x) for x in local_point]
        p[self.axis_idx] = 2.0 * self.plane_offset - p[self.axis_idx]
        return p


def _bone_pairs(core_facade, axis_idx):
    from ..weight_toolkit_feature import MirrorPreferencesService
    from .logic import build_layer_mirror_plan
    sym = detect.Symmetry(axis_idx, 1.0, "native")
    return build_layer_mirror_plan(
        core_facade, sym, 'POS_NEG', MirrorPreferencesService.get_mirror_search_replace_pairs(),
    ) or {}


def bone_partners(core_facade, axis_idx):
    _bone_to_id, id_to_bone = core_facade.get_unified_mapping()
    return {id_to_bone[a]: id_to_bone[b] for a, b in _bone_pairs(core_facade, axis_idx).items()
            if a != b and a in id_to_bone and b in id_to_bone}


_cache = (None, None)


def _key(core_facade, axis_idx):
    from ..weight_toolkit_feature import MirrorPreferencesService
    mesh = core_facade.get_mesh()
    co = mesh_arrays.local_co(mesh)
    bone_to_id, _id_to_bone = core_facade.get_unified_mapping()
    sr = tuple(tuple(p) for p in MirrorPreferencesService.get_mirror_search_replace_pairs() or ())
    return (
        core_facade.get_obj().name, axis_idx, len(mesh.polygons), len(mesh.edges),
        hash(co.tobytes()), tuple(sorted(bone_to_id.items())), sr,
    )


def mirror_paint_map(core_facade, axis_idx):
    global _cache
    key = _key(core_facade, axis_idx)
    if _cache[0] == key:
        return _cache[1]
    with core_facade.profile_section("mirror_paint.build", size=core_facade.get_num_verts()):
        built = MirrorPaintMap(core_facade, axis_idx)
    _cache = (key, built)
    return built


def mirrored_point(obj, point_world, axis):
    if axis is None:
        return None
    built = _cache[1]
    offset = built.plane_offset if built is not None and _cache[0][:2] == (obj.name, axis) else 0.0
    local = obj.matrix_world.inverted_safe() @ point_world
    local[axis] = 2.0 * offset - local[axis]
    return obj.matrix_world @ local


def clear_cache():
    global _cache
    _cache = (None, None)
