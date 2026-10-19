
import ctypes
import gpu
from gpu_extras.batch import batch_for_shader

GL_POLYGON_OFFSET_FILL = 0x8037

_GL_LIBRARIES = ("opengl32.dll", "libGL.so.1", "libGL.so")
_UNRESOLVED = object()
_libGL = _UNRESOLVED


def _gl():
    global _libGL
    if _libGL is not _UNRESOLVED:
        return _libGL
    _libGL = None
    try:
        backend = gpu.platform.backend_type_get()
    except Exception:
        return None
    if backend != 'OPENGL':
        return None
    for name in _GL_LIBRARIES:
        try:
            _libGL = ctypes.CDLL(name)
            break
        except OSError:
            continue
    return _libGL


def gl_polygon_offset(f, u):
    lib = _gl()
    if lib is not None:
        lib.glPolygonOffset(ctypes.c_float(f), ctypes.c_float(u))


def gl_enable(cap):
    lib = _gl()
    if lib is not None:
        lib.glEnable(ctypes.c_uint(cap))


def gl_disable(cap):
    lib = _gl()
    if lib is not None:
        lib.glDisable(ctypes.c_uint(cap))


BONE_COLORS = (
    (0.90, 0.10, 0.15),
    (0.10, 0.85, 0.25),
    (0.10, 0.15, 0.70),
    (1.00, 0.90, 0.10),
    (0.85, 0.15, 0.75),
    (0.10, 0.90, 0.95),
    (0.95, 0.45, 0.05),
    (0.35, 0.10, 0.50),
    (0.65, 0.95, 0.10),
    (0.05, 0.40, 0.65),
)

_custom_wire_shader = None
_custom_point_shader = None


def get_custom_wire_shader():
    global _custom_wire_shader
    if _custom_wire_shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        info.push_constant('VEC4', "color")
        info.vertex_in(0, 'VEC3', "pos")
        info.fragment_out(0, 'VEC4', "FragColor")
        info.vertex_source(
            "void main() {\n"
            "    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);\n"
            "    gl_Position.z -= 0.00004 * gl_Position.w;\n"
            "}\n")
        info.fragment_source(
            "void main() {\n"
            "    FragColor = color;\n"
            "}\n")
        _custom_wire_shader = gpu.shader.create_from_info(info)
    return _custom_wire_shader


def get_custom_point_shader():
    global _custom_point_shader
    if _custom_point_shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        info.push_constant('VEC4', "color")
        info.vertex_in(0, 'VEC3', "pos")
        info.fragment_out(0, 'VEC4', "FragColor")
        info.vertex_source(
            "void main() {\n"
            "    gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0);\n"
            "    gl_Position.z -= 0.0002 * gl_Position.w;\n"
            "}\n")
        info.fragment_source(
            "void main() {\n"
            "    vec2 coord = gl_PointCoord - vec2(0.5);\n"
            "    float dist = length(coord);\n"
            "    float alpha_mask = smoothstep(0.5, 0.45, dist);\n"
            "    if (alpha_mask == 0.0) discard;\n"
            "    FragColor = vec4(color.rgb, color.a * alpha_mask);\n"
            "}\n")
        _custom_point_shader = gpu.shader.create_from_info(info)
    return _custom_point_shader
