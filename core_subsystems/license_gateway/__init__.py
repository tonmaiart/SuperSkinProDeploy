"""license_gateway -- encapsulated license verification and Pro-tier feature gating subsystem.

Public surface: only ``LicenseGateway`` is exported from this package.
"""

from importlib import reload

from . import license_gateway as _lg

__all__ = ["LicenseGateway"]

# Export binding must come AFTER reload -- see core_subsystems/
# layer_compositor/__init__.py's comment for the "Reload-Ordering Footgun"
# this avoids (binding before reload permanently captures a stale,
# pre-reload class object on every F3 Reload Scripts after the first).
for _mod in (_lg,):
    try:
        reload(_mod)
    except Exception:
        pass

from .license_gateway import LicenseGateway
