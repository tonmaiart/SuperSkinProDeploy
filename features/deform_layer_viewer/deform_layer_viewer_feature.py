"""DeformLayerViewerFeature — the single UnifiedFeatureExtension for the
merged `deform_layer_viewer` domain.

Merges the former standalone `layer_viewer` (LAYER tab) and
`deform_bone_viewer` (SKINNING tab) domains into one, registered under both
tabs via `draw_tab=('LAYER', 'SKINNING')` (see
`interface/registry/register_api.py`). See
docs/domains/deform_layer_viewer.md for the full rationale and history.

**Both tabs draw the SAME combined body, always (2026-09-10, per explicit
user request):** the Deform Bones list and the Layer list, side by side in
one row (Deform Bones on the left, Layer list on the right, 2026-09-13 per
explicit user request), then ONE shared "[Object/Pose] [Edit Bone] [Edit
Mask]" widget row below spanning the full width -- as top-level SIBLING
blocks in `_draw_combined()` below, regardless of Object/Edit Mode. Earlier
revisions of this merge kept each half's own
`draw_section()` unchanged and dispatched per tab (LAYER tab → Layer list
only, SKINNING tab → Deform Bones list, with the Layer list embedded via a
`top_fn` hook only while `obj.mode == 'EDIT'`). That was reverted for two
reasons: (1) the Layer list visibly disappearing outside Edit Mode read as
broken/inconsistent; (2) nesting one full list-with-sidebar widget inside
another's `top_fn` misaligned the inner list's own side-button column
(`draw_list_with_sidebar()`'s fixed top offset is calibrated for a
`top_fn` that draws a single simple row, not an entire second widget) --
see `deform_bone_viewer_feature.py`'s `draw_section()` docstring for the
full diagnosis. Both lists are now drawn as independent, top-level calls
so each one's own sidebar aligns correctly again.

**One exception to "always the same body" (2026-09-15, per explicit user
request):** the "Edit Mask" button is now skipped on the SKINNING tab only
-- it moved into `features/skin_tools_ui/ui_weight_apply.py`'s own N-panel
column there, which only exists on that tab. The LAYER tab still draws it
here (`_draw_combined()`'s `tab_key` parameter gates this).
"""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...interface.template_ui import draw_lists_side_by_side
from ...interface.utils.utils import _has_layer_system
from ...interface.utils.mode_edit_toggle import draw_mode_edit_toggle, draw_edit_mask_button
from .layer_viewer import object_selector
from .layer_viewer.public_api import draw_layer_list
from .deform_bone_viewer.ui import draw_influence_list_system
from .deform_bone_viewer.deform_bone_viewer_feature import DeformBoneViewerFeature


# ==============================================================================
# DeformLayerViewerFeature — UnifiedFeatureExtension
# ==============================================================================

