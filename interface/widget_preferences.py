"""SuperSkinPro N-panel sidebar — interface-split UI (no tab bar).

The panel adapts its content to ``WindowManager.superskin_active_interface``
(owned by ``panel_main.py``), NOT to the current Blender interaction mode:

  LAYER    — collapsible LAYER sections (LayerViewer with entry gate,
             weight_transfer — which also owns Export/Import JSON).

  SKINNING — collapsible SKINNING sections (DeformBoneViewer with exit gate,
             weight_apply, mirror, …).

This state is deliberately decoupled from ``context.mode`` — see
``features/controller/ops_scene_modes.py`` for the three points where it
flips (Edit Layer Weight, Save Weights, and the auto-save guard's
unguarded-Tab-exit detection).

System settings plus PREFERENCE-tab feature extensions (including the
``debug_console`` domain, which now owns the per-category debug log toggles)
are hosted in the settings popover (``SUPERSKIN_PT_settings_popup``,
opened from the gear icon in ``panel_main.py``'s top row), rendered by
``draw_preferences_body()``. License activation lives in its own compact
row in the top row itself (see ``features/activate/README.md``), not here.

All preference *data* lives on ``WindowManager.superskin_prefs`` (core) or on
feature-domain PointerProperties. This module only draws; it holds no state.

Feature domains register via ``UnifiedRegistry`` (Unified Component Architecture).
Each ``UnifiedFeatureExtension`` exposes ``draw_section(layout, context)``,
``is_collapsible()``, ``get_section_title()``, and ``get_draw_tabs()``.
"""


# =========================================================================
#  Primary entry point — called from panel_main.draw()
# =========================================================================

def draw_mode_split_ui(layout, context):
    """Render either the Layer or Skinning interface based on
    ``context.window_manager.superskin_active_interface`` (not context.mode)."""
    prefs = context.window_manager.superskin_prefs
    active = context.window_manager.superskin_active_interface
    if active == 'LAYER':
        _draw_layer_interface(layout, context, prefs)
    elif active == 'SKINNING':
        _draw_skinning_interface(layout, context, prefs)


# =========================================================================
#  State A: Layer interface — collapsible LAYER spec sections
# =========================================================================

def _draw_layer_interface(layout, context, prefs):
    """Layer-interface content."""
    _draw_viewer_spec(layout, context, 'LAYER')
    _draw_tool_specs(layout, context, 'LAYER')


# =========================================================================
#  State B: Skinning interface — collapsible SKINNING spec sections
# =========================================================================

def _draw_skinning_interface(layout, context, prefs):
    """Skinning-interface content.

    Initialization is the LAYER tab's own explicit step (`superskin.layer_init`)
    -- this used to silently auto-initialize any active mesh lacking a layer
    system, which meant re-selecting a mesh right after "Clean-up" (LAYER tab)
    would immediately reseed a fresh "Base" layer behind the user's back. The
    SKINNING-tab specs below already tolerate a missing layer system on their
    own (empty influence list, "-" active layer label, poll-gated buttons)."""
    _draw_viewer_spec(layout, context, 'SKINNING')
    _draw_tool_specs(layout, context, 'SKINNING')


# =========================================================================
#  Spec-section draw helpers — Unified Component Architecture
# =========================================================================

def _draw_viewer_spec(layout, context, tab_key):
    """Draw the first non-collapsible spec (the list viewer widget) for *tab_key*."""
    from .registry.register_api import UnifiedRegistry

    for ext in UnifiedRegistry.get_by_tab(tab_key):
        if not ext.is_collapsible():
            ext.draw_section_for_tab(layout, context, tab_key)
            return


def _draw_tool_specs(layout, context, tab_key):
    """Draw all collapsible tool specs for *tab_key* with separators."""
    from .registry.register_api import UnifiedRegistry

    for ext in UnifiedRegistry.get_by_tab(tab_key):
        if ext.is_collapsible():
            layout.separator(factor=0.2)
            _draw_collapsible_box_ext(layout, context, ext, tab_key)


# =========================================================================
#  Collapsible section helper
# =========================================================================

def _draw_collapsible_box_ext(layout, context, ext, tab_key, force_locked_expanded=False):
    """Draw a section for a ``UnifiedFeatureExtension`` under *tab_key*.

    Two modes, both showing ``ext.get_section_title()`` as a header unless
    ``ext.is_section_label_shown()`` is ``False``:
      - ``ext.is_locked_expanded()`` (or *force_locked_expanded*): plain,
        non-interactive label -- no collapse arrow, not clickable -- with
        the body always drawn.
      - Otherwise: the normal collapsible ``layout.panel()`` with a
        Blender-managed identifier derived from ``ext.get_id()`` so
        expand/collapse state persists across redraws.

    *force_locked_expanded* overrides the extension's own
    ``is_locked_expanded()`` without touching its class attribute -- used by
    ``_draw_preferences()`` to make every section in the settings popover
    non-collapsible regardless of how each individual domain is configured,
    without having to edit every domain's own file to set
    ``locked_expanded = True``. The SKINNING/LAYER tool-spec loop
    (``_draw_tool_specs()``) does not pass this, so those sections keep
    their per-domain collapsible/locked-expanded behavior unchanged.

    *tab_key* is forwarded to ``ext.draw_section_for_tab()`` so an
    extension registered under multiple tabs can draw different content
    in each one; single-tab extensions can ignore it (the default
    ``draw_section_for_tab()`` implementation already does).
    """
    if force_locked_expanded or ext.is_locked_expanded():
        if ext.is_section_label_shown():
            # Colon suffix so this visibly reads as a plain static caption
            # rather than a (non-functional) collapsible header -- there's
            # no disclosure arrow to signal that on its own the way
            # layout.panel()'s header row does.
            layout.label(text=f"{ext.get_section_title()}:")
        ext.draw_section_for_tab(layout, context, tab_key)
        return

    domain_id = ext.get_id()
    panel_id = f"superskin_{domain_id}_section"
    header, body = layout.panel(panel_id, default_closed=not ext.is_expanded_by_default())
    if ext.is_section_label_shown():
        header.label(text=ext.get_section_title())
    if body is not None:
        ext.draw_section_for_tab(body, context, tab_key)


