
from ...core.facade import CoreFacade

_listeners = []


def add_listener(fn) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn) -> None:
    while fn in _listeners:
        _listeners.remove(fn)


def publish(obj, layer_int, id_to_bone, dirty_verts) -> None:
    for fn in tuple(_listeners):
        try:
            fn(obj, layer_int, id_to_bone, dirty_verts)
        except Exception as exc:
            CoreFacade.debug_log("feature_domains", f"weight_apply live listener failed: {exc!r}")
