"""Per-UE traffic flows that match pfcpsim's session assignment.

pfcpsim (internal/pfcpsim/server.go) assigns, for ``count`` sessions from ``base_id``:
  * uplink TEID = base_id + 10*k          (SessionStep = 10)
  * UE address  = pool_network + 1 + k    (NextIP over the pool, with carry)
for k = 0..count-1. Any N3 traffic must use these exact (TEID, UE-IP) pairs so each
GTP-U flow hits the right per-UE PDR in the UPF. Shared by Suite 1 (single flow) and
Suite 2 (many flows); the pfcpsim control exposes it via ``aligned_flows()``.
"""
from __future__ import annotations

import ipaddress

SESSION_STEP = 10


def session_flows(base_id: int, count: int, ue_pool: str) -> tuple[list[int], list[str]]:
    """Return (teids, ue_ips) parallel lists matching pfcpsim's allocation."""
    base_int = int(ipaddress.ip_network(ue_pool, strict=False).network_address)
    teids = [base_id + SESSION_STEP * k for k in range(count)]
    ue_ips = [str(ipaddress.ip_address(base_int + 1 + k)) for k in range(count)]
    return teids, ue_ips
