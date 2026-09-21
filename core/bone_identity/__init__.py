"""SuperSkinPro Bone Identity — closed module.

External code must only import ``BoneIdentityService`` from this package.
``armature_ids`` and ``orphan_resolver`` are private implementation
details and must never be imported directly from outside this folder.
"""

from importlib import reload

from . import armature_ids
from . import orphan_resolver
from . import ops
from . import bone_identity_service

# Reload BEFORE binding BoneIdentityService below -- same Reload-Ordering
# Footgun documented in core_subsystems/profiler/__init__.py and fixed this
# session in six core_subsystems packages plus core/facade/__init__.py and
# core/layer_storage/__init__.py. Binding the class before
# reload(bone_identity_service) runs would permanently capture the
# pre-reload class, silently keeping every method on it one generation
# behind on every F3 Reload Scripts after the first.
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
