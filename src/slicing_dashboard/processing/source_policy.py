"""Shared cache lifetime and query serialization for chart data sources."""
from functools import wraps
import inspect
from threading import Lock, RLock

SOURCE_TTL_SECONDS = 60
_INITIALIZE = Lock()


def serialized_source(function):
    """An older request for the same scope cannot replace a newer local read."""
    signature = inspect.signature(function)

    @wraps(function)
    def read(manager, *args, **kwargs):
        arguments = signature.bind(manager, *args, **kwargs)
        arguments.apply_defaults()
        key = (function.__name__, *(arguments.arguments.get(name) for name in
               ('start_date', 'end_date', 'start', 'end', 'role', 'status', 'target_date')))
        with _INITIALIZE:
            if not hasattr(manager, '_source_read_locks'):
                manager._source_read_locks = {}
            lock = manager._source_read_locks.setdefault(key, RLock())
        with lock:
            return function(manager, *args, **kwargs)
    return read
