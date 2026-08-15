"""Auto-heal layer & mask storage after mesh topology changes.

Interpolates weights/masks for vertices created by topology edits (loop
cuts, extrude, subdivide, etc.) and prunes entries for deleted vertices.
Delegates pure healing to LayerCompositor.
"""

from ...core_subsystems.layer_compositor import LayerCompositor


def heal_new_vertices_storage(storage, obj):
    if not obj or obj.type != 'MESH' or not storage.has_layer_system():
        return False

    mesh = obj.data
    num_verts = len(mesh.vertices)
    if num_verts == 0:
        return False

    # Only heal when the vertex count has actually changed since the last
    # heal. heal_mask_dict()/heal_layer_dict() cannot tell "vertex is
    # topologically new" apart from "vertex legitimately carries zero/
    # default weight and was therefore never stored" -- both look
    # identical as "index absent from the dict", which is the normal,
    # expected shape of a sparse mask/layer dict (see
    # read_temp_vgs_from_bm()'s `if w <= 0.0: continue`). Without this
    # guard, heal_new_vertices_storage() ran unconditionally on every
    # Enter Edit Mode call (heal_topology_if_needed() has no topology-
    # changed check of its own) and re-densified every sparse mask with
    # neighbour-interpolated values on every single entry -- silently
    # reviving weight on vertices the user had just explicitly scaled to
    # zero and saved. See the mask_trace adhoc debug deck session that
    # diagnosed this: a mask correctly baked to a sparse 3038-vertex dict
    # came back as a dense 14268-vertex dict with a different sum the very
    # next Enter Edit Mode call, with no other write in between.
    last_healed_verts = mesh.get("ss_healed_vert_count")
    if last_healed_verts == num_verts:
        return False

    neighbours = storage.build_mesh_neighbors()
    meta = storage.read_meta_list()
    healed = False

    for layer in meta:
        l_idx = layer["index"]

        raw_layer = storage.read_layer_raw(l_idx)
        if raw_layer:
            layer_dict = LayerCompositor.decode(raw_layer)
            if layer_dict:
                layer_dict, mod = LayerCompositor.heal_layer_dict(layer_dict, neighbours, num_verts)
                if mod:
                    storage.write_layer_dict(l_idx, layer_dict)
                    healed = True

        raw_mask = storage.read_mask_raw(l_idx)
        mask_default = float(layer.get("mask_default", 1.0))
        if raw_mask:
            mask_dict = LayerCompositor.decode(raw_mask)
            if mask_dict:
                mask_dict, mod = LayerCompositor.heal_mask_dict(mask_dict, neighbours, num_verts, mask_default)
                if mod:
                    storage.write_mask_dict(l_idx, mask_dict)
                    healed = True

    mesh["ss_healed_vert_count"] = num_verts
    return healed
