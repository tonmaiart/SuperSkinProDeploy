
from importlib import reload

from . import armature_ids
from . import orphan_resolver
from . import ops
from . import bone_identity_service

for mod in (armature_ids, orphan_resolver, ops, bone_identity_service):
    try:
        reload(mod)
    except Exception:
        pass

from .bone_identity_service import BoneIdentityService


def register():
    ops.register()


def unregister():
    BoneIdentityService.clear_scan_cache()
    ops.unregister()
