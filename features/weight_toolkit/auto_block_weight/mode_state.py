
_VALID_MODES = ('CLOSEST_BONE', 'HEAT_MAP', 'GEODIVOXEL')

_pending_mode = 'CLOSEST_BONE'


def set_mode(value: str) -> None:
    global _pending_mode
    _pending_mode = value if value in _VALID_MODES else 'CLOSEST_BONE'


def get_mode() -> str:
    return _pending_mode
