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

**LAYER/SKINNING top-level dispatch moved out to two registered domains
(2026-09-14).** ``draw_mode_split_ui()`` / ``_draw_layer_interface()`` /
``_draw_skinning_interface()`` / ``_draw_viewer_spec()`` / ``_draw_tool_specs()``
used to live in this module and be called directly from
``interface.panel_main._draw_skin_tab()``. Per explicit user request, that
whole loop (iterate ``UnifiedRegistry.get_by_tab(tab_key)``, draw the first
non-collapsible viewer spec, then every collapsible tool spec with
separators) now lives inside ``features/object_tools_ui/`` (LAYER) and
``features/skin_tools_ui/`` (SKINNING) themselves — each is a real
registered ``UnifiedFeatureExtension`` whose ``draw_section()`` does the
looping itself, using only the public ``UnifiedRegistry`` API (``get_by_tab()``
and the new ``draw_collapsible_section()`` wrapper below) to reach the
individual domains' own content and the shared collapsible-box chrome. This
module no longer owns that top-level loop at all — see
``docs/domains/object_tools_ui.md`` / ``docs/domains/skin_tools_ui.md``.
``draw_collapsible_box_ext()`` / ``_draw_section_header()`` /
``_draw_link_button()`` below are unchanged and still used by the
PREFERENCE-tab body (``_draw_preferences()``, further down this file) —
``UnifiedRegistry.draw_collapsible_section()`` is a new, purely additive
public wrapper around ``draw_collapsible_box_ext()``, reusing the exact
same chrome-drawing code rather than duplicating it.

System settings plus PREFERENCE-tab feature extensions draw inside a popup
-- opened by the "Settings" button in ``draw_settings_toggle_row()`` (a
``wm.call_panel`` trigger for ``SUPERSKIN_PT_settings_popup``,
``interface/ops_preferences.py`` -- see that function's own docstring for
the inline-toggle shape this reverted from, 2026-09-14), rendered by
``draw_preferences_body()``. ``debug_console`` (per-category debug log
toggles) and ``profiler`` moved OUT of this popup into their own
``draw_tab = "DEV_DEBUG"`` -- a separate, always-visible, non-activation-gated
docked panel (``VIEW3D_PT_superskin_dev_debugger``, ``panel_dev_debugger.py``)
stacked below the main panel, drawn by ``draw_dev_debugger_body()`` further
down this file -- see that function's docstring for why. License
activation lives in its own compact row in the header label row itself
(see ``docs/domains/activate.md``), not here. The docs link ("How to use")
is also NOT drawn here anymore -- it's the other half of
``draw_settings_toggle_row()``'s row (still a plain inline toggle, unlike
"Settings"), opening ``_DOCS_URL``.

All preference *data* lives on ``WindowManager.superskin_prefs`` (core) or on
feature-domain PointerProperties. This module only draws; it holds no state.

