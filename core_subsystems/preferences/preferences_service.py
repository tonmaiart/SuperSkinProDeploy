
import bpy

from . import io



_default_dict = None


def _get_default_dict() -> dict:
    global _default_dict
    if _default_dict is None:
        path = io.default_json_path()
        _default_dict = io.load_json_safe(path)
        if not _default_dict:
            _safe_print(f"[SuperSkinPro] default_prefs.json not found or empty at {path} "
                        f"— preferences will start blank.")
    return _default_dict


def _get_nested(d: dict, path: tuple) -> dict:
    node = d
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key, {})
    return node if isinstance(node, dict) else {}


def _safe_print(message: str) -> None:
    try:
        print(message)
    except OSError:
        pass


class PreferencesService:
    """Stateless service for reading/writing preferences via JSON files."""

    _loading = False

    @classmethod
    def load(cls) -> None:
        from ...interface.registry.register_api import UnifiedRegistry

        cls._loading = True
        try:
            default = _get_default_dict()
            user    = io.load_json_safe(io.user_json_path())
            merged  = io.deep_merge(default, user)

            prefs = bpy.context.window_manager.superskin_prefs
            cls._populate_from_dict(prefs, merged)

            for ext in UnifiedRegistry.get_all():
                defaults_path = ext.get_defaults_path()
                if defaults_path is None:
                    continue
                ext_defaults = io.load_json_safe(defaults_path)
                user_section = _get_nested(user, ext.get_json_path())
                ext_data     = io.deep_merge(ext_defaults, user_section)
                try:
                    ext.populate(ext_data)
                except Exception as e:
                    _safe_print(f"[SuperSkinPro] Failed to load unified prefs for '{ext.get_id()}': {e}")
        finally:
            cls._loading = False

    @classmethod
    def save_to_user_file(cls) -> None:
        if cls._loading:
            return

        from ...interface.registry.register_api import UnifiedRegistry

        prefs = bpy.context.window_manager.superskin_prefs
        data  = cls._dict_from_property_group(prefs)

        for ext in UnifiedRegistry.get_all():
            try:
                ext.serialize_into(data)
            except Exception as e:
                _safe_print(f"[SuperSkinPro] Failed to serialize unified prefs for '{ext.get_id()}': {e}")

        io.save_json(io.user_json_path(), data)

    @classmethod
    def reset_to_default(cls) -> None:
        from ...interface.registry.register_api import UnifiedRegistry

        cls._loading = True
        try:
            default = _get_default_dict()
            prefs   = bpy.context.window_manager.superskin_prefs
            cls._populate_from_dict(prefs, default)

            for ext in UnifiedRegistry.get_all():
                if not ext.supports_reset_to_default_action():
                    continue
                defaults_path = ext.get_defaults_path()
                if defaults_path is None:
                    continue
                ext_defaults = io.load_json_safe(defaults_path)
                try:
                    ext.populate(ext_defaults)
                except Exception as e:
                    _safe_print(f"[SuperSkinPro] Failed to reset unified prefs for '{ext.get_id()}': {e}")
        finally:
            cls._loading = False


    @classmethod
    def get_mirror_axis(cls) -> str:
        from ...features.weight_toolkit.weight_toolkit_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_axis()

    @classmethod
    def get_mirror_direction(cls) -> str:
        from ...features.weight_toolkit.weight_toolkit_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_direction()

    @classmethod
    def get_mirror_data(cls) -> str:
        from ...features.weight_toolkit.weight_toolkit_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_data()

    @classmethod
    def get_mirror_search_replace_pairs(cls) -> list:
        from ...features.weight_toolkit.weight_toolkit_feature import MirrorPreferencesService
        return MirrorPreferencesService.get_mirror_search_replace_pairs()


    @staticmethod
    def _populate_from_dict(prefs, data: dict) -> None:
        from ..debug_logging.debug_log_service import CATEGORIES
        debug_data = data.get("debug", {})
        for cat in CATEGORIES:
            setattr(prefs.debug, cat, bool(debug_data.get(cat, False)))

    @staticmethod
    def _dict_from_property_group(prefs) -> dict:
        from ..debug_logging.debug_log_service import CATEGORIES
        debug = {cat: getattr(prefs.debug, cat) for cat in CATEGORIES}

        return {
            "_schema_version": 1,
            "debug": debug,
        }
