
import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

_pixel_handle = None
_obj_name = ""
_symmetry = None
_preview = None

_SOURCE_STYLE = ((1.00, 1.00, 1.00), 0.08, 0.35, 'ALPHA')
_TARGET_STYLE = ((0.15, 0.68, 1.00), 0.45, 1.0, 'ADDITIVE')
_DIVIDER_COLOR = (1.0, 1.0, 1.0, 0.9)

_HALF_WIDTH = 150.0


def current():
    return (_obj_name, _symmetry) if _pixel_handle is not None else None


def tag_redraw():
    wm = bpy.context.window_manager
    for window in getattr(wm, "windows", ()):
        if window.screen is None:
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def preview(direction):
    global _preview
    if _pixel_handle is None or direction == _preview:
        return
    _preview = direction
    tag_redraw()


def _world_axis(obj, idx):
    direction = obj.matrix_world.to_3x3() @ Vector([1.0 if i == idx else 0.0 for i in range(3)])
    return direction.normalized() if direction.length > 1e-9 else None


def _closest_on_line(direction, region, rv3d):
    center = Vector((region.width / 2.0, region.height / 2.0))
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, center)
    ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, center)
    d = direction.dot(ray_dir)
    denom = 1.0 - d * d
    if denom < 1e-9:
        return direction.dot(ray_origin)
    w = -ray_origin
    return (d * ray_dir.dot(w) - direction.dot(w)) / denom


def _side_styles():
    if _preview == 'RIGHT_TO_LEFT':
        return _TARGET_STYLE, _SOURCE_STYLE
    return _SOURCE_STYLE, _TARGET_STYLE


def _draw():
    obj = bpy.data.objects.get(_obj_name)
    context = bpy.context
    region, rv3d = context.region, context.region_data
    if _symmetry is None or obj is None or region is None or rv3d is None:
        return
    scale = context.preferences.system.ui_scale
    left_world = _world_axis(obj, _symmetry.axis_idx)
    if left_world is None:
        return
    left_x = left_world.x * _symmetry.left_sign
    side_dir = Vector((-1.0 if left_x < 0.0 else 1.0, 0.0, 0.0))
    up_dir = Vector((0.0, 0.0, 1.0))

    def project(point):
        return view3d_utils.location_3d_to_region_2d(region, rv3d, point)

    t = _closest_on_line(up_dir, region, rv3d)
    probe = max(rv3d.view_distance, 1e-3) * 0.05
    view_right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
    p_mid, p_right = project(up_dir * t), project(up_dir * t + view_right * probe)
    if p_mid is None or p_right is None or (p_right - p_mid).length < 1e-6:
        return
    px_per_unit = (p_right - p_mid).length / probe
    half = _HALF_WIDTH * scale / px_per_unit
    bottom_w, top_w = up_dir * (t - half), up_dir * (t + half)
    left_w = side_dir * half

    corners = [project(p) for p in (bottom_w, top_w, bottom_w + left_w, top_w + left_w,
                                    bottom_w - left_w, top_w - left_w)]
    if any(c is None for c in corners):
        return
    bottom, top, bottom_l, top_l, bottom_r, top_r = corners
    left_style, right_style = _side_styles()

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    shader.bind()

    gpu.state.line_width_set(2.0)
    for quad, (rgb, fill_alpha, edge_alpha, fill_blend) in (([bottom, bottom_l, top_l, top], left_style),
                                                             ([bottom, bottom_r, top_r, top], right_style)):
        gpu.state.blend_set(fill_blend)
        shader.uniform_float("color", (*rgb, fill_alpha))
        batch_for_shader(shader, 'TRIS', {"pos": [quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]]}).draw(shader)
        gpu.state.blend_set('ALPHA')
        shader.uniform_float("color", (*rgb, edge_alpha))
        batch_for_shader(shader, 'LINE_STRIP', {"pos": quad}).draw(shader)

    gpu.state.line_width_set(3.0)
    shader.uniform_float("color", _DIVIDER_COLOR)
    batch_for_shader(shader, 'LINES', {"pos": [bottom, top]}).draw(shader)

    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def show(obj_name, symmetry, direction):
    global _pixel_handle, _obj_name, _symmetry, _preview
    _obj_name = obj_name
    _symmetry = symmetry
    _preview = direction
    if _pixel_handle is None:
        _pixel_handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    tag_redraw()


def hide():
    global _pixel_handle, _symmetry, _preview
    if _pixel_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_pixel_handle, 'WINDOW')
        _pixel_handle = None
        tag_redraw()
    _symmetry = None


def unregister():
    hide()
