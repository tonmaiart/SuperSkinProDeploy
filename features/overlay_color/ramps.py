
import json
import time

import bpy

from . import _ramp_io

_EDIT_RAMP_STOPS = [
    (0.0,  (0.0, 0.0, 0.0)),
    (0.0001,  (0.0, 0.0, 1.0)),
    (0.25, (0.0, 1.0, 1.0)),
    (0.5,  (0.0, 1.0, 0.0)),
    (0.75, (1.0, 1.0, 0.0)),
    (0.9999,  (1.0, 0.0, 0.0)),
    (1.0,  (0.85, 0.85, 0.85)),
]
_MASK_RAMP_STOPS = [
    (0.00, (0x00 / 255, 0x00 / 255, 0x00 / 255)),
    (0.0001, (0.0, 0.0, 1.0)),
    (0.15, (0x8A / 255, 0x00 / 255, 0xFF / 255)),
    (0.65, (0xFF / 255, 0x14 / 255, 0x93 / 255)),
    (0.9999, (0xFF / 255, 0xB6 / 255, 0xD9 / 255)),
    (1.00, (0.85, 0.85, 0.85)),
]
_LEGACY_MASK_RAMPS = (
    [
        (0.00, (0.0, 0.0, 0.0)),
        (0.001, (0.0, 0.0, 1.0)),
        (0.15, (0x8A / 255, 0x00 / 255, 0xFF / 255)),
        (0.65, (0xFF / 255, 0x14 / 255, 0x93 / 255)),
        (0.9999, (0xFF / 255, 0xB6 / 255, 0xD9 / 255)),
        (1.00, (0.85, 0.85, 0.85)),
    ],
    [
        (0.00, (0.0, 0.0, 0.0)),
        (0.01, (0.0, 0.0, 1.0)),
        (0.15, (0x8A / 255, 0x00 / 255, 0xFF / 255)),
        (0.65, (0xFF / 255, 0x14 / 255, 0x93 / 255)),
        (0.88, (0xFF / 255, 0xB6 / 255, 0xD9 / 255)),
        (1.00, (0.85, 0.85, 0.85)),
    ],
    [
        (0.00, (0.0, 0.0, 0.0)),
        (0.01, (0.0, 0.0, 1.0)),
        (0.15, (0x8A / 255, 0x00 / 255, 0xFF / 255)),
        (0.65, (0xFF / 255, 0x14 / 255, 0x93 / 255)),
        (0.9999, (0xFF / 255, 0xB6 / 255, 0xD9 / 255)),
        (1.00, (0.85, 0.85, 0.85)),
    ],
)

_RAMP_MODES = ("interpolation", "color_mode")

_SCENE_SNAPSHOT_KEY = "__ssp_ramp_snapshot"


def _is_addon_ramp(stops) -> bool:
    if not stops:
        return False
    for own in (_EDIT_RAMP_STOPS, _MASK_RAMP_STOPS, *_LEGACY_MASK_RAMPS):
        if len(stops) != len(own):
            continue
        if all(
            abs(pos - own_pos) < 1e-3 and all(abs(c - o) < 1e-3 for c, o in zip(rgb[:3], own_rgb))
            for (pos, rgb), (own_pos, own_rgb) in zip(stops, own)
        ):
            return True
    return False


def _restore_saved_native_ramp():
    scene = bpy.context.scene if bpy.context else None
    if scene is None or _SCENE_SNAPSHOT_KEY not in scene:
        return None
    raw = scene.get(_SCENE_SNAPSHOT_KEY)
    del scene[_SCENE_SNAPSHOT_KEY]
    try:
        snap = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        snap = None
    if not isinstance(snap, dict):
        return None
    stops = snap.get("stops")
    stops = [(pos, tuple(rgb)) for pos, rgb in stops] if stops else None
    use_range = snap.get("use_weight_color_range")
    if _is_addon_ramp(stops):
        stops, use_range = None, False
    view_prefs = bpy.context.preferences.view
    try:
        if stops:
            _ramp_io.write_stops(view_prefs.weight_color_range, stops)
        if isinstance(use_range, bool):
            view_prefs.use_weight_color_range = use_range
        modes = snap.get("ramp_modes")
        for attr, value in (modes.items() if isinstance(modes, dict) else ()):
            if attr in _RAMP_MODES and isinstance(value, str):
                setattr(view_prefs.weight_color_range, attr, value)
    except Exception as exc:
        from ...core.facade import CoreFacade
        CoreFacade.debug_log("feature_domains", f"restoring the user's weight colour ramp raised: {exc!r}")
    return None