class DeformLayerViewerFeature(UnifiedFeatureExtension):
    """Non-collapsible viewer extension rendering the Layer List and the
    Deform Bones List together -- one domain, two tabs, identical body."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "deform_layer_viewer"
    # Only the deform_bone_viewer half owns real dispatchable actions (the
    # ten Clipboard Bone/Layer Weight menu entries).
    actions = DeformBoneViewerFeature.actions
    draw_tab = ("LAYER", "SKINNING")
    collapsible = True
    priority = 0
    expanded_by_default = True
    locked_expanded = True
    show_section_label = False
    keymaps = DeformBoneViewerFeature.keymaps

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # LayerViewerFeature is NOT instantiated here -- nothing about it
        # is reused anymore (its execute() was already a permanent no-op
        # before the merge, and draw_section() is superseded by
        # _draw_combined() below, which reuses its underlying
        # ui.draw_layer_list()/object_selector logic directly instead of
        # going through that class). DeformBoneViewerFeature IS still kept
        # for execute()/get_keymap_items() -- the only two things this
        # class doesn't reimplement itself.
        self._bone_ext = DeformBoneViewerFeature()

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade) -> dict:
        return self._bone_ext.execute(action, context, core_facade)

    def get_keymap_items(self) -> list:
        return self._bone_ext.get_keymap_items()

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section_for_tab(self, layout, context, tab_key: str) -> None:
        self._draw_combined(layout, context, tab_key)

    def draw_section(self, layout, context) -> None:
        # Abstract method must exist on a concrete UnifiedFeatureExtension,
        # but this extension always renders through draw_section_for_tab()
        # since it's registered under two tabs -- never called in practice.
        self._draw_combined(layout, context, "LAYER")

    def _draw_combined(self, layout, context, tab_key: str) -> None:
        """Top row: the "[Object/Pose/Edit Bone]" cycle button, the "bind
        mesh" dropdown, and the "Settings"/"How to use" toggles, all in
        ONE row, left to right, the toggles hugging the row's right edge
        (2026-09-14, per two successive explicit user requests -- first
        moving the cycle button next to the dropdown, then moving the
        toggles from their own separate row into this same one -- see
        "Three zones in one row" and "'Settings'/'How to use' relocated
        here" below). Then Deform Bones list (left column) and Layer list
        (right column) side by side in one row (via
        `draw_lists_side_by_side()`, see below), then a final row holding
        only the right-hugging "Edit Mask" button -- always, on both tabs,
        regardless of Object/Edit Mode. Each list is still drawn into its
        own column (never one nested inside the other's `top_fn` -- that
        hook has since been removed entirely, see `draw_layer_list()`'s
        docstring) -- see this module's docstring for why that nesting
        shape was rejected in the first place.

        **Three zones in one row, via nested `split()` (2026-09-14, per
        explicit user request):** the "Settings"/"How to use" toggles used
        to draw on their own separate row directly below this one. A
        single `split(factor=X)` only fixes its FIRST zone's width --
        every zone requested after it splits whatever space is left
        evenly among however many there are (see "Layout: two or three
        sequential `row.operator()` calls" in
        `docs/core-interfaces/mode-edit-toggle-widget.md`), so a plain
        3-way `top_row.split(factor=0.30)` would have made the toggles'
        zone exactly as wide as the mesh dropdown's -- too wide for two
        small buttons, too narrow for a mesh name. Nesting a second
        `split()` inside the first split's remainder zone gives each of
        the three zones an independently-tunable width instead:
        `outer_split = top_row.split(factor=0.30)` -> `cycle_zone` (30%)
        and `rest_zone` (70%); `inner_split = rest_zone.split(factor=0.55)`
        -> `mesh_zone` (55% of the 70%) and `settings_zone` (the remaining
        45% of the 70%). Both factors (0.30, 0.55) are a first-pass guess,
        not measured against real button/label widths -- revisit if either
        zone reads as visibly too cramped or too roomy once seen in
        Blender.

        **Cycle button moved into the mesh-dropdown row (2026-09-14, per
        explicit user request):** the "[Object/Pose/Edit Bone]" cycle
        button used to sit in its own row below both lists, split with
        "Edit Mask" (`factor=0.35`, cycle left / mask right). It now sits
        in the TOP row instead, at the LEFT of `outer_split`. "Edit Mask"
        stays behind in its own row below the lists, alone, still
        right-aligned via `mask_zone.alignment = 'RIGHT'` -- it was not
        part of this move. The cycle button's own `cycle_zone.scale_y =
        1.4` preserves its previous enlarged look without inflating
        anything else in the row (each `split()` zone carries its own
        `scale_y` independently).

        **"Settings"/"How to use" relocated here (2026-09-14, per explicit
        user request, in two steps the same day):** this control used to
        be `interface/panel_main.py`'s `_draw_settings_row()`, drawn
        unconditionally at the very top of the whole sidebar panel, before
        either tab's body -- so it was visible even before activation and
        regardless of which tab/mode was showing. It first moved to its
        own row directly below the mesh dropdown; a further request the
        same day merged it INTO that row instead, sharing `outer_split`'s
        remainder zone with the mesh dropdown (see "Three zones in one
        row" above). Reached via `UnifiedRegistry.draw_settings_toggle_row(
        settings_zone, context)` (`interface/registry/register_api.py`) --
        the sanctioned wrapper around
        `interface.widget_preferences.draw_settings_toggle_row()`, since
        this domain (like every `features/*` package) is forbidden from
        importing `interface.widget_preferences` directly. That function
        sets its own inner row's `alignment = 'RIGHT'`, which -- combined
        with `settings_zone`'s fixed split width -- hugs both buttons to
        this row's right edge. Accepted tradeoff, confirmed with the user
        before making this change: the row (and "How to use" with it) now
        only renders wherever this domain's own top row does -- after
        activation, on the LAYER/SKINNING tab, once a mesh is resolved --
        instead of always, on every tab, even before activation.

        **Mesh dropdown drawn as its own standalone row-zone (2026-09-13,
        per explicit user request):** no longer routed through
        `draw_layer_list()`'s `top_fn` hook (it used to land inside the
        Layer list's own `col_list`, matching that list's column width
        exactly) -- it now draws directly into `layout` here, independent
        of either list's internal column layout. It reuses
        ``layer_viewer/object_selector.py``'s exact same
        ``draw_object_selectors()`` call `LayerViewerFeature.draw_section()`
        used to run -- inlined here rather than calling that method, since
        this combined draw also needs to insert the Deform Bones list and
        skip that method's own (now redundant) widget row. Per explicit
        user request, the dropdown is always the same "bind mesh" control
        in both Object and Edit Mode -- it no longer swaps to an
        active-Layer Label while editing, since the mesh can't actually be
        swapped out from under a live Edit Mode session anyway; it is
        simply locked (drawn disabled) instead, so its current value stays
        visible without inviting a click that would go nowhere. The cycle
        button next to it is NOT disabled while editing -- it's the only
        way to exit the Edit Layer Weight session.
        """
        obj = object_selector.get_effective_mesh(context)
        is_edit = obj is not None and obj.mode == 'EDIT'
        needs_init = obj is not None and not _has_layer_system(obj)

        # Cycle button, Mesh dropdown, and the Settings/How-to-use toggles
        # all share this one top row now (2026-09-14, per explicit user
        # request putting all four in the same row instead of the toggles
        # sitting on their own row below). Two nested `split()`s, since a
        # single `split(factor=X)` only fixes the FIRST zone's width --
        # every zone after it splits whatever's left evenly (see the
        # "Layout" note in docs/domains/deform_layer_viewer.md), which
        # would make the Settings zone as wide as the Mesh dropdown zone.
        # Nesting a second `split()` inside the first's remainder zone
        # gives each of the three zones its own independent width instead.
        top_row = layout.row(align=True)
        outer_split = top_row.split(factor=0.30)
        cycle_zone = outer_split.row()
        cycle_zone.scale_y = 1.4
        draw_mode_edit_toggle(
            cycle_zone, context,
            pose_toggle_idname="superskin.layer_enter_pose_mode",
            enter_edit_idname="superskin.enter_layer_edit",
            enter_edit_text="Init & Weight Paint" if needs_init else "Weight Paint",
            edit_mask_idname="superskin.toggle_mask_mode",
            edit_mask_text="Edit Mask",
            edit_mask_enter_text="Init & Edit Mask" if needs_init else "Edit Mask",
            mask_state_obj=obj,
            # "Edit Mask" is still drawn as its own separate button below.
            show_edit_mask=False,
            # Pins the Pose-state click to exactly this dropdown-resolved
            # mesh instead of letting _enter_edit_mode() re-derive its own
            # target from the per-armature scene cache -- see
            # docs/domains/controller.md's "Force Pose Mode: stale
            # per-armature 'last mesh' cache" section.
            target_mesh_obj=obj,
        )

        rest_zone = outer_split.row(align=True)
        inner_split = rest_zone.split(factor=0.55)
        mesh_zone = inner_split.row(align=True)
        if is_edit:
            mesh_zone.enabled = False
        object_selector.draw_object_selectors(mesh_zone, context)

        # "Settings"/"How to use" toggles, right-hugging this same row's
        # far right zone -- reached only through the sanctioned
        # UnifiedRegistry wrapper, since this domain never imports
        # interface.widget_preferences directly, per the Interface
        # closed-subsystem rule (docs/core-interfaces/interface.md).
        # draw_settings_toggle_row() sets its own inner row's
        # `alignment = 'RIGHT'`, which -- combined with this zone's fixed
        # split width -- hugs both buttons to this zone's (and therefore
        # the whole top row's) right edge. A direct consequence of the
        # move out of panel_main.py: this row (and "How to use" with it)
        # now only renders wherever this domain's own top row does --
        # after activation, on the LAYER/SKINNING tab, once a mesh is
        # resolved -- rather than unconditionally on every tab.
        settings_zone = inner_split.row(align=True)
        UnifiedRegistry.draw_settings_toggle_row(settings_zone, context)

        # Breathing room below the shared top row, before the two lists
        # (2026-09-14, per explicit user request) -- top_row previously ran
        # straight into draw_lists_side_by_side() with no gap at all.
        layout.separator(factor=0.5)

        # Deform Bones list (left) and Layer list (right) side by side in
        # one row, per explicit user request. The equal-width split itself
        # (2026-09-13, per a later explicit user request) is no longer
        # hand-rolled here -- `draw_lists_side_by_side()`
        # (`interface/template_ui/layout.py`) is the shared template's own
        # native two-list layout, called once with both lists' own draw
        # wrappers, rather than this call site building
        # `layout.split(factor=0.5, align=True)` and two `.column()` calls
        # itself and drawing each list into its own half separately. See
        # that function's docstring for why `split(factor=0.5, align=True)`
        # specifically is required for each list's own button toolbar to
        # stay aligned with its list/search box below it.
        draw_lists_side_by_side(
            layout,
            left=(draw_influence_list_system, dict(context=context, rows=10, obj=obj)),
            right=(draw_layer_list, dict(context=context, rows=10, obj=obj)),
        )
        layout.separator(factor=0.4)

        # "Edit Mask" alone now -- the cycle button moved up into top_row
        # above (per explicit user request), so this row only ever holds
        # the right-hugging "Edit Mask" button (empty while not editing,
        # see draw_edit_mask_button()'s own docstring).
        #
        # SKINNING tab only (2026-09-15, per explicit user request): this
        # button now also lives in weight_apply's own N-panel column (see
        # `features/skin_tools_ui/ui_weight_apply.py::_draw_tool_column()`),
        # which is only ever drawn on the SKINNING tab. Drawing it here too
        # on that same tab would show it twice, so it's skipped there --
        # the LAYER tab still draws it here since weight_apply's panel
        # doesn't exist on that tab at all.
        if tab_key != "SKINNING":
            row = layout.row()
            row.scale_y = 1.4
            mask_zone = row.row()
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
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the merged feature with UnifiedRegistry."""
    UnifiedRegistry.register(DeformLayerViewerFeature())


def unregister():
    """Unregister the merged feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("deform_layer_viewer")
