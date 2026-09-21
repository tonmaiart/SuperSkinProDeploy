"""Hammer logic -- replaces selected vertices' weights with the average of their neighbours,
propagating inward from the unselected boundary."""


def _build_adjacency(mesh):
    adjacency = {}
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    return adjacency


def _average_weights(neighbors, layer_data):
    """Mean of the neighbours' bone weights; a neighbour with no data counts as zero."""
    totals = {}
    for n in neighbors:
        for bone, w in layer_data.get(n, {}).items():
            totals[bone] = totals.get(bone, 0.0) + w
    inv = 1.0 / len(neighbors)
    return {bone: w * inv for bone, w in totals.items() if w * inv > 1e-6}


def hammer_weights(selected, adjacency, layer_data) -> dict:
    """Return {vertex: bone_weights} for every reachable selected vertex.

    Each round resolves the selected vertices that touch an already-resolved vertex, so
    weights flow from unselected neighbours toward the middle of the region.
    """
    resolved = {}
    remaining = set(selected)

    while remaining:
        known = {**layer_data, **resolved}
        round_result = {}
        for v in remaining:
            neighbors = [
                n for n in adjacency.get(v, ())
                if n not in selected or n in resolved
            ]
            if neighbors:
                round_result[v] = _average_weights(neighbors, known)
        if not round_result:
            break
        resolved.update(round_result)
        remaining.difference_update(round_result)

    return resolved


_pending_blend = 1.0


def set_blend(value: float) -> None:
    """Blend factor read by the next hammer() call; the dispatch path carries no parameters."""
    global _pending_blend
    _pending_blend = max(0.0, min(1.0, float(value)))


def _lerp_weights(old, new, t):
    out = {}
    for bone in old.keys() | new.keys():
        w = old.get(bone, 0.0) * (1.0 - t) + new.get(bone, 0.0) * t
        if w > 1e-6:
            out[bone] = w
    return out


def hammer(core_facade) -> int:
    mesh = core_facade.get_mesh()
    selected = set(core_facade.get_selected_verts())
    if not selected:
        raise ValueError("No vertices selected -- select some before clicking Hammer")

    adjacency = _build_adjacency(mesh)

    with core_facade.mutate_active_layer() as layer_data:
        result = hammer_weights(selected, adjacency, layer_data)
        if not result:
            raise ValueError("Selected vertices have no unselected neighbours to hammer from")
        t = _pending_blend
        for v_idx, bone_weights in result.items():
            if t < 1.0:
                bone_weights = _lerp_weights(layer_data.get(v_idx, {}), bone_weights, t)
            if bone_weights:
                layer_data[v_idx] = bone_weights
            else:
                layer_data.pop(v_idx, None)

    return len(result)