Feature domains register via ``UnifiedRegistry`` (Unified Component Architecture).
Each ``UnifiedFeatureExtension`` exposes ``draw_section(layout, context)``,
``is_collapsible()``, ``get_section_title()``, and ``get_draw_tabs()``.
"""

from .utils import icons as _icons

# Temporary global kill-switch for every section header's link/info button
# (the icon opening `ext.link`) while testing header layouts -- set back to
# True to restore. Each domain's own `link` attribute is untouched; this
# only stops widget_preferences.py from drawing anything for it.
_LINK_BUTTONS_ENABLED = False

# Canonical home for these three -- moved here from panel_main.py
# (2026-09-14, per explicit user request relocating the "Settings"/"How to
# use" toggle row itself, see draw_settings_toggle_row() below) since
# _draw_how_to_use_body() is the only remaining reader of any of them.
_DOCS_URL = "https://docs.superskinpro.com/"
_DISCORD_URL = "https://discord.gg/BCEJZBnTfM"
_TUTORIALS_URL = None


# =========================================================================
#  Collapsible section helper — shared chrome, reused by object_tools_ui /
#  skin_tools_ui (via UnifiedRegistry.draw_collapsible_section()) and by
#  the PREFERENCE-tab body (_draw_preferences(), further down this file)
# =========================================================================

def draw_collapsible_box_ext(layout, context, ext, tab_key, force_locked_expanded=False, draw_body=None):
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
    ``_draw_preferences()`` to make every section in the Preferences panel
    non-collapsible regardless of how each individual domain is configured,
    without having to edit every domain's own file to set
    ``locked_expanded = True``. The SKINNING/LAYER tool-spec loops (now in
    ``features/object_tools_ui/`` / ``features/skin_tools_ui/``, reached via
    ``UnifiedRegistry.draw_collapsible_section()``) do not pass this, so
    those sections keep their per-domain collapsible/locked-expanded
    behavior unchanged.

    *draw_body*, when given, is called as ``draw_body(layout, context)``
    for the actual body content INSTEAD of ``ext.draw_section_for_tab(...)``
    -- used by ``object_tools_ui``/``skin_tools_ui``, whose migrated domains
    no longer implement real drawing on their own class (their
    ``draw_section_for_tab()`` is a stub) -- the actual widget code now
    lives in a ``features/object_tools_ui/ui_<name>.py`` /
    ``features/skin_tools_ui/ui_<name>.py`` module instead, supplied here as
    a plain callable. *ext* is still consulted for every metadata call in
    this function (section title, locked/collapsible state, link) -- only
    the body content itself is redirected. Omitted (the default) preserves
    the exact previous behavior for every other caller (``_draw_preferences()``
    and any not-yet-migrated domain).

    *tab_key* is forwarded to ``ext.draw_section_for_tab()`` (or *draw_body*)
    so an extension registered under multiple tabs can draw different
    content in each one; single-tab extensions can ignore it (the default
    ``draw_section_for_tab()`` implementation already does).
    """
    body_fn = draw_body if draw_body is not None else (
        lambda body_layout, body_context: ext.draw_section_for_tab(body_layout, body_context, tab_key)
    )

    if force_locked_expanded or ext.is_locked_expanded():
        if ext.is_section_label_shown():
            # Colon suffix so this visibly reads as a plain static caption
            # rather than a (non-functional) collapsible header -- there's
            # no disclosure arrow to signal that on its own the way
            # layout.panel()'s header row does.
            _draw_section_header(layout, ext, suffix=":")
        body_fn(layout, context)
        return

    domain_id = ext.get_id()
    panel_id = f"superskin_{domain_id}_section"
    header, body = layout.panel(panel_id, default_closed=not ext.is_expanded_by_default())
    if ext.is_section_label_shown():
        _draw_section_header(header, ext, right_align=True)
    if body is not None:
        body_fn(body, context)


def _draw_section_header(container, ext, *, suffix="", right_align=False):
    """Draw ``ext.get_section_title()`` (plus optional *suffix*) and the
    link/info button -- the single shared template every tool section's
    header uses, collapsible or ``locked_expanded`` alike. Both branches
    push the link button to the row's right edge; they just get there
    differently since *container* is a different kind of layout in each
    case.

    *right_align* picks which of two layouts is used:

    - ``False`` (default -- the ``locked_expanded`` plain-caption case,
      where *container* is a plain box/column, not a native panel header,
      so there is no built-in "push the second child right" behavior to
      lean on): when ``ext.get_link()`` is set, a fixed-ratio
      ``container.split(factor=0.85)`` puts the title in the 85% left zone
      (``alignment = 'LEFT'``) and the link button alone in the remaining
      15% right zone (``alignment = 'RIGHT'``) -- the same idiom
      ``panel_main.py``'s top row and
      ``features/tool_socket/tool_socket_feature.py``'s own right-hugging
      info button already use. ``row.separator_spacer()`` was tried for
      this exact kind of right-alignment elsewhere in this codebase and
      reverted -- it made Blender miscompute the N-panel sidebar's
      required width (see ``panel_main.py``'s ``_draw_top_row()``
      docstring) -- so a fixed-ratio ``split()`` is used instead, which has
      no such issue. When there's no link to draw, the split is skipped
      entirely and the title is drawn in a plain left-aligned row, since a
      split with an empty second zone buys nothing.
    - ``True`` (the normal collapsible ``layout.panel()`` header case,
      where *container* is the ``header`` sub-layout ``layout.panel()``
      returns): title and button are drawn as two separate top-level calls
      directly on *container* instead, which lets Blender's native
      panel-header layout do the work -- it gives the header's *first*
      top-level child the flexible/growing width share and pushes any
      *second* child all the way to the right edge (the same convention
      that puts a Modifier's enable checkbox at the far right of its
      header), so the link button ends up right-aligned in the header row
      with no manual split needed.
    """
    if right_align:
        container.label(text=f"{ext.get_section_title()}{suffix}")
        _draw_link_button(container, ext)
        return

    if not (_LINK_BUTTONS_ENABLED and ext.get_link()):
        title_row = container.row(align=True)
        title_row.alignment = 'LEFT'
        title_row.label(text=f"{ext.get_section_title()}{suffix}")
        return

    split = container.split(factor=0.85)
    title_zone = split.row(align=True)
    title_zone.alignment = 'LEFT'
    title_zone.label(text=f"{ext.get_section_title()}{suffix}")
    link_zone = split.row(align=True)
    link_zone.alignment = 'RIGHT'
    _draw_link_button(link_zone, ext)


