
import bpy

from .brush_stamps import FALLOFF_IDS, FALLOFF_SHAPES, _FALLOFF_ITEMS, _OLD_FALLOFF_NAMES

_falloff_items_cache = [None, []]


def _falloff_items(_self, _context):
    from ....interface.utils.icons import get_falloff_icon_id
    icon_ids = tuple(get_falloff_icon_id(ident) for ident in FALLOFF_IDS)
    if _falloff_items_cache[0] != icon_ids:
        _falloff_items_cache[0] = icon_ids
        _falloff_items_cache[1] = [
            (ident, label, desc, icon_id or icon, number)
            for (ident, label, desc, icon, number), icon_id in zip(_FALLOFF_ITEMS, icon_ids)
        ]
    return _falloff_items_cache[1]


def _on_brush_changed(self, context):
    from ....core.facade import CoreFacade
    CoreFacade.save_prefs()
    CoreFacade.tag_redraw_areas()


_PROJECTION_ITEMS = (
    ('SURFACE', "Surface", "Paint only the visible surface under the brush"),
    ('SCREEN', "Projected", "Paint every vertex inside the circle on screen, including hidden and back-facing ones"),
    ('VOLUME', "Volume", "Paint every vertex within the brush radius in 3D, including stacked layers such as clothing merged over a body, but not parts that only the pose brought close, such as a hand resting on a knee"),
)


def _get_projected(self):
    return self.brush_projection == 'SCREEN'


def _set_projected(self, value):
    if value:
        self.brush_projection = 'SCREEN'
    else:
        self.brush_projection = 'VOLUME' if self.keep_volume else 'SURFACE'


_BRUSH_MODE_ITEMS = (
    ('SURFACE', "Surface", "Paint the surface under the brush (Alt+F to switch)", 0),
    ('SCREEN', "Projected", "Paint every vertex inside the circle on screen, including hidden and back-facing ones (Alt+F to switch)", 1),
)


def _get_brush_mode(self):
    return 1 if _get_projected(self) else 0


def _set_brush_mode(self, value):
    _set_projected(self, value == 1)


def _on_keep_volume(self, context):
    if self.brush_projection != 'SCREEN':
        self.brush_projection = 'VOLUME' if self.keep_volume else 'SURFACE'
    _on_brush_changed(self, context)
    from ..draw import sync_keep_volume_hud
    sync_keep_volume_hud()


def _on_auto_mirror(self, context):
    _on_brush_changed(self, context)
    from ..draw import sync_auto_mirror_hud
    sync_auto_mirror_hud()


SURFACE_RADIUS_MAX = 1000.0
FALLOFF_DEFAULTS = {'SURFACE': 'SMOOTH', 'SCREEN': 'CONSTANT'}
_FALLOFF_STORE_ITEMS = [(ident, label, desc) for ident, label, desc, _icon, _n in _FALLOFF_ITEMS]


def _side(self):
    return 'screen' if self.brush_projection == 'SCREEN' else 'surface'


def _get_connected_only(self):
    return getattr(self, f"connected_only_{_side(self)}")


def _set_connected_only(self, value):
    setattr(self, f"connected_only_{_side(self)}", value)


def _get_falloff(self):
    return FALLOFF_IDS.index(getattr(self, f"falloff_{_side(self)}"))


def _set_falloff(self, value):
    setattr(self, f"falloff_{_side(self)}", FALLOFF_IDS[value])


PRESSURE_DEFAULTS = {'surface': True, 'screen': False}


def _get_pressure_strength(self):
    return getattr(self, f"use_pressure_strength_{_side(self)}")


def _set_pressure_strength(self, value):
    setattr(self, f"use_pressure_strength_{_side(self)}", value)


CURSOR_COLOR_DEFAULTS = {
    "SURFACE": (1.0, 1.0, 0.0, 0.902),
    "SCREEN": (1.0, 0.0039, 0.0118, 0.902),
}
_CURSOR_COLOR_PROPS = {
    "SURFACE": "cursor_surface_color",
    "SCREEN": "cursor_screen_color",
    "VOLUME": "cursor_surface_color",
}
SCREEN_RADIUS_MAX = 1.0


