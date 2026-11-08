
from functools import partial

import bpy
import numpy as np
from bpy.app.handlers import persistent

from .. import mesh_flags

_auto_hidden = {}
_is_hidden = {}
_brush_was_active = {}


def _brush_active(context):
    from ..brush_tool.brush_tool import _WEIGHT_BRUSH_TOOL_IDNAME, _get_active_tool_idname
    return _get_active_tool_idname(context) == _WEIGHT_BRUSH_TOOL_IDNAME


def _forms_a_face(mesh, selected):
    loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    return bool(len(starts)) and bool(np.logical_and.reduceat(selected[loop_verts], starts).any())


def isolate(mesh):
    from .select_ops import _set_hidden
    hidden = mesh_flags.vertex_hide(mesh)
    selected = mesh_flags.vertex_select(mesh) & ~hidden
    if not selected.any():
        return False
    if not _forms_a_face(mesh, selected):
        return False
    newly = ~selected & ~hidden
    if not newly.any():
        return False
    previous = _auto_hidden.get(mesh.name)
    if previous is not None and _is_hidden.get(mesh.name) and len(previous) == len(newly):
        newly = newly | previous
    _auto_hidden[mesh.name] = newly
    _is_hidden[mesh.name] = True
    _set_hidden(mesh, hidden | newly)
    return True


def reveal(mesh):
    from .select_ops import _set_hidden
    mask = _auto_hidden.get(mesh.name)
    if mask is None or not _is_hidden.get(mesh.name):
        return False
    _is_hidden[mesh.name] = False
    hidden = mesh_flags.vertex_hide(mesh)
    if len(mask) != len(hidden) or not (hidden & mask).any():
        return False
    _set_hidden(mesh, hidden & ~mask)
    return True


def reveal_all():
    for name in list(_auto_hidden):
        mesh = bpy.data.meshes.get(name)
        try:
            if mesh is not None:
                reveal(mesh)
        except Exception:
            pass
    _auto_hidden.clear()
    _is_hidden.clear()
    _brush_was_active.clear()


def _resync():
    from .select_ops import _set_hidden
    for name, mask in list(_auto_hidden.items()):
        mesh = bpy.data.meshes.get(name)
        if mesh is None:
            continue
        try:
            hidden = mesh_flags.vertex_hide(mesh)
            if len(mask) != len(hidden):
                continue
            wanted = (hidden | mask) if _is_hidden.get(name) else (hidden & ~mask)
            if (wanted != hidden).any():
                _set_hidden(mesh, wanted)
        except Exception:
            continue
    return None


@persistent
def _on_undo_redo(*_args):
    if _auto_hidden:
        bpy.app.timers.register(_resync, first_interval=0.0)


def _apply(obj_name, entering):
    obj = bpy.data.objects.get(obj_name)
    if obj is None or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return None
    try:
        if entering:
            isolate(obj.data)
        else:
            reveal(obj.data)
    except Exception:
        pass
    return None


def sync(context, obj):
    active = _brush_active(context)
    if _brush_was_active.get(obj.name) == active:
        return
    _brush_was_active[obj.name] = active
    bpy.app.timers.register(partial(_apply, obj.name, active), first_interval=0.0)


_HANDLERS = (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post)


def register():
    for handlers in _HANDLERS:
        if _on_undo_redo not in handlers:
            handlers.append(_on_undo_redo)


def unregister():
    for handlers in _HANDLERS:
        while _on_undo_redo in handlers:
            handlers.remove(_on_undo_redo)