def _draw_link_button(layout, ext):
    """Draw an icon button opening ``ext.get_link()`` if set, using the
    custom help icon (``assets/icon_help.png``, loaded by
    ``interface.utils.icons``) in place of a built-in icon. Falls back to
    the built-in ``INFO`` icon if the custom one failed to load (e.g. the
    asset file is missing) rather than drawing no icon at all.

    Normal emboss (not ``emboss=False``) -- reads as an actual clickable
    button rather than a bare icon glyph floating in the header.

    No-op when the extension didn't set ``link`` -- most domains never draw
    anything here. Also a no-op while ``_LINK_BUTTONS_ENABLED`` is ``False``
    (temporary global kill-switch, see that module-level constant).
    """
    if not _LINK_BUTTONS_ENABLED:
        return
    link = ext.get_link()
    if not link:
        return
    icon_id = _icons.get_help_icon_id()
    op_layout = layout.operator(
        "wm.url_open", text="",
        **({"icon_value": icon_id} if icon_id else {"icon": 'INFO'}),
    )
    op_layout.url = link


# =========================================================================
#  Settings / How-to-use toggle row -- relocatable entry point
# =========================================================================

def draw_settings_toggle_row(layout, context, activated: bool):
    """A row holding the "Settings" popover trigger and the "How to use"
    toggle, side by side.

    **"Settings" is a popup again (2026-09-14, per explicit user request
    reverting the previous inline-expanding-toggle shape):** a plain
    ``wm.call_panel`` operator button opening ``SUPERSKIN_PT_settings_popup``
    (``interface/ops_preferences.py``) -- the same trigger idiom
    ``draw_preferences_body()``'s own "Edit Shortcuts" button uses for
    ``SUPERSKIN_PT_shortcuts_editor`` (a plain operator button rather than
    ``layout.popover()``, so no dropdown-arrow decoration is drawn next to
    the gear/lock icon). ``draw_preferences_body()`` itself is unchanged --
    only where it's drawn (inside the popover's own ``Panel.draw()`` now,
    not inline below this row) moved.
    ``WindowManager.superskin_settings_expanded`` and its mutual-exclusivity
    ``update=`` callback are gone entirely (removed from
    ``panel_main.py``'s ``register()``) -- a popup has no persistent
    expanded state to store, and nothing is left for "How to use" to stay
    mutually exclusive with.

    "How to use" is UNCHANGED -- still a plain
    ``WindowManager.superskin_how_to_use_expanded`` bool toggle expanding
    ``_draw_how_to_use_body()`` inline right below this row (no popup),
    just without the mutual-exclusivity ``update=`` callback anymore (moot
    now that "Settings" is not a sibling toggle to stay exclusive with).

    Moved here from ``panel_main.py``'s ``_draw_settings_row()``
    (2026-09-14, per explicit user request relocating the row itself out
    of its old fixed position at the very top of the panel, to sit right
    after the "bind mesh" dropdown in ``deform_layer_viewer_feature.py``'s
    own top row instead) -- this is the sanctioned entry point
    ``UnifiedRegistry.draw_settings_toggle_row()`` (`interface/registry/
    register_api.py`) wraps so a feature domain can call it without
    importing this module directly (`interface.widget_preferences` stays
    off-limits to `features/*` per the Interface closed-subsystem rule --
    see `docs/core-interfaces/interface.md`'s Invariants).

    "Settings" being disabled (and icon-swapped to ``LOCKED``) until
    *activated* is unchanged from the original -- *activated* is passed in
    by the caller (``UnifiedRegistry.draw_settings_toggle_row()`` reads it
    from ``CoreFacade.is_system_activated()``) rather than recomputed here,
    so this module stays a pure drawing layer with no ``core/`` import of
    its own.

    No ``layout.box()`` wrapper on the row itself -- consistent with the
    flat, borderless style used throughout this panel. "How to use"'s
    expanded body still draws inside its own fresh ``layout.box()``.

    **Right-aligned (2026-09-14, per explicit user request):** ``toggle_row
    .alignment = 'RIGHT'`` makes both buttons size to their natural
    (label) width instead of stretching to fill the row, and pushes that
    shrunk group to the row's right edge -- the same plain
    ``row.alignment = 'RIGHT'`` idiom ``features/weight_apply/ui.py``'s
    "Smooth Affected Only" toggle already uses. No ``split()`` needed here
    (unlike the ``cycle_zone``/``mask_zone`` pairs elsewhere in this
    codebase) since there's nothing else sharing this row that needs its
    own reserved left-hand zone.
    """
    wm = context.window_manager

    toggle_row = layout.row(align=True)
    toggle_row.alignment = 'RIGHT'
    # Matches the "Object/Pose/Edit Bone" cycle button's own row height
    # (2026-09-14, per explicit user request) -- both live in the same
    # shared top row at the call site, so mismatched heights read as
    # misaligned buttons.
    toggle_row.scale_y = 1.4
    # A bit wider than Blender's default icon-only button width
    # (2026-09-14, per explicit user request) -- now that these are
    # icon-only (text=""), the two buttons read as cramped at scale_x=1.0.
    # `scale_x` widens the buttons themselves without affecting `row`'s
    # `alignment='RIGHT'` shrink-to-content/right-hug behavior.
    toggle_row.scale_x = 1.3
    settings_toggle = toggle_row.row(align=True)
    settings_toggle.enabled = activated
    # Icon-only (2026-09-14, per explicit user request) -- text="" drops
    # the label, leaving just the gear/lock icon.
    settings_btn = settings_toggle.operator(
        "wm.call_panel", text="",
        icon='PREFERENCES' if activated else 'LOCKED',
    )
    settings_btn.name = "SUPERSKIN_PT_settings_popup"
    settings_btn.keep_open = True
    toggle_row.prop(
        wm, "superskin_how_to_use_expanded", text="",
        icon='HELP', toggle=True,
    )

    if wm.superskin_how_to_use_expanded:
        _draw_how_to_use_body(layout.box(), context)


