
import array
from contextlib import contextmanager

import bmesh


@contextmanager
def read_deform(mesh):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        yield bm, bm.verts.layers.deform.active
    finally:
        bm.free()


def iter_group_weights(mesh, vert_indices=None):
    if vert_indices is None:
        with read_deform(mesh) as (bm, deform):
            if deform is None:
                return
            for bv in bm.verts:
                yield bv.index, bv[deform].items()
        return
    vertices = mesh.vertices
    for v_idx in sorted(vert_indices):
        yield v_idx, [(g.group, g.weight) for g in vertices[v_idx].groups]


_SELECT_VERT = ".select_vert"


def _vertex_selection(mesh):
    selection = array.array('b', bytes(len(mesh.vertices)))
    attr = mesh.attributes.get(_SELECT_VERT)
    if attr is None:
        return selection
    if attr.data_type == 'BOOLEAN' and attr.domain == 'POINT':
        attr.data.foreach_get("value", selection)
    else:
        mesh.vertices.foreach_get("select", selection)
    return selection


def _restore_vertex_selection(mesh, selection):
    attr = mesh.attributes.get(_SELECT_VERT)
    if attr is None:
        if not any(selection):
            return
        attr = mesh.attributes.new(_SELECT_VERT, 'BOOLEAN', 'POINT')
    if attr.data_type == 'BOOLEAN' and attr.domain == 'POINT':
        attr.data.foreach_set("value", selection)
    else:
        mesh.vertices.foreach_set("select", selection)


@contextmanager
def write_deform(mesh):
    selection = _vertex_selection(mesh)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        deform = bm.verts.layers.deform.verify()
        bm.verts.ensure_lookup_table()
        yield bm, deform
        bm.to_mesh(mesh)
    finally:
        bm.free()
    _restore_vertex_selection(mesh, selection)
