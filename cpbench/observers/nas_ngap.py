"""NAS/NGAP wire observer, capture + decode the N1/N2 signaling to assert security
properties (NAS ciphered/integrity-protected after SMC, replay rejected, SUCI vs SUPI).

Phase 2.0: interface only. Phase 2.1 backs this with tshark/pyshark on the N2 SCTP link.
When capture is unavailable/unprivileged, the AMF-SEC-* tests grade 'na', never a pass.
"""
from __future__ import annotations

from typing import Any


class NasNgapObserver:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store

    def available(self) -> bool:
        """Whether packet capture on the N2 path is possible here. Phase 2.0: False."""
        return False

    def assert_nas_protected(self) -> dict[str, Any]:
        return {"ok": False, "not_implemented": True,
                "note": "NAS/NGAP capture pending Phase 2.1"}
