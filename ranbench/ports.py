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

# nFAPI, the SCF split 6 interface between the VNF (L2) and the PNF (L1).
#
# These are NOT assignments the way the 3GPP ports above are. SCF225 defines the P5 and P7
# interfaces and their transports (SCTP for P5, UDP for P7) but leaves the port numbers to the
# deployment, so these are the values the reference configs use and every one of them is
# overridable from the campaign file. The engine still needs defaults, because a capture filter
# has to name a port before anything is running.
NFAPI_P5_PNF_PORT = 50000   # SCF225 P5, PNF side
NFAPI_P5_VNF_PORT = 50001   # SCF225 P5, VNF side. The VNF listens here and the PNF dials it
NFAPI_P7_PNF_PORT = 50010   # SCF225 P7, PNF side
NFAPI_P7_VNF_PORT = 50011   # SCF225 P7, VNF side
