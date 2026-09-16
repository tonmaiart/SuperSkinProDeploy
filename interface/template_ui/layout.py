"""Shared layout helper — list + search + a button toolbar above it.

Canonical location: shared/list_widget/layout.py
The copy at ui/list_widget/layout.py is a compatibility shim.

``draw_list_with_sidebar`` replaces the hand-rolled ``template_list`` +
search-row + side-button code that was duplicated in both
``widget_deform_bones.py`` and ``widget_tools.py``. The function name is
historical -- its buttons drew in a right-hand sidebar column originally,
moved to a horizontal toolbar row above the list (2026-09-13), moved back
to the side column the same day per a further request, then moved back to
the toolbar row here again (2026-09-14, per explicit user request); kept
unrenamed to avoid a blast-radius rename across every caller/doc reference
for a purely cosmetic layout change.

``draw_lists_side_by_side`` (added 2026-09-13) is the native two-list
counterpart -- see its own docstring below.
"""


def draw_list_with_sidebar(layout, context, *,
                           ui_list_idname: str,
                           data,
                           collection_prop: str,
                           active_data,
                           active_prop: str,
                           search_owner=None,
                           search_prop: str = None,
                           rows: int = 8,
                           button_defs=(),
                           list_enabled: bool = True,
                           after_list_fn=None):
    """Draw a ``template_list`` with an optional search box and a button
    toolbar above it.

    The list, its search box, and the button toolbar all stack in one
    column -- the toolbar sits directly above ``template_list``, rather
    than in a separate right-hand column.

    Args:
        layout: The Blender ``UILayout`` to draw into.
        context: ``bpy.context``.
        ui_list_idname: Registered UIList class name.
        data: Data-block owning the collection property.
        collection_prop: Name of the collection property on *data*.
        active_data: Data-block owning the active-index property.
        active_prop: Name of the active-index property on *active_data*.
        search_owner: Data-block owning the search property.
        search_prop: Name of the search StringProperty on *search_owner*.
        rows: Number of visible rows in the list (default 8).
        list_enabled: When ``False``, the button toolbar, the
            ``template_list`` itself, and its search row are all greyed
            out and non-interactive (e.g. no mesh yet, or the mesh has no
            underlying collection data). Replaces the older caller-side
            pattern of wrapping the entire call in a disabled column.
        after_list_fn: Optional ``(col_list) -> None`` callback, invoked
            immediately after ``template_list`` (before the search row).
        button_defs: Iterable of button definitions for the toolbar row,
            drawn left to right above the list. Each entry is one of:

            * ``("operator", op_idname, icon, text, extra_props_dict)``
            * ``("operator", op_idname, icon, text, extra_props_dict, depress)``
              -- 6-tuple variant; ``depress`` is a ``bool`` or a
              ``(context) -> bool`` callable, drawing the button in its
              pressed visual state without needing a bound BoolProperty
              (which would fire its ``update`` callback on every
              programmatic write, not just an explicit click).
              ``icon`` may be an ``int`` (or a ``(context) -> int``
              callable) instead of a built-in icon identifier string --
              drawn via ``icon_value=`` instead of ``icon=`` so a custom
              ``bpy.utils.previews`` glyph (``interface/utils/icons.py``)
              can be used here, same convention as everywhere else in the
              codebase that draws a custom icon with a built-in fallback.
            * ``("toggle", owner, prop_name, icon)``
            * ``("enum_radio", owner, prop_name, enum_value, icon)``
            * ``("menu", menu_idname, icon, text)`` -- ``icon`` may be an
              ``int`` (or a ``(context) -> int`` callable) instead of a
              built-in icon identifier string, same convention as the
              ``"operator"`` kind above -- drawn via ``icon_value=``.
            * ``("separator", factor)``
            * ``("spacer",)`` -- a flexible, expanding gap
              (``UILayout.separator_spacer()``) rather than a fixed-width
              one, used to push whatever follows it to the toolbar's far
              right edge.
    """
    # Single column: the button toolbar, then list + search. `col_list`
    # itself is never disabled -- only the toolbar row and `list_body` (a
    # nested column created below) carry *list_enabled*.
    col_list = layout.column(align=False)
    col_list.scale_y = 1

    # Button toolbar -- a horizontal row directly above the list, instead
    # of a narrow right-hand column.
    if button_defs:
        btn_row = col_list.row(align=True)
        btn_row.enabled = list_enabled

        for entry in button_defs:
            kind = entry[0]

            if kind == "operator":
                if len(entry) == 6:
                    _, op_idname, icon, text, extra_props, depress = entry
                else:
                    _, op_idname, icon, text, extra_props = entry
                    depress = False
                icon_val = icon(context) if callable(icon) else icon
                depress_val = bool(depress(context) if callable(depress) else depress)
                if isinstance(icon_val, int):
                    op = btn_row.operator(op_idname, text=text, icon_value=icon_val, depress=depress_val)
                else:
                    op = btn_row.operator(op_idname, text=text, icon=icon_val, depress=depress_val)
                for prop_name, prop_value in extra_props.items():
                    setattr(op, prop_name, prop_value)

            elif kind == "toggle":
                _, owner, prop_name, icon = entry
                icon_str = icon(context) if callable(icon) else icon
                btn_row.prop(owner, prop_name, text="", icon=icon_str, toggle=True)

            elif kind == "enum_radio":
                _, owner, prop_name, enum_value, icon = entry
                icon_str = icon(context) if callable(icon) else icon
                btn_row.prop_enum(owner, prop_name, enum_value, text="", icon=icon_str)

            elif kind == "menu":
                _, menu_idname, icon, text = entry
                icon_val = icon(context) if callable(icon) else icon
                if isinstance(icon_val, int):
                    btn_row.menu(menu_idname, text=text, icon_value=icon_val)
                else:
                    btn_row.menu(menu_idname, text=text, icon=icon_val)

            elif kind == "separator":
                _, factor = entry
                btn_row.separator(factor=factor)

            elif kind == "spacer":
                btn_row.separator_spacer()

            else:
                raise ValueError(f"Unknown button_def kind: {kind!r}")

        col_list.separator(factor=0.3)

    list_body = col_list.column(align=False)
    list_body.enabled = list_enabled
    list_body.template_list(
        ui_list_idname, "",
        data, collection_prop,
        active_data, active_prop,
        rows=rows,
        maxrows=rows,
    )

    if after_list_fn is not None:
        after_list_fn(list_body)

    if search_prop and search_owner is not None:
        row_search = list_body.row(align=True)
        row_search.scale_y = 0.95
        row_search.prop(search_owner, search_prop, text="", icon='VIEWZOOM')


