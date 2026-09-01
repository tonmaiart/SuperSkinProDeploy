"""GPU shader primitives shared between core and feature domains.

Extracted here so feature packages can access GL helpers and the MULTI-mode
palette without importing from core/ sub-modules (which is forbidden by the
Core Boundary Rule).
"""

import ctypes
import gpu
from gpu_extras.batch import batch_for_shader

try:
    _libGL = ctypes.CDLL("libGL.so.1")
except Exception:
    try:
        _libGL = ctypes.CDLL("opengl32.dll")
    except Exception:
        _libGL = ctypes.CDLL(
            "/System/Library/Frameworks/OpenGL.framework/OpenGL")

GL_POLYGON_OFFSET_FILL = 0x8037


def gl_polygon_offset(f, u):
    _libGL.glPolygonOffset(ctypes.c_float(f), ctypes.c_float(u))


def gl_enable(cap):
    _libGL.glEnable(ctypes.c_uint(cap))


def gl_disable(cap):
    _libGL.glDisable(ctypes.c_uint(cap))


BONE_COLORS = (
(0.90, 0.10, 0.15),  # 1. แดงเข้มสด (Deep Red)
    (0.10, 0.85, 0.25),  # 2. เขียวสด (Bright Emerald Green)
    (0.10, 0.15, 0.70),  # 3. น้ำเงินเข้ม (Deep Royal Blue)
    (1.00, 0.90, 0.10),  # 4. เหลืองสว่างจัด (High-key Yellow)
    (0.85, 0.15, 0.75),  # 5. บานเย็นสด (Bright Magenta)
    (0.10, 0.90, 0.95),  # 6. ฟ้าไซแอนสว่าง (Bright Cyan)
    (0.95, 0.45, 0.05),  # 7. ส้มสดเข้ม (Vibrant Orange)
    (0.35, 0.10, 0.50),  # 8. ม่วงเข้มมืด (Dark Violet)
    (0.65, 0.95, 0.10),  # 9. เขียวมะนาวสะท้อนแสง (Neon Lime)
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
