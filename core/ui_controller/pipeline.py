

def flatten_to_mesh(ctrl):
    ctrl.storage.flatten_visible_layers_to_mesh(ctrl.obj)


def restore_layer_state(ctrl):
    meta = ctrl.storage.read_meta_list()
    idx = ctrl.active_layer_index

    locks = ctrl._layer_mgr.get_bone_locks(meta, idx)
    for item in ctrl.obj.superskin_bones_collection:
        item.lock_weight = locks.get(item.name, False)


def heal_topology_if_needed(ctrl) -> bool:
    healed = ctrl.storage.heal_new_vertices(ctrl.obj)
    if healed:
        ctrl.storage.flatten_visible_layers_to_mesh(ctrl.obj)
        ctrl.mesh.update()
        ctrl.obj.update_tag()
    return healed
