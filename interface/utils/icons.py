"""Custom icon loader for SuperSkinPro's interface.

A single ``bpy.utils.previews`` collection (currently nine icons: the
help/link glyph, the addon-updater's download glyph, the layer_viewer
Mesh selector's uninitialised/initialised body-type glyphs, the deform
bone list's edit-mask glyph, the layer list's combine/duplicate/edit
glyphs, and the bone-weight glyph), loaded once at addon-register time
and torn down at unregister. No core/ or core_subsystems/ imports (only
``os``, ``gpu``, and Blender's own ``bpy.utils.previews``), so this loads
at module scope from ``interface/utils/__init__.py`` exactly like
``gpu_utils`` does -- no deferred-loading dance needed.

``get_*_icon_id()`` functions return a UILayout-facing ``icon_value``.
``get_*_icon_texture()`` functions return a ``gpu.types.GPUTexture`` built
from the same source PNG's full-resolution pixels, lazily on first call
and cached thereafter -- for callers that need to draw the icon directly
in a raw ``SpaceView3D`` ``POST_PIXEL`` handler (e.g.
``core/shaders/shader_manager.py``'s shared HUD stack), where a UILayout
``icon_value`` cannot be used.
"""

import os
import bpy.utils.previews
import gpu

_ADDON_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_ICON_HELP_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_help.png")
_ICON_UPDATE_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-download-64.png")
_ICON_MESH_UNINIT_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_mesh_uninit.png")
_ICON_MESH_INIT_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_mesh_init.png")
_ICON_LAYER_MASK_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-layer-mask-50.png")
_ICON_COMBINE_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-combine-50.png")
_ICON_DUPLICATE_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-duplicate-24.png")
_ICON_EDIT_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-edit-48.png")
_ICON_BONE_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_bone.png")

_preview_collection = None
_icon_textures = {}


def get_help_icon_id() -> int:
    """Return the ``icon_value`` for the custom help/link icon, or ``0``
    (Blender's "no icon" sentinel) if it failed to load -- callers should
    fall back to a built-in icon rather than pass ``0`` straight through."""
    if _preview_collection is None or "icon_help" not in _preview_collection:
        return 0
    return _preview_collection["icon_help"].icon_id


def get_update_icon_id() -> int:
    """Return the ``icon_value`` for the custom addon-update download icon,
    or ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_update" not in _preview_collection:
        return 0
    return _preview_collection["icon_update"].icon_id


def get_mesh_uninit_icon_id() -> int:
    """Return the ``icon_value`` for a mesh with no layer system initialised
    yet, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_mesh_uninit" not in _preview_collection:
        return 0
    return _preview_collection["icon_mesh_uninit"].icon_id


