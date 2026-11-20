from __future__ import annotations

import os

import bpy

_ADDON_PACKAGE = __package__.rsplit(".", 2)[0]
_LEGACY_CONFIG_SUBDIR = "SuperSkinPro"


class DevRecordsService:
    """Writable per-user folders: the add-on's own install folder may be read-only, so nothing is
    ever written there."""

    @classmethod
    def user_data_dir(cls, sub: str = "") -> str:
        try:
            return bpy.utils.extension_path_user(_ADDON_PACKAGE, path=sub, create=True)
        except ValueError:
            base = bpy.utils.user_resource('CONFIG', path=_LEGACY_CONFIG_SUBDIR, create=True)
            target = os.path.join(base, sub) if sub else base
            os.makedirs(target, exist_ok=True)
            return target

    @classmethod
    def legacy_config_dir(cls) -> str:
        return bpy.utils.user_resource('CONFIG', path=_LEGACY_CONFIG_SUBDIR)

    @classmethod
    def get_dir(cls, name: str) -> str:
        if not name or os.sep in name or (os.altsep and os.altsep in name):
            raise ValueError(
                f"DevRecordsService.get_dir(name) expects a bare folder name, got {name!r}"
            )
        return cls.user_data_dir(name)