class SSPrefWeightBrush(bpy.types.PropertyGroup):
    """Weight Brush settings (per-machine)."""
    brush_projection: bpy.props.EnumProperty(
        name="Projection",
        description="How the brush reaches vertices (Alt+F to switch)",
        items=[(ident, label, desc) for ident, label, desc in _PROJECTION_ITEMS],
        default='SURFACE',
        update=_on_brush_changed,
    )
    brush_mode: bpy.props.EnumProperty(
        name="Projection",
        description="How the brush reaches vertices (Alt+F to switch)",
        items=_BRUSH_MODE_ITEMS,
        get=_get_brush_mode,
        set=_set_brush_mode,
        options={'SKIP_SAVE'},
    )
    projected: bpy.props.BoolProperty(
        name="Projected",
        description="Paint every vertex inside the circle on screen, including hidden and back-facing ones",
        get=_get_projected,
        set=_set_projected,
        options={'SKIP_SAVE'},
    )
    keep_volume: bpy.props.BoolProperty(
        name="Keep Volume",
        description="Maintains gaps between overlapping layers (like clothing and body) so they deform together naturally.",
        default=False,
        update=_on_keep_volume,
    )
    brush_radius_surface: bpy.props.FloatProperty(
        name="Surface Radius", default=0.1, min=0.0, max=SURFACE_RADIUS_MAX,
    )
    brush_radius_screen: bpy.props.FloatProperty(
        name="Screen Radius", default=0.1, min=0.0, max=1.0,
    )
    brush_radius: bpy.props.FloatProperty(
        name="Size",
        description=(
            "Brush size (F to adjust in the viewport)"
        ),
        default=0.1, min=0.0, max=SURFACE_RADIUS_MAX,
        get=lambda self: (
            self.brush_radius_screen if self.brush_projection == 'SCREEN'
            else self.brush_radius_surface
        ),
        set=lambda self, value: (
            setattr(self, "brush_radius_screen", min(value, SCREEN_RADIUS_MAX))
            if self.brush_projection == 'SCREEN'
            else setattr(self, "brush_radius_surface", value)
        ),
        update=_on_brush_changed,
    )
    use_pressure_strength_surface: bpy.props.BoolProperty(
        default=PRESSURE_DEFAULTS['surface'], options={'HIDDEN'},
    )
    use_pressure_strength_screen: bpy.props.BoolProperty(
        default=PRESSURE_DEFAULTS['screen'], options={'HIDDEN'},
    )
    use_pressure_strength: bpy.props.BoolProperty(
        name="Strength Pressure",
        description="Use pen pressure to control brush strength.",
        get=_get_pressure_strength,
        set=_set_pressure_strength,
        options={'SKIP_SAVE'},
        update=_on_brush_changed,
    )
    mirror_x: bpy.props.BoolProperty(
        name="Auto Mirror",
        description=(
            "Paint both sides of the mesh at once, mirrored across its X axis. Left bones are "
            "written to their matching right bones"
        ),
        default=False,
        update=_on_auto_mirror,
    )
    connected_only_surface: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    connected_only_screen: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    connected_only: bpy.props.BoolProperty(
        name="Connected Only",
        description=(
            "Limits painting strictly to the connected mesh piece where the stroke began, avoiding "
            "bleed onto adjacent parts."
        ),
        get=_get_connected_only,
        set=_set_connected_only,
        options={'SKIP_SAVE'},
        update=_on_brush_changed,
    )
    falloff_surface: bpy.props.EnumProperty(
        items=_FALLOFF_STORE_ITEMS, default=FALLOFF_DEFAULTS['SURFACE'], options={'HIDDEN'},
    )
    falloff_screen: bpy.props.EnumProperty(
        items=_FALLOFF_STORE_ITEMS, default=FALLOFF_DEFAULTS['SCREEN'], options={'HIDDEN'},
    )
    falloff: bpy.props.EnumProperty(
        name="", description="Brush falloff for every weight action",
        items=_falloff_items, get=_get_falloff, set=_set_falloff, options={'SKIP_SAVE'},
        update=_on_brush_changed,
    )
    cursor_surface_color: bpy.props.FloatVectorProperty(
        name="Surface Color", description="Brush cursor color in Surface projection",
        subtype='COLOR_GAMMA', size=4, min=0.0, max=1.0,
        default=CURSOR_COLOR_DEFAULTS["SURFACE"], update=_on_brush_changed,
    )
    cursor_screen_color: bpy.props.FloatVectorProperty(
        name="Projected Color", description="Brush cursor color in Projected projection",
        subtype='COLOR_GAMMA', size=4, min=0.0, max=1.0,
        default=CURSOR_COLOR_DEFAULTS["SCREEN"], update=_on_brush_changed,
    )


