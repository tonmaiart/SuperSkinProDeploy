"""SuperSkinListMixin — shared UIList filtering and drawing logic."""

import traceback
from fnmatch import fnmatch as _fnmatch


# Last native filter (pattern, invert) per collection property, so off-draw-cycle callers
# can reproduce what the UIList currently shows.
_last_filter: dict = {}


def _matches(pattern: str, text: str) -> bool:
    """Case-insensitive match padded with ``*`` on both sides, like Blender's native filter."""
    return _fnmatch(text.lower(), f"*{pattern.lower()}*")


class SuperSkinListMixin:
    """Mixin that supplies ``filter_items`` and ``draw_item`` for UIList subclasses."""

    use_filter_show = True
    row_scale_y = 1

    # ------------------------------------------------------------------
    # Hook methods – subclasses MUST override these
    # ------------------------------------------------------------------

    def domain(self) -> str:
        """Return the domain identifier (e.g. ``'BONES'`` or ``'LAYERS'``)."""
        raise NotImplementedError("domain()")

    def get_item_key(self, item) -> str:
        """Stable string identity for the row."""
        raise NotImplementedError("get_item_key()")

    def get_display_order(self, context, data) -> list:
        """Return original collection indices in the order they should appear."""
        raise NotImplementedError("get_display_order()")

    def is_selected(self, context, data, key: str) -> bool:
        """Return *True* if *key* is currently in the domain's selection pool."""
        raise NotImplementedError("is_selected()")

    def draw_main_icon(self, context, data, item) -> str:
        """Return the Blender icon enum for the row's leading icon."""
        raise NotImplementedError("draw_main_icon()")

    def draw_main_icon_value(self, context, data, item) -> int:
        """Return a custom ``icon_value`` for the row's leading icon, overriding the built-in
        ``draw_main_icon()`` string enum for this one row."""
        return 0

    # ------------------------------------------------------------------
    # Hook methods – sensible defaults, override as needed
    # ------------------------------------------------------------------

    def get_search_text(self, item) -> str:
        """Text matched against the wildcard filter (default: ``item.name``)."""
        return getattr(item, 'name', '')

    def extra_keep_predicate(self, context, data, item, original_idx: int) -> bool:
        """Additional item-visibility filter beyond the search bar."""
        return True

    def is_active(self, context, data, index: int, active_data, active_propname: str) -> bool:
        """Return *True* if *index* is the active row."""
        active_index = getattr(active_data, active_propname, -1)
        return index == active_index

    def draw_extra_icon(self, context, layout, data, item, index: int):
        """Draw the per-row icon/button on the left edge."""
        pass

    def _ensure_filter_dependencies(self, context, data):
        """Read extra properties so Blender's dependency tracker re-runs ``filter_items`` when
        they change."""
        pass

    # ------------------------------------------------------------------
    # Shared compute_visible_keys — canonical "what's currently visible"
    # ------------------------------------------------------------------

    def compute_visible_keys(self, context, data, propname, filter_state=None) -> list:
        """Return item keys in the filtered+ordered sequence the UIList displays. Without
        *filter_state* (``(pattern, invert)``), the last state drawn for *propname* is used."""
        items = getattr(data, propname)
        try:
            order = self.get_display_order(context, data)
        except Exception:
            traceback.print_exc()
            order = list(range(len(items)))

        if filter_state is None:
            filter_state = _last_filter.get(propname, ("", False))
        pattern, invert = filter_state

        visible_keys = []
        for original_idx in order:
            if original_idx < 0 or original_idx >= len(items):
                continue
            item = items[original_idx]

            if pattern and _matches(pattern, self.get_search_text(item)) == invert:
                continue

            if not self.extra_keep_predicate(context, data, item, original_idx):
                continue

            visible_keys.append(self.get_item_key(item))

        return visible_keys

    # ------------------------------------------------------------------
    # Shared filter_items implementation
    # ------------------------------------------------------------------

    def filter_items(self, context, data, propname):
        """Build filtered and ordered indices for the list."""
        items = getattr(data, propname)
        n = len(items)
        filter_flags = [self.bitflag_filter_item] * n
        filter_neworder = [0] * n

        # Ensure Blender re-runs us when domain-specific toggles change
        self._ensure_filter_dependencies(context, data)

        try:
            filter_state = (self.filter_name.strip(), self.use_filter_invert)
            _last_filter[propname] = filter_state
            visible_keys = self.compute_visible_keys(context, data, propname, filter_state)
        except Exception:
            traceback.print_exc()
            return filter_flags, list(range(n))

        # Build key → original index map for translation
        key_to_orig_idx = {}
        for idx, item in enumerate(items):
            key_to_orig_idx[self.get_item_key(item)] = idx

        visible_orig_indices = set()
        visible_queue = []
        for key in visible_keys:
            orig_idx = key_to_orig_idx.get(key)
            if orig_idx is not None:
                visible_queue.append(orig_idx)
                visible_orig_indices.add(orig_idx)

        # Everything else is hidden
        hidden_queue = [i for i in range(n) if i not in visible_orig_indices]

        # Assign sequential slots: visible first, then hidden
        slot = 0
        for orig_idx in visible_queue:
            filter_neworder[orig_idx] = slot
            slot += 1
        for orig_idx in hidden_queue:
            filter_neworder[orig_idx] = slot
            filter_flags[orig_idx] &= ~self.bitflag_filter_item
            slot += 1

        return filter_flags, filter_neworder

    # ------------------------------------------------------------------
    # Hook methods — subclasses SHOULD override to wire their own operator
    # ------------------------------------------------------------------

    def get_row_operator_id(self, item=None) -> str:
        """Return the ``bl_idname`` of the operator that fires on row click."""
        raise NotImplementedError("get_row_operator_id()")

    def set_row_operator_props(self, op, item):
        """Set the per-domain properties on a freshly-created row operator."""
        raise NotImplementedError("set_row_operator_props()")

    # ------------------------------------------------------------------
    # Shared draw_item implementation
    # ------------------------------------------------------------------

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        """Draw one row: an optional extra icon/button at the far left, then the main icon and
        name button (wired to the domain's dedicated select operator)."""
        if not item:
            return

        key = self.get_item_key(item)
        selected = self.is_selected(context, data, key)
        active = self.is_active(context, data, index, active_data, active_propname)

        main_row = layout.row(align=True)
        main_row.scale_y = self.row_scale_y
        item_split = main_row.split(factor=0.12, align=True)

        left_zone = item_split.row(align=True)
        left_zone.alignment = 'LEFT'
        self.draw_extra_icon(context, left_zone, data, item, index)

        text_row = item_split.row(align=True)

        main_icon = self.draw_main_icon(context, data, item)
        main_icon_value = self.draw_main_icon_value(context, data, item)

        if selected and not active:
            text_row.alert = True
            text_row.active = True

        icon_kwargs = {"icon_value": main_icon_value} if main_icon_value else {"icon": main_icon}
        op_text = text_row.operator(
            self.get_row_operator_id(item),
            text=self.get_search_text(item), emboss=False, **icon_kwargs,
        )
        self.set_row_operator_props(op_text, item)
