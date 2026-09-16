"""BuiltinTutorial feature package.

A "Quick Start Guide" popup -- a left-hand list of tutorial steps and a
right-hand image + description panel -- opened from panel_main.py's "How to
use" toggle body. See docs/domains/builtin_tutorial.md for the full
architecture and content-authoring format.
"""

from importlib import reload

from . import logic
from . import ops
from . import builtin_tutorial_feature

# Bottom-up reload -- foundations before wrappers. Both ops and
# builtin_tutorial_feature import from logic at module level.
for mod in (logic, ops, builtin_tutorial_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    logic.register()
    ops.register()
    builtin_tutorial_feature.register()


def unregister():
    builtin_tutorial_feature.unregister()
    ops.unregister()
    logic.unregister()
