"""Mirror domain package — mirror weight operator + search/replace pair
management, grouped per Unified Component Architecture.

mirror_feature.py owns the PropertyGroup, UI layout, action dispatch,
and JSON persistence in a single UnifiedFeatureExtension subclass.
logic.py holds the Rust-accelerated pipeline. ops.py holds operator
classes (including the legacy OBJECT_OT_MirrorWeights)."""

from importlib import reload

from . import logic
from . import ops
from . import mirror_feature

for mod in (logic, mirror_feature, ops):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    mirror_feature.register()
    ops.register()


def unregister():
    ops.unregister()
    mirror_feature.unregister()
