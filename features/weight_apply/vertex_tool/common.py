
import numpy as np

from ....core.facade import CoreFacade

LASSO_TOOL_IDNAME = "superskin.weight_select_tool"
WEIGHT_OPTIONS_PANEL_IDNAME = "SUPERSKIN_PT_weight_apply_options"

NATIVE_KEY_BLOCKS = tuple(
    ("superskin.block_native_key", {"type": key, "value": 'PRESS', **mods}, None)
    for key, mods in (
        ('X', {"ctrl": True}),
        ('X', {"shift": True}),
        ('X', {"shift": True, "ctrl": True}),
        ('A', {"shift": True}),
        ('A', {"shift": True, "alt": True}),
        ('F', {"ctrl": True}),
        ('F', {"shift": True}),
        ('LEFT_BRACKET', {}),
        ('RIGHT_BRACKET', {}),
        ('V', {}),
        ('M', {}),
    )
)


def poll_session_mesh(context):
    return CoreFacade.is_skinning_session(context)


def evaluated_local_coords(obj, depsgraph):
    from ..brush_tool.brush_logic import _coarse_vertices_lead_evaluated_mesh
    mesh = obj.data
    count = len(mesh.vertices)
    source = obj.evaluated_get(depsgraph).data
    evaluated = len(source.vertices)
    if evaluated != count and not (evaluated > count and _coarse_vertices_lead_evaluated_mesh(obj)):
        source = mesh
    co = np.empty(evaluated * 3 if source is not mesh else count * 3, dtype=np.float32)
    source.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)[:count]
