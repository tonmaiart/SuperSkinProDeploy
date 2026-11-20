
import gc
from contextlib import contextmanager


@contextmanager
def collection_paused():
    if not gc.isenabled():
        yield
        return
    gc.disable()
    try:
        yield
    finally:
        gc.enable()
