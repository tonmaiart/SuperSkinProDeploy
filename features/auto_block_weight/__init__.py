"""Auto Block Weight domain package — auto assign closest unlocked bone."""

from importlib import reload

from . import logic
from . import ui
from . import ops
from . import auto_block_feature

# `logic` (math/data logic) reloads before `ops`/`auto_block_feature`
# (registration wrappers), per CLAUDE.md's Deep Matrix Reload Rule --
# otherwise F3 Reload Scripts would keep executing a stale cached `logic`
# module even after editing it, since it's only ever imported lazily
# (inside `auto_block_feature.execute()`) rather than at module load time.
for mod in (logic, ui, ops, auto_block_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    auto_block_feature.register()


def unregister():
    auto_block_feature.unregister()
    ops.unregister()
