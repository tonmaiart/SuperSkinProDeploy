
import numpy as np

from ....core.facade import CoreFacade

LASSO_TOOL_IDNAME = "superskin.weight_select_tool"
WEIGHT_OPTIONS_PANEL_IDNAME = "SUPERSKIN_PT_weight_apply_options"


def poll_session_mesh(context):
    obj = context.active_object
    return (CoreFacade.is_editing_weights() and obj is not None and obj.type == 'MESH'
            and obj.mode == 'WEIGHT_PAINT')


def evaluated_local_coords(obj, depsgraph):
    mesh = obj.data
    count = len(mesh.vertices)
    source = obj.evaluated_get(depsgraph).data
    if len(source.vertices) != count:
        source = mesh
    co = np.empty(count * 3, dtype=np.float32)
    source.vertices.foreach_get("co", co)
    return co.reshape(count, 3)
