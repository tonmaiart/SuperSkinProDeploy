"""Armature/Mesh object selector for the layer_viewer domain."""

import bpy

from ....interface.utils.utils import _has_layer_system
from ....interface.utils.icons import get_mesh_init_icon_id, get_mesh_uninit_icon_id


def _mesh_state_icon_kwargs(obj) -> dict:
    """Return the ``layout.operator()``/``layout.popover()`` icon kwargs reflecting whether
    *obj* already has a layer system initialised."""
    icon_id = get_mesh_init_icon_id() if _has_layer_system(obj) else get_mesh_uninit_icon_id()
    if icon_id:
        return {"icon_value": icon_id}
    return {"icon": 'FILEBROWSER' if _has_layer_system(obj) else 'NONE'}


# ==============================================================================
# Helpers
# ==============================================================================

def _armature_for_mesh(mesh_obj):
    """Return the first Armature object driving *mesh_obj* via an Armature
    modifier, or None."""
    if not mesh_obj or mesh_obj.type != 'MESH':
        return None
    for mod in mesh_obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    return None


def _select_single(context, obj):
    """Deselect everything else and make *obj* the sole selected + active
    object, mirroring a plain viewport click."""
    for other in context.view_layer.objects:
        if other is not obj and other.select_get():
            other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


_last_mesh_name = ""


def get_effective_mesh(context):
    """Return the active mesh; with an armature active, the last active mesh bound to it."""
    global _last_mesh_name
    obj = context.active_object
    if obj is None:
        return None
    if obj.type == 'MESH':
        if _armature_for_mesh(obj) is not None:
            _last_mesh_name = obj.name
        return obj
    if obj.type == 'ARMATURE':
        remembered = context.view_layer.objects.get(_last_mesh_name)
        if remembered is not None and _armature_for_mesh(remembered) == obj:
            return remembered
        bound = sorted(
            (o for o in context.view_layer.objects if _armature_for_mesh(o) == obj),
            key=lambda o: o.name,
        )
        return bound[0] if bound else None
    return None


def get_effective_armature(context):
    """Return the active armature, or the armature driving the active mesh, otherwise None."""
    obj = context.active_object
    if obj and obj.type == 'ARMATURE':
        return obj
    return _armature_for_mesh(obj)


def run_with_object_active(context, target_obj, callback, *args):
    """Run *callback(*args)* with *target_obj* temporarily made the sole selected + active
    object, restoring the previous active object and selection afterward."""
    prev_active = context.view_layer.objects.active
    if prev_active is target_obj:
        return callback(*args)

    prev_selected = [o for o in context.view_layer.objects if o.select_get()]
    for o in prev_selected:
        o.select_set(False)
    target_obj.select_set(True)
    context.view_layer.objects.active = target_obj
    try:
        return callback(*args)
    finally:
        target_obj.select_set(False)
        for o in prev_selected:
            o.select_set(True)
        context.view_layer.objects.active = prev_active


# ==============================================================================
# Mesh options popover -- opened the same way mirror's gear-icon settings
# button opens SUPERSKIN_PT_mirror_options (bl_region_type='HEADER', invoked
# only via layout.popover(), never auto-docked). Replaces the old plain
# dropdown Menu: the mesh list now lives inside this popup panel, alongside
# "Clean-up This Mesh" / "Clean-up All Meshes" (moved in from ui.py's
# SUPERSKIN_MT_layer_rename_overflow, see that file's docstring). Selecting
# a mesh entry just reselects the object in the viewport.
# ==============================================================================

class SUPERSKIN_PT_layer_viewer_mesh_options(bpy.types.Panel):
    """Popover content for picking the working mesh and cleaning up its (or every mesh's) layer
    data, opened from the Mesh selector button in the LAYER tab's object-selector row."""
    bl_idname = "SUPERSKIN_PT_layer_viewer_mesh_options"
    bl_label = "Mesh Options"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 7

    def draw(self, context):
        layout = self.layout
        meshes = [o for o in context.view_layer.objects if _armature_for_mesh(o) is not None]

        layout.label(text="Bind Mesh :")
        col = layout.column(align=True)
        if not meshes:
            col.label(text="No mesh available")
        else:
            current = context.active_object
            for obj in sorted(meshes, key=lambda o: o.name):
                icon_kwargs = _mesh_state_icon_kwargs(obj)
                op = col.operator(
                    "superskin.layer_viewer_select_mesh", text=obj.name,
                    depress=(obj == current), **icon_kwargs,
                )
                op.mesh_name = obj.name

        layout.separator()
        col_cleanup = layout.column(align=True)
        col_cleanup.operator("superskin.layer_remove_data",
                              text="Clean-up This Mesh", icon='TRASH')
        col_cleanup.operator("superskin.layer_remove_all_data",
                              text="Clean-up All Meshes", icon='TRASH')


class SUPERSKIN_OT_layer_viewer_select_mesh(bpy.types.Operator):
    """Select ``mesh_name`` in the viewport, driven by the Mesh options
    popover above."""
    bl_idname = "superskin.layer_viewer_select_mesh"
    bl_label = "Select Mesh"
    bl_options = {'INTERNAL', 'UNDO'}

    mesh_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.view_layer.objects.get(self.mesh_name)
        if obj is None or obj.type != 'MESH':
            return {'CANCELLED'}
        _select_single(context, obj)
        return {'FINISHED'}


# ==============================================================================
# UI
# ==============================================================================

def draw_object_selectors(layout, context):
    """Draw the Mesh dropdown row and return it so callers can append further controls to the
    same row instead of starting a new one."""
    mesh = get_effective_mesh(context)
    if _armature_for_mesh(mesh) is None:
        mesh = None

    row = layout.row(align=True)
    row.scale_y = 1.4
    mesh_label = mesh.name if mesh else ""
    icon_kwargs = _mesh_state_icon_kwargs(mesh) if mesh else {"icon": 'NONE'}
    row.popover(SUPERSKIN_PT_layer_viewer_mesh_options.bl_idname, text=mesh_label, **icon_kwargs)
    return row


# ==============================================================================
# Registration
# ==============================================================================

def register():
    bpy.utils.register_class(SUPERSKIN_PT_layer_viewer_mesh_options)
    bpy.utils.register_class(SUPERSKIN_OT_layer_viewer_select_mesh)


def unregister():
    global _last_mesh_name
    _last_mesh_name = ""
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_viewer_select_mesh)
    bpy.utils.unregister_class(SUPERSKIN_PT_layer_viewer_mesh_options)
