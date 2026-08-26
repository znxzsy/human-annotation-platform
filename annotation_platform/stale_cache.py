from __future__ import annotations

import threading
import time


class StaleCache:
    """A tiny stale-while-revalidate cache with one in-flight loader."""

    def __init__(self, loader, ttl_seconds, clock=None):
        self.loader = loader
        self.ttl_seconds = float(ttl_seconds)
        self.clock = clock or time.monotonic
        self._condition = threading.Condition()
        self._value = None
        self._created_at = 0.0
        self._has_value = False
        self._refreshing = False

    def _finish_refresh(self, background):
        try:
            value = self.loader()
        except Exception:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            if not background:
                raise
            return
        with self._condition:
            self._value = value
            self._created_at = self.clock()
            self._has_value = True
            self._refreshing = False
            self._condition.notify_all()

    def get(self):
        start_background = False
        run_synchronously = False
        with self._condition:
            if self._has_value:
                stale = self.clock() - self._created_at >= self.ttl_seconds
                if stale and not self._refreshing:
                    self._refreshing = True
                    start_background = True
                value = self._value
            else:
                if self._refreshing:
                    while self._refreshing and not self._has_value:
                        self._condition.wait()
                    if self._has_value:
                        return self._value
                self._refreshing = True
                run_synchronously = True
                value = None

        if start_background:
            threading.Thread(
                target=self._finish_refresh,
                args=(True,),
                name="annotation-stale-cache-refresh",
                daemon=True,
            ).start()
            return value
        if run_synchronously:
            self._finish_refresh(False)
            with self._condition:
                return self._value
        return value
