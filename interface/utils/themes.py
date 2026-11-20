
EXPORT_FORMAT = "superskinpro.theme"
EXPORT_VERSION = 1


def _theme_domains():
    from ..registry.register_api import UnifiedRegistry
    return [ext for ext in UnifiedRegistry.get_all() if ext.get_theme() is not None]


def export_theme() -> dict:
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION,
            "theme": {ext.get_id(): ext.get_theme() for ext in _theme_domains()}}


def import_theme(data) -> int:
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT or not isinstance(data.get("theme"), dict):
        raise ValueError("Not a SuperSkinPro theme file")
    if data.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported theme file version: {data.get('version')}")
    applied = 0
    for ext in _theme_domains():
        section = data["theme"].get(ext.get_id())
        if isinstance(section, dict):
            ext.set_theme(section)
            applied += 1
    return applied


def reset_theme() -> None:
    for ext in _theme_domains():
        ext.set_theme(ext.default_theme())


def _close(a, b) -> bool:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-4
    return a == b


def theme_is_default() -> bool:
    return all(_close(ext.get_theme(), ext.default_theme()) for ext in _theme_domains())

