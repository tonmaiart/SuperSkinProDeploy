
import numpy as np

from ..weight_apply.public_api import vertex_positions


def local_co(mesh):
    return vertex_positions(mesh).reshape(-1).astype(np.float64)


def edge_verts(mesh):
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edges)
    return edges.astype(np.int64)


def loop_triangles(mesh):
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    return tris.astype(np.int64)


def polygons(mesh):
    loop_vert = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vert)
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    totals = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", totals)
    return loop_vert.astype(np.int64), starts.astype(np.int64), totals.astype(np.int64)


def weights_coo(layer_dict, key_of=lambda bone: bone):
    slots = {}
    slot_of = {}
    vs, bs, ws = [], [], []
    for v_idx, bone_weights in layer_dict.items():
        for bone, w in bone_weights.items():
            slot = slot_of.get(bone)
            if slot is None:
                key = key_of(bone)
                slot = slot_of[bone] = -1 if key is None else slots.setdefault(key, len(slots))
            if slot < 0:
                continue
            vs.append(v_idx)
            bs.append(slot)
            ws.append(w)
    return (
        list(slots),
        np.asarray(vs, dtype=np.int64),
        np.asarray(bs, dtype=np.int64),
        np.asarray(ws, dtype=np.float64),
    )


def mask_arrays(mask_dict):
    n = len(mask_dict)
    return (
        np.fromiter(mask_dict.keys(), dtype=np.int64, count=n),
        np.fromiter((float(x) for x in mask_dict.values()), dtype=np.float64, count=n),
    )


def surface_sampler(core_facade, co_flat, triangles):
    return core_facade.get_rust_gateway("surface_logic").call(
        "rust_surface_sampler", co_flat, triangles, None,
    )


def coo_rows(rows, bones, weights, names):
    out = {}
    for r, b, w in zip(rows, bones, weights):
        out.setdefault(r, {})[names[b]] = w
    return out
