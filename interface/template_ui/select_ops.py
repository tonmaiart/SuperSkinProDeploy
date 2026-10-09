
import time


class ListSelectionAdapter:
    """Protocol for domain-specific multi-select state."""

    def get_keys_in_visual_order(self, context, obj) -> list:
        raise NotImplementedError

    def read_selection(self, context, obj):
        raise NotImplementedError

    def write_selection(self, context, obj, selected_keys, last_clicked_key, history):
        raise NotImplementedError

    def on_single_select(self, context, obj, key: str):
        raise NotImplementedError



_adapter_registry = {}


def register_adapter(domain: str, adapter: ListSelectionAdapter):
    _adapter_registry[domain] = adapter


def get_adapter(domain: str) -> ListSelectionAdapter:
    return _adapter_registry[domain]



def resolve_row_click_selection(selected_keys, last_key, history,
                                 item_key, visual_order, event):
    if event.alt and event.shift:
        selected_keys.clear()
        selected_keys.update(visual_order)
        history = list(visual_order)
        last_key = item_key

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

    else:
        selected_keys.clear()
        selected_keys.add(item_key)
        history = [item_key]
        last_key = item_key

    return selected_keys, last_key, history


def _build_range_history(prev_history, visual_order, start_pos, end_pos):
    new_hist = [k for k in prev_history
                if k not in visual_order[start_pos:end_pos + 1]]
    for p in range(start_pos, end_pos + 1):
        new_hist.append(visual_order[p])
    return new_hist



_DOUBLE_CLICK_THRESHOLD = 0.35

_last_click_by_domain = {}


def is_double_click(domain: str, item_key: str, event, threshold: float = _DOUBLE_CLICK_THRESHOLD) -> bool:
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
    _last_click_by_domain[domain] = (0.0, None)


