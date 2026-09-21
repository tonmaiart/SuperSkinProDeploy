"""SupportReportFeature — Unified Component Architecture implementation for the support_report
domain."""

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
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Just the bare button."""
        layout.operator("superskin.export_support_report", text="Export Diagnostic Report", icon='EXPORT')


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    UnifiedRegistry.register(SupportReportFeature())


def unregister():
    UnifiedRegistry.unregister("support_report")
