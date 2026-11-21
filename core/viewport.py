
import bpy


def tag_redraw_areas(area_types=frozenset({'VIEW_3D'}), window_manager=None) -> None:
    wm = window_manager if window_manager is not None else bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area_types is None or area.type in area_types:
                area.tag_redraw()
