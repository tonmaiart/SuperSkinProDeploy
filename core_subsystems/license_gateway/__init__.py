"""license_gateway -- encapsulated license verification and Pro-tier feature gating subsystem.

Public surface: only ``LicenseGateway`` is exported from this package.
"""

from importlib import reload

from . import license_gateway as _lg

__all__ = ["LicenseGateway"]

from .license_gateway import LicenseGateway

for _mod in (_lg,):
    try:
        reload(_mod)
    except Exception:
        pass
