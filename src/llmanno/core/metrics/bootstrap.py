"""Perzentil-Bootstrap über Einheiten oder Gruppen (Cluster), reproduzierbar per Seed."""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import numpy as np

DEFAULT_RESAMPLES = 1000
# Abbruch einer laufenden Auswertung (Projektwechsel, neue Berechnung): Jede Ziehung prüft das
# Ereignis des Threads, der rechnet.
_CANCEL: ContextVar[threading.Event | None] = ContextVar("bootstrap_cancel", default=None)


class Cancelled(Exception):
    """Die Auswertung wurde abgebrochen."""


@contextmanager
def cancellable(event: threading.Event) -> Iterator[None]:
    token = _CANCEL.set(event)
    try:
        yield
    finally:
        _CANCEL.reset(token)


@dataclass(frozen=True)
class Estimate:
    value: float
    low: float
    high: float
    n: int
    resamples: int


def bootstrap(
    statistic: Callable[[np.ndarray], float],
    n: int,
    *,
    clusters: Sequence[Hashable] | None = None,
    resamples: int = DEFAULT_RESAMPLES,
    level: float = 0.95,
    seed: int = 0,
) -> Estimate:
    """`statistic` bekommt ein Index-Array (mit Wiederholungen) und liefert den Kennwert.

    Mit `clusters` werden ganze Gruppen gezogen (etwa Transkripte), weil Einheiten einer
    Gruppe über das Kontextfenster voneinander abhängen. NaN-Ziehungen werden verworfen.
    """
    value = statistic(np.arange(n))
    rng = np.random.default_rng(seed)
    if clusters is None:
        groups = [np.array([i]) for i in range(n)]
    else:
        members: dict[Hashable, list[int]] = {}
        for i, c in enumerate(clusters):
            members.setdefault(c, []).append(i)
        groups = [np.array(m) for m in members.values()]
    draws = []
    cancel = _CANCEL.get()
    for _ in range(resamples):
        if cancel is not None and cancel.is_set():
            raise Cancelled
        chosen = rng.integers(0, len(groups), len(groups))
        result = statistic(np.concatenate([groups[g] for g in chosen]))
        if not np.isnan(result):
            draws.append(result)
    if not draws:
        return Estimate(value, float("nan"), float("nan"), n, 0)
    alpha = (1 - level) / 2
    low, high = np.quantile(draws, [alpha, 1 - alpha])
    return Estimate(value, float(low), float(high), n, len(draws))