RAMP_KEYS = ("influence", "mask")
_DEFAULT_STOPS = {"influence": _EDIT_RAMP_STOPS, "mask": _MASK_RAMP_STOPS}
WIRE_COLOR_DEFAULT = (0.212231, 0.212231, 0.212231, 1.0)
_TEXTURE_NAMES = {
    "influence": ".SSP Influence Colors",
    "mask": ".SSP Mask Colors",
}
_MODE_DEFAULTS = {"interpolation": 'LINEAR', "color_mode": 'RGB', "hue_interpolation": 'NEAR'}
_SAMPLES = 256
_SAVE_DELAY = 0.5
_LIVE_INTERVAL = 1.0 / 30.0
_LIVE_IDLE = 2.0

_ramps = {}
_wire_color = WIRE_COLOR_DEFAULT
_signatures = {}
_save_pending = False
_live_seen = 0.0


def _rgba(color):
    return tuple(float(c) for c in color[:3]) + (float(color[3]) if len(color) > 3 else 1.0,)


def _default_ramp(key):
    return {"stops": [(float(pos), _rgba(color)) for pos, color in _DEFAULT_STOPS[key]], **_MODE_DEFAULTS}


def _valid_color(raw):
    try:
        if len(raw) == 4:
            return tuple(min(max(float(c), 0.0), 1.0) for c in raw)
    except (TypeError, ValueError):
        pass
    return WIRE_COLOR_DEFAULT


def _valid_ramp(key, raw):
    if not isinstance(raw, dict):
        return _default_ramp(key)
    stops = []
    for stop in raw.get("stops") or ():
        try:
            pos, color = stop
            if len(color) not in (3, 4):
                raise ValueError
            stops.append((min(max(float(pos), 0.0), 1.0), _rgba(color)))
        except (TypeError, ValueError):
            return _default_ramp(key)
    if not stops:
        return _default_ramp(key)
    ramp = {"stops": sorted(stops, key=lambda stop: stop[0])}
    for attr, default in _MODE_DEFAULTS.items():
        value = raw.get(attr)
        ramp[attr] = value if isinstance(value, str) else default
    return ramp


def ramp_texture(key):
    textures = getattr(bpy.data, "textures", None)
    return textures.get(_TEXTURE_NAMES[key]) if textures is not None else None


def _signature(color_ramp):
    return (tuple((round(el.position, 6), tuple(round(c, 6) for c in el.color)) for el in color_ramp.elements),
            color_ramp.interpolation, color_ramp.color_mode, color_ramp.hue_interpolation)


def _write_texture(key):
    tex = ramp_texture(key)
    if tex is None:
        return
    ramp = _ramps.get(key) or _default_ramp(key)
    _ramp_io.write_stops(tex.color_ramp, ramp["stops"])
    for attr in _MODE_DEFAULTS:
        try:
            setattr(tex.color_ramp, attr, ramp[attr])
        except (TypeError, ValueError):
            setattr(tex.color_ramp, attr, _MODE_DEFAULTS[attr])
    _signatures[key] = _signature(tex.color_ramp)


def ensure_textures():
    created = False
    for key in RAMP_KEYS:
        if ramp_texture(key) is None:
            tex = bpy.data.textures.new(_TEXTURE_NAMES[key], type='BLEND')
            tex.use_color_ramp = True
            _write_texture(key)
            created = True
    if created:
        push_to_display()
    return None


def request_textures():
    if not bpy.app.timers.is_registered(ensure_textures):
        bpy.app.timers.register(ensure_textures, first_interval=0.0)


def _display_stops(key):
    tex = ramp_texture(key)
    if tex is None:
        return (_ramps.get(key) or _default_ramp(key))["stops"]
    ramp = tex.color_ramp
    if ramp.color_mode == 'RGB' and ramp.interpolation == 'LINEAR':
        return [(el.position, tuple(el.color)) for el in ramp.elements]
    return [(i / (_SAMPLES - 1), tuple(ramp.evaluate(i / (_SAMPLES - 1)))) for i in range(_SAMPLES)]


def push_to_display():
    from ..weight_apply.public_api import set_display_ramps, set_display_wire_color
    set_display_ramps(_display_stops("influence"), _display_stops("mask"))
    set_display_wire_color(_wire_color)


