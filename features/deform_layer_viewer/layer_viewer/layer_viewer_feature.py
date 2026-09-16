"""LayerViewerFeature — Unified Component Architecture implementation for the layer_viewer domain.

Collapses the old LayerViewerDomain (action dispatch) and prefs.py (draw,
persistence) into a single UnifiedFeatureExtension subclass.

This is a non-collapsible viewer domain that renders the Layer List at the
top of the LAYER tab at full width.
"""

import os
import bpy

from ....interface.registry.register_api import UnifiedFeatureExtension
from ....core.facade import CoreFacade
from ....interface.utils.utils import _has_layer_system
from ....interface.utils.mode_edit_toggle import draw_mode_edit_toggle, draw_edit_mask_button
from . import ui
from . import object_selector


_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# LayerViewerFeature — UnifiedFeatureExtension
# ==============================================================================

class LayerViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension for the Layer List in the LAYER tab."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "layer_viewer"
    actions = []
    section_title = "Layer List"
    draw_tab = "LAYER"
    link = "https://docs.superskinpro.com/layer_view/"
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    show_section_label = False
    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Render the Mesh dropdown first, at the very top of the section,
        as its own standalone row directly above ``ui.draw_layer_list()``
        (formerly drawn via that function's ``top_fn`` hook -- removed
        2026-09-13, per explicit user request, once nothing used it
        anymore) --

        **Superseded as of the `deform_layer_viewer` merge (2026-09-10) --
        not called by the registered `DeformLayerViewerFeature`.** Per
        explicit user request that the Layer list and Deform Bones list
        both show together, always, in both Object and Edit Mode, the
        parent class now draws the combined body itself
        (`deform_layer_viewer_feature.py`'s `_draw_combined()`), reusing
        this method's own mesh-dropdown/`is_edit`-label logic inline rather
        than calling this method, so it can insert the Deform Bones list
        and a single shared widget row around it. This method still exists
        only because `UnifiedFeatureExtension.draw_section()` is
        `@abstractmethod` -- `LayerViewerFeature` is otherwise unused now
        (its `execute()` was already a permanent no-op before the merge).
        The docstring below still accurately describes what this method's
        OWN body does when read as a standalone reference; it just isn't
        invoked in practice anymore.
        Then the Layer List itself, then the "[Object/Pose] [Edit Bone]
        [Edit Mask]" widget on its own full-width row at the bottom. See
        "History" in ``docs/domains/deform_layer_viewer.md`` for the
        sequence of revisions this layout has gone through, including an
        earlier attempt at hand-mirroring the list's column widths locally,
        and the later ``top_fn``-hook shape this superseded in turn.

        While actually editing (``obj.mode == 'EDIT'``), the Mesh dropdown
        stays the same "bind mesh" control -- it is simply drawn locked
        (disabled), per explicit user request, rather than swapping to a
        different control -- the mesh can't be changed while it's the one
        actually in Edit Mode, so a click on it would go nowhere anyway;
        locking it communicates that without hiding which mesh is bound.

        No inner ``box()`` here (removed) -- drawing straight into *layout*
        so this section looks flat/consistent with the other
        ``locked_expanded`` sections (weight_apply, mirror, etc.), none of
        which wrap themselves in their own box.

        ``ui.draw_layer_list()`` is called with ``obj`` possibly ``None``
        (when ``get_effective_mesh()`` can't resolve one) -- it already
        no-ops in that case (see its own guard).

        The Mesh selector itself, drawn on its own row above the list, is
        entirely independent of the list's own enabled state -- it must stay usable
        even when the active object is an Armature (or nothing at all), or
        when the mesh has no layer system yet (both of which leave the
        list below greyed out via ``draw_list_with_sidebar()``'s
        ``list_enabled=False``), since that's exactly the case it exists
        to recover from (selecting the rig otherwise loses SuperSkinPro's
        active-mesh detection entirely).

        No "No mesh active" warning is drawn anywhere in this section --
        with no resolvable mesh, the list below already renders empty and
        disabled, and every button in the widget/selector rows already
        reads as locked via its own ``poll()`` (grayed out, not clickable).
        That's enough signal on its own; a separate warning label was
        removed as redundant noise on top of an already-self-explanatory
        locked state.

        The widget row is a plain ``layout.row(align=True)`` -- it only
        ever holds two items ("Object"/"Pose" and "Edit Bone"/"Edit Mask"),
        so Blender's default equal-width stretch is enough; no ``split()``
        factor is needed.
        """
        obj = object_selector.get_effective_mesh(context)

        # Mesh dropdown, drawn ABOVE the Layer List via the list widget's
        # own `top_fn` hook (`interface/template_ui/layout.py::
        # draw_list_with_sidebar()`) -- per explicit user request,
        # implemented as a reusable hook on the shared list widget itself
        # rather than a hand-mirrored column split at this call site (an
        # earlier revision tried manually replicating
        # draw_list_with_sidebar()'s own column widths here, which is
        # exactly the kind of drift `top_fn` now avoids: `top_fn` draws
        # into the REAL `col_list` layout the list/search box use, so the
        # width match is automatic and exact, not approximated).
        #
        # `top_fn` also stays interactive regardless of the list's own
        # `list_enabled` state (see that function's docstring) -- required
        # here since the Mesh selector must remain usable even when the
        # list itself is greyed out (no mesh at all, or a mesh with no
        # layer system yet), since it's exactly what lets the user recover
        # from that state.
        #
        # While actually editing (`obj.mode == 'EDIT'`), the same Mesh
        # dropdown is drawn locked (disabled) rather than swapped for
        # something else: the mesh can't change out from under an active
        # Edit Mode session, so the control would have nothing useful to
        # do, but keeping it visible (just non-interactive) still shows
        # which mesh is bound without needing a separate label.
        #
        # `is_edit` mirrors the exact definition
        # interface/utils/mode_edit_toggle.py's own widget uses
        # (`obj.type == 'MESH' and obj.mode == 'EDIT'`) -- `obj` here is
        # already guaranteed to be a mesh or None (get_effective_mesh()'s
        # own contract), so only the mode check is needed. Blender only
        # ever puts the ACTIVE object into Edit Mode, so whenever this is
        # True, `obj` already IS `context.active_object` -- no need to
        # route through `object_selector.run_with_object_active()` (that
        # helper exists for the Armature-active/Pose-Mode case, which
        # can't coexist with `obj.mode == 'EDIT'`) before asking the
        # facade for the active layer's name.
        #
        # Neither branch sets a fixed `ui_units_x` on its content -- each
        # is the SOLE item inside the column `top_fn` receives, so Blender
        # stretches it to fill that column's own width automatically.
        is_edit = obj is not None and obj.mode == 'EDIT'

        # `top_fn` was removed from `draw_list_with_sidebar()` /
        # `draw_layer_list()` (2026-09-13, per explicit user request --
        # this method was already the hook's only real caller, and it's
        # unreachable itself, see the docstring above). Drawn as its own
        # standalone row directly here instead, mirroring
        # `deform_layer_viewer_feature.py::_draw_combined()`'s own
        # `mesh_row` -- kept only so this dead method's own body stays
        # internally consistent with the current template surface, not
        # because this branch runs in practice.
        if is_edit:
            locked = layout.column()
            locked.enabled = False
            object_selector.draw_object_selectors(locked, context)
        else:
            object_selector.draw_object_selectors(layout, context)
        layout.separator(factor=0.2)

        # Always drawn, even with no layer system yet -- an empty list
        # (rather than the whole list disappearing) keeps the panel's
        # layout/height stable regardless of init state. Also safe to call
        # with obj=None (see docstring above).
        ui.draw_layer_list(layout, context, rows=7, obj=obj)
        layout.separator(factor=0.4)

        # "[Object -> Pose -> Edit Bone] .... [Edit Mask]" widget
        # (interface/utils/mode_edit_toggle.py), same one used by
        # deform_bone_viewer, per explicit user request that both look and
        # behave identically. This call site additionally opts into the
        # widget's Mask-editing variant (edit_mask_idname/edit_mask_text),
        # per a later explicit user request extending Mask editing to this
        # tab too (formerly SKINNING-tab-only) -- "Edit Mask" is drawn as
        # its own separate button (via `draw_edit_mask_button()` below, not
        # the widget's own inline `show_edit_mask` branch), pushed to the
        # row's far-right edge via a fixed-ratio `split()` zone with
        # `alignment='RIGHT'` (NOT `separator_spacer()` -- see the split()
        # call below for why), per a later explicit user request; clicking
        # it lands directly in Mask sub-mode (a no-op if already there).
        # "Object"/"Pose"/"Edit Bone" were later merged into a single cycle
        # button (2026-09-13, per explicit user request) -- see
        # docs/core-interfaces/mode-edit-toggle-widget.md's "Merged into a
        # single Object/Pose/Edit Bone cycle button" section for the full
        # history, including the earlier two-separate-buttons and
        # merged-single-toggle shapes this superseded.
        #
        # This tab's own operators are still used instead of the widget's
        # plain defaults, since they carry poll() gating this domain
        # genuinely needs: "superskin.layer_enter_pose_mode" additionally
        # checks CoreFacade.is_system_activated() and armature-resolvability
        # before enabling the button (see layer_viewer.md's own rationale
        # for why it's a duplicated port of object.mw_force_pose_mode
        # rather than a bl_idname reference to it), and
        # "superskin.enter_layer_edit" gates on
        # superskin_active_interface == 'LAYER' (always true here, but
        # correct to keep).
        #
        # The cycle button renders on EVERY state here (not just while
        # editing), since `is_edit` is essentially never True at this call
        # site in practice: entering a real Edit Layer Weight session flips
        # superskin_active_interface to 'SKINNING', which hides the LAYER
        # tab (and this whole section) before the widget could ever render
        # its "is_edit" branch. So the Pose-state click (dispatching
        # superskin.enter_layer_edit via SUPERSKIN_OT_toggle_mask_mode's
        # enter_edit_idname handling, see
        # features/deform_layer_viewer/deform_bone_viewer/ops.py) is what
        # actually gets exercised here, landing the freshly-opened session
        # directly in Bone sub-mode.
        #
        # enter_edit_text ("Init & Edit Bone" vs plain "Edit Bone") is now
        # UNUSED by the cycle button -- merging removed the standalone
        # pre-entry "Edit Bone" button this label used to describe, so the
        # "this click also seeds the mesh's first Layer" cue is no longer
        # shown anywhere in this row. superskin.enter_layer_edit still
        # auto-initialises the layer system itself on first entry
        # regardless (see the Object-Mode bounce inside _enter_edit_mode()
        # in features/controller/ops_scene_modes.py) -- only the label cue
        # was lost, not the behavior. Still passed here (harmlessly) so as
        # not to special-case this call site's argument list.
        # "Init & Edit Mask" (edit_mask_enter_text) is unaffected -- "Edit
        # Mask" is still its own separate button and still shows it.
        needs_init = obj is not None and not _has_layer_system(obj)
        # Fixed-ratio split(), NOT row.separator_spacer() -- this codebase
        # already hit and reverted separator_spacer() for this exact
        # "push a button to the far right" purpose three times
        # (panel_main.py's _draw_top_row(), widget_preferences.py's
        # _draw_section_header(), tool_socket_feature.py's header row) --
        # it makes Blender miscompute the N-panel sidebar's required width,
        # visibly shifting/overlapping OTHER UI below it. split(factor=...)
        # + alignment='RIGHT' on the right-hand zone is the established fix
        # this codebase uses everywhere else for the same "left content,
        # right-hugging button" shape.
        row = layout.row()
        row.scale_y = 1.4
        # factor=0.35 (narrowed from an initial 0.6, per explicit user
        # request that the cycle button read as narrower/less dominant) --
        # "Object"/"Pose"/"Edit Bone" are all short labels, so the cycle
        # button doesn't need much width; the rest goes to "Edit Mask"'s
        # own right-hugging zone (empty while not editing, see
        # draw_edit_mask_button()'s own docstring).
        split = row.split(factor=0.35)
        cycle_zone = split.row()
        draw_mode_edit_toggle(
            cycle_zone, context,
            pose_toggle_idname="superskin.layer_enter_pose_mode",
            enter_edit_idname="superskin.enter_layer_edit",
            enter_edit_text="Init & Edit Bone" if needs_init else "Edit Bone",
            edit_mask_idname="superskin.toggle_mask_mode",
            edit_mask_text="Edit Mask",
            edit_mask_enter_text="Init & Edit Mask" if needs_init else "Edit Mask",
            mask_state_obj=obj,
            # "Edit Mask" drawn separately below instead (see this block's
            # own comment above).
            show_edit_mask=False,
            # Pins the Pose-state click to exactly this dropdown-resolved
            # mesh, instead of letting _enter_edit_mode() re-derive its own
            # target from the per-armature scene cache (which can point at
            # a different, already-initialised mesh -- see
            # docs/domains/controller.md's "Force Pose Mode: stale
            # per-armature 'last mesh' cache" section).
            target_mesh_obj=obj,
        )
        mask_zone = split.row()
        mask_zone.alignment = 'RIGHT'
        draw_edit_mask_button(
            mask_zone, context,
            enter_edit_idname="superskin.enter_layer_edit",
            edit_mask_idname="superskin.toggle_mask_mode",
            edit_mask_text="Mask",
            edit_mask_enter_text="Init & Mask" if needs_init else "Mask",
        )

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration
# ==============================================================================
#
# This class is no longer registered with UnifiedRegistry directly -- it and
# its `deform_bone_viewer` counterpart were merged into one domain,
# `deform_layer_viewer`, registered under both the LAYER and SKINNING tabs
# (see docs/domains/deform_layer_viewer.md). `deform_layer_viewer_feature.py`
# (the package parent) instantiates this class and delegates draw_section()
# to it for the LAYER tab; no `register()`/`unregister()` function is needed
# here anymore.
