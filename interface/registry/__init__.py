"""Registry sub-package for the Interface subsystem.

Unified Component Architecture:
    UnifiedFeatureExtension — single-class contract for feature domains
    UnifiedRegistry           — central registry for actions + UI + persistence
    SUPERSKIN_OT_execute_action — universal proxy operator
"""

from importlib import reload

from . import register_api

for _mod in (register_api,):
    try:
        reload(_mod)
    except Exception:
        pass

from .register_api import (
    UnifiedFeatureExtension,
    UnifiedRegistry,
    SUPERSKIN_OT_execute_action,
    register_operator,
    unregister_operator,
)
