"""WeightInfo feature package.

Read-only "selected vertices' active-layer weight" viewer in the SKINNING
tab. Single-file domain — see weight_info_feature.py.
"""

from importlib import reload

from . import weight_info_feature

for mod in (weight_info_feature,):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    weight_info_feature.register()


def unregister():
    weight_info_feature.unregister()
