"""Content loading + image preview helpers for the builtin_tutorial domain.

Tutorial content lives in content/steps.json (an ordered list of
{id, title, image, description} objects) plus screenshots dropped into
content/images/, referenced by filename from each step's "image" field.
Both are read fresh on every call -- no persistent cache -- so editing
tutorial content is picked up the next time the popup is opened, without
needing an F3 Reload Scripts (that's only required for code edits, per the
usual Python hot-reload rules). See docs/domains/builtin_tutorial.md for
the full content-authoring format.
"""

import json
import os

import bpy.utils.previews

_CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")
_STEPS_JSON_PATH = os.path.join(_CONTENT_DIR, "steps.json")
_IMAGES_DIR = os.path.join(_CONTENT_DIR, "images")

_preview_collection = None


def load_steps() -> list:
    """Return the ordered list of tutorial steps from content/steps.json.

    Returns an empty list (never raises) if the file is missing or
    malformed, so a corrupted/absent content file degrades to "no tutorial
    content" instead of breaking the popup or addon registration.
    """
    try:
        with open(_STEPS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def get_step_id(step: dict, index: int) -> str:
    """Stable string id for *step*, falling back to its position in the
    list when the JSON entry has no explicit "id" field. The single source
    of truth for step identity -- every caller that needs to match a step
    against WindowManager.superskin_builtin_tutorial_prefs.active_step must
    go through this function, never re-derive it inline."""
    return str(step.get("id", index))


def get_step_image_icon_id(filename):
    """Return the Blender icon_id for *filename* under content/images/, or
    None if there is no preview collection, no filename, or the file does
    not exist -- callers must treat None as "show a placeholder", never as
    an error.

    Loaded lazily and cached by filename in the module's
    bpy.utils.previews.ImagePreviewCollection so repeatedly opening the
    popup doesn't reload the same image from disk every time.
    """
    if not filename or _preview_collection is None:
        return None
    if filename in _preview_collection:
        return _preview_collection[filename].icon_id
    path = os.path.join(_IMAGES_DIR, filename)
    if not os.path.isfile(path):
        return None
    try:
        preview = _preview_collection.load(filename, path, 'IMAGE')
        return preview.icon_id
    except Exception:
        return None


def register():
    global _preview_collection
    _preview_collection = bpy.utils.previews.new()


def unregister():
    global _preview_collection
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None
