"""Operator shells for the overlay_color feature domain.

Currently just the Multi Color Preview toggle. The weight/mask ramp editors
(``_ramp_io.py`` + Blender's own native ColorRamp widget) need no operators
of their own — ``template_color_ramp()`` already provides add/remove/drag
UI natively.
"""

import bpy
from ...core.facade import CoreFacade
from ...interface.utils.op_exec import run_domain_via_unified


class SUPERSKIN_OT_toggle_multi_color(bpy.types.Operator):
    """Press Alt+3 to toggle multi-bone color preview on/off.

    Used to be a hold gesture (press to start, release to stop, via a
    modal operator watching its own trigger key's RELEASE event) — now a
    plain one-shot press-to-toggle, matching the `toggle_multi_color`
    action name that already existed for this (`draw.toggle()` /
    `multi_color_draw.toggle()`) but was previously only reachable by a
    different code path.
    """
    bl_idname = "superskin.toggle_multi_color"
    bl_label = "Multi Color Preview (Toggle)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_editing_weights()

    def execute(self, context):
        return run_domain_via_unified(context, "overlay_color", "toggle_multi_color")


class SUPERSKIN_OT_toggle_multi_color_mask(bpy.types.Operator):
    """Manual toggle for the multi-Layer MASK color preview
    (`multi_color_mask_draw.py`) -- kept as a plain callable domain action
    (e.g. for a future UI button or the Python console) even though it no
    longer has a keymap of its own. It used to share Alt+3 with the bone
    preview above on the 'Object Mode'/'Pose' keymap categories; that
    binding was removed in favor of `SUPERSKIN_OT_start_multi_color_mask`/
    `SUPERSKIN_OT_stop_multi_color_mask` below, which the (since removed,
    per a later explicit user request) `features/layer_picker` domain's
    Alt+1 modal used to call via `bpy.ops` so the preview auto-activated
    for the duration of a Layer-picking session instead of needing its own
    shortcut -- see docs/domains/overlay_color.md. With that domain gone,
    the start/stop pair has no automatic caller either, reachable only via
    manual `bpy.ops` or the Python console.
    Gated on `multi_color_mask_draw.can_toggle()` resolving a target Mesh
    -- as of 2026-09-11 that means a Mesh in Edit Mode with a layer system
    already initialised (Object/Pose Mode support was dropped entirely,
    see that module's docstring).

    `execute()` is not a plain `run_domain_via_unified()` shell like the
    operator above -- `CoreFacade(context)` (constructed inside that
    helper) hard-requires `context.active_object` to already be a Mesh.
    This still wraps the dispatch in
    `multi_color_mask_draw._with_target_active()` for API symmetry with the
    other two operators below, though it's a no-op passthrough now that the
    resolved target is always already `context.active_object` in Edit
    Mode (no Pose Mode Armature-active case to swap around anymore)."""
    bl_idname = "superskin.toggle_multi_color_mask"
    bl_label = "Multi Color Mask Preview (Toggle)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        from . import multi_color_mask_draw
        return multi_color_mask_draw.can_toggle()

    def execute(self, context):
        from . import multi_color_mask_draw
        target = multi_color_mask_draw._resolve_target_mesh()
        if target is None:
            return {'CANCELLED'}
        return multi_color_mask_draw._with_target_active(
            target,
            lambda: run_domain_via_unified(context, "overlay_color", "toggle_multi_color_mask"),
        )


class SUPERSKIN_OT_start_multi_color_mask(bpy.types.Operator):
    """Force-start the multi-Layer MASK color preview. Not bound to any
    keymap and, since the `features/layer_picker` domain that used to call
    it (via `bpy.ops.superskin.start_multi_color_mask()` the moment its
    Alt+1 picking modal began) was removed entirely per a later explicit
    user request, no longer has any automatic caller either -- reachable
    only via manual `bpy.ops` or the Python console. See
    docs/domains/overlay_color.md for the history of that integration.

    Idempotent: a no-op if the preview is already active
    (`multi_color_mask_draw.start()`'s own `_start_handlers()` guard).
    Same `_with_target_active()` wrapping as the toggle operator above (a
    no-op passthrough in Edit Mode, kept for API symmetry)."""
    bl_idname = "superskin.start_multi_color_mask"
    bl_label = "Multi Color Mask Preview (Start)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        from . import multi_color_mask_draw
        return multi_color_mask_draw.can_toggle()

    def execute(self, context):
        from . import multi_color_mask_draw
        target = multi_color_mask_draw._resolve_target_mesh()
        if target is None:
            return {'CANCELLED'}
        return multi_color_mask_draw._with_target_active(
            target,
            lambda: run_domain_via_unified(context, "overlay_color", "start_multi_color_mask"),
        )


class SUPERSKIN_OT_stop_multi_color_mask(bpy.types.Operator):
    """Force-stop the multi-Layer MASK color preview -- the counterpart to
    the start operator above. Used to be called by the (since removed)
    `features/layer_picker` domain's Alt+1 modal on every exit path
    (confirm, cancel, ESC, and Blender's own forced `cancel()`) so the
    preview never outlived a picking session; now has no automatic caller
    either.

    Idempotent: a no-op if the preview isn't active. Falls back to
    `multi_color_mask_draw._bound_obj` (the Mesh the preview is actually
    bound to) when `_resolve_target_mesh()` can no longer resolve one from
    the current context (e.g. selection changed mid-hold), so stopping
    never silently fails just because the context moved on."""
    bl_idname = "superskin.stop_multi_color_mask"
    bl_label = "Multi Color Mask Preview (Stop)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        from . import multi_color_mask_draw
        return multi_color_mask_draw.is_enabled()

    def execute(self, context):
        from . import multi_color_mask_draw
        target = multi_color_mask_draw._resolve_target_mesh() or multi_color_mask_draw._bound_obj
        if target is None:
            multi_color_mask_draw.stop()
            return {'FINISHED'}
        return multi_color_mask_draw._with_target_active(
            target,
            lambda: run_domain_via_unified(context, "overlay_color", "stop_multi_color_mask"),
        )


_classes = (
    SUPERSKIN_OT_toggle_multi_color,
    SUPERSKIN_OT_toggle_multi_color_mask,
    SUPERSKIN_OT_start_multi_color_mask,
    SUPERSKIN_OT_stop_multi_color_mask,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
