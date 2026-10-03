
import numpy as np

from . import refine_logic, result_cache

MAX_SMOOTH = 2.0
MAX_REPEAT = 50

_DEFAULTS = (0.0, 0, False)
_pending = _DEFAULTS


def set_options(smooth: float, repeat: int, rings: bool) -> None:
    global _pending
    _pending = (
        max(0.0, min(MAX_SMOOTH, float(smooth))), max(0, min(MAX_REPEAT, int(repeat))), bool(rings),
    )


def take_options() -> tuple:
    global _pending
    value, _pending = _pending, _DEFAULTS
    return value


def soften_assignment(core_facade, bone_data, assignment, smooth, repeat=0, cache_key=None, rings=False) -> dict:
    if cache_key is None:
        mesh_graph = refine_logic.build_mesh(core_facade)
    else:
        mesh_graph = result_cache.extra(cache_key, "mesh", lambda: refine_logic.build_mesh(core_facade))
    names = [name for name, _h, _t in bone_data]
    index = {name: i for i, name in enumerate(names)}
    verts = list(assignment.keys())
    labels = [index[assignment[v]] for v in verts]

    rows, bones, weights = mesh_graph.soften(
        np.asarray(verts, dtype=np.int64),
        np.asarray(labels, dtype=np.int64),
        refine_logic.bones_flat(bone_data),
        float(smooth),
        int(repeat),
        bool(rings),
    )
    result = {}
    for r, b, w in zip(rows, bones, weights):
        result.setdefault(verts[r], {})[names[b]] = w
    return result
