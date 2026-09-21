# Copyright (c) 2026 Natchapon Srisuk. All rights reserved.
"""Clipboard domain package — Layout & Engine initializer."""

from importlib import reload

from . import logic
from . import ops
from . import clipboard_feature

for mod in (logic, ops, clipboard_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    clipboard_feature.register()


def unregister():
    clipboard_feature.unregister()
    ops.unregister()