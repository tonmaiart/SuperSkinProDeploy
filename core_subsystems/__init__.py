
from importlib import reload

from . import dev_records
from . import profiler
from . import rust_weight_engine
from . import layer_compositor
from . import topology_cache_manager
from . import context_selection_service
from . import license_gateway
from . import debug_logging
from . import hud_registry
from . import support_report

from . import preferences

_encapsulated = (
    dev_records,
    profiler,
    rust_weight_engine,
    layer_compositor,
    topology_cache_manager,
    context_selection_service,
    license_gateway,
    debug_logging,
    hud_registry,
    support_report,
)
_legacy_packages = (
    preferences,
)

for mod in (*_encapsulated, *_legacy_packages):
    try:
        reload(mod)
    except Exception:
        pass
