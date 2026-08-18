"""ranbench: the CNTC RAN test engine (Stage 3).

Sibling to ``upfbench`` (user plane) and ``cpbench`` (core control plane): same plugin
architecture, same ``cntc_common`` result schema, graded by the same ``cntc`` umbrella.

Where ``cpbench`` drives the core's network functions, ``ranbench`` drives a **disaggregated
NG-RAN node**, the O-CU-CP, O-CU-UP and O-DU product classes of 3GPP TS 33.523, over their
real interfaces (N2/NGAP, F1-C/F1AP, E1/E1AP, F1-U and N3 GTP-U, and RRC as carried inside the
F1AP RRC containers), each test anchored to that class's 3GPP protocol + SCAS spec.

The split matters: in a CU/DU deployment every RRC message crosses F1 wrapped in an F1AP
container (TS 38.473 clause 8.4), so RRC conformance and AS-security activation are observable
on a normal IP link, no radio, no PHY decoding, no key extraction.

See ~/cntc-ran-docs/RANBENCH-PLAN.md for the full plan and test catalog.
"""
__version__ = "0.1.0"
