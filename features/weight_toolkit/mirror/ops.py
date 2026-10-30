
import json

import bpy
from ....core.facade import CoreFacade
from . import draw as mirror_draw
from . import ui as mirror_ui
from .detect import Symmetry, build_name_pairs, detect_symmetry
from .logic import execute_mirror_pipeline, resolve_direction
from ..weight_toolkit_feature import MirrorPreferencesService, _DEFAULTS_PATH



_MIRROR_TIPS = (
    "\n\u2022 Select several layers to mirror them together"
    "\n\u2022 With vertices selected, only those are mirrored"
)


class OBJECT_OT_MirrorWeights(bpy.types.Operator):
    """Mirror weights from the character's left side to its right, or back. While editing a layer, only selected vertices change. Select several layers to mirror them all at once"""
    bl_idname = "object.mirror_weights"
    bl_label = "Mirror Weights"
    bl_options = {'UNDO'}

    direction: bpy.props.EnumProperty(
        name="Direction",
        items=[
            ('SAVED', "Last Used", "Use the last used direction"),
            ('LEFT_TO_RIGHT', "Mirror to Right", "Copy the character's left side onto its right side"),
            ('RIGHT_TO_LEFT', "Mirror to Left", "Copy the character's right side onto its left side"),
        ],
        default='SAVED',
        options={'SKIP_SAVE'},
    )
    all_layers: bpy.props.BoolProperty(
        name="Mirror All Layers",
        description="Mirror every layer instead of only the active or selected layers",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_skinning_session(context)

    @classmethod
    def description(cls, context, properties):
        if properties.direction == 'RIGHT_TO_LEFT':
            return "Copy the character's right side weights onto its left side" + _MIRROR_TIPS
        if properties.direction == 'LEFT_TO_RIGHT':
            return "Copy the character's left side weights onto its right side" + _MIRROR_TIPS
        return "Mirror weights onto the chosen Target Side" + _MIRROR_TIPS

    def execute(self, context):
        if self.direction != 'SAVED':
            MirrorPreferencesService.set_mirror_direction(self.direction)
        shown = mirror_draw.current()
        facade = CoreFacade(context)
        sym = shown[1] if shown and shown[0] == facade.get_obj().name else None
        sym, direction = resolve_direction(facade, sym)

        mirror_data = MirrorPreferencesService.get_mirror_data()
        do_mask = mirror_data in ('MASK', 'BOTH')
        if mirror_data in ('BONE', 'BOTH'):
            pairs, _stats = build_name_pairs(
                facade, sym, direction, MirrorPreferencesService.get_mirror_search_replace_pairs(),
            )
            if not pairs:
                if not do_mask:
                    self.report({'WARNING'}, "No mirror pairs found.")
                    return {'CANCELLED'}
                self.report(
                    {'WARNING'},
                    "No bone weight mirror pairs found, only the mask was mirrored. "
                    "Check that the mesh has an Armature modifier with deform bones.",
                )

        context.scene.superskin_internal_transaction = True
        try:
            count = execute_mirror_pipeline(facade, sym, all_layers=self.all_layers)
        except ValueError:
            return {'CANCELLED'}
        finally:
            context.scene.superskin_internal_transaction = False
        target = "Right" if MirrorPreferencesService.get_mirror_source_side() == 'LEFT' else "Left"
        suffix = f", {count} layers" if count > 1 else ""
        self.report({'INFO'}, f"Mirrored to {target} on the {sym.axis} axis{suffix}")
        return {'FINISHED'}


class SUPERSKIN_OT_open_mirror_options(bpy.types.Operator):
    bl_idname = "superskin.open_mirror_options"
    bl_label = "Mirror Weight"
    bl_description = "Mirror weights from the character's left side to its right, or back" + _MIRROR_TIPS
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_skinning_session(context)

    def invoke(self, context, event):
        obj = context.active_object
        if obj is not None and obj.type == 'MESH':
            try:
                sym = detect_symmetry(CoreFacade(context))
            except Exception as e:
                CoreFacade.debug_log("feature_domains", f"mirror: symmetry detection failed: {e!r}")
                sym = Symmetry(0, 1.0, "default")
            mirror_draw.show(obj.name, sym, MirrorPreferencesService.get_mirror_direction())
        context.window_manager.modal_handler_add(self)
        bpy.ops.wm.call_panel(name="SUPERSKIN_PT_mirror_options", keep_open=True)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return bpy.ops.superskin.open_mirror_options('INVOKE_DEFAULT')

    def modal(self, context, event):
        if event.type in _NON_INPUT_EVENTS:
            return {'PASS_THROUGH'}
        mirror_draw.hide()
        return {'FINISHED', 'PASS_THROUGH'}

    def cancel(self, context):
        mirror_draw.hide()


_NON_INPUT_EVENTS = {'NONE', 'TIMER', 'TIMER0', 'TIMER1', 'TIMER2', 'TIMER_JOBS', 'TIMER_AUTOSAVE',
                     'TIMER_REPORT', 'TIMERREGION', 'WINDOW_DEACTIVATE', 'ACTIONZONE_AREA',
                     'ACTIONZONE_REGION', 'ACTIONZONE_FULLSCREEN'}



class SUPERSKIN_OT_add_mirror_sr(bpy.types.Operator):
    """Add a bone name pair, such as Left and Right"""
    bl_idname = "superskin.add_mirror_sr"
    bl_label = "Add Mirror Search/Replace Pair"
    bl_options = {'REGISTER'}

    def execute(self, context):
        mirror = context.window_manager.superskin_mirror_prefs
        sr_coll = mirror.search_replace_pairs
        sr_coll.add()
        mirror.search_replace_index = len(sr_coll) - 1
        CoreFacade.save_prefs()
        return {'FINISHED'}


class SUPERSKIN_OT_remove_mirror_sr(bpy.types.Operator):
    """Remove the selected bone name pair"""
    bl_idname = "superskin.remove_mirror_sr"
    bl_label = "Remove Mirror Search/Replace Pair"
    bl_options = {'REGISTER'}

    index: bpy.props.IntProperty(
        name="Index",
        description="Index of the pair to remove",
        default=0,
        min=0,
    )

    def execute(self, context):
        mirror = context.window_manager.superskin_mirror_prefs
        sr_coll = mirror.search_replace_pairs
        if 0 <= self.index < len(sr_coll):
            sr_coll.remove(self.index)
        mirror.search_replace_index = max(0, min(mirror.search_replace_index, len(sr_coll) - 1))
        CoreFacade.save_prefs()
        return {'FINISHED'}


class SUPERSKIN_OT_reset_mirror_sr(bpy.types.Operator):
    """Clear the custom bone name keywords back to the default list"""
    bl_idname = "superskin.reset_mirror_sr"
    bl_label = "Reset Mapping Keywords"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        with open(_DEFAULTS_PATH, encoding="utf-8") as f:
            defaults = json.load(f).get("search_replace_pairs", [])
        mirror = context.window_manager.superskin_mirror_prefs
        sr_coll = mirror.search_replace_pairs
        sr_coll.clear()
        for search_text, replace_text in defaults:
            item = sr_coll.add()
            item.search_text = search_text
            item.replace_text = replace_text
        mirror.search_replace_index = 0
        CoreFacade.save_prefs()
        return {'FINISHED'}



_classes = (
    OBJECT_OT_MirrorWeights,
    SUPERSKIN_OT_open_mirror_options,
    SUPERSKIN_OT_add_mirror_sr,
    SUPERSKIN_OT_remove_mirror_sr,
    SUPERSKIN_OT_reset_mirror_sr,
)


def register():
    mirror_ui.register()
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    mirror_draw.unregister()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    mirror_ui.unregister()
