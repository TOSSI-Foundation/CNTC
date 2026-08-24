"""A core that ranbench does not manage: the default assumption for any 5G core.

``ranbench`` certifies the RAN. The core is the peer the gNB needs in order to be exercised at
all, and it belongs to the tester: it may be shared with other work, deployed by hand, or a
product under its own test. So the honest default is to touch nothing and assume nothing.

Use this adapter with any core (Open5GS, OAI-CN5G, SD-Core, a commercial one, hardware) by
setting ``core.adapter: manual``. Every RAN test still runs; ranbench simply does not probe or
provision the core, and the tester is responsible for it being up with the SIM provisioned.

**Writing a real adapter.** Nothing in ranbench requires one. Declare only what your core can
actually do, in ``capabilities()``, and the runner and doctor will use exactly that and skip the
rest, there is no penalty for declaring nothing. The optional capabilities are:

    provision_subscribers(subs)   ensure the SIMs exist, so 5G-AKA can succeed
    amf_reachable()               is N2 accepting? a preflight check, no side effects
    subscriber_data_ready(sub)    can the core serve this SIM's session data? PDU sessions
                                  fail silently when it cannot
    reset_ue_contexts()           clear leftover UE state so a campaign starts defined.
                                  MUTATES the core, so make it opt-in in your own config as
                                  ``core_free5gc_k8s`` does; never reset by default.

A core that offers none of these is fully supported. It only means the tester confirms
readiness themselves, and that a run which fails for core reasons is reported as an incomplete
stimulus rather than diagnosed for them.
"""
from __future__ import annotations

from ranbench.drivers.base import Driver as BaseDriver


class Driver(BaseDriver):
    name = "core_manual"

    def capabilities(self) -> set[str]:
        """Nothing. The core is up, provisioned and managed by the tester."""
        return set()
