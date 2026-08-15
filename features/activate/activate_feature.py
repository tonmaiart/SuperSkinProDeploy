"""ActivateFeature — Unified Component Architecture implementation for the
activate domain.

Frontend-only consolidation: a compact license-entry prompt in its own box
(the "Activate to continue" label, a text field + narrow "Activate" button,
plus a "Where can I get activate key?" help row -- see
`draw_activate_prompt()`) drawn directly by `interface/panel_main.py`'s
`_draw_skin_tab()` in place of the main artwork body -- NOT inside the top
row's box. It renders only while the system is not yet activated -- once
activated it draws nothing at all, with no residual "Activated" indicator
left behind. The actual license/security backend is untouched -- this only
re-hosts a `layout.operator(...)` call by `bl_idname` string against the
existing `superskin.activate_license` operator (still defined in
`interface/ops_preferences.py`, unchanged), and reads the existing
`context.window_manager.superskin_prefs.license` PropertyGroup. The
"Reset All Activate" button (`superskin.reset_license_activation`) no
longer lives in this domain at all -- it moved to `widget_preferences.py`'s
"System Actions" box, next to "Reset to Default".

`draw_tab = "PREFERENCE"` is set for domain-registry bookkeeping/
documentation consistency only; the PREFERENCE-tab loop in
`widget_preferences.py` explicitly skips this domain's id to avoid a double
draw (mirrors how `debug_console`/`addon_updater` are already excluded
there), even though this domain draws nothing there.
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

# Points at the installation guide specifically (not the docs root like
# widget_preferences.py's own _DOCS_URL) -- not imported from interface/*,
# which is a closed subsystem features are forbidden from importing directly
# (see interface/README.md), the same reasoning that used to justify
# duplicating _addon_version() here.
_DOCS_URL = "https://docs.superskinpro.com/installation/"


# ==============================================================================
# ActivateFeature — UnifiedFeatureExtension
# ==============================================================================

class ActivateFeature(UnifiedFeatureExtension):
    """Unified extension for the consolidated Activate/Status UI."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "activate"
    actions = []  # No dispatch actions -- draws existing operators by bl_idname only.
    section_title = "Activate"
    draw_tab = "PREFERENCE"  # Bookkeeping only -- drawn directly by panel_main.py, excluded from the generic tab loop.

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_activate_prompt(self, layout, context) -> None:
        """License-entry prompt drawn by `panel_main.py`'s `_draw_skin_tab()`
        in place of the main artwork body. Draws nothing once the system is
        activated -- there is deliberately no residual "Activated" indicator
        left behind, per the project's UI consolidation decision.

        Wrapped in its own `layout.box()` so the whole group (including the
        "Activate to continue" label itself) reads as one visually distinct
        block instead of blending into the surrounding body. Three rows
        inside the box:
          1. the "Activate to continue" label (`INFO` icon)
          2. the license key field and a narrow, icon-less "Activate"
             button (`scale_x` shrunk on a sub-row so it doesn't dominate
             the row -- icon dropped, text-only, same reasoning as the
             update button in the top row)
          3. an icon-only `QUESTION` button first, then a "Where can I get
             activate key?" label -- the button opens `_DOCS_URL`, pointed
             at the installation guide for now, until there's a dedicated
             purchase/key page to link instead.
        """
        if CoreFacade.is_system_activated():
            return
        prefs = context.window_manager.superskin_prefs
        lic = prefs.license

        box = layout.box()
        box.label(text="Please Activate License to continue!")

        key_row = box.row(align=True)
        key_row.prop(lic, "license_key", text="")
        activate_sub = key_row.row(align=True)
        activate_sub.scale_x = 0.7
        key_row.scale_y=1.4

        activate_button_row = box.row(align=True)
        activate_button_row.scale_y = 1.4
        activate_button_row.operator("superskin.activate_license", text="Activate Key")

        help_row = box.row(align=True)
        help_row.scale_y=1.2
        help_row.operator("wm.url_open", text="What is an activate key", icon='QUESTION').url = _DOCS_URL

    def draw_section(self, layout, context) -> None:
        """Kept only to satisfy `UnifiedFeatureExtension`'s abstract contract
        -- nothing calls this anymore (`panel_main.py` draws
        `draw_activate_prompt()` directly instead). Delegates to
        `draw_activate_prompt()` so the two can never drift if this ever
        needs to be wired back up somewhere."""
        self.draw_activate_prompt(layout, context)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register the feature with UnifiedRegistry."""
    UnifiedRegistry.register(ActivateFeature())


def unregister():
    """Unregister the feature from UnifiedRegistry."""
    UnifiedRegistry.unregister("activate")
