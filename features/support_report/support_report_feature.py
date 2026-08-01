"""SupportReportFeature — Unified Component Architecture implementation for
the support_report domain.

Presents a single "Export Diagnostic Report" button in the PREFERENCE tab.
Bundles a sanitized environment + log snapshot via
CoreFacade.export_support_report() — see README.md for the full
architecture and the rationale for registering zero dispatch actions.

Owns no PropertyGroup — there is nothing to persist or mirror into a
CollectionProperty, just one button.
"""

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry


class SupportReportFeature(UnifiedFeatureExtension):
    """Unified extension for the Support Report domain."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "support_report"
    actions = []
    section_title = "Support Report"
    draw_tab = "PREFERENCE"

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade) -> dict:
        # No actions registered — see README.md "Why no dispatch actions".
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        col = layout.column()
        col.label(text="Bundles a sanitized diagnostic report to send to support.")
        col.operator("superskin.export_support_report", text="Export Diagnostic Report", icon='EXPORT')


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    UnifiedRegistry.register(SupportReportFeature())


def unregister():
    UnifiedRegistry.unregister("support_report")
