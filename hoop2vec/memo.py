"""Results of slow, deterministic steps for completed seasons, kept on disk and keyed by a hash of their inputs.

Completed seasons don't change from one run to the next, so the daily and live jobs reuse what an earlier run computed
for them; the result is exactly what recomputing would give (any change in the inputs changes the key). Files live
in data/<league>/processed/memo/ (the Actions data cache), one per step and season; a new key replaces the old file.
"""
from __future__ import annotations

import hashlib
import pickle

import numpy as np
import pandas as pd

from .config import PROC

DIR = PROC / "memo"
VERSION = "1"  # bump to invalidate every stored result (e.g. after changing a memoized function)


def _feed(h, x):
    if isinstance(x, pd.DataFrame):
        h.update(repr((list(map(str, x.columns)), [str(t) for t in x.dtypes], x.shape)).encode())
        if len(x):
            h.update(pd.util.hash_pandas_object(x, index=True).to_numpy().tobytes())
    elif isinstance(x, pd.Series):
        _feed(h, x.to_frame())
    elif isinstance(x, np.ndarray):
        h.update(repr((x.dtype.str, x.shape)).encode())
        h.update(np.ascontiguousarray(x).tobytes())
    elif isinstance(x, dict):
        for k in sorted(x, key=repr):
            h.update(repr(k).encode())
            _feed(h, x[k])
    elif isinstance(x, (list, tuple)):
        h.update(f"<{type(x).__name__}{len(x)}>".encode())
        for v in x:
            _feed(h, v)
    else:
        h.update(repr(x).encode())


def key(*parts) -> str:
    h = hashlib.sha1(VERSION.encode())
    for p in parts:
        _feed(h, p)
    return h.hexdigest()[:20]


def cached(name: str, parts: tuple, fn):
    """fn() for these inputs, from disk when an earlier run already computed it. ``name`` should say which step and
    season (e.g. "snaps-2019"); its previous result under another key is deleted."""
    k = key(*parts)
    f = DIR / f"{name}.{k}.pkl"
    if f.exists():
        try:
            with open(f, "rb") as fh:
                return pickle.load(fh)
        except Exception:  # unreadable (e.g. an interrupted write): recompute
            pass
    out = fn()
    DIR.mkdir(parents=True, exist_ok=True)
    for old in DIR.glob(f"{name}.*.pkl"):
        old.unlink(missing_ok=True)
    tmp = f.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(f)
    return out
