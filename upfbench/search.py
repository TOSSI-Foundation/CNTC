"""NDR/PDR binary search (RFC 2544 / ETSI NFV-TST 009).

NDR (No-Drop Rate)   = max offered load with 0% loss.
PDR (Partial Drop)   = max offered load with loss <= tolerance.

The search is generator-agnostic: the caller supplies ``trial(load) -> loss_ratio``
which runs one steady-state trial at the given offered load (in Mpps) and returns the
measured loss ratio in [0, 1]. This is the algorithm RFC 9004 mandates for the
back-to-back/throughput search; reused by Suite 1 (TC-01) and Suite 2 (LT-02).
"""
from __future__ import annotations

from collections.abc import Callable


def binary_search(
    trial: Callable[[float], float],
    *,
    max_load: float,
    min_load: float = 0.0,
    tolerance: float = 0.0,
    resolution: float = 0.01,
    max_iters: int = 20,
) -> dict[str, float]:
    """Find the highest offered load whose loss ratio <= ``tolerance``.

    Returns ``{"rate": Mpps, "loss": ratio, "iters": n}``. ``tolerance=0`` gives NDR;
    ``tolerance=0.001`` gives PDR@0.1%. ``resolution`` is the Mpps step at which the
    search stops narrowing.
    """
    lo, hi = min_load, max_load
    best = min_load
    best_loss = 0.0
    iters = 0
    while hi - lo > resolution and iters < max_iters:
        mid = (lo + hi) / 2.0
        loss = trial(mid)
        iters += 1
        if loss <= tolerance:
            best, best_loss = mid, loss
            lo = mid          # can push harder
        else:
            hi = mid          # back off
    return {"rate": round(best, 4), "loss": round(best_loss, 6), "iters": iters}


def find_rates(
    trial: Callable[[float], float],
    *,
    max_load: float,
    thresholds: dict[str, float],
    min_load: float = 0.0,
    resolution: float = 0.01,
    max_iters: int = 20,
) -> dict[str, object]:
    """One binary search that yields the max passing rate for SEVERAL loss tolerances.

    ``thresholds`` maps a label (e.g. ``{"NDR": 0.0, "PDR": 0.001}``) to its allowed
    loss ratio. The search is driven by the loosest tolerance, and every ``(rate, loss)``
    trial is recorded; each threshold's rate is then the highest sampled rate whose loss
    is within that tolerance. Deriving all results from a SHARED sample set removes the
    run-to-run inconsistency of independent searches and guarantees monotonicity
    (a looser tolerance never yields a lower rate than a stricter one). Returns
    ``{"rates": {label: rate}, "iters": n, "samples": [(rate, loss), ...]}``.
    """
    drive_tol = max(thresholds.values())
    lo, hi = min_load, max_load
    samples: list[tuple[float, float]] = []
    iters = 0
    while hi - lo > resolution and iters < max_iters:
        mid = (lo + hi) / 2.0
        loss = trial(mid)
        iters += 1
        samples.append((mid, loss))
        if loss <= drive_tol:
            lo = mid          # can push harder
        else:
            hi = mid          # back off
    rates = {}
    for label, tol in thresholds.items():
        passing = [r for r, ls in samples if ls <= tol]
        rates[label] = round(max(passing), 4) if passing else round(min_load, 4)
    return {"rates": rates, "iters": iters,
            "samples": [(round(r, 4), round(ls, 6)) for r, ls in samples]}
