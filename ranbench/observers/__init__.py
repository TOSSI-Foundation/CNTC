"""Observers: decode what actually happened on the wire.

The RAN's evidence comes from two independent sources, and the observer merges them:

  1. **The stack's own per-interface pcaps** (OCUDU writes NGAP, F1AP, E1AP, F1-U and N3
     captures when enabled), located via ``RanAdapter.pcap_paths``.
  2. **Our own capture** on the F1-C / E1 / N2 / N3 links (tcpdump), decoded with tshark.

Because RRC crosses F1 inside F1AP RRC containers (TS 38.473 clause 8.4), the same F1AP decode
yields both the F1AP procedure evidence and the RRC procedure evidence, including whether
post-AS-SMC RRC is integrity-protected and ciphered.

The concrete observer lands in P2 alongside the CU-CP suite it serves.
"""