def _draw_how_to_use_body(layout, context):
    """Four-button body expanded by the "How to use" toggle above:
    "Quick Start Guide" (the in-app, image-driven walkthrough from the
    ``builtin_tutorial`` feature domain -- see
    ``docs/domains/builtin_tutorial.md`` -- drawn by looking up its
    extension in ``UnifiedRegistry`` and calling ``draw_section()``
    directly, same mechanism ``support_report`` uses for its own button in
    ``draw_preferences_body()`` above), "Quick Start Tutorials" (a future
    YouTube link -- ``_TUTORIALS_URL`` is ``None`` until one is published,
    so this button stays disabled with a "(Coming Soon)" label rather than
    opening a dead/placeholder URL), "Read Documentation" (``_DOCS_URL``),
    and "Join Discord Server" (``_DISCORD_URL``, the same invite link
    documented in ``documents/docs/faq.md``'s "Where can I ask further
    questions or report issues?" entry).

    Moved here from ``panel_main.py`` alongside ``draw_settings_toggle_row()``
    above -- see that function's docstring for why.
    """
    from .registry.register_api import UnifiedRegistry

    builtin_tutorial_ext = UnifiedRegistry.get_by_id("builtin_tutorial")
    if builtin_tutorial_ext is not None:
        builtin_tutorial_ext.draw_section(layout, context)

    tutorials_row = layout.row()
    tutorials_row.enabled = _TUTORIALS_URL is not None
    tutorials_row.operator(
        "wm.url_open",
        text="Quick Start Tutorials" if _TUTORIALS_URL else "Quick Start Tutorials (Coming Soon)",
        icon='PLAY',
    ).url = _TUTORIALS_URL or ""
    layout.operator("wm.url_open", text="Read Documentation", icon='URL').url = _DOCS_URL
    layout.operator("wm.url_open", text="Join Discord Server", icon='URL').url = _DISCORD_URL


