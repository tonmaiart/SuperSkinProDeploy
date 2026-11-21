
from importlib import reload

from . import hud_layout as _layout
from . import hud_slot_registry as _hsr

__all__ = ["HudSlotRegistry", "hud_layout"]

for _mod in (_layout, _hsr):
    try:
        reload(_mod)
    except Exception:
        pass

from . import hud_layout
from .hud_slot_registry import HudSlotRegistry