def wire_color():
    return _wire_color


def wire_is_default() -> bool:
    return all(abs(a - b) < 1e-4 for a, b in zip(_wire_color, WIRE_COLOR_DEFAULT))


def set_wire_color(rgba) -> None:
    global _wire_color
    color = _valid_color(tuple(rgba))
    if color == _wire_color:
        return
    _wire_color = color
    from ..weight_apply.public_api import set_display_wire_color
    set_display_wire_color(color)
    _schedule_save()


def _save():
    global _save_pending
    _save_pending = False
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()
    return None


def _schedule_save():
    global _save_pending
    if not _save_pending:
        _save_pending = True
        bpy.app.timers.register(_save, first_interval=_SAVE_DELAY)


def sync_from_textures() -> bool:
    changed = False
    for key in RAMP_KEYS:
        tex = ramp_texture(key)
        if tex is None:
            continue
        signature = _signature(tex.color_ramp)
        if signature == _signatures.get(key):
            continue
        ramp = tex.color_ramp
        _ramps[key] = {
            "stops": [(el.position, _rgba(el.color)) for el in sorted(ramp.elements, key=lambda el: el.position)],
            "interpolation": ramp.interpolation, "color_mode": ramp.color_mode,
            "hue_interpolation": ramp.hue_interpolation,
        }
        _signatures[key] = signature
        changed = True
    if changed:
        push_to_display()
        _schedule_save()
    return changed


def keep_live_sync() -> None:
    global _live_seen
    _live_seen = time.monotonic()
    if not bpy.app.timers.is_registered(_live_sync):
        bpy.app.timers.register(_live_sync, first_interval=_LIVE_INTERVAL)


def _live_sync():
    global _live_seen
    if sync_from_textures():
        _live_seen = time.monotonic()
    if time.monotonic() - _live_seen > _LIVE_IDLE:
        return None
    return _LIVE_INTERVAL


def ramp_is_default(key) -> bool:
    ramp, default = _ramps.get(key), _default_ramp(key)
    if ramp is None:
        return True
    if any(ramp.get(attr) != default[attr] for attr in _MODE_DEFAULTS):
        return False
    if len(ramp["stops"]) != len(default["stops"]):
        return False
    return all(abs(pa - pb) < 1e-4 and all(abs(a - b) < 1e-4 for a, b in zip(ca, cb))
               for (pa, ca), (pb, cb) in zip(ramp["stops"], default["stops"]))


def reset_ramp(key) -> None:
    if key == "wire":
        set_wire_color(WIRE_COLOR_DEFAULT)
        return
    _ramps[key] = _default_ramp(key)
    _write_texture(key)
    push_to_display()
    _schedule_save()


def load(data) -> None:
    global _wire_color
    _wire_color = _valid_color(data.get("wire_color") if isinstance(data, dict) else None)
    saved = data.get("ramps") if isinstance(data, dict) else None
    saved = saved if isinstance(saved, dict) else {}
    for key in RAMP_KEYS:
        _ramps[key] = _valid_ramp(key, saved.get(key))
        _write_texture(key)
    push_to_display()


def dump() -> dict:
    out = {}
    for key in RAMP_KEYS:
        ramp = _ramps.get(key) or _default_ramp(key)
        out[key] = {"stops": [[pos, list(color)] for pos, color in ramp["stops"]],
                    **{attr: ramp[attr] for attr in _MODE_DEFAULTS}}
    return {"ramps": out, "wire_color": list(_wire_color)}


def default_dump() -> dict:
    out = {key: {"stops": [[pos, list(color)] for pos, color in _default_ramp(key)["stops"]], **_MODE_DEFAULTS}
           for key in RAMP_KEYS}
    return {"ramps": out, "wire_color": list(WIRE_COLOR_DEFAULT)}


@bpy.app.handlers.persistent
def _on_load_post(*_):
    bpy.app.timers.register(_restore_saved_native_ramp, first_interval=0.0)
    _signatures.clear()
    request_textures()


def register():
    for key in RAMP_KEYS:
        _ramps.setdefault(key, _default_ramp(key))
    push_to_display()
    request_textures()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    while _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    for timer in (ensure_textures, _save, _live_sync):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    try:
        for key in RAMP_KEYS:
            tex = ramp_texture(key)
            if tex is not None and tex.users == 0:
                bpy.data.textures.remove(tex)
    except (AttributeError, RuntimeError):
        pass
    _signatures.clear()
