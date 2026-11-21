
from typing import NamedTuple

import numpy as np

from ....core.facade import CoreFacade
from .. import mesh_arrays
from . import side_keywords

AXES = "XYZ"

_BONE_MATCH_FRAC = 0.25
_MESH_SAMPLE = 4000
_MESH_MIN_RATIO = 0.3


class Symmetry(NamedTuple):
    axis_idx: int
    left_sign: float
    method: str

    @property
    def axis(self):
        return AXES[self.axis_idx]

    def direction(self, source):
        sign = self.left_sign if source == 'LEFT' else -self.left_sign
        return 'POS_NEG' if sign > 0 else 'NEG_POS'


def _armature(obj):
    return next((m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None)


def bone_segments(core_facade):
    obj = core_facade.get_obj()
    arm = _armature(obj)
    if arm is None:
        return {}
    to_local = obj.matrix_world.inverted_safe() @ arm.matrix_world
    managed = CoreFacade.managed_vg_names(obj)
    segs = {}
    for name in managed:
        bone = arm.data.bones.get(name)
        if bone is not None:
            segs[name] = (np.array(to_local @ bone.head_local), np.array(to_local @ bone.tail_local))
    return segs


def _plane_tolerance(segs):
    if not segs:
        return 1e-5
    extent = max(float(np.abs(np.concatenate(pair)).max()) for pair in segs.values())
    return max(extent * 1e-3, 1e-5)


def _centers(segs):
    return {n: (h + t) * 0.5 for n, (h, t) in segs.items()}


def _detect_from_names(segs):
    names = set(segs)
    centers = _centers(segs)
    votes = {}
    for name in segs:
        hit = side_keywords.find_counterpart(name, names)
        if hit is None or hit[1] != side_keywords.LEFT:
            continue
        delta = centers[name] - centers[hit[0]]
        axis = int(np.argmax(np.abs(delta)))
        if abs(delta[axis]) < 1e-9:
            continue
        key = (axis, 1.0 if delta[axis] > 0 else -1.0)
        votes[key] = votes.get(key, 0) + 1
    if not votes:
        return None
    (axis, sign), _count = max(votes.items(), key=lambda kv: kv[1])
    return Symmetry(axis, sign, "names")


def _mirror_scores(heads, tails, axis):
    fh, ft = heads.copy(), tails.copy()
    fh[:, axis] *= -1.0
    ft[:, axis] *= -1.0
    return (np.linalg.norm(fh[:, None, :] - heads[None, :, :], axis=2)
            + np.linalg.norm(ft[:, None, :] - tails[None, :, :], axis=2))


def _detect_from_bones(segs):
    if len(segs) < 2:
        return None
    heads = np.array([h for h, _t in segs.values()])
    tails = np.array([t for _h, t in segs.values()])
    lengths = np.linalg.norm(tails - heads, axis=1)
    plane_tol = _plane_tolerance(segs)
    centers = (heads + tails) * 0.5
    best = None
    for axis in range(3):
        off_plane = np.abs(centers[:, axis]) > plane_tol
        if not off_plane.any():
            continue
        scores = _mirror_scores(heads, tails, axis)
        np.fill_diagonal(scores, np.inf)
        matched = int((off_plane & (scores.min(axis=1) < lengths * _BONE_MATCH_FRAC + plane_tol)).sum())
        if matched and (best is None or matched > best[1]):
            best = (axis, matched)
    return Symmetry(best[0], 1.0, "bones") if best else None


def _detect_from_mesh(core_facade):
    co = mesh_arrays.local_co(core_facade.get_mesh()).reshape(-1, 3)
    if len(co) < 2:
        return None
    diag = float(np.linalg.norm(co.max(axis=0) - co.min(axis=0)))
    if diag <= 0.0:
        return None
    tol = diag * 2e-3
    keys = set(map(tuple, np.round(co / tol).astype(np.int64).tolist()))
    sample = co[np.linspace(0, len(co) - 1, min(len(co), _MESH_SAMPLE)).astype(np.int64)]
    best = None
    for axis in range(3):
        pts = sample[np.abs(sample[:, axis]) > tol].copy()
        if not len(pts):
            continue
        pts[:, axis] *= -1.0
        hits = sum(1 for k in map(tuple, np.round(pts / tol).astype(np.int64).tolist()) if k in keys)
        ratio = hits / len(pts)
        if ratio >= _MESH_MIN_RATIO and (best is None or ratio > best[1]):
            best = (axis, ratio)
    return Symmetry(best[0], 1.0, "mesh") if best else None


def detect_symmetry(core_facade):
    segs = bone_segments(core_facade)
    sym = _detect_from_names(segs) or _detect_from_bones(segs) or _detect_from_mesh(core_facade)
    return sym or Symmetry(0, 1.0, "default")


def _generate_rule_pairs(names, centers, sr_list, axis, direction):
    result = CoreFacade.get_rust_gateway("mirror_generate_pairs").call(
        "rust_mirror_generate_pairs",
        names, {n: tuple(float(x) for x in c) for n, c in centers.items()},
        sr_list, axis, direction,
    )
    return {str(k): str(v) for k, v in result.items()}


def build_name_pairs(core_facade, sym, direction, sr_list):
    segs = bone_segments(core_facade)
    stats = {"rules": 0, "keywords": 0, "position": 0}
    if not segs:
        return {}, stats

    axis_idx = sym.axis_idx
    centers = _centers(segs)
    names = sorted(segs)
    src_sign = 1.0 if direction == 'POS_NEG' else -1.0
    pairs = _generate_rule_pairs(names, centers, sr_list, sym.axis, direction) if sr_list else {}
    if not pairs:
        pairs = {n: n for n in names if centers[n][axis_idx] * src_sign >= -1e-5}
    stats["rules"] = sum(1 for s, t in pairs.items() if s != t)

    plane_tol = _plane_tolerance(segs)
    name_set = set(names)
    taken = {t for s, t in pairs.items() if s != t}

    def _target_side(name):
        return centers[name][axis_idx] * src_sign < plane_tol

    for src in [s for s, t in pairs.items() if s == t]:
        for candidate, _side in side_keywords.side_swaps(src):
            if candidate in name_set and candidate not in taken and _target_side(candidate):
                pairs[src] = candidate
                taken.add(candidate)
                stats["keywords"] += 1
                break

    sources = [s for s, t in pairs.items() if s == t and abs(centers[s][axis_idx]) > plane_tol]
    targets = [n for n in names
               if n not in taken and n not in pairs and centers[n][axis_idx] * src_sign < -plane_tol]
    if sources and targets:
        s_heads = np.array([segs[n][0] for n in sources])
        s_tails = np.array([segs[n][1] for n in sources])
        t_heads = np.array([segs[n][0] for n in targets])
        t_tails = np.array([segs[n][1] for n in targets])
        s_heads[:, axis_idx] *= -1.0
        s_tails[:, axis_idx] *= -1.0
        scores = (np.linalg.norm(s_heads[:, None, :] - t_heads[None, :, :], axis=2)
                  + np.linalg.norm(s_tails[:, None, :] - t_tails[None, :, :], axis=2))
        limits = np.linalg.norm(s_tails - s_heads, axis=1) * _BONE_MATCH_FRAC + plane_tol
        used_s, used_t = set(), set()
        for flat in np.argsort(scores, axis=None):
            i, j = divmod(int(flat), len(targets))
            if scores[i, j] >= limits[i]:
                continue
            if i in used_s or j in used_t:
                continue
            used_s.add(i)
            used_t.add(j)
            pairs[sources[i]] = targets[j]
            stats["position"] += 1
    return pairs, stats
