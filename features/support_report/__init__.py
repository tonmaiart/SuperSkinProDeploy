"""SupportReport feature package."""

from importlib import reload

from . import ops
from . import support_report_feature

for mod in (ops, support_report_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()
    support_report_feature.register()


def unregister():
    support_report_feature.unregister()
    ops.unregister()
