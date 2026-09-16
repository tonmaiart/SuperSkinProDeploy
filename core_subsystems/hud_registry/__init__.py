"""hud_registry -- shared bottom-left viewport HUD stack.

Public surface: only ``HudSlotRegistry`` is exported from this package.
"""

from importlib import reload

from . import hud_slot_registry as _hsr

__all__ = ["HudSlotRegistry"]

for _mod in (_hsr,):
    try:
        reload(_mod)
    except Exception:
        pass

from .hud_slot_registry import HudSlotRegistry
