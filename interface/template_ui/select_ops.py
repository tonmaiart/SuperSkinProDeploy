"""Domain-agnostic selection logic — adapter protocol and a pure multi-select
resolution function.

Canonical location: shared/list_widget/select_ops.py
The copy at ui/list_widget/select_ops.py is a compatibility shim that
re-exports from here so all consumers share the same ``_adapter_registry``
singleton.

``ListSelectionAdapter`` is an ABC that each domain (BONES / LAYERS) may
implement to decouple selection storage from per-domain row-click operators.

``resolve_row_click_selection()`` is a pure function that encapsulates
the modifier-key range-select / toggle / select-all logic WITHOUT coupling
operator identities across domains.
"""

# ==============================================================================
# Selection adapter protocol
# ==============================================================================

class ListSelectionAdapter:
    """Protocol for domain-specific multi-select state.

    Each domain (``'BONES'``, ``'LAYERS'``) may register one adapter via
    ``register_adapter()``.  Per-domain operators call the adapter's
    ``read_selection`` / ``write_selection`` and optionally
    ``get_keys_in_visual_order`` when they need to resolve modifier-key
    range selection behaviour.
    """

    def get_keys_in_visual_order(self, context, obj) -> list:
        """Return item keys in the same order the UIList shows them."""
        raise NotImplementedError

    def read_selection(self, context, obj):
        """Read the current selection state.

        Returns:
            ``(selected_keys: set[str], last_clicked_key: str | None,
              history: list[str])``
        """
        raise NotImplementedError

    def write_selection(self, context, obj, selected_keys, last_clicked_key, history):
        """Persist *selected_keys*, *last_clicked_key*, and *history*."""
        raise NotImplementedError

    def on_single_select(self, context, obj, key: str):
        """Called once per click, regardless of modifier keys."""
        raise NotImplementedError


# ==============================================================================
# Adapter registry — canonical singleton; ui/list_widget/ re-exports from here
# ==============================================================================

_adapter_registry = {}


def register_adapter(domain: str, adapter: ListSelectionAdapter):
    """Register an adapter for *domain* (e.g. ``'BONES'`` or ``'LAYERS'``).

    Called at module-load / ``register()`` time by the owning feature domain.
    """
    _adapter_registry[domain] = adapter


def get_adapter(domain: str) -> ListSelectionAdapter:
    """Return the registered adapter for *domain*.

    Raises ``KeyError`` if no adapter has been registered.
    """
    return _adapter_registry[domain]


# ==============================================================================
# Pure modifier-key selection logic (no bpy side effects)
# ==============================================================================

def resolve_row_click_selection(selected_keys, last_key, history,
                                 item_key, visual_order, event):
    """Given current selection state + the clicked key + modifier flags,
    return the new ``(selected_keys, last_key, history)``.

    This function has **no bpy side effects** and **no operator-class
    coupling**.  It is safe to call from any domain's dedicated operator
    ``invoke()``.

    Args:
        selected_keys: ``set[str]`` — currently selected item keys.
        last_key: ``str | None`` — the last-clicked key.
        history: ``list[str]`` — click history (keys in click order).
        item_key: ``str`` — the key of the row that was clicked.
        visual_order: ``list[str]`` — all visible keys in display order.
        event: ``bpy.types.Event`` — read for ``alt``, ``shift``, ``ctrl``.

    Returns:
        ``(selected_keys: set[str], last_key: str | None, history: list[str])``
    """
    # Alt+Shift: select all visible
    if event.alt and event.shift:
        selected_keys.clear()
        selected_keys.update(visual_order)
        history = list(visual_order)
        last_key = item_key

    # Shift: range-select
    elif event.shift and last_key is not None:
        if item_key in visual_order and last_key in visual_order:
            pos_start = visual_order.index(last_key)
            pos_end = visual_order.index(item_key)
            low = min(pos_start, pos_end)
            high = max(pos_start, pos_end)

            for k in visual_order[low:high + 1]:
                selected_keys.discard(k)
            selected_keys.update(visual_order[low:high + 1])
            history = _build_range_history(history, visual_order, low, high)
            last_key = item_key
        else:
            selected_keys.clear()
            selected_keys.add(item_key)
            history = [item_key]
            last_key = item_key

    # Ctrl: toggle
    elif event.ctrl:
        if item_key in selected_keys:
            selected_keys.discard(item_key)
            if item_key in history:
                history.remove(item_key)
            last_key = history[-1] if history else item_key
        else:
            selected_keys.add(item_key)
            if item_key in history:
                history.remove(item_key)
            history.append(item_key)
            last_key = item_key

    # Plain click: clear all, select only this
    else:
        selected_keys.clear()
        selected_keys.add(item_key)
        history = [item_key]
        last_key = item_key

    return selected_keys, last_key, history


def _build_range_history(prev_history, visual_order, start_pos, end_pos):
    """Return a new history list reflecting a range-select."""
    new_hist = [k for k in prev_history
                if k not in visual_order[start_pos:end_pos + 1]]
    for p in range(start_pos, end_pos + 1):
        new_hist.append(visual_order[p])
    return new_hist