def get_mesh_init_icon_id() -> int:
    """Return the ``icon_value`` for a mesh that already has a layer system
    initialised, or ``0`` (Blender's "no icon" sentinel) if it failed to
    load -- callers should fall back to a built-in icon rather than pass
    ``0`` straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_mesh_init" not in _preview_collection:
        return 0
    return _preview_collection["icon_mesh_init"].icon_id


def get_layer_mask_icon_id() -> int:
    """Return the ``icon_value`` for the custom edit-mask icon, or ``0``
    (Blender's "no icon" sentinel) if it failed to load -- callers should
    fall back to a built-in icon rather than pass ``0`` straight through.
    Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_layer_mask" not in _preview_collection:
        return 0
    return _preview_collection["icon_layer_mask"].icon_id


def get_combine_icon_id() -> int:
    """Return the ``icon_value`` for the custom merge/combine layers icon,
    or ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_combine" not in _preview_collection:
        return 0
    return _preview_collection["icon_combine"].icon_id


def get_duplicate_icon_id() -> int:
    """Return the ``icon_value`` for the custom duplicate-layer icon, or
    ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_duplicate" not in _preview_collection:
        return 0
    return _preview_collection["icon_duplicate"].icon_id


def get_edit_icon_id() -> int:
    """Return the ``icon_value`` for the custom "Edit Layer Weight" icon,
    or ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_edit" not in _preview_collection:
        return 0
    return _preview_collection["icon_edit"].icon_id


def get_bone_icon_id() -> int:
    """Return the ``icon_value`` for the custom bone-weight icon, or ``0``
    (Blender's "no icon" sentinel) if it failed to load -- callers should
    fall back to a built-in icon rather than pass ``0`` straight through.
    Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_bone" not in _preview_collection:
        return 0
    return _preview_collection["icon_bone"].icon_id


def _build_icon_texture(name: str):
    """Build (or return the cached) ``gpu.types.GPUTexture`` for preview
    *name*, sourced from that preview's full-resolution pixels rather than
    its small fixed-size icon thumbnail. Returns ``None`` if the preview
    failed to load -- callers should skip drawing an icon rather than pass
    ``None`` into a texture sampler."""
    if name in _icon_textures:
        return _icon_textures[name]
    if _preview_collection is None or name not in _preview_collection:
        return None
    preview = _preview_collection[name]
    width, height = preview.image_size[:]
    if width == 0 or height == 0:
        return None
    pixels = gpu.types.Buffer('FLOAT', width * height * 4, list(preview.image_pixels_float))
    texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=pixels)
    _icon_textures[name] = texture
    return texture


def get_layer_mask_icon_texture():
    """Return the cached ``gpu.types.GPUTexture`` for the custom edit-mask
    icon (same source PNG as ``get_layer_mask_icon_id()``), or ``None`` if
    it failed to load. For raw ``SpaceView3D`` HUD drawing -- see this
    module's docstring."""
    return _build_icon_texture("icon_layer_mask")


def get_bone_icon_texture():
    """Return the cached ``gpu.types.GPUTexture`` for the custom
    bone-weight icon (same source PNG as ``get_bone_icon_id()``), or
    ``None`` if it failed to load. For raw ``SpaceView3D`` HUD drawing --
    see this module's docstring."""
    return _build_icon_texture("icon_bone")


def register():
    global _preview_collection
    _preview_collection = bpy.utils.previews.new()
    if os.path.isfile(_ICON_HELP_PATH):
        _preview_collection.load("icon_help", _ICON_HELP_PATH, 'IMAGE')
    if os.path.isfile(_ICON_UPDATE_PATH):
        _preview_collection.load("icon_update", _ICON_UPDATE_PATH, 'IMAGE')
    if os.path.isfile(_ICON_MESH_UNINIT_PATH):
        _preview_collection.load("icon_mesh_uninit", _ICON_MESH_UNINIT_PATH, 'IMAGE')
    if os.path.isfile(_ICON_MESH_INIT_PATH):
        _preview_collection.load("icon_mesh_init", _ICON_MESH_INIT_PATH, 'IMAGE')
    if os.path.isfile(_ICON_LAYER_MASK_PATH):
        _preview_collection.load("icon_layer_mask", _ICON_LAYER_MASK_PATH, 'IMAGE')
    if os.path.isfile(_ICON_COMBINE_PATH):
        _preview_collection.load("icon_combine", _ICON_COMBINE_PATH, 'IMAGE')
    if os.path.isfile(_ICON_DUPLICATE_PATH):
        _preview_collection.load("icon_duplicate", _ICON_DUPLICATE_PATH, 'IMAGE')
    if os.path.isfile(_ICON_EDIT_PATH):
        _preview_collection.load("icon_edit", _ICON_EDIT_PATH, 'IMAGE')
    if os.path.isfile(_ICON_BONE_PATH):
        _preview_collection.load("icon_bone", _ICON_BONE_PATH, 'IMAGE')


def unregister():
    global _preview_collection
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None
    for texture in _icon_textures.values():
        if hasattr(texture, "free"):
            texture.free()
    _icon_textures.clear()
