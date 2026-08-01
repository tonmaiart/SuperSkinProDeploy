"""GPU shaders and GL helpers for SuperSkinPro color visualizer (Low-level Utilities).

This module's import (specifically, importing interface.utils.gpu_utils below)
is load-bearing for interface/ package initialisation order during core/
init -- see interface/__init__.py and interface/utils/__init__.py module
docstrings. Do not delete this file even though most of its GPU-draw logic
was retired after the switch to Blender's native Vertex Group Weight Overlay.
"""

# Re-export GPU primitives from interface/utils/gpu_utils.py so core callers
# keep working without change and feature packages can also import from
# interface/utils/ directly.
from ...interface.utils.gpu_utils import (
    GL_POLYGON_OFFSET_FILL,
    gl_polygon_offset,
    gl_enable,
    gl_disable,
    BONE_COLORS,
    get_custom_wire_shader,
    get_custom_point_shader,
)


def register(): pass
def unregister(): pass
