"""Domain-agnostic selection logic — adapter protocol and a pure multi-select resolution function."""

import time

# ==============================================================================
# Selection adapter protocol
# ==============================================================================

class ListSelectionAdapter:
    """Protocol for domain-specific multi-select state."""

    def get_keys_in_visual_order(self, context, obj) -> list:
        """Return item keys in the same order the UIList shows them."""
        raise NotImplementedError

    def read_selection(self, context, obj):
        """Read the current selection state."""
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
    """Register an adapter for *domain* (e.g. ``'BONES'`` or ``'LAYERS'``)."""
    _adapter_registry[domain] = adapter


def get_adapter(domain: str) -> ListSelectionAdapter:
    """Return the registered adapter for *domain*."""
    return _adapter_registry[domain]


# ==============================================================================
# Pure modifier-key selection logic (no bpy side effects)
# ==============================================================================

def resolve_row_click_selection(selected_keys, last_key, history,
                                 item_key, visual_order, event):
    """Given current selection state + the clicked key + modifier flags, return the new
    ``(selected_keys, last_key, history)``."""
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


# ==============================================================================
# Shared double-click-on-a-row detection
# ==============================================================================

_DOUBLE_CLICK_THRESHOLD = 0.35  # seconds

# domain (str) -> (last_click_time: float, last_click_key: str | None)
_last_click_by_domain = {}


def is_double_click(domain: str, item_key: str, event, threshold: float = _DOUBLE_CLICK_THRESHOLD) -> bool:
    """Return True if this click on *item_key* is a double-click, i.e. the same row was clicked
    less than *threshold* seconds ago with no modifier keys held."""
    now = time.time()
    last_time, last_key = _last_click_by_domain.get(domain, (0.0, None))
    result = (
        item_key == last_key
        and (now - last_time) < threshold
        and not event.ctrl and not event.shift and not event.alt
    )
    _last_click_by_domain[domain] = (now, item_key)
    return result


def reset_double_click(domain: str):
    """Clear *domain*'s tracked click state so a 3rd quick click can't chain into another
    double-click trigger."""
    _last_click_by_domain[domain] = (0.0, None)


# ==============================================================================
# Last-active-list tracking
# ==============================================================================

_last_active_domain = 'BONES'


def set_last_active_domain(domain: str):
    """Record *domain* (``'BONES'`` or ``'LAYERS'``) as the list most
    recently clicked in."""
    global _last_active_domain
    _last_active_domain = domain


def get_last_active_domain() -> str:
    """Return the domain most recently clicked in, defaulting to
    ``'BONES'`` before either list has been clicked this session."""
    return _last_active_domain


def invert_selection(selected_keys, last_key, visual_order):
    """Return a new ``(selected_keys, last_key, history)`` with every visible key's selection
    state flipped."""
    new_selected = set(visual_order) - set(selected_keys)
    new_history = [k for k in visual_order if k in new_selected]
    new_last_key = last_key if last_key in new_selected else (new_history[-1] if new_history else None)
    return new_selected, new_last_key, new_history
