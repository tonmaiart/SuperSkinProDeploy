"""Armature/Mesh object selector for the layer_viewer domain.

Selecting the rig's Armature in the viewport normally makes SuperSkinPro
lose track of "the active mesh" entirely, since every domain reads
``context.active_object`` and expects a MESH. The Mesh dropdown lets the
user drive object selection explicitly instead: pick one of the meshes
rigged to the current Armature (via an Armature modifier), and the pick
re-selects/activates that object in the viewport exactly like clicking it
there directly would.

``target_armature`` still exists on the backing PropertyGroup and is kept
in sync from the viewport (see ``_sync_from_viewport()`` below) -- it just
no longer has its own dropdown widget in the panel. It's still needed to
filter the Mesh dropdown's choices (``_poll_target_mesh``) and to resolve
the target Armature for the Enter Pose Mode button.

Sync is bidirectional:
  - Picking a dropdown value (a genuine UI interaction, dispatched through
    Blender's property-update mechanism, not from inside draw()) drives
    viewport selection via ``_select_single()``.
  - Picking an object in the viewport is detected at the top of
    ``draw_object_selectors()`` (called every redraw) and, if it disagrees
    with the stored dropdown state, the actual write-back is deferred to a
    one-shot ``bpy.app.timers`` callback, since Blender forbids some
    ID-related mutations from inside a draw() call and timers are the
    established escape hatch in this codebase.

Both directions converge in a single step (each write is skipped once the
two sides already agree), so there is no feedback loop between them.
"""

import bpy

from ...interface.utils.utils import _has_layer_system
from ...interface.utils.icons import get_mesh_init_icon_id, get_mesh_uninit_icon_id


def _mesh_state_icon_kwargs(obj) -> dict:
    """Return the ``layout.operator()``/``layout.popover()`` icon kwargs
    reflecting whether *obj* already has a layer system initialised --
    custom body-type glyphs (``icon_mesh_init``/``icon_mesh_uninit``,
    ``interface/utils/icons.py``) with a graceful built-in fallback if
    either failed to load, same convention as
    ``addon_updater_feature.py``'s ``icon_kwargs`` pattern."""
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


def _meshes_rigged_to(armature_obj, view_layer):
    """Return every MESH object in *view_layer* whose Armature modifier
    targets *armature_obj*."""
    return [
        obj for obj in view_layer.objects
        if obj.type == 'MESH' and _armature_for_mesh(obj) == armature_obj
    ]


def _select_single(context, obj):
    """Deselect everything else and make *obj* the sole selected + active
    object, mirroring a plain viewport click."""
    for other in context.view_layer.objects:
        if other is not obj and other.select_get():
            other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def get_effective_mesh(context):
    """Return the mesh layer_viewer should display/operate on: the active
    object if it's already a mesh, otherwise the last mesh remembered by the
    Armature/Mesh selectors (e.g. right after entering Pose Mode -- native
    Blender Ctrl+Tab or "Enter Pose Mode" alike -- makes an Armature the
    active object, but the user is still conceptually working on the mesh
    it drives). Returns None if neither is available.

    This is what lets the Layer Management panel keep showing/operating on
    the last mesh instead of collapsing to "No mesh active" the moment an
    Armature becomes active."""
    obj = context.active_object
    if obj and obj.type == 'MESH':
        return obj
    remembered = context.window_manager.superskin_layer_viewer_selector.target_mesh
    if remembered and remembered.type == 'MESH':
        return remembered
    return None


def run_with_object_active(context, target_obj, callback, *args):
    """Run *callback(*args)* with *target_obj* temporarily made the sole
    selected + active object, restoring the previous active object and
    selection afterward. No-op passthrough if *target_obj* is already
    active.

    Lets layer_viewer's mutation operators (Initialize Layer, Remove Data)
    target the mesh returned by ``get_effective_mesh()`` even while a
    different object -- typically an Armature in Pose Mode -- is actually
    active, since CoreFacade requires ``context.active_object`` to already
    be the mesh."""
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
# PropertyGroup — WindowManager.superskin_layer_viewer_selector
# ==============================================================================

def _poll_target_armature(self, obj):
    return obj.type == 'ARMATURE'


def _poll_target_mesh(self, obj):
    if obj.type != 'MESH':
        return False
    armature = self.target_armature
    return armature is None or _armature_for_mesh(obj) == armature


def _on_target_armature_update(self, context):
    obj = self.target_armature
    if obj and context.view_layer.objects.active != obj:
        _select_single(context, obj)


def _on_target_mesh_update(self, context):
    obj = self.target_mesh
    if obj and context.view_layer.objects.active != obj:
        _select_single(context, obj)


class SSPropLayerViewerSelector(bpy.types.PropertyGroup):
    target_armature: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="Armature",
        description="Pick an Armature to select it in the viewport",
        poll=_poll_target_armature,
        update=_on_target_armature_update,
    )
    target_mesh: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="Mesh",
        description="Pick a mesh rigged to the chosen Armature to select it in the viewport",
        poll=_poll_target_mesh,
        update=_on_target_mesh_update,
    )


# ==============================================================================
# Viewport -> dropdown sync (deferred, see module docstring)
# ==============================================================================

_sync_pending = False


def _needs_sync(state, active):
    if active is None:
        return False
    if active.type == 'ARMATURE':
        return state.target_armature != active
    if active.type == 'MESH':
        if state.target_mesh != active:
            return True
        armature = _armature_for_mesh(active)
        return bool(armature and state.target_armature != armature)
    return False


