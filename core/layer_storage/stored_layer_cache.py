

_stored_layer_cache: dict = {}


_readonly_layer_cache: dict = {}


def read_stored_layer_readonly(storage, layer_idx: int) -> dict:
    raw = storage.read_layer_raw(layer_idx)
    if not raw:
        return {}
    key = storage.mesh.name
    cached = _readonly_layer_cache.get(key)
    if cached is not None and cached[0] == raw:
        return cached[1]
    written = peek_stored_layer(raw)
    if written is not None:
        return written
    layer = storage.read_layer_dict(layer_idx)
    _readonly_layer_cache[key] = (raw, layer)
    return layer


def _remember_readonly_layer(storage, layer_idx: int, layer: dict) -> None:
    _readonly_layer_cache[storage.mesh.name] = (storage.read_layer_raw(layer_idx), layer)


def peek_stored_layer(raw: str):
    for _layer_idx, cached_raw, layer in _stored_layer_cache.values():
        if isinstance(layer, dict) and cached_raw == raw:
            return layer
    return None


def _read_stored_layer(storage, mesh_name: str, layer_idx: int):
    raw = storage.read_layer_raw(layer_idx)
    cached = _stored_layer_cache.pop(mesh_name, None)
    if cached is not None and cached[0] == layer_idx and cached[1] == raw:
        return cached[2]
    arrays = storage.read_layer_arrays(layer_idx)
    return arrays if arrays is not None else storage.read_layer_dict(layer_idx)
