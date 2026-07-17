"""cpbench — the CNTC control-plane test engine (Stage 2).

Sibling to ``upfbench``: same plugin architecture, same ``cntc_common`` result schema, graded
by the same ``cntc`` umbrella. Where ``upfbench`` drives a UPF over N3/N4, ``cpbench`` drives
the 5G core control-plane NFs (AMF, SMF, NRF, AUSF, UDM) **per network function**, each test
anchored to that NF's 3GPP protocol + SCAS spec. See docs/PLAN-CONTROL-PLANE.md.
"""
__version__ = "0.1.0"
