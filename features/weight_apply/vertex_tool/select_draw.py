"""Vertex overlay for Edit Layer Weight: selected yellow, and with the Weight Select tool also unselected gray and the hovered vertex red."""

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from .common import evaluated_local_coords
from .pick import is_select_tool_active

_SESSION_MARKER = "__ssp_wp_session"
_SELECTED_COLOR = (1.0, 0.75, 0.0, 1.0)
_UNSELECTED_COLOR = (0.824, 0.486, 1, 1.0)
_HOVER_COLOR = (1.0, 0.1, 0.1, 1.0)
_SELECTED_SIZE = 5.0
_UNSELECTED_SIZE = 3.0
_HOVER_SIZE = 7.0

_LIFT_IN_POINT_SIZES = 1.5

_draw_handle = None
_shader = None
_hover_index = None
_cache_key = None
_cache = None


def _get_shader():
    global _shader
    if _shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        info.push_constant('VEC4', "color")
        info.push_constant('FLOAT', "size")
        info.push_constant('VEC3', "eye")
        info.push_constant('VEC3', "toward")
        info.push_constant('FLOAT', "persp")
        info.push_constant('FLOAT', "pixel_scale")
        info.vertex_in(0, 'VEC3', "pos")
        info.fragment_out(0, 'VEC4', "fragColor")
        info.vertex_source(
            "void main() {"
            "  float s = pixel_scale * size * %.3f;"
            "  vec3 lifted = pos + (eye - pos) * s * persp + toward * s * (1.0 - persp);"
            "  gl_Position = ModelViewProjectionMatrix * vec4(lifted, 1.0);"
            "  gl_PointSize = size;"
            "}" % _LIFT_IN_POINT_SIZES
        )
        info.fragment_source("void main() { fragColor = color; }")
        _shader = gpu.shader.create_from_info(info)
    return _shader


def set_hover(index, context=None):
    global _hover_index
    if index == _hover_index:
        return
    _hover_index = index
    area = getattr(context, "area", None)
    if area is not None:
        area.tag_redraw()


def _build_batches(shader, co, selected, visible):
    world = np.array(bpy.context.active_object.matrix_world, dtype=np.float32)
    co = co @ world[:3, :3].T + world[:3, 3]
    rest = visible & ~selected
    chosen_mask = visible & selected
    unselected = batch_for_shader(shader, 'POINTS', {"pos": co[rest]}) if rest.any() else None
    chosen = batch_for_shader(shader, 'POINTS', {"pos": co[chosen_mask]}) if chosen_mask.any() else None
    return co, unselected, chosen, visible


def _draw_points(shader, batch, color, size):
    shader.uniform_float("color", color)
    shader.uniform_float("size", size)
    batch.draw(shader)


def _draw_callback():
    global _cache_key, _cache
    context = bpy.context
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT' or not obj.get(_SESSION_MARKER):
        return
    mesh = obj.data
    count = len(mesh.vertices)
    if count == 0:
        return

    selected = np.empty(count, dtype=np.bool_)
    mesh.vertices.foreach_get("select", selected)
    hidden = np.empty(count, dtype=np.bool_)
    mesh.vertices.foreach_get("hide", hidden)
    visible = ~hidden
    selected &= visible
    show_all = is_select_tool_active(context)
    if not show_all and not selected.any():
        return

    local = evaluated_local_coords(obj, context.evaluated_depsgraph_get())
    key = (obj.name, count, float(local.sum(dtype=np.float64)), tuple(map(tuple, obj.matrix_world)),
           hash(selected.tobytes()), hash(hidden.tobytes()))
    if key != _cache_key:
        _cache = _build_batches(_get_shader(), local, selected, visible)
        _cache_key = key
    world_co, unselected, chosen, visible = _cache

    shader = _get_shader()
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.program_point_size_set(True)
    gpu.state.blend_set('ALPHA')
    shader.bind()
    rv3d = context.region_data
    view_inv = rv3d.view_matrix.inverted()
    shader.uniform_float("eye", view_inv.translation)
    shader.uniform_float("toward", view_inv.col[2].xyz.normalized())
    shader.uniform_float("persp", 1.0 if rv3d.is_perspective else 0.0)
    shader.uniform_float("pixel_scale", 2.0 / (rv3d.window_matrix[1][1] * context.region.height))
    if show_all and unselected is not None:
        _draw_points(shader, unselected, _UNSELECTED_COLOR, _UNSELECTED_SIZE)
    if chosen is not None:
        _draw_points(shader, chosen, _SELECTED_COLOR, _SELECTED_SIZE)
    if show_all and _hover_index is not None and _hover_index < count and visible[_hover_index]:
        hover = batch_for_shader(shader, 'POINTS', {"pos": world_co[_hover_index:_hover_index + 1]})
        _draw_points(shader, hover, _HOVER_COLOR, _HOVER_SIZE)
    gpu.state.program_point_size_set(False)
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')


def register():
    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _draw_handle, _cache_key, _cache, _hover_index
    _cache_key = _cache = _hover_index = None
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
