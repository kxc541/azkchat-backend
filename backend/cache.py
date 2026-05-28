import time
import threading


class TTLCache:
    def __init__(self):
        self.store = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            entry = self.store.get(key)
            if not entry:
                return None

            value, expires_at = entry
            if expires_at < time.time():
                self.store.pop(key, None)
                return None

            return value

    def set(self, key, value, ttl=10):
        with self.lock:
            expires_at = time.time() + ttl
            self.store[key] = (value, expires_at)

    def clear(self, key=None):
        with self.lock:
            if key:
                self.store.pop(key, None)
            else:
                self.store.clear()


cache = TTLCache()


def cache_key(*parts):
    return ":".join(str(p) for p in parts)