def cursor_color_prop(projection: str) -> str:
    return _CURSOR_COLOR_PROPS[projection]


def get_brush_prefs() -> "SSPrefWeightBrush":
    return bpy.context.window_manager.superskin_weight_brush_prefs


def mirror_axis():
    prefs = getattr(bpy.context.window_manager, "superskin_weight_brush_prefs", None)
    return 0 if prefs is not None and prefs.mirror_x else None


def populate_prefs(data: dict) -> None:
    p = get_brush_prefs()
    projection = data.get("brush_projection", "SURFACE")
    p.keep_volume = bool(data.get("keep_volume", projection == "VOLUME"))
    p.mirror_x = bool(data.get("mirror_x", False))
    p.brush_projection = projection
    legacy_radius = float(data.get("brush_radius", 0.1))
    p.brush_radius_surface = float(data.get("brush_radius_surface", legacy_radius))
    p.brush_radius_screen = float(data.get("brush_radius_screen", legacy_radius))
    p.use_pressure_strength_surface = bool(data.get(
        "use_pressure_strength_surface", data.get("use_pressure_strength", PRESSURE_DEFAULTS['surface'])))
    p.use_pressure_strength_screen = bool(data.get(
        "use_pressure_strength_screen", PRESSURE_DEFAULTS['screen']))
    for side, key in (('surface', 'SURFACE'), ('screen', 'SCREEN')):
        setattr(p, f"connected_only_{side}", bool(data.get(f"connected_only_{side}", False)))
        shape = data.get(f"falloff_{side}", FALLOFF_DEFAULTS[key])
        shape = _OLD_FALLOFF_NAMES.get(shape, shape)
        setattr(p, f"falloff_{side}", shape if shape in FALLOFF_SHAPES else FALLOFF_DEFAULTS[key])
    for projection, default in CURSOR_COLOR_DEFAULTS.items():
        prop = _CURSOR_COLOR_PROPS[projection]
        color = data.get(prop)
        valid = isinstance(color, (list, tuple)) and len(color) == 4
        setattr(p, prop, [float(c) for c in color] if valid else default)


def serialize_prefs() -> dict:
    p = get_brush_prefs()
    return {
        "brush_projection": p.brush_projection,
        "keep_volume": p.keep_volume,
        "mirror_x": p.mirror_x,
        "brush_radius_surface": p.brush_radius_surface,
        "brush_radius_screen": p.brush_radius_screen,
        "use_pressure_strength_surface": p.use_pressure_strength_surface,
        "use_pressure_strength_screen": p.use_pressure_strength_screen,
        "connected_only_surface": p.connected_only_surface,
        "connected_only_screen": p.connected_only_screen,
        "falloff_surface": p.falloff_surface,
        "falloff_screen": p.falloff_screen,
        **{_CURSOR_COLOR_PROPS[proj]: list(getattr(p, _CURSOR_COLOR_PROPS[proj])) for proj in CURSOR_COLOR_DEFAULTS},
    }
