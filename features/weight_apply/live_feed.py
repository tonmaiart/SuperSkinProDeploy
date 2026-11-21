
from ...core.facade import CoreFacade

_listeners = []
_mask_listeners = []


def add_listener(fn) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn) -> None:
    while fn in _listeners:
        _listeners.remove(fn)


def add_mask_listener(fn) -> None:
    if fn not in _mask_listeners:
        _mask_listeners.append(fn)


def remove_mask_listener(fn) -> None:
    while fn in _mask_listeners:
        _mask_listeners.remove(fn)


def _notify(listeners, *args) -> None:
    for fn in tuple(listeners):
        try:
            fn(*args)
        except Exception as exc:
            CoreFacade.debug_log("feature_domains", f"weight_apply live listener failed: {exc!r}")


def publish(obj, layer_int, id_to_bone, dirty_verts) -> None:
    _notify(_listeners, obj, layer_int, id_to_bone, dirty_verts)


def publish_mask(obj, mask_dict, dirty_verts) -> None:
    _notify(_mask_listeners, obj, mask_dict, dirty_verts)
