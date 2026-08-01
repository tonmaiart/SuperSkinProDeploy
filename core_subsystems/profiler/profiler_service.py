"""profiler_service -- in-memory rolling-window performance timing.

Consumed exclusively by core/ (via CoreFacade -- see core/facade/README.md's
"Profiler" section). Must not be imported from features/ directly (Import
Invariant #1).

No bpy.context, bpy.ops, or handler registration (INV-3). The enabled flag is
a plain class attribute toggled only via set_enabled() -- this module never
reads bpy.context itself. The WindowManager checkbox in features/profiler/
calls set_enabled() from its own PropertyGroup update= callback.
"""
from __future__ import annotations

import collections
import json
import os
import time

from ..dev_records import DevRecordsService

_WINDOW_SIZE = 60  # ~1 second of samples at the addon's 60Hz gesture interval
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
        """Turn profiling capture on/off.

        Args:
            value: True to start recording samples, False to stop. Never
                reads bpy.context -- callers push this in explicitly.
        """
        cls._enabled = bool(value)

    @classmethod
    def is_enabled(cls) -> bool:
        """Return True if profiling capture is currently active."""
        return cls._enabled

    @classmethod
    def record(cls, key: str, duration_ms: float, size: int | None = None) -> None:
        """Append one sample to *key*'s rolling window, creating it on first use.

        Args:
            key: Metric identifier, e.g. "weight_apply.add.rust_ffi".
            duration_ms: Elapsed time in milliseconds.
            size: Optional input-size hint for this call (e.g. the number of
                vertices this call actually touched). Lets an offline reader
                distinguish "cost scales with input, as expected" from "cost
                stays flat regardless of input -- a fixed per-call overhead
                worth chasing." `None` when no single size number applies.
        """
        buf = cls._metrics.get(key)
        if buf is None:
            buf = collections.deque(maxlen=_WINDOW_SIZE)
            cls._metrics[key] = buf
        buf.append((duration_ms, size))
        cls._call_counts[key] = cls._call_counts.get(key, 0) + 1

    @classmethod
    def get_stats(cls) -> dict:
        """Return per-key summary stats over the current rolling window.

        Returns:
            ``{key: {"last_ms", "avg_ms", "min_ms", "max_ms", "call_count",
            "avg_size"}}``. ``call_count`` is the all-time total (not
            windowed -- reset only by clear()). ``avg_size`` is the average
            of every non-`None` size recorded within the current window, or
            `None` if no sample in the window carried a size hint.
        """
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
        """Discard all recorded samples and reset call counts."""
        cls._metrics.clear()
        cls._call_counts.clear()

    @classmethod
    def export_to_file(cls) -> str:
        """Write all currently recorded metrics to a timestamped JSON file.

        Written to ``<addon_root>/profiler_records/profile_<timestamp>.json``,
        creating the ``profiler_records/`` folder on first use. Includes the
        full raw ``(ms, size)`` sample list per key (not just the summary)
        so an offline reader (human or AI) can recompute percentiles, plot
        duration against input size, or compare distributions across export
        runs -- not just the rolling summary the live panel shows.

        Returns:
            Absolute path to the written file.
        """
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
