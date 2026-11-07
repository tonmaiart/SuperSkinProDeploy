
import numpy as np

SESSION_SELECT = ".ssp_session_select_vert"
USER_HIDE_MARKER = ".ssp_user_hide_vert"
_USER_PREFIX = ".ssp_user_"
_SESSION_PREFIX = ".ssp_session_"


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


def _read_stored(mesh, name, domain, count):
    out = np.zeros(count, dtype=np.bool_)
    attr = mesh.attributes.get(name)
    if count and attr is not None and attr.data_type == 'BOOLEAN' and attr.domain == domain:
        attr.data.foreach_get("value", out)
    return out


def _write_stored(mesh, name, domain, values):
    attr = mesh.attributes.get(name)
    if attr is not None and (attr.data_type != 'BOOLEAN' or attr.domain != domain):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(name, 'BOOLEAN', domain)
    attr.data.foreach_set("value", np.asarray(values, dtype=np.bool_))


def session_selection_live(mesh):
    return mesh.attributes.get(USER_HIDE_MARKER) is not None


def vertex_select(mesh):
    if session_selection_live(mesh):
        return _read_stored(mesh, SESSION_SELECT, 'POINT', len(mesh.vertices))
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
    if session_selection_live(mesh):
        _write_stored(mesh, SESSION_SELECT, 'POINT', values)
        return
    values = np.asarray(values, dtype=np.bool_)
    _write(mesh, ".select_vert", 'POINT', mesh.vertices, "select", values)
    edge_verts = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edge_verts)
    _write(mesh, ".select_edge", 'EDGE', mesh.edges, "select", values[edge_verts.reshape(-1, 2)].all(axis=1))
    if len(mesh.polygons):
        loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_verts)
        starts = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("loop_start", starts)
        _write(mesh, ".select_poly", 'FACE', mesh.polygons, "select",
               np.logical_and.reduceat(values[loop_verts], starts))


def write_vertex_hide(mesh, values):
    _write(mesh, ".hide_vert", 'POINT', mesh.vertices, "hide", values)


def write_edge_hide(mesh, values):
    _write(mesh, ".hide_edge", 'EDGE', mesh.edges, "hide", values)


def write_polygon_hide(mesh, values):
    _write(mesh, ".hide_poly", 'FACE', mesh.polygons, "hide", values)


def clear_selection(mesh):
    if session_selection_live(mesh):
        _write_stored(mesh, SESSION_SELECT, 'POINT', np.zeros(len(mesh.vertices), np.bool_))
        return
    _write(mesh, ".select_vert", 'POINT', mesh.vertices, "select", np.zeros(len(mesh.vertices), np.bool_))
    _write(mesh, ".select_edge", 'EDGE', mesh.edges, "select", np.zeros(len(mesh.edges), np.bool_))
    _write(mesh, ".select_poly", 'FACE', mesh.polygons, "select", np.zeros(len(mesh.polygons), np.bool_))


_HIDE_LAYERS = (
    ("hide_vert", 'POINT', "vertices", "hide"),
    ("hide_edge", 'EDGE', "edges", "hide"),
    ("hide_poly", 'FACE', "polygons", "hide"),
)
_SELECT_LAYERS = (
    ("select_vert", 'POINT', "vertices", "select"),
    ("select_edge", 'EDGE', "edges", "select"),
    ("select_poly", 'FACE', "polygons", "select"),
)


def _stash(mesh, prefix, layers):
    for name, domain, coll, prop in layers:
        _write_stored(mesh, prefix + name, domain, _read(mesh, "." + name, domain, getattr(mesh, coll), prop))


def _load(mesh, prefix, layers):
    for name, domain, coll, prop in layers:
        collection = getattr(mesh, coll)
        values = _read_stored(mesh, prefix + name, domain, len(collection))
        _write(mesh, "." + name, domain, collection, prop, values)


def _drop(mesh, prefix, layers):
    for name, *_ in layers:
        attr = mesh.attributes.get(prefix + name)
        if attr is not None:
            mesh.attributes.remove(attr)


def has_session_flags(mesh):
    return mesh.attributes.get(SESSION_SELECT) is not None


def swap_in_session_flags(mesh):
    if session_selection_live(mesh):
        return
    _stash(mesh, _USER_PREFIX, _SELECT_LAYERS + _HIDE_LAYERS)
    _load(mesh, _SESSION_PREFIX, _HIDE_LAYERS)
    for name, domain, coll, prop in _SELECT_LAYERS:
        collection = getattr(mesh, coll)
        _write(mesh, "." + name, domain, collection, prop, np.zeros(len(collection), np.bool_))
    mesh.update()


def swap_out_session_flags(mesh):
    if not session_selection_live(mesh):
        return
    _stash(mesh, _SESSION_PREFIX, _HIDE_LAYERS)
    _load(mesh, _USER_PREFIX, _SELECT_LAYERS + _HIDE_LAYERS)
    _drop(mesh, _USER_PREFIX, _SELECT_LAYERS + _HIDE_LAYERS)
    mesh.update()
