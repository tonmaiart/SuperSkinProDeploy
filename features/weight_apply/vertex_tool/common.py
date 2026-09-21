"""Shared constants and helpers for the vertex tool subpackage."""

import numpy as np

LASSO_TOOL_IDNAME = "superskin.weight_select_tool"
WEIGHT_OPTIONS_PANEL_IDNAME = "SUPERSKIN_PT_weight_apply_options"


def evaluated_local_coords(obj, depsgraph):
    """(N, 3) object-space positions of the deformed mesh; rest positions if the modifier stack
    changes the vertex count."""
    mesh = obj.data
    count = len(mesh.vertices)
    source = obj.evaluated_get(depsgraph).data
    if len(source.vertices) != count:
        source = mesh
    co = np.empty(count * 3, dtype=np.float32)
    source.vertices.foreach_get("co", co)
    return co.reshape(count, 3)