# =========================================================================
#  Preference section entry point (interface/panel_main.py)
# =========================================================================

def draw_preferences_body(layout, context):
    """Draw the System/Customize section inside the settings popover
    (``SUPERSKIN_PT_settings_popup``, see ``panel_main.py``).

    Formerly hosted in Blender's native Add-on Preferences window; moved here
    so users don't have to leave the viewport to reach these settings.
    """
    prefs = context.window_manager.superskin_prefs
    _draw_preferences(layout, context, prefs)


# =========================================================================
#  Preference body — visual customization + feature extensions + system/debug
# =========================================================================

def _draw_preferences(layout, context, prefs):
    """Preference panel body: feature extensions, system actions. Per-category
    debug log toggles and the live log view live in the ``debug_console``
    feature extension, drawn as part of the PREFERENCE-tab extensions loop
    below, not hardcoded here.

    The former "Single Mode Color Ramp" / "Mask / Layer Color Ramp" /
    "Multi Mode Color Palette" hardcoded boxes that used to be drawn here
    (formerly the standalone CUSTOMIZE tab) are all gone; two of the three
    concepts live on today in the `overlay_color` feature domain (see
    features/overlay_color/README.md) -- its own weight/mask edit ramps,
    drawn with Blender's native `template_color_ramp()` widget, plus the
    Alt+3 Multi Color Preview toggle, both merged into that one domain's
    PREFERENCE-tab section (drawn through the normal extensions loop below,
    no special-casing here). The third ("Multi Mode Color Palette") was
    removed outright rather than migrated.

    License activation is intentionally NOT drawn here — it's a compact row
    in ``panel_main.py``'s top row instead (see
    ``features/activate/README.md``), reachable without opening this
    popover at all. The addon-update checker's full-detail section is
    likewise not drawn here (or anywhere in this popover) — only the top
    row's compact control remains, see ``features/addon_updater/README.md``.

    Every section drawn by this function passes
    ``force_locked_expanded=True`` to ``_draw_collapsible_box_ext()``, so
    each one renders as a plain, non-collapsible label with its body always
    shown, regardless of that domain's own ``locked_expanded`` setting.
    """
    from .registry.register_api import UnifiedRegistry

    layout.use_property_decorate = False

    # `debug_console` pinned above the PREFERENCE-tab extensions loop.
    # UnifiedFeatureExtension.priority only sorts extensions *within* a tab's
    # own loop (see UnifiedRegistry.get_by_tab()) -- there is no generic hook
    # for an extension to ask to be drawn before this function's hardcoded
    # content, so this is a deliberate, explicit special case rather than a
    # reusable mechanism. Excluded from the extensions loop below to avoid a
    # duplicate draw. force_locked_expanded=True so every section in the
    # settings popover reads as a plain, non-collapsible label -- nothing
    # here can be hidden away by accident.
    debug_console_ext = UnifiedRegistry.get_by_id("debug_console")
    if debug_console_ext is not None:
        _draw_collapsible_box_ext(layout, context, debug_console_ext, 'PREFERENCE', force_locked_expanded=True)
        layout.separator(factor=0.2)

    # PREFERENCE-tab extensions from feature domains (e.g. Bone Picker Colors,
    # VGColor). `debug_console` is pinned above and excluded here to avoid a
    # double draw. `addon_updater` is not drawn anywhere in this popover at
    # all anymore -- its full-detail section was removed once the top row's
    # compact update control (see `_draw_top_row()` in panel_main.py) became
    # the only update-checking entry point -- see
    # features/addon_updater/README.md's "Placement" section; still excluded
    # here by id in case its `draw_tab` metadata ever gets treated as a
    # signal to draw it generically. `activate` draws nothing here at all
    # either (its content lives in panel_main.py's top row instead, see
    # features/activate/README.md) but is still excluded for documentation
    # consistency with the other two.
    for ext in UnifiedRegistry.get_by_tab('PREFERENCE'):
        if ext.get_id() in ("debug_console", "addon_updater", "activate"):
            continue
        layout.separator(factor=0.2)
        _draw_collapsible_box_ext(layout, context, ext, 'PREFERENCE', force_locked_expanded=True)

    layout.separator(factor=0.4)
    layout.label(text="System Actions:")
    layout.operator("superskin.reset_license_activation", text="Reset All Activate", icon='TRASH')
    # Only draw "Save As Default" while at least one domain still opts into
    # supports_dev_override -- currently none do (overlay_color/bone_picker
    # both opted out, always live-saving straight to user.json instead), so
    # this button stays hidden rather than sit there doing nothing.
    if any(ext.supports_developer_override() for ext in UnifiedRegistry.get_all()):
        layout.operator("superskin.override_dev_defaults", text="Save As Default", icon='FILE_TICK')
