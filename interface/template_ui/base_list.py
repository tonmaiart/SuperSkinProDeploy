
import traceback
from fnmatch import fnmatch as _fnmatch


_last_filter: dict = {}

SELECTED_ROW_STYLE = 'MENU'


def _matches(pattern: str, text: str) -> bool:
    return _fnmatch(text.lower(), f"*{pattern.lower()}*")


class SuperSkinListMixin:
    """Mixin that supplies ``filter_items`` and ``draw_item`` for UIList subclasses."""

    use_filter_show = True
    row_scale_y = 1


    def domain(self) -> str:
        raise NotImplementedError("domain()")

    def get_item_key(self, item) -> str:
        raise NotImplementedError("get_item_key()")

    def get_display_order(self, context, data) -> list:
        raise NotImplementedError("get_display_order()")

    def is_selected(self, context, data, key: str) -> bool:
        raise NotImplementedError("is_selected()")

    def draw_main_icon(self, context, data, item) -> str:
        raise NotImplementedError("draw_main_icon()")

    def draw_main_icon_value(self, context, data, item) -> int:
        return 0


    def get_search_text(self, item) -> str:
        return getattr(item, 'name', '')

    def extra_keep_predicate(self, context, data, item, original_idx: int) -> bool:
        return True

    def is_active(self, context, data, index: int, active_data, active_propname: str) -> bool:
        active_index = getattr(active_data, active_propname, -1)
        return index == active_index

    def draw_extra_icon(self, context, layout, data, item, index: int):
        pass


    right_zone_factor = 0.0

    def draw_right_icon(self, context, layout, data, item, index: int):
        pass

    def _ensure_filter_dependencies(self, context, data):
        pass


    def compute_visible_keys(self, context, data, propname, filter_state=None) -> list:
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


    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        n = len(items)
        filter_flags = [self.bitflag_filter_item] * n
        filter_neworder = [0] * n

        self._ensure_filter_dependencies(context, data)

        try:
            filter_state = (self.filter_name.strip(), self.use_filter_invert)
            _last_filter[propname] = filter_state
            visible_keys = self.compute_visible_keys(context, data, propname, filter_state)
        except Exception:
            traceback.print_exc()
            return filter_flags, list(range(n))

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

        hidden_queue = [i for i in range(n) if i not in visible_orig_indices]

        slot = 0
        for orig_idx in visible_queue:
            filter_neworder[orig_idx] = slot
            slot += 1
        for orig_idx in hidden_queue:
            filter_neworder[orig_idx] = slot
            filter_flags[orig_idx] &= ~self.bitflag_filter_item
            slot += 1

        return filter_flags, filter_neworder


    def get_row_operator_id(self, item=None) -> str:
        raise NotImplementedError("get_row_operator_id()")

    def set_row_operator_props(self, op, item):
        raise NotImplementedError("set_row_operator_props()")


    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
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

        highlight = selected and not active
        style = SELECTED_ROW_STYLE
        if highlight and style in ('ALERT', 'DEPRESS_ALERT', 'MENU_ALERT'):
            text_row.alert = True
            text_row.active = True

        if self.right_zone_factor > 0:
            text_split = text_row.split(factor=1.0 - self.right_zone_factor, align=True)
            name_zone = text_split.row(align=True)
            right_zone = text_split.row(align=True)
            right_zone.alignment = 'RIGHT'
        else:
            name_zone = text_row
            right_zone = None

        icon_kwargs = {"icon_value": main_icon_value} if main_icon_value else {"icon": main_icon}
        button_highlight = highlight and style in ('DEPRESS', 'DEPRESS_ALERT', 'MENU', 'MENU_ALERT')
        if button_highlight and style in ('MENU', 'MENU_ALERT'):
            name_zone.emboss = 'PULLDOWN_MENU'
        op_text = name_zone.operator(
            self.get_row_operator_id(item),
            text=self.get_search_text(item), emboss=button_highlight,
            depress=button_highlight, **icon_kwargs,
        )
        self.set_row_operator_props(op_text, item)

        if right_zone is not None:
            self.draw_right_icon(context, right_zone, data, item, index)
