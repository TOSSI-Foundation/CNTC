"""Drivers: how the RAN under test is stimulated.

The RAN inverts the control-plane picture: there, the core was the subject and a UE was the
peer. Here the RAN is the subject, so we must supply *both* peers, a UE below it and a 5G core
above it, and then observe what the RAN did in between.

  * ``ue_*``    drive Uu: cell search, RACH, RRC setup, registration, DRB, data, release.
  * ``core_*``  provide/inspect the AMF (N2) and UPF (N3) peer, and provision subscribers.
  * ``probe_*`` crude non-compliant input for the Level 1 no-crash checks.

Concrete drivers land with the phases that verify them against the live stack (P2-P4); until
then a suite with no driver wired grades its cases 'na', never a pass.
"""
