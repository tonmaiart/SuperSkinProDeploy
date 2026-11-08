
import numpy as np

from .. import mesh_flags
from ..brush_tool.brush_logic import _WELD_EDGE_SHARE, coincident_groups, open_edges

_cache_key = None
_cache = None


def _rest_coords(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def _weld_reps(mesh, co):
    rep = np.arange(len(co), dtype=np.int64)
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    if not len(edges):
        return rep
    median = float(np.median(np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)))
    _pairs, on_rim = open_edges(mesh)
    for v, r in coincident_groups(co, np.flatnonzero(on_rim), median * _WELD_EDGE_SHARE).items():
        rep[v] = r
    return rep


def keep_volume_on():
    from ..weight_apply_feature import keep_volume_enabled
    return keep_volume_enabled()


def weld_reps(mesh):
    global _cache_key, _cache
    if not keep_volume_on() or not len(mesh.vertices):
        return None
    co = _rest_coords(mesh)
    key = (mesh.as_pointer(), len(mesh.vertices), len(mesh.edges), hash(co.tobytes()))
    if key != _cache_key:
        rep = _weld_reps(mesh, co)
        _cache_key, _cache = key, rep if (rep != np.arange(len(rep))).any() else None
    return _cache


def overlap_mask(rep):
    return np.bincount(rep, minlength=len(rep))[rep] > 1


def weld_pairs(rep):
    moved = np.flatnonzero(rep != np.arange(len(rep)))
    return np.stack([moved, rep[moved]], axis=1)


def expand(rep, mask):
    hit = np.zeros(len(rep), dtype=np.bool_)
    hit[rep[mask]] = True
    return hit[rep]


def write_selection(mesh, selected, chosen=None):
    rep = weld_reps(mesh)
    if rep is not None and len(rep) == len(selected):
        before = mesh_flags.vertex_select(mesh)
        on = expand(rep, selected & ~before)
        off = expand(rep, before & ~selected) & ~on
        if chosen is not None:
            touched = expand(rep, chosen)
            picked_on = expand(rep, chosen & selected)
            on = (on & ~touched) | picked_on
            off = (off & ~touched) | (touched & ~picked_on)
        selected = (selected | on) & ~off & ~mesh_flags.vertex_hide(mesh)
    mesh_flags.write_vertex_select(mesh, selected)
    mesh.update()
