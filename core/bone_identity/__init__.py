"""SuperSkinPro Bone Identity — closed module.

External code must only import ``BoneIdentityService`` from this package.
``armature_ids`` and ``orphan_resolver`` are private implementation
details and must never be imported directly from outside this folder.
"""

from importlib import reload

from .bone_identity_service import BoneIdentityService
from . import armature_ids
from . import orphan_resolver
from . import ops
from . import bone_identity_service


for mod in (armature_ids, orphan_resolver, ops, bone_identity_service):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    ops.register()


def unregister():
    BoneIdentityService.clear_scan_cache()
    ops.unregister()
