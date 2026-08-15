"""Shared ColorRamp read/write helpers for the overlay_color domain.

``native_sync.py`` uses these to snapshot the user's own native
``weight_color_range`` before overriding it, and to restore it afterward.
The weight/mask ramps themselves are hardcoded constants (see
``native_sync.py``'s ``_EDIT_RAMP_STOPS`` / ``_MASK_RAMP_STOPS``) rather than
addon-owned editable ColorRamps, so this module no longer manages any
Texture datablocks.
"""


def read_stops(ramp) -> list:
    """Return a ColorRamp's elements as ``[(position, (r, g, b)), ...]``, sorted."""
    stops = [(el.position, (el.color[0], el.color[1], el.color[2])) for el in ramp.elements]
    stops.sort(key=lambda t: t[0])
    return stops


def write_stops(ramp, stops: list) -> None:
    """Rebuild *ramp*'s elements to match *stops* (list of (pos, rgb-or-rgba)).

    Standard safe pattern for scripting a ColorRamp: trim down to exactly
    one element first (a ColorRamp can never have zero), set it to the
    first stop, then ``.new(pos)`` for every remaining stop -- ``.new()``
    inserts already position-sorted, avoiding the reordering hazards of
    mutating ``.position`` on pre-existing elements in an arbitrary order.
    """
    if not stops:
        return
    stops = sorted(stops, key=lambda t: t[0])
    elements = ramp.elements
    while len(elements) > 1:
        elements.remove(elements[-1])

    def _rgba(c):
        return (c[0], c[1], c[2], c[3] if len(c) > 3 else 1.0)

    pos0, color0 = stops[0]
    elements[0].position = pos0
    elements[0].color = _rgba(color0)
    for pos, color in stops[1:]:
        el = elements.new(pos)
        el.color = _rgba(color)
