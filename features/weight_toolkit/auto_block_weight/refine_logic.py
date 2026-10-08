
import numpy as np


def bones_flat(bone_data):
    return np.array([c for _n, h, t in bone_data for c in (*h, *t)], dtype=np.float64)


def _read(collection, attr, count, dtype):
    out = np.empty(count, dtype=dtype)
    collection.foreach_get(attr, out)
    return out


def build_mesh(core_facade):
    mesh = core_facade.get_mesh()
    obj = core_facade.get_obj()
    n_verts = len(mesh.vertices)
    n_loops = len(mesh.loops)
    n_polys = len(mesh.polygons)
    mesh.calc_loop_triangles()
    n_tris = len(mesh.loop_triangles)
    return core_facade.get_rust_gateway("auto_block").call(
        "rust_auto_block_mesh",
        _read(mesh.vertices, "co", n_verts * 3, np.float32).astype(np.float64),
        _read(mesh.vertex_normals, "vector", n_verts * 3, np.float32).astype(np.float64),
        [float(x) for row in obj.matrix_world for x in row],
        _read(mesh.loops, "vertex_index", n_loops, np.int32).astype(np.int64),
        _read(mesh.loops, "edge_index", n_loops, np.int32).astype(np.int64),
        _read(mesh.polygons, "loop_start", n_polys, np.int32).astype(np.int64),
        _read(mesh.polygons, "loop_total", n_polys, np.int32).astype(np.int64),
        _read(mesh.edges, "vertices", len(mesh.edges) * 2, np.int32).astype(np.int64),
        _read(mesh.loop_triangles, "vertices", n_tris * 3, np.int32).astype(np.int64),
        _read(mesh.loop_triangles, "polygon_index", n_tris, np.int32).astype(np.int64),
    )


def assign_bones(mesh_graph, bone_data, selected_verts, rings=False, point=False) -> dict:
    if not selected_verts:
        return {}
    names = [name for name, _h, _t in bone_data]
    picks = mesh_graph.assign(np.asarray(selected_verts, dtype=np.int64), bones_flat(bone_data), bool(rings), bool(point))
    return {v: names[b] for v, b in zip(selected_verts, picks) if b >= 0}
