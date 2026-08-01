"""SuperSkinListMixin — shared UIList filtering and drawing logic.

Canonical location: shared/list_widget/base_list.py
The copy at ui/list_widget/base_list.py is a compatibility shim
that re-exports from here.

Concrete UIList subclasses must:
1. Inherit from ``(SuperSkinListMixin, bpy.types.UIList)``.
2. Override the required hook methods (``domain``, ``get_item_key``,
   ``get_display_order``, ``is_selected``, ``draw_main_icon``).
3. Optionally override ``get_search_text``, ``extra_keep_predicate``,
   ``is_active``, ``draw_extra_icon``, ``get_filter_query``, and
   ``_ensure_filter_dependencies`` for domain-specific behaviour.

The mixin provides the shared ``filter_items`` and ``draw_item``
implementations.
"""

import traceback
from fnmatch import fnmatch as _fnmatch


def _match_wildcard(pattern: str, text: str) -> bool:
    """Match *pattern* against *text* with optional wildcard support."""
    if '*' not in pattern:
        return pattern in text
    return _fnmatch(text, pattern)


class SuperSkinListMixin:
    """Mixin that supplies ``filter_items`` and ``draw_item`` for UIList subclasses.

    Intended usage::

        class MyList(SuperSkinListMixin, bpy.types.UIList):
            ...
    """

    use_filter_show = False

    def draw_filter(self, context, layout):
        """No-op: suppresses Blender's built-in filter row.

        Domains use their own search box (see ``draw_list_with_sidebar``)
        instead of the default filter UI. ``template_list`` always draws
        the filter-toggle icon itself for 'DEFAULT'/'GRID' types — there's
        no public flag to remove that icon — so overriding this to do
        nothing means toggling it reveals an empty row instead of
        Blender's text/case/whole-word/invert/sort controls.
        """
        pass

    # ------------------------------------------------------------------
    # Hook methods – subclasses MUST override these
    # ------------------------------------------------------------------

    def domain(self) -> str:
        """Return the domain identifier (e.g. ``'BONES'`` or ``'LAYERS'``).

        Used to resolve the selection adapter and passed as a property on
        the row-select operator.
        """
        raise NotImplementedError("domain()")

    def get_item_key(self, item) -> str:
        """Stable string identity for the row.

        Bones: the vertex-group name.
        Layers: ``str(item.index)`` (the slot index).
        """
        raise NotImplementedError("get_item_key()")

    def get_display_order(self, context, data) -> list:
        """Return original collection indices in the order they should appear.

        Bones: hierarchy-ordered indices via ``_get_cached_display_order``.
        Layers: identity order ``list(range(len(items)))``.
        """
        raise NotImplementedError("get_display_order()")

    def is_selected(self, context, data, key: str) -> bool:
        """Return *True* if *key* is currently in the domain's selection pool.

        Should delegate to the registered adapter where possible.
        """
        raise NotImplementedError("is_selected()")

    def draw_main_icon(self, context, data, item) -> str:
        """Return the Blender icon enum for the row's leading icon.

        Bones: ``'BONE_DATA'`` (weighted) / ``'BLANK1'`` (empty).
        Layers: ``'NONE'``.
        """
        raise NotImplementedError("draw_main_icon()")

    # ------------------------------------------------------------------
    # Hook methods – sensible defaults, override as needed
    # ------------------------------------------------------------------

    def get_search_text(self, item) -> str:
        """Text matched against the wildcard filter (default: ``item.name``)."""
        return getattr(item, 'name', '')

    def get_filter_query(self, context, data) -> str:
        """Return the user's current search/filter query string.

        Default: reads ``filter_name`` from ``data.superskin_storage``.
        Override if the domain stores its filter text elsewhere.
        """
        storage = getattr(data, 'superskin_storage', None)
        if storage is not None:
            return getattr(storage, 'filter_name', '')
        return ''

    def extra_keep_predicate(self, context, data, item, original_idx: int) -> bool:
        """Additional item-visibility filter beyond the search bar.

        Bones: pinned-selected + hide-empty-bones logic.
        Layers: always *True* (for now).
        """
        return True

    def is_active(self, context, data, index: int, active_data, active_propname: str) -> bool:
        """Return *True* if *index* is the active row.

        Default: compares ``index`` against ``getattr(active_data, active_propname)``.
        """
        active_index = getattr(active_data, active_propname, -1)
        return index == active_index

    def draw_extra_icon(self, context, layout, data, item, index: int):
        """Draw the per-row icon/button on the right side.

        Bones: lock-toggle button.
        Layers: eye-toggle button.
        Default: no-op.
        """
        pass

    def _ensure_filter_dependencies(self, context, data):
        """Read extra properties so Blender's dependency tracker re-runs
        ``filter_items`` when they change.

        Bones override: reads ``adv.bone_list_filter_mode`` so switching it
        re-filters immediately.
        """
        pass

    # ------------------------------------------------------------------
    # Shared compute_visible_keys — canonical "what's currently visible"
    # ------------------------------------------------------------------

    def compute_visible_keys(self, context, data, propname) -> list:
        """Return item keys in the same filtered+ordered sequence the UIList
        currently displays, using the exact same hooks as ``filter_items``.

        Plain instance method. Callers outside the normal draw cycle (e.g.
        adapters) should instantiate a plain-Python twin class that inherits
        *only* ``SuperSkinListMixin`` (not ``bpy.types.UIList``) and call
        this on that instance. ``bpy.types.UIList`` subclasses cannot be
        freely instantiated outside Blender's own ``template_list`` draw
        call — ``bpy_struct.__new__`` rejects zero-arg construction.

        ``filter_items`` delegates to this method so the two can never drift
        apart.
        """
        items = getattr(data, propname)
        try:
            order = self.get_display_order(context, data)
        except Exception:
            traceback.print_exc()
            order = list(range(len(items)))

        query = self.get_filter_query(context, data).strip().lower()
        keywords = query.split() if query else []

        visible_keys = []
        for original_idx in order:
            if original_idx < 0 or original_idx >= len(items):
                continue
            item = items[original_idx]

            if keywords:
                search_text = self.get_search_text(item).lower()
                if not any(_match_wildcard(kw, search_text) for kw in keywords):
                    continue

            if not self.extra_keep_predicate(context, data, item, original_idx):
                continue

            visible_keys.append(self.get_item_key(item))

        return visible_keys

    # ------------------------------------------------------------------
    # Shared filter_items implementation
    # ------------------------------------------------------------------

    def filter_items(self, context, data, propname):
        """Build filtered and ordered indices for the list.

        Delegates to ``compute_visible_keys`` for the filtering logic so
        the two can never drift apart.  Builds the ``filter_flags`` /
        ``filter_neworder`` arrays from the keys returned by that method.
        """
        items = getattr(data, propname)
        n = len(items)
        filter_flags = [self.bitflag_filter_item] * n
        filter_neworder = [0] * n

        # Ensure Blender re-runs us when domain-specific toggles change
        self._ensure_filter_dependencies(context, data)

        try:
            visible_keys = self.compute_visible_keys(context, data, propname)
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
        """Return the ``bl_idname`` of the operator that fires on row click.

        Subclasses MUST override this.  Each domain uses its own dedicated
        operator class so that Blender's per-``bl_idname`` state (redo data,
        last-used properties) cannot bleed across domains.
        """
        raise NotImplementedError("get_row_operator_id()")

    def set_row_operator_props(self, op, item):
        """Set the per-domain properties on a freshly-created row operator.

        Subclasses MUST override this.
        """
        raise NotImplementedError("set_row_operator_props()")

    # ------------------------------------------------------------------
    # Shared draw_item implementation
    # ------------------------------------------------------------------

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        """Draw one row: main icon, name button (wired to the domain's
        dedicated select operator), and an optional extra icon/button
        on the right.
        """
        if not item:
            return

        key = self.get_item_key(item)
        selected = self.is_selected(context, data, key)
        active = self.is_active(context, data, index, active_data, active_propname)

        main_row = layout.row(align=True)
        item_split = main_row.split(factor=0.85, align=True)

        # Left side: name + leading icon
        text_row = item_split.row(align=True)

        main_icon = self.draw_main_icon(context, data, item)

        if selected and not active:
            text_row.alert = True
            text_row.active = True

        op_text = text_row.operator(
            self.get_row_operator_id(item),
            text=self.get_search_text(item), icon=main_icon, emboss=False,
        )
        self.set_row_operator_props(op_text, item)

        # Right side: domain-specific extra icon/button
        right_zone = item_split.row(align=True)
        right_zone.alignment = 'RIGHT'
        self.draw_extra_icon(context, right_zone, data, item, index)
