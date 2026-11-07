
import time

_slots: dict[str, dict] = {}
_token_counter = 0

_report: dict | None = None


class HudSlotRegistry:
    """Pure request/release/query surface for the shared bottom-center HUD stack."""

    @staticmethod
    def request_slot(
        owner_id: str,
        text: str,
        *,
        slot: int,
        timeout: float | None = None,
        color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
        icon_texture=None,
    ) -> int:
        global _token_counter
        _token_counter += 1
        token = _token_counter
        now = time.monotonic()
        _slots[owner_id] = {
            "text": text,
            "slot": slot,
            "color": color,
            "icon_texture": icon_texture,
            "requested_at": now,
            "expires_at": (now + timeout) if timeout is not None else None,
            "token": token,
        }
        return token

    @staticmethod
    def release_slot(owner_id: str, *, token: int | None = None) -> None:
        current = _slots.get(owner_id)
        if current is None:
            return
        if token is not None and current["token"] != token:
            return
        del _slots[owner_id]

    @staticmethod
    def get_active_entries() -> list[dict]:
        now = time.monotonic()
        expired = [owner_id for owner_id, slot in _slots.items()
                   if slot["expires_at"] is not None and slot["expires_at"] <= now]
        for owner_id in expired:
            del _slots[owner_id]

        return [
            {
                "slot": s["slot"],
                "text": s["text"],
                "color": s["color"],
                "icon_texture": s["icon_texture"],
            }
            for s in _slots.values()
        ]

    @staticmethod
    def clear_all() -> None:
        _slots.clear()


    @staticmethod
    def request_report(
        text: str,
        *,
        hold: float = 2.0,
        fade: float = 1.0,
        color: tuple[float, float, float, float] = (1.0, 0.8, 0.0, 1.0),
    ) -> None:
        global _report
        _report = {
            "text": text,
            "color": color,
            "requested_at": time.monotonic(),
            "hold": hold,
            "fade": fade,
        }

    @staticmethod
    def get_active_report() -> dict | None:
        global _report
        if _report is None:
            return None

        elapsed = time.monotonic() - _report["requested_at"]
        total = _report["hold"] + _report["fade"]
        if elapsed >= total:
            _report = None
            return None

        if elapsed <= _report["hold"]:
            alpha = 1.0
        elif _report["fade"] > 0:
            alpha = 1.0 - (elapsed - _report["hold"]) / _report["fade"]
        else:
            alpha = 0.0

        return {"text": _report["text"], "color": _report["color"], "alpha": alpha}

    @staticmethod
    def clear_report() -> None:
        global _report
        _report = None
