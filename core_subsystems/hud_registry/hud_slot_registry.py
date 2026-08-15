"""HudSlotRegistry -- shared bottom-left viewport HUD stack.

Multiple feature domains (Multi Color Mode, Deform Bone List's Mask mode,
Bone Picker, ...) each want to show a persistent status label at the same
fixed screen position. Before this subsystem existed, each domain hand-rolled
its own SpaceView3D draw handler for that position -- two of them
(overlay_color and bone_picker) even hardcoded the identical y-offset, so
their labels could literally overlap pixel-for-pixel if both became active
at once.

This module holds only the pure stacking logic: which owners are currently
active, and which fixed screen row each one draws on. It has no
bpy.context/bpy.ops access and registers no handlers -- the actual
SpaceView3D draw callback and bpy.app.timers-based auto-release live in
core/shaders/shader_manager.py, the addon's designated HUD home (see its
own module docstring).

Requests are keyed by an arbitrary caller-chosen ``owner_id`` string (by
convention, the feature domain's name, e.g. "overlay_color").

**Reserved slots, not chronological stacking.** Every request must pass a
``slot`` -- a caller-chosen integer row number, ``0`` at the anchor
(bottom-most / closest to the screen edge) and increasing upward. A slot's
screen position is a static reservation: it never shifts based on which
other slots happen to be active, or the order/timing requests came in --
the exact problem an earlier priority + requested_at ordering scheme had
(two lines could visually swap position over a session depending on which
domain happened to activate first). The canonical slot assignments live in
``core/facade/README.md``'s "Shared HUD Stack" section -- consult it (and
add a row there) before claiming a new one. Two owners claiming the same
slot number is a caller bug, not something this module detects or
prevents -- they will simply draw on top of each other.
"""

import time

_slots: dict[str, dict] = {}
_token_counter = 0

# Single dedicated bottom-center report line -- unlike _slots above (one
# stacked row per owner_id, held until released/timed-out), there is only
# ever one report at a time and a newer one always replaces whatever is
# currently showing. Pure hold+fade timing math lives here; the bpy-facing
# draw callback and animation timer live in core/shaders/shader_manager.py.
_report: dict | None = None


class HudSlotRegistry:
    """Pure request/release/query surface for the shared bottom-left HUD stack."""

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
        """Upsert *owner_id*'s slot with *text*.

        Args:
            owner_id: Caller-chosen identifier, stable for the lifetime of a
                single logical request (e.g. a feature domain name).
            text: The label to display for this slot's line.
            slot: Fixed row number for this owner's line -- ``0`` is the
                anchor (bottom-most), increasing values stack upward. A
                static reservation (see the module docstring), not a
                priority -- doesn't change with activation order or which
                other slots are currently active.
            timeout: Optional seconds after which this slot auto-expires,
                as if ``release_slot(owner_id)`` had been called. ``None``
                (default) means the slot persists until explicitly released.
            color: RGBA color for this slot's text (and its icon, if any --
                the icon is tinted by this same color).
            icon_texture: Optional pre-built ``gpu.types.GPUTexture`` drawn
                to the left of the text, tinted by *color*. ``None``
                (default) draws no icon. This module has no ``gpu`` import
                of its own -- it only stores and returns whatever opaque
                texture object the caller passed in.

        Returns:
            An opaque token identifying this exact request. Pass it back to
            ``release_slot(owner_id, token=token)`` to release only if this
            request hasn't since been superseded by a newer one for the same
            owner_id -- the guard a timeout callback needs so it can't wipe
            out a request that already refreshed itself.
        """
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
        """Release *owner_id*'s slot.

        Args:
            owner_id: The identifier previously passed to ``request_slot()``.
            token: ``None`` (default) always releases -- this is what an
                explicit "I'm done" call from the owning feature should pass.
                A specific token only releases if the slot hasn't been
                refreshed since that token was issued -- this is what a
                ``timeout``-driven auto-release callback should pass, so it
                never clobbers a newer request made before it fired.
        """
        current = _slots.get(owner_id)
        if current is None:
            return
        if token is not None and current["token"] != token:
            return
        del _slots[owner_id]

    @staticmethod
    def get_active_entries() -> list[dict]:
        """Return every non-expired slot's ``{"slot", "text", "color", "icon_texture"}``.

        Prunes expired slots first. Each entry keeps its caller-assigned,
        fixed ``slot`` number -- the caller (shader_manager.py's draw
        callback) positions it directly from that number rather than from
        list order, so an inactive slot leaves a gap instead of the lines
        above it sliding down to fill it.
        """
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
        """Discard every slot. Used on Edit Layer Weight exit and as a
        hot-reload / addon unregister safety hook."""
        _slots.clear()

    # ── Dedicated bottom-center report line ─────────────────────────────

    @staticmethod
    def request_report(
        text: str,
        *,
        hold: float = 2.0,
        fade: float = 1.0,
        color: tuple[float, float, float, float] = (1.0, 0.8, 0.0, 1.0),
    ) -> None:
        """Replace the single bottom-center report line with *text*.

        Args:
            text: The message to display.
            hold: Seconds the line stays fully opaque before fading starts.
            fade: Seconds the line takes to fade from opaque to invisible
                after the hold window ends.
            color: RGBA color of the text (alpha here is the line's own
                base alpha, multiplied by the computed fade alpha).

        There is only ever one report -- calling this again before the
        previous one has finished fading simply replaces it outright, it
        does not queue.
        """
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
        """Return ``{"text", "color", "alpha"}`` for the current report, or
        ``None`` once it has fully faded out (and clears it at that point).

        ``alpha`` is ``1.0`` throughout the hold window, then eases linearly
        down to ``0.0`` across the fade window.
        """
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
        """Discard the report line immediately, regardless of fade state."""
        global _report
        _report = None
