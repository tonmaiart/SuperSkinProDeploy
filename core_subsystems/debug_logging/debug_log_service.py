
import time

CATEGORIES = (
    "temp_vg",
    "core_pipeline",
    "rust_ffi",
    "viewport_viz",
    "bone_id",
    "feature_domains",
)

_MAX_LOG_LINES = 200
_log_buffer: list[dict] = []
_print_enabled = False


class DebugLogService:
    """Unconditional capture + query surface for the runtime debug-log buffer."""

    CATEGORIES = CATEGORIES

    @staticmethod
    def log(category: str, message: str) -> None:
        _log_buffer.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "category": category,
            "message": message,
        })
        if len(_log_buffer) > _MAX_LOG_LINES:
            del _log_buffer[0]
        if not _print_enabled:
            return
        try:
            print(f"[SSP:{category.upper()}] {message}")
        except OSError:
            pass

    @staticmethod
    def is_print_enabled() -> bool:
        return _print_enabled

    @staticmethod
    def set_print_enabled(value: bool) -> None:
        global _print_enabled
        _print_enabled = bool(value)

    @staticmethod
    def get_logs(category_filter: str | None = None, search: str = "") -> list[dict]:
        needle = search.lower()
        return [
            entry for entry in _log_buffer
            if (category_filter in (None, "ALL") or entry["category"] == category_filter)
            and (not needle or needle in entry["message"].lower())
        ]

    @staticmethod
    def clear_logs() -> None:
        _log_buffer.clear()

    @staticmethod
    def get_adhoc_categories() -> list[str]:
        return sorted({
            entry["category"] for entry in _log_buffer
            if entry["category"].startswith("adhoc:")
        })
