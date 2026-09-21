"""Shared layout helper — list + a right-hand button column."""


def draw_list_with_sidebar(layout, context, *,
                           ui_list_idname: str,
                           data,
                           collection_prop: str,
                           active_data,
                           active_prop: str,
                           rows: int = 8,
                           button_defs=(),
                           list_enabled: bool = True,
                           after_list_fn=None):
    """Draw a ``template_list`` with an right-hand button column."""
    row = layout.row(align=False)

    col_list = row.column(align=False)
    col_list.scale_y = 1
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

    if not button_defs:
        return

    col_btns = row.column(align=True)
    col_btns.enabled = list_enabled
    col_btns.scale_y = 1
    col_btns.ui_units_x = 1

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
                op = col_btns.operator(op_idname, text=text, icon_value=icon_val, depress=depress_val)
            else:
                op = col_btns.operator(op_idname, text=text, icon=icon_val, depress=depress_val)
            for prop_name, prop_value in extra_props.items():
                setattr(op, prop_name, prop_value)

        elif kind == "toggle":
            _, owner, prop_name, icon = entry
            icon_str = icon(context) if callable(icon) else icon
            col_btns.prop(owner, prop_name, text="", icon=icon_str, toggle=True)

        elif kind == "enum_radio":
            _, owner, prop_name, enum_value, icon = entry
            icon_str = icon(context) if callable(icon) else icon
            col_btns.prop_enum(owner, prop_name, enum_value, text="", icon=icon_str)

        elif kind == "menu":
            _, menu_idname, icon, text = entry
            icon_val = icon(context) if callable(icon) else icon
            if isinstance(icon_val, int):
                col_btns.menu(menu_idname, text=text, icon_value=icon_val)
            else:
                col_btns.menu(menu_idname, text=text, icon=icon_val)

        elif kind == "separator":
            _, factor = entry
            col_btns.separator(factor=factor)

        elif kind == "spacer":
            col_btns.separator_spacer()

        else:
            raise ValueError(f"Unknown button_def kind: {kind!r}")


def draw_lists_side_by_side(layout, *, left, right, factor: float = 0.5):
    """Draw two independent list widgets side by side in one row."""
    row = layout.split(factor=factor, align=True)
    left_fn, left_kwargs = left
    right_fn, right_kwargs = right
    left_fn(row.column(), **left_kwargs)
    right_fn(row.column(), **right_kwargs)
