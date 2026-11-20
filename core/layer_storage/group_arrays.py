
import bpy
import numpy as np

MIN_VERTS = 50_000

_TEMP_NAME = "__ssp_group_io"
_ATTR_PREFIX = "__ssp_gio_"
_FAILURES = (RuntimeError, AttributeError, TypeError, ValueError, KeyError, ReferenceError)


def use_bulk(mesh) -> bool:
    return len(mesh.vertices) >= MIN_VERTS


def _new_tree(pairs):
    tree = bpy.data.node_groups.new(_TEMP_NAME, 'GeometryNodeTree')
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    geometry = nodes.new("NodeGroupInput").outputs[0]
    for src, dst in pairs:
        read = nodes.new("GeometryNodeInputNamedAttribute")
        read.data_type = 'FLOAT'
        read.inputs["Name"].default_value = src
        store = nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = 'FLOAT'
        store.domain = 'POINT'
        store.inputs["Name"].default_value = dst
        links.new(geometry, store.inputs["Geometry"])
        links.new(read.outputs["Attribute"], store.inputs["Value"])
        geometry = store.outputs["Geometry"]
    links.new(geometry, nodes.new("NodeGroupOutput").inputs[0])
    return tree


def _evaluate(obj, tree, context):
    obj.modifiers.new(_TEMP_NAME, 'NODES').node_group = tree
    context.scene.collection.objects.link(obj)
    depsgraph = context.evaluated_depsgraph_get()
    depsgraph.update()
    return obj.evaluated_get(depsgraph)


def _remove(obj, mesh, tree):
    for collection, block in ((bpy.data.objects, obj), (bpy.data.meshes, mesh),
                              (bpy.data.node_groups, tree)):
        if block is not None:
            try:
                collection.remove(block)
            except _FAILURES:
                pass


def _unused_attribute_names(mesh, count):
    taken = {a.name for a in mesh.attributes}
    names = []
    i = 0
    while len(names) < count:
        name = f"{_ATTR_PREFIX}{i}"
        if name not in taken:
            names.append(name)
        i += 1
    return names


def read_group_weights(obj, names, context=None):
    context = context or bpy.context
    mesh = obj.data
    count = len(mesh.vertices)
    names = list(dict.fromkeys(names))
    outputs = _unused_attribute_names(mesh, len(names))
    copy = probe = tree = None
    try:
        copy = mesh.copy()
        probe = bpy.data.objects.new(_TEMP_NAME, copy)
        tree = _new_tree(zip(names, outputs))
        evaluated = _evaluate(probe, tree, context).data
        if len(evaluated.vertices) != count:
            return None
        result = {}
        for name, output in zip(names, outputs):
            attr = evaluated.attributes.get(output)
            if attr is None or attr.domain != 'POINT' or attr.data_type != 'FLOAT':
                return None
            values = np.empty(count, dtype=np.float32)
            attr.data.foreach_get("value", values)
            result[name] = values
        return result
    except _FAILURES:
        return None
    finally:
        _remove(probe, copy, tree)


def add_members(vg, verts, weights) -> None:
    verts = np.asarray(verts, dtype=np.int64)
    if not len(verts):
        return
    weights = np.asarray(weights, dtype=np.float32)
    order = np.argsort(weights, kind="stable")
    verts = verts[order].tolist()
    weights = weights[order]
    cuts = np.flatnonzero(np.diff(weights)) + 1
    starts = np.concatenate(([0], cuts)).tolist()
    ends = np.concatenate((cuts, [len(weights)])).tolist()
    add = vg.add
    for start, end, w in zip(starts, ends, weights[starts].tolist()):
        add(verts[start:end], w, 'REPLACE')
