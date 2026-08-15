"""Scrollable UIList classes for Preferences collections that can grow without
bound (e.g. mirror search/replace pairs via "Add Pair").

Plain ``template_list`` rows with no filtering or multi-select — unlike the
sealed ``ui/list_widget`` package that backs the bones/layers domains, these
only need a fixed visible-row count so the popup height stops scaling with
user data; everything beyond ``rows`` scrolls instead.

``SUPERSKIN_UL_ramp_stops`` (the custom color-ramp stop list) was removed
along with the "Single Mode Color Ramp" / "Mask / Layer Color Ramp"
preference sections it backed -- see `features/overlay_color/README.md`
(that domain's weight/mask ramps use Blender's own native
`template_color_ramp()` widget instead, which needs no custom UIList at
all). No UIList classes are currently registered here; the module is kept
for the registration-order bookkeeping documented in `interface/README.md`.
"""

import bpy

_classes = []


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
