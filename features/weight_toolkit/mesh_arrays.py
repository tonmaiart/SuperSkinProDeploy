
import numpy as np

EMPTY_I = np.empty(0, dtype=np.int64)
EMPTY_F = np.empty(0, dtype=np.float64)


def local_co(mesh):
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    return co.astype(np.float64)


def edge_verts(mesh):
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edges)
    return edges.astype(np.int64)


def loop_triangles(mesh):
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    return tris.astype(np.int64)


def weights_coo(layer_dict, key_of=lambda bone: bone):
    slots = {}
    vs, bs, ws = [], [], []
    for v_idx, bone_weights in layer_dict.items():
        for bone, w in bone_weights.items():
            key = key_of(bone)
            if key is None:
                continue
            vs.append(v_idx)
            bs.append(slots.setdefault(key, len(slots)))
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


def surface_sampler(core_facade, co_flat, triangles, allowed_verts=None):
    allowed = None if allowed_verts is None else np.fromiter(allowed_verts, dtype=np.int64)
    return core_facade.get_rust_gateway("surface_logic").call(
        "rust_surface_sampler", co_flat, triangles, allowed,
    )


def coo_rows(rows, bones, weights, names):
    out = {}
    for r, b, w in zip(rows, bones, weights):
        out.setdefault(r, {})[names[b]] = w
    return out
