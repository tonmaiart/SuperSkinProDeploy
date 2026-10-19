
from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry


class SupportReportFeature(UnifiedFeatureExtension):
    """Unified extension for the Support Report domain."""


    domain_id = "support_report"
    actions = []
    section_title = "Support Report"
    draw_tab = "PREFERENCE"


    def execute(self, action: str, context, core_facade) -> dict:
        return {"status": "CANCELLED"}


    def draw_section(self, layout, context) -> None:
        layout.operator("superskin.export_support_report", text="Export Diagnostic Report", icon='EXPORT')



def register():
    UnifiedRegistry.register(SupportReportFeature())


def unregister():
    UnifiedRegistry.unregister("support_report")
