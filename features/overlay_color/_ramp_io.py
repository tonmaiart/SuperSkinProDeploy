"""Shared ColorRamp read/write helpers for the overlay_color domain."""


def read_stops(ramp) -> list:
    """Return a ColorRamp's elements as ``[(position, (r, g, b)), ...]``, sorted."""
    stops = [(el.position, (el.color[0], el.color[1], el.color[2])) for el in ramp.elements]
    stops.sort(key=lambda t: t[0])
    return stops


def write_stops(ramp, stops: list) -> None:
    """Rebuild *ramp*'s elements to match *stops* (list of (pos, rgb-or-rgba))."""
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
