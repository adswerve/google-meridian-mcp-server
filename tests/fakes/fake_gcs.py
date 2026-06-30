"""Minimal in-memory stand-in for the google-cloud-storage surface we use."""

from __future__ import annotations


class _PreconditionFailed(Exception):
    pass


class FakeBlob:
    def __init__(self, store: dict, name: str, counters: dict):
        self._store = store
        self.name = name
        self._counters = counters

    @property
    def generation(self):
        entry = self._store.get(self.name)
        return entry[1] if entry else None

    def exists(self):
        return self.name in self._store

    def upload_from_string(self, text, *, if_generation_match=None, **_):
        cur = self._store.get(self.name)
        cur_gen = cur[1] if cur else 0
        if if_generation_match is not None and if_generation_match != cur_gen:
            raise _PreconditionFailed(self.name)
        self._store[self.name] = (text, cur_gen + 1)

    def download_as_text(self):
        self._counters[self.name.rsplit("/", 1)[-1]] = (
            self._counters.get(self.name.rsplit("/", 1)[-1], 0) + 1
        )
        return self._store[self.name][0]

    def delete(self):
        self._store.pop(self.name, None)


class FakeBucket:
    def __init__(self, store, counters):
        self._store = store
        self._counters = counters

    def blob(self, name):
        return FakeBlob(self._store, name, self._counters)

    def list_blobs(self, prefix="", delimiter=None):
        names = [n for n in self._store if n.startswith(prefix)]
        return [FakeBlob(self._store, n, self._counters) for n in names]


class FakeGcsClient:
    def __init__(self):
        self._store: dict = {}
        self._counters: dict = {}

    def bucket(self, name):
        return FakeBucket(self._store, self._counters)

    def reads_of(self, basename: str) -> int:
        return self._counters.get(basename, 0)
