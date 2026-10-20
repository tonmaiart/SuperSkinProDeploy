
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

SUPERSAMPLE = 2

_offscreen = None
_downsample_shader = None


def offscreen_for(width, height):
    global _offscreen
    size = (width * SUPERSAMPLE, height * SUPERSAMPLE)
    if _offscreen is not None and (_offscreen.width, _offscreen.height) != size:
        _offscreen.free()
        _offscreen = None
    if _offscreen is None:
        _offscreen = gpu.types.GPUOffScreen(*size)
    return _offscreen


def _get_downsample_shader():
    global _downsample_shader
    if _downsample_shader is None:
        iface = gpu.types.GPUStageInterfaceInfo("ssp_bone_hud_downsample_iface")
        iface.smooth('VEC2', "uv")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        info.push_constant('FLOAT', "alpha")
        info.push_constant('INT', "factor")
        info.push_constant('BOOL', "srgbTarget")
        info.sampler(0, 'FLOAT_2D', "image")
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_in(1, 'VEC2', "texCoord")
        info.vertex_out(iface)
        info.fragment_out(0, 'VEC4', "fragColor")
        info.vertex_source(
            "void main() { uv = texCoord; gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0); }")
        info.fragment_source(
            "vec3 to_target(vec3 c) {\n"
            "  if (!srgbTarget) { return c; }\n"
            "  c = max(c, vec3(0.0));\n"
            "  return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), c));\n"
            "}\n"
            "void main() {\n"
            "  ivec2 base = ivec2(floor(uv * vec2(textureSize(image, 0)) - 0.5 * float(factor) + 0.5));\n"
            "  vec4 sum = vec4(0.0);\n"
            "  for (int y = 0; y < factor; y++) {\n"
            "    for (int x = 0; x < factor; x++) {\n"
            "      vec4 t = texelFetch(image, base + ivec2(x, y), 0);\n"
            "      sum += vec4(to_target(t.rgb) * t.a, t.a);\n"
            "    }\n"
            "  }\n"
            "  fragColor = sum / float(factor * factor) * alpha;\n"
            "}\n")
        _downsample_shader = gpu.shader.create_from_info(info)
    return _downsample_shader


def composite(offscreen, width, height, alpha):
    gpu.state.blend_set('ALPHA_PREMULT')
    shader = _get_downsample_shader()
    shader.uniform_sampler("image", offscreen.texture_color)
    shader.uniform_float("alpha", float(alpha))
    shader.uniform_int("factor", SUPERSAMPLE)
    batch_for_shader(shader, 'TRI_FAN', {
        "pos": ((0, 0), (width, 0), (width, height), (0, height)),
        "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1)),
    }).draw(shader)


def render_flat(region, pos, col):
    width, height = region.width, region.height
    offscreen = offscreen_for(width, height)
    with offscreen.bind():
        gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0))
        with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
            gpu.matrix.load_identity()
            gpu.matrix.load_projection_matrix(Matrix((
                (2.0 / width, 0.0, 0.0, -1.0),
                (0.0, 2.0 / height, 0.0, -1.0),
                (0.0, 0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            )))
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            shader = gpu.shader.from_builtin('FLAT_COLOR')
            batch_for_shader(shader, 'TRIS', {
                "pos": np.ascontiguousarray(pos, dtype=np.float32),
                "color": np.ascontiguousarray(col, dtype=np.float32),
            }).draw(shader)
    composite(offscreen, width, height, 1.0)


def free():
    global _offscreen
    if _offscreen is not None:
        _offscreen.free()
        _offscreen = None