# =========================================================================
#  Preference section entry point
# =========================================================================

def draw_preferences_body(layout, context):
    """Draw the System/Customize section inline, right below the
    "Settings" toggle drawn by ``draw_settings_toggle_row()`` above -- no
    popup/popover involved.

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
    feature extension, which no longer draws here at all -- see
    ``draw_dev_debugger_body()`` further down this file.

    The former "Single Mode Color Ramp" / "Mask / Layer Color Ramp" /
    "Multi Mode Color Palette" hardcoded boxes that used to be drawn here
    (formerly the standalone CUSTOMIZE tab) are all gone; two of the three
    concepts live on today in the `overlay_color` feature domain (see
    docs/domains/overlay_color.md) -- its own weight/mask edit ramps,
    drawn with Blender's native `template_color_ramp()` widget, plus the
    Alt+3 Multi Color Preview toggle, both merged into that one domain's
    PREFERENCE-tab section (drawn through the normal extensions loop below,
    no special-casing here). The third ("Multi Mode Color Palette") was
    removed outright rather than migrated.

    License activation is intentionally NOT drawn here — it's a compact row
    in ``panel_main.py``'s top row instead (see
    ``docs/domains/activate.md``), reachable without toggling "Settings"
    on at all. The addon-update checker's full-detail section is
    likewise not drawn here (or anywhere in this section) — only the top
    row's compact control remains, see ``docs/domains/addon_updater.md``.
    The docs link ("Documentation") that used to sit at the bottom of this
    body is also gone -- it's the "How to use" button in
    ``draw_settings_toggle_row()`` now, a sibling of the "Settings" toggle
    that expands this body, so it's reachable without expanding Settings
    first.

    Every section drawn by this function passes
    ``force_locked_expanded=True`` to ``draw_collapsible_box_ext()``, so
    each one renders as a plain, non-collapsible label with its body always
    shown, regardless of that domain's own ``locked_expanded`` setting.
    """
    from .registry.register_api import UnifiedRegistry

    layout.use_property_decorate = False

    # PREFERENCE-tab extensions from feature domains (e.g. Bone Picker Colors,
    # VGColor). `debug_console`/`profiler` no longer register under this tab
    # at all -- both moved to `draw_tab = "DEV_DEBUG"`, drawn instead by
    # `draw_dev_debugger_body()` below inside the standalone Dev Debugger
    # panel (`panel_dev_debugger.py`), so there is nothing to exclude for
    # them here anymore. `addon_updater` is not drawn anywhere in this panel
    # at all -- its full-detail section was removed once the top row's
    # compact update control (see `_draw_top_row()` in panel_main.py) became
    # the only update-checking entry point -- see
    # docs/domains/addon_updater.md's "Placement" section; still excluded
    # here by id in case its `draw_tab` metadata ever gets treated as a
    # signal to draw it generically. `activate` draws nothing here at all
    # either (its content lives in panel_main.py's top row instead, see
    # docs/domains/activate.md) but is still excluded for documentation
    # consistency with the other two. `support_report` is excluded the same
    # way -- its button is drawn directly in the System Actions box below
    # (bare, no header/collapsible wrapper), per explicit request, instead
    # of through this generic loop. `builtin_tutorial` is excluded the same
    # way too -- its "Quick Start Guide" button is drawn directly inside
    # panel_main.py's "How to use" toggle body instead (see
    # docs/domains/builtin_tutorial.md), not this "Settings" body at all.
    for ext in UnifiedRegistry.get_by_tab('PREFERENCE'):
        if ext.get_id() in ("addon_updater", "activate", "support_report", "builtin_tutorial"):
            continue
        layout.separator(factor=0.2)
        draw_collapsible_box_ext(layout, context, ext, 'PREFERENCE', force_locked_expanded=True)

    layout.separator(factor=0.4)
    layout.label(text="System Actions:")
    # Opens SUPERSKIN_PT_shortcuts_editor (interface/ops_preferences.py) --
    # the full in-panel click-to-rebind list plus "Reset All Shortcuts",
    # both moved there so this row stays a single compact button instead
    # of a permanently-expanded list. Used to jump straight to Blender's
    # native Preferences > Keymap tab (screen.userpref_show,
    # section='KEYMAP') since SuperSkinPro's items live in Blender's own
    # built-in 'Mesh' keymap category and couldn't be deep-linked to a
    # custom section there -- no longer needed now that every shortcut is
    # editable in-panel via keymap_editor.py.
    # Plain operator button (wm.call_panel, the exact operator
    # layout.popover() invokes internally) instead of layout.popover()
    # itself -- popover() always draws a small dropdown-menu arrow next to
    # its icon (Blender's native "this opens a submenu" decoration), which a
    # plain operator button doesn't get. "Edit Shortcuts" is still its own
    # real popover (a full click-to-rebind list doesn't belong inline in
    # this already-long Settings body) -- unlike the bottom row's
    # "Settings" toggle, which no longer opens anything at all, see
    # panel_main.py's _draw_settings_row().
    shortcuts_btn = layout.operator(
        "wm.call_panel", text="Edit Shortcuts", icon='PREFERENCES',
    )
    shortcuts_btn.name = "SUPERSKIN_PT_shortcuts_editor"
    shortcuts_btn.keep_open = True
    # `support_report`'s own draw_section() is just this one bare button (no
    # label/header) -- called directly here instead of through the generic
    # PREFERENCE-tab loop above, which excludes this domain_id for exactly
    # this reason. Confirmation is handled by the operator's own invoke()
    # (superskin.export_support_report, see features/support_report/ops.py),
    # not drawn here.
    support_report_ext = UnifiedRegistry.get_by_id("support_report")
    if support_report_ext is not None:
        support_report_ext.draw_section(layout, context)
    layout.operator("superskin.reset_license_activation", text="Reset All Activate", icon='TRASH')
    # Only draw "Save As Default" while at least one domain still opts into
    # supports_dev_override -- currently none do (overlay_color/bone_picker
    # both opted out, always live-saving straight to user.json instead), so
    # this button stays hidden rather than sit there doing nothing.
    if any(ext.supports_developer_override() for ext in UnifiedRegistry.get_all()):
        layout.operator("superskin.override_dev_defaults", text="Save As Default", icon='FILE_TICK')


