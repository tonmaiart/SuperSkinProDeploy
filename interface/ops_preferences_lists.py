"""Scrollable UIList classes for Preferences collections that can grow without bound (e.g.
mirror search/replace pairs via "Add Pair")."""

import bpy

_classes = []


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
