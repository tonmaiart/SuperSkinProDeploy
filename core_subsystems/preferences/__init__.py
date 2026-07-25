"""SuperSkinPro Preferences — core_subsystems backend layer.

Provides PreferencesService and Blender PropertyGroup definitions.
Blender lifecycle registration (load_post handler, temp-VG recovery) is
owned by core/preferences/__init__.py to avoid an upward dependency on
core/layer_storage from inside core_subsystems.

External code must only import ``PreferencesService`` from this package.
``io`` and ``property_groups`` are private implementation details.
"""

from importlib import reload

from . import io
from . import property_groups
from . import preferences_service

for mod in (io, property_groups, preferences_service):
    try:
        reload(mod)
    except Exception:
        pass

# Re-exported *after* the reload loop above, not before it — binding this
# name to preferences_service.PreferencesService before reload(preferences_service)
# runs left it pointing at the pre-reload class, permanently disconnected
# from the fresh class every populate()/serialize_into() call actually runs
# against. Two classes both existing, each with its own independent
# `_loading` flag, silently broke the load()/save_to_user_file() reentrancy
# guard: a callsite that resolved this stale export could call
# save_to_user_file() mid-populate without `_loading` being visible as True,
# writing a torn snapshot over whatever another extension (e.g. mirror) had
# already written. See docs/bug-history for the write-up this was diagnosed
# from.
from .preferences_service import PreferencesService
