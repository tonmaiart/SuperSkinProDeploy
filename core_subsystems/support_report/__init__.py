"""support_report -- encapsulated user-facing diagnostic-report subsystem.

Public surface: only ``SupportReportService`` is exported from this
package. Depends on ``dev_records`` (output folder resolution) and
``debug_logging`` (the log buffer to bundle) -- both must reload before
this package in core_subsystems/__init__.py's ``_encapsulated`` tuple.
"""

from importlib import reload

from . import environment_collector as _ec
from . import support_report_service as _srs

for _mod in (_ec, _srs):
    try:
        reload(_mod)
    except Exception:
        pass

__all__ = ["SupportReportService"]

from .support_report_service import SupportReportService
