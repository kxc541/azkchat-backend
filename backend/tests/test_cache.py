import time
from cache import TTLCache, cache_key


class TestTTLCache:
    def setup_method(self):
        self.cache = TTLCache()

    def test_get_returns_none_for_missing_key(self):
        assert self.cache.get("nonexistent") is None

    def test_set_and_get_value(self):
        self.cache.set("k", {"data": 1}, ttl=10)
        assert self.cache.get("k") == {"data": 1}

    def test_expired_entry_returns_none(self):
        self.cache.set("k", "value", ttl=0.01)
        time.sleep(0.02)
        assert self.cache.get("k") is None

    def test_clear_specific_key(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.clear("a")
        assert self.cache.get("a") is None
        assert self.cache.get("b") == 2

    def test_clear_all_keys(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.clear()
        assert self.cache.get("a") is None
        assert self.cache.get("b") is None

    def test_overwrite_existing_key(self):
        self.cache.set("k", "old")
        self.cache.set("k", "new")
        assert self.cache.get("k") == "new"


class TestCacheKey:
    def test_joins_two_parts(self):
        assert cache_key("admin", "tenants") == "admin:tenants"

    def test_joins_multiple_parts(self):
        assert cache_key("admin", "messages", "uid123", "conv456") == "admin:messages:uid123:conv456"

    def test_coerces_non_strings(self):
        assert cache_key("admin", 42, "files") == "admin:42:files"
