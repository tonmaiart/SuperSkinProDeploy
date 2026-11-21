from __future__ import annotations

import collections
import json
import os
import time

from ..dev_records import DevRecordsService

_WINDOW_SIZE = 60
_RECORDS_DIRNAME = "profiler_records"


class ProfilerService:
    """Rolling-window timing recorder. Enabled flag + buffers are class-level.

    Each sample is stored as ``(duration_ms, size)`` where ``size`` is an
    optional caller-supplied input-size hint (e.g. ``len(dirty_verts)`` or a
    mesh's vertex count) -- correlating duration against input size is what
    tells "this is a fixed per-call cost" apart from "this scales with input,
    as expected." ``size`` may be ``None`` for call sites where no single
    input-size number is meaningful; it is simply excluded from avg_size in
    that case, not treated as zero.
    """

    _enabled: bool = False
    _metrics: dict = {}
    _call_counts: dict = {}

    @classmethod
    def set_enabled(cls, value: bool) -> None:
        cls._enabled = bool(value)

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def record(cls, key: str, duration_ms: float, size: int | None = None) -> None:
        buf = cls._metrics.get(key)
        if buf is None:
            buf = collections.deque(maxlen=_WINDOW_SIZE)
            cls._metrics[key] = buf
        buf.append((duration_ms, size))
        cls._call_counts[key] = cls._call_counts.get(key, 0) + 1

    @classmethod
    def get_stats(cls) -> dict:
        stats = {}
        for key, buf in cls._metrics.items():
            if not buf:
                continue
            durations = [d for d, _ in buf]
            sizes = [s for _, s in buf if s is not None]
            stats[key] = {
                "last_ms": durations[-1],
                "avg_ms": sum(durations) / len(durations),
                "min_ms": min(durations),
                "max_ms": max(durations),
                "call_count": cls._call_counts.get(key, 0),
                "avg_size": (sum(sizes) / len(sizes)) if sizes else None,
            }
        return stats

    @classmethod
    def clear(cls) -> None:
        cls._metrics.clear()
        cls._call_counts.clear()

    @classmethod
    def export_to_file(cls) -> str:
        records_dir = DevRecordsService.get_dir(_RECORDS_DIRNAME)

        stats = cls.get_stats()
        payload = {
            key: {
                **stats[key],
                "sample_count": len(cls._metrics[key]),
                "samples": [{"ms": d, "size": s} for d, s in cls._metrics[key]],
            }
            for key in stats
        }

        filename = f"profile_{time.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(records_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return filepath


class profile_section:
    """Zero-overhead-when-disabled context manager for one timed block.

    Mirrors the ``_PROFILE_COMPOSITOR`` gate shape in
    core_subsystems/layer_compositor/codec.py: checks the enabled flag once
    in __enter__ and only calls time.perf_counter() if true, so a disabled
    profiler costs one attribute read and nothing else.

    Usage:
        with CoreFacade.profile_section("weight_apply.add.rust_ffi", size=len(dirty_verts)):
            ...
    """

    __slots__ = ("key", "size", "t0")

    def __init__(self, key: str, size: int | None = None) -> None:
        self.key = key
        self.size = size
        self.t0 = None

    def __enter__(self) -> "profile_section":
        if ProfilerService.is_enabled():
            self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.t0 is not None:
            ProfilerService.record(self.key, 1000.0 * (time.perf_counter() - self.t0), self.size)
        return False
