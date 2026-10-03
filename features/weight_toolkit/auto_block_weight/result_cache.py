
import numpy as np

_entry = {"key": None, "assignment": None, "extras": {}}


def make_key(obj, mesh, bone_data, selected_verts, rings=False) -> tuple:
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    matrix = tuple(round(x, 6) for row in obj.matrix_world for x in row)
    bones = tuple(
        (name, tuple(round(c, 6) for c in head), tuple(round(c, 6) for c in tail))
        for name, head, tail in bone_data
    )
    return (
        obj.name, mesh.name, len(mesh.vertices), len(mesh.edges), len(mesh.polygons),
        hash(co.tobytes()), matrix, bones, hash(tuple(selected_verts)), bool(rings),
    )


def get(key):
    return _entry["assignment"] if key == _entry["key"] else None


def put(key, assignment) -> None:
    _entry["key"] = key
    _entry["assignment"] = assignment
    _entry["extras"] = {}


def extra(key, name, build):
    if key != _entry["key"]:
        return build()
    extras = _entry["extras"]
    if name not in extras:
        extras[name] = build()
    return extras[name]
