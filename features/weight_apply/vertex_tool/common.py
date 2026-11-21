
import numpy as np

from ....core.facade import CoreFacade

LASSO_TOOL_IDNAME = "superskin.weight_select_tool"
WEIGHT_OPTIONS_PANEL_IDNAME = "SUPERSKIN_PT_weight_apply_options"

def poll_session_mesh(context):
    return CoreFacade.is_skinning_session(context)


def evaluated_local_coords(obj, depsgraph):
    from ..evaluated_map import displayed
    return displayed(obj, depsgraph).coords


_view_key = None
_view = None
_view_serial = 0


def displayed_view(context, obj):
    global _view_key, _view, _view_serial
    from ..brush_tool.brush_prep import get_shape_gen
    from ..evaluated_map import displayed
    from ..session_display import drawn_generation
    depsgraph = context.evaluated_depsgraph_get()
    mesh = obj.data
    key = (obj.name, mesh.session_uid, len(mesh.vertices), get_shape_gen(),
           depsgraph.scene.frame_current_final, drawn_generation())
    if key == _view_key:
        return _view_serial, _view
    view = displayed(obj, depsgraph)
    if not (_view_key is not None and _view_key[:3] == key[:3] and _same_view(view, _view)):
        view.coords.flags.writeable = False
        _view = view
        _view_serial += 1
    _view_key = key
    return _view_serial, _view


def _same_view(a, b):
    def same(x, y):
        return x is y or (x is not None and y is not None and np.array_equal(x, y))
    return (a.source == b.source and same(a.coords, b.coords) and same(a.first, b.first)
            and same(a.orig_of_eval, b.orig_of_eval))
