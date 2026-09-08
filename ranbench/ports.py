"""The well-known transport ports of the split-gNB interfaces.

These are 3GPP assignments, not properties of any one RAN stack, so they belong here rather
than in a vendor adapter. They previously lived in ``ranbench.adapters.ocudu``, which meant a
suite could not name an interface without importing one particular product's adapter.

Ports are how the engine separates the interfaces on the wire. That matters most where two
interfaces share a protocol and a port: F1-U and N3 are both GTP-U on 2152 and are told apart
only by address, so a capture filter for either has to pin the address as well.
"""
from __future__ import annotations

NGAP_SCTP_PORT = 38412      # N2, TS 38.412
E1_SCTP_PORT = 38462        # E1, TS 38.462
F1C_SCTP_PORT = 38472       # F1-C, TS 38.472
GTPU_UDP_PORT = 2152        # F1-U and N3, TS 29.281
