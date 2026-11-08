
import json
import math

import bpy

_PREFIX = "__ssp_ovr_"
_MISSING = object()

_suspended = []


def _key(owner: str) -> str:
    return _PREFIX + owner


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), abs_tol=1e-6)
        except (TypeError, ValueError):
            return False
    return a == b


def _plain(value):
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _resolve(base, path: str):
    *parents, attr = path.split(".")
    for name in parents:
        base = getattr(base, name)
    return base, attr


def _get(base, path: str):
    obj, attr = _resolve(base, path)
    return getattr(obj, attr)


def _set(base, path: str, value) -> bool:
    obj, attr = _resolve(base, path)
    if _same(getattr(obj, attr), value):
        return False
    setattr(obj, attr, value)
    return True


def _targets(id_block):
    if isinstance(id_block, bpy.types.Screen):
        out = []
        for ai, area in enumerate(id_block.areas):
            for si, space in enumerate(area.spaces):
                if space.type == 'VIEW_3D':
                    out.append(((ai, si), space))
        return out
    return [((), id_block)]


def _target_at(id_block, address):
    if not address:
        return id_block
    ai, si = address
    try:
        return id_block.areas[ai].spaces[si]
    except (IndexError, AttributeError):
        return None


def _read(id_block, key: str):
    raw = id_block.get(key)
    if not isinstance(raw, str):
        return None
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    if not (isinstance(rec, dict) and isinstance(rec.get("orig"), dict)
            and isinstance(rec.get("forced"), dict)):
        return None
    return rec


def _write(id_block, key: str, rec: dict) -> None:
    try:
        id_block[key] = json.dumps(rec)
    except Exception:
        pass


def _drop(id_block, key: str) -> None:
    try:
        if key in id_block:
            del id_block[key]
    except Exception:
        pass


def _restore_record(id_block, rec: dict) -> list:
    written = []
    for path, orig in rec["orig"].items():
        forced = rec["forced"].get(path, _MISSING)
        for address, base in _targets(id_block):
            try:
                if forced is not _MISSING and not _same(_get(base, path), forced):
                    continue
                if _set(base, path, orig):
                    written.append((address, path))
            except Exception:
                pass
    return written


def _owned_ids():
    yield from bpy.data.screens
    yield from bpy.data.scenes


def apply(owner: str, id_block, values: dict, *, once: bool = False) -> bool:
    targets = _targets(id_block)
    if not targets:
        return False
    key = _key(owner)
    rec = _read(id_block, key)
    if rec is not None and once:
        return False
    if rec is None:
        rec = {"orig": {}, "forced": {}}
    dirty = False
    changed = False
    for path, value in values.items():
        if path not in rec["orig"]:
            orig = _MISSING
            for _address, base in targets:
                try:
                    current = _get(base, path)
                except Exception:
                    continue
                if orig is _MISSING:
                    orig = _plain(current)
                if not _same(current, value):
                    orig = _plain(current)
                    break
            if orig is _MISSING:
                continue
            rec["orig"][path] = orig
            dirty = True
        if not _same(rec["forced"].get(path, _MISSING), value):
            rec["forced"][path] = value
            dirty = True
        for _address, base in targets:
            try:
                changed |= _set(base, path, value)
            except Exception:
                pass
    if dirty:
        _write(id_block, key, rec)
    return changed


def apply_view3d(owner: str, values: dict, *, once: bool = False) -> bool:
    changed = False
    for screen in bpy.data.screens:
        changed |= apply(owner, screen, values, once=once)
    return changed


def is_applied(owner: str) -> bool:
    key = _key(owner)
    return any(key in id_block for id_block in _owned_ids())


def restore(owner: str) -> bool:
    key = _key(owner)
    changed = False
    for id_block in _owned_ids():
        if key not in id_block:
            continue
        rec = _read(id_block, key)
        if rec is not None:
            changed |= bool(_restore_record(id_block, rec))
        _drop(id_block, key)
    return changed


def restore_all() -> None:
    for id_block in _owned_ids():
        for key in [k for k in id_block.keys() if k.startswith(_PREFIX)]:
            rec = _read(id_block, key)
            if rec is not None:
                _restore_record(id_block, rec)
            _drop(id_block, key)


def _collection_of(id_block) -> str:
    return "screens" if isinstance(id_block, bpy.types.Screen) else "scenes"


@bpy.app.handlers.persistent
def _on_save_pre(*_):
    _suspended.clear()
    try:
        for id_block in _owned_ids():
            for key in [k for k in id_block.keys() if k.startswith(_PREFIX)]:
                rec = _read(id_block, key)
                written = _restore_record(id_block, rec) if rec is not None else []
                _drop(id_block, key)
                if rec is not None:
                    _suspended.append((_collection_of(id_block), id_block.name, key, rec, written))
    except Exception as exc:
        print(f"[SuperSkinPro] viewport overrides: save_pre suspend failed: {exc}")


@bpy.app.handlers.persistent
def _on_save_post(*_):
    try:
        for coll_name, name, key, rec, written in _suspended:
            id_block = getattr(bpy.data, coll_name).get(name)
            if id_block is None:
                continue
            for address, path in written:
                base = _target_at(id_block, address)
                if base is None:
                    continue
                try:
                    _set(base, path, rec["forced"][path])
                except Exception:
                    pass
            _write(id_block, key, rec)
    except Exception as exc:
        print(f"[SuperSkinPro] viewport overrides: save_post resume failed: {exc}")
    finally:
        _suspended.clear()


def _handler_lists():
    lists = [(bpy.app.handlers.save_pre, _on_save_pre), (bpy.app.handlers.save_post, _on_save_post)]
    fail = getattr(bpy.app.handlers, "save_post_fail", None)
    if fail is not None:
        lists.append((fail, _on_save_post))
    return lists


def _remove_by_name(handler_list, fn) -> None:
    for existing in list(handler_list):
        if (getattr(existing, "__name__", None) == fn.__name__
                and getattr(existing, "__module__", None) == fn.__module__):
            handler_list.remove(existing)


def register():
    for handler_list, fn in _handler_lists():
        _remove_by_name(handler_list, fn)
        handler_list.append(fn)


def unregister():
    for handler_list, fn in _handler_lists():
        try:
            _remove_by_name(handler_list, fn)
        except Exception:
            pass
