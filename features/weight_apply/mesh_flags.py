
import numpy as np


def _read(mesh, name, domain, collection, rna_prop):
    count = len(collection)
    out = np.zeros(count, dtype=np.bool_)
    if not count:
        return out
    attr = mesh.attributes.get(name)
    if attr is None:
        return out
    if attr.data_type == 'BOOLEAN' and attr.domain == domain:
        attr.data.foreach_get("value", out)
    else:
        collection.foreach_get(rna_prop, out)
    return out


def vertex_select(mesh):
    return _read(mesh, ".select_vert", 'POINT', mesh.vertices, "select")


def vertex_hide(mesh):
    return _read(mesh, ".hide_vert", 'POINT', mesh.vertices, "hide")


def edge_hide(mesh):
    return _read(mesh, ".hide_edge", 'EDGE', mesh.edges, "hide")


def polygon_hide(mesh):
    return _read(mesh, ".hide_poly", 'FACE', mesh.polygons, "hide")


def _write(mesh, name, domain, collection, rna_prop, values):
    values = np.asarray(values, dtype=np.bool_)
    attr = mesh.attributes.get(name)
    if attr is None:
        if not values.any():
            return
        attr = mesh.attributes.new(name, 'BOOLEAN', domain)
    if attr.data_type == 'BOOLEAN' and attr.domain == domain:
        attr.data.foreach_set("value", values)
    else:
        collection.foreach_set(rna_prop, values)


def write_vertex_select(mesh, values):
    _write(mesh, ".select_vert", 'POINT', mesh.vertices, "select", values)


def write_vertex_hide(mesh, values):
    _write(mesh, ".hide_vert", 'POINT', mesh.vertices, "hide", values)


def write_edge_hide(mesh, values):
    _write(mesh, ".hide_edge", 'EDGE', mesh.edges, "hide", values)


def write_polygon_hide(mesh, values):
    _write(mesh, ".hide_poly", 'FACE', mesh.polygons, "hide", values)


def clear_selection(mesh):
    _write(mesh, ".select_vert", 'POINT', mesh.vertices, "select", np.zeros(len(mesh.vertices), np.bool_))
    _write(mesh, ".select_edge", 'EDGE', mesh.edges, "select", np.zeros(len(mesh.edges), np.bool_))
    _write(mesh, ".select_poly", 'FACE', mesh.polygons, "select", np.zeros(len(mesh.polygons), np.bool_))