def _sync_from_viewport():
    global _sync_pending
    _sync_pending = False
    ctx = bpy.context
    state = ctx.window_manager.superskin_layer_viewer_selector
    active = ctx.view_layer.objects.active

    if active is None:
        return None
    if active.type == 'ARMATURE':
        if state.target_armature != active:
            state.target_armature = active
    elif active.type == 'MESH':
        armature = _armature_for_mesh(active)
        if armature and state.target_armature != armature:
            state.target_armature = armature
        if state.target_mesh != active:
            state.target_mesh = active
    return None


# ==============================================================================
# Mesh options popover -- opened the same way mirror's gear-icon settings
# button opens SUPERSKIN_PT_mirror_options (bl_region_type='HEADER', invoked
# only via layout.popover(), never auto-docked). Replaces the old plain
# dropdown Menu: the mesh list now lives inside this popup panel, alongside
# "Clean-up This Mesh" / "Clean-up All Meshes" (moved in from ui.py's
# SUPERSKIN_MT_layer_rename_overflow, see that file's docstring). Selecting
# a mesh entry just reselects the object in the viewport; the existing
# viewport->dropdown sync (_needs_sync / _sync_from_viewport, above) picks
# up the new active object into target_mesh/target_armature on the next
# redraw, so no separate write-back is needed here.
# ==============================================================================

class SUPERSKIN_PT_layer_viewer_mesh_options(bpy.types.Panel):
    """Popover content for picking the working mesh and cleaning up its (or
    every mesh's) layer data, opened from the Mesh selector button in the
    LAYER tab's object-selector row."""
    bl_idname = "SUPERSKIN_PT_layer_viewer_mesh_options"
    bl_label = "Mesh Options"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- see SUPERSKIN_PT_mirror_options's docstring
    # (features/mirror/mirror_feature.py) for why: a Panel registered
    # against 'UI' with no bl_category gets auto-docked as its own stray
    # sidebar panel in addition to being popover()-invocable. 'HEADER' is
    # only ever drawn when explicitly invoked.
    bl_region_type = 'HEADER'
    bl_ui_units_x = 7

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.superskin_layer_viewer_selector
        armature = state.target_armature
        view_layer = context.view_layer
        meshes = (
            _meshes_rigged_to(armature, view_layer) if armature is not None
            else [o for o in view_layer.objects if o.type == 'MESH']
        )

        layout.label(text="Select Mesh:")
        col = layout.column(align=True)
        if not meshes:
            col.label(text="No mesh available")
        else:
            current = state.target_mesh
            for obj in sorted(meshes, key=lambda o: o.name):
                # Custom body-type glyph flags a mesh that already has a
                # layer system initialised, so the user can tell at a
                # glance which candidates picking "Init & Edit" on would
                # actually re-use vs. seed fresh (see
                # layer_viewer_feature.py's Edit/Init button).
                icon_kwargs = _mesh_state_icon_kwargs(obj)
                # depress=True highlights the row matching the currently
                # selected mesh, same on/off-toggle visual convention used
                # by the Enter/Exit Pose Mode button in
                # layer_viewer_feature.py's draw_section().
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
    """Draw the Mesh dropdown row and return it so callers can append
    further controls (e.g. the Enter/Exit Pose Mode button) to the same row
    instead of starting a new one. Must stay usable even when the active
    object is an Armature (or nothing at all).

    Only ``target_mesh`` is exposed here -- ``target_armature`` has no
    dropdown widget anymore, but the property itself is still kept in sync
    from the viewport (see ``_sync_from_viewport()``) since it still drives
    the Mesh dropdown's filtering (``_poll_target_mesh``) and the Enter Pose
    Mode resolution chain."""
    global _sync_pending

    wm = context.window_manager
    state = wm.superskin_layer_viewer_selector

    if not _sync_pending and _needs_sync(state, context.view_layer.objects.active):
        _sync_pending = True
        bpy.app.timers.register(_sync_from_viewport, first_interval=0.0)

    row = layout.row(align=True)
    row.scale_y = 1.0
    mesh_label = state.target_mesh.name if state.target_mesh else "No Mesh"
    # Same custom body-type glyph convention as the popup's mesh list itself
    # (SUPERSKIN_PT_layer_viewer_mesh_options.draw(), via
    # _mesh_state_icon_kwargs()) -- shown on the closed button too, so the
    # current pick's init state is visible without having to open the popup.
    icon_kwargs = _mesh_state_icon_kwargs(state.target_mesh) if state.target_mesh else {"icon": 'NONE'}
    row.popover(SUPERSKIN_PT_layer_viewer_mesh_options.bl_idname, text=mesh_label, **icon_kwargs)
    return row


# ==============================================================================
# Registration
# ==============================================================================

def register():
    bpy.utils.register_class(SSPropLayerViewerSelector)
    bpy.types.WindowManager.superskin_layer_viewer_selector = bpy.props.PointerProperty(
        type=SSPropLayerViewerSelector, options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(SUPERSKIN_PT_layer_viewer_mesh_options)
    bpy.utils.register_class(SUPERSKIN_OT_layer_viewer_select_mesh)


def unregister():
    global _sync_pending
    _sync_pending = False
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_viewer_select_mesh)
    bpy.utils.unregister_class(SUPERSKIN_PT_layer_viewer_mesh_options)
    del bpy.types.WindowManager.superskin_layer_viewer_selector
    bpy.utils.unregister_class(SSPropLayerViewerSelector)
