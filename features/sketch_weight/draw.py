"""Live viewport overlay for the Sketch Weight Guide stroke -- draws the
in-progress polyline while ``MESH_OT_ssp_sketch_guide_draw``'s modal
LEFTMOUSE drag is active (``ops.py`` calls ``show()``/``update()``/``hide()``
around its own invoke/modal lifecycle). No persistent draw handler, same
gesture-scoped convention as ``weight_apply/draw.py``.
"""

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

_draw_handle = None
_points = []

_LINE_COLOR = (1.0, 0.85, 0.1, 0.9)
_LINE_WIDTH = 3.0


def _draw_callback():
    if len(_points) < 2:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(_LINE_WIDTH)
    shader.uniform_float("color", _LINE_COLOR)
    batch_for_shader(shader, 'LINE_STRIP', {"pos": _points}).draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def show():
    """Install the draw handler and reset the stroke buffer -- called from
    ops.py's invoke(), before any drag sample has been captured."""
    global _draw_handle, _points
    _points = []
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_PIXEL')
    _tag_redraw()


def update(screen_points):
    """Refresh the live polyline -- called from ops.py's modal() on every
    accepted MOUSEMOVE sample."""
    global _points
    _points = list(screen_points)
    _tag_redraw()


def hide():
    global _draw_handle, _points
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    _points = []
    _tag_redraw()


def cleanup():
    """Defensive unregister-time cleanup -- guards against an F3 script
    reload landing mid-stroke and leaving a dangling draw handle behind."""
    hide()


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