def draw_lists_side_by_side(layout, *, left, right):
    """Draw two independent list widgets side by side in one equal-width
    row -- the native template counterpart of hand-rolling
    ``layout.split(factor=0.5, align=True)`` at a call site (2026-09-13,
    per explicit user request that this domain's two draw calls no longer
    build the split/columns themselves and stitch the two lists together
    after the fact -- the shared template now owns that layout decision).

    *left* and *right* are each a ``(draw_fn, kwargs)`` pair -- *draw_fn* is
    called as ``draw_fn(column, **kwargs)``, where *column* is this
    function's own half-width column. Each *draw_fn* is expected to be a
    domain's own list-drawing wrapper (e.g. ``draw_layer_list()``,
    ``draw_influence_list_system()``) that eventually calls
    ``draw_list_with_sidebar()`` above -- this function only owns the
    outer split, not either list's own internals.

    ``layout.split(factor=0.5, align=True)`` specifically -- not
    ``row()``/``row(align=True)`` with two plain ``.column()`` calls -- is
    required here: a plain row does not guarantee its child columns get
    equal width (Blender can size each by its own content instead), which
    is what let each list's own button toolbar (sized independently, above
    its list) drift out of horizontal alignment with its own
    ``template_list()``/search box below it. ``split(factor=0.5)`` pins
    both columns to exactly half the row's width each, deterministically,
    and ``align=True`` closes Blender's default inter-item gap between
    them. See ``docs/domains/deform_layer_viewer.md``'s "Side-by-side
    layout" section for the three-revision history that arrived at this
    exact combination.
    """
    row = layout.split(factor=0.5, align=True)
    left_fn, left_kwargs = left
    right_fn, right_kwargs = right
    left_fn(row.column(), **left_kwargs)
    right_fn(row.column(), **right_kwargs)
