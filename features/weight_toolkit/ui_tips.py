

def draw_tip(layout, lines) -> None:
    tip = layout.column(align=True)
    tip.scale_y = 0.8
    for i, line in enumerate(lines):
        tip.label(text=line, icon='INFO' if i == 0 else 'BLANK1')