# =========================================================================
#  Dev Debugger panel body — DEV_DEBUG-tab extensions (debug_console, profiler)
# =========================================================================

def draw_dev_debugger_body(layout, context):
    """Draw every ``draw_tab = "DEV_DEBUG"`` extension's section, in order.

    Called by ``panel_dev_debugger.py``'s standalone
    ``VIEW3D_PT_superskin_dev_debugger`` panel -- a second, always-visible
    docked panel stacked below the main "Super Skin Pro" panel in the
    N-panel sidebar (same ``bl_category``), not inside the "Settings"
    popup. `debug_console` and `profiler` are the only two domains
    registered under this tab today (moved here from `PREFERENCE`, where
    `debug_console` used to be pinned above the generic loop and `profiler`
    drew through it) -- both are dev/diagnostic tools that need to keep
    working even when the rest of the addon can't (no license, no active
    mesh), which is exactly why they live in their own panel with no
    activation gate, instead of behind the "Settings" button (which itself
    is disabled until activated -- see ``draw_settings_toggle_row()``).

    Each extension keeps its own ``collapsible``/``locked_expanded``
    behavior (no ``force_locked_expanded`` override, unlike
    ``_draw_preferences()`` above) so Debug Console and Profiler can be
    expanded/collapsed independently of one another.
    """
    from .registry.register_api import UnifiedRegistry

    layout.use_property_decorate = False

    extensions = UnifiedRegistry.get_by_tab('DEV_DEBUG')
    for i, ext in enumerate(extensions):
        if i > 0:
            layout.separator(factor=0.2)
        draw_collapsible_box_ext(layout, context, ext, 'DEV_DEBUG')
