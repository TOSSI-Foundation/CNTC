"""The PNF and VNF suites, driven against the real captures in ``tests/data``.

These exercise the actual test cases, not just the decoder underneath them. The two properties
worth locking are the ones that are easy to lose in a refactor and expensive to lose in a
report:

* **the na discipline**: with no evidence, every case must record 'na' with a reason. A case
  that returns 'fail' when it simply could not look is a false accusation, and the whole
  framework rests on that never happening.
* **the direction split**: one absence on one wire is a different verdict for each end. The
  node-sync exchange is the live example, and it is asserted rather than described.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ranbench import config as cfgmod
from ranbench.observers.nfapi import NfapiObserver, PNF_TO_VNF, VNF_TO_PNF
from ranbench.suites.base import RunContext
from ranbench.suites.registry import build_suite

DATA = Path(__file__).parent / "data"
P5 = str(DATA / "nfapi-p5-lifecycle.pcap")
P7 = str(DATA / "nfapi-p7-slice.pcap")


def _observation(p5: str | None = P5, p7: str | None = P7) -> dict:
    """The observation the live UE driver builds, assembled here from the fixtures."""
    nf = NfapiObserver()
    paths = {k: v for k, v in (("p5", p5), ("p7", p7)) if v}
    o: dict = {"ok": True, "pcaps": paths, "nfapi": nf, "synchronized": True,
               "rrc_connected": True, "registration_accept": True, "pdu_session": True}
    for iface, path in paths.items():
        o[f"procs.{iface}.pnf"] = sorted(nf.procedures(path, PNF_TO_VNF) or [])
        o[f"procs.{iface}.vnf"] = sorted(nf.procedures(path, VNF_TO_PNF) or [])
        o[f"counts.{iface}"] = nf.counts(path)
        o[f"transport.{iface}"] = sorted(nf.transport(path) or [])
        o[f"epoch0.{iface}"] = nf.epoch0(path)
    if "p7" in paths:
        o["slot"] = nf.slot_timing(paths["p7"])
        first = (nf.messages(paths["p7"]) or [None])[0]
        o["p7_first_t"] = first.t if first else None
    return o


class _Ran:
    name = "fapi_split"
    def node_alive(self, target):   return None      # unobservable offline -> those grade na
    def node_endpoint(self, target): return ""
    def interface_addrs(self, iface): return ["127.0.0.1"]


def _ctx(target: str, obs: dict) -> RunContext:
    class _Ue:
        def observe_attach(self, ran, observer):
            return obs
    cfg = cfgmod.load("configs/fapi-split.yaml")
    return RunContext(cfg=cfg, ran=_Ran(), core=None, ue=_Ue(), observer=None, store=None,
                      target=target, endpoint="", knobs=cfg.knobs.get(target, {}))


def _run(target: str, obs: dict) -> dict[str, object]:
    return {c.id: c.run(_ctx(target, obs)) for c in build_suite(target)}


# --- every case must run, and produce a legal status -------------------------------
@pytest.mark.parametrize("target", ["pnf", "vnf"])
def test_every_case_runs_and_returns_a_legal_status(target):
    results = _run(target, _observation())
    catalog_ids = [c.id for c in build_suite(target)]
    assert sorted(results) == sorted(catalog_ids)
    for tid, r in results.items():
        assert r.status in ("pass", "fail", "na"), f"{tid} returned {r.status!r}"
        assert r.notes, f"{tid} produced no reason"


@pytest.mark.parametrize("target", ["pnf", "vnf"])
def test_real_captures_produce_real_verdicts(target):
    """A suite that only ever answers 'na' would be useless and would look like it worked."""
    results = _run(target, _observation())
    passes = [t for t, r in results.items() if r.status == "pass"]
    assert len(passes) >= 8, f"{target}: only {len(passes)} cases reached a pass"


# --- the na discipline -------------------------------------------------------------
@pytest.mark.parametrize("target", ["pnf", "vnf"])
def test_no_capture_is_na_everywhere_never_fail(target):
    """With nothing captured, nothing can be judged.

    A 'fail' here would accuse a product of not performing a procedure when the truth is that
    nobody looked. Every case must say so instead.
    """
    blind = {"ok": True, "pcaps": {}, "decode_error": "no nFAPI capture was produced"}
    for tid, r in _run(target, blind).items():
        assert r.status == "na", f"{tid} returned {r.status!r} with no evidence: {r.notes}"


@pytest.mark.parametrize("target", ["pnf", "vnf"])
def test_a_broken_attach_is_na_never_fail(target):
    for tid, r in _run(target, {"error": "the UE binary is not built"}).items():
        assert r.status == "na", f"{tid} returned {r.status!r} on a failed attach"


# --- the direction split, which is the point of the design -------------------------
def test_one_absence_is_attributed_to_one_side():
    """Neither end exchanged node sync in these captures.

    For the VNF that is a failure: it is the side that initiates DL_NODE_SYNC and it did not.
    For the PNF it is 'na': it was never asked, and a product cannot be failed for not answering
    a question nobody put to it. Same wire, same absence, two different verdicts.
    """
    obs = _observation()
    pnf = _run("pnf", obs)["PNF-SYNC-01"]
    vnf = _run("vnf", obs)["VNF-SYNC-01"]
    assert vnf.status == "fail", vnf.notes
    assert pnf.status == "na", pnf.notes
    assert "never sent DL_NODE_SYNC" in pnf.notes


def test_pnf_is_not_blamed_for_a_request_the_vnf_never_made():
    """Strip the VNF's side of P5 and the PNF's responses become unjudgeable, not absent."""
    obs = _observation()
    obs["procs.p5.vnf"] = []
    for tid in ("PNF-P5-01", "PNF-P5-02", "PNF-P5-03", "PNF-P5-04", "PNF-P5-06"):
        r = _run("pnf", obs)[tid]
        assert r.status == "na", f"{tid}: {r.status} {r.notes}"
        assert "never sent" in r.notes


# --- specific verdicts the fixtures should produce ---------------------------------
def test_p5_transport_passes_on_sctp():
    for target, tid in (("pnf", "PNF-P5-09"), ("vnf", "VNF-P5-07")):
        r = _run(target, _observation())[tid]
        assert r.status == "pass" and "SCTP" in r.notes


def test_slot_sequence_passes_on_an_intact_loop():
    r = _run("pnf", _observation())["PNF-P7-02"]
    assert r.status == "pass"
    assert r.metrics["breaks"] == 0


def test_slot_cadence_is_not_certified_on_a_simulated_radio():
    """The rig declares radio: simulated, so the figure is recorded and not graded. Measured
    twice on the same hardware it differed threefold, which is the reason."""
    r = _run("pnf", _observation())["PNF-P7-01"]
    assert r.status == "na"
    assert "radio simulator" in r.notes
    assert r.metrics.get("rate_hz") is not None, "the measurement must still be recorded"


def test_slot_cadence_is_graded_when_the_radio_is_real():
    """Declaring hardware turns the same measurement into a verdict."""
    obs = _observation()
    ctx = _ctx("pnf", obs)
    ctx.knobs = {**ctx.knobs, "radio": "hardware"}
    case = next(c for c in build_suite("pnf") if c.id == "PNF-P7-01")
    r = case.run(ctx)
    assert r.status in ("pass", "fail")


def test_tti_lead_tolerates_one_slot_of_capture_skew_but_records_it():
    """A request trailing by a single slot is inside the resolution of two independently
    captured directions, so it must not fail, and it must not be hidden either."""
    r = _run("vnf", _observation())["VNF-P7-01"]
    assert r.status == "pass"
    assert "within_capture_resolution" in r.metrics
    assert r.metrics["late"] == 0


# --- the host-jitter guard on tti_lead (VNF-P7-01/02/05) -----------------------------
def _slotslot(sfn: int, slot: int) -> bytes:
    return sfn.to_bytes(2, "big") + slot.to_bytes(2, "big")


def _late_tti_obs(gap_max_ms: float):
    """An observation with one DL_TTI naming a slot the PHY has passed by three, and a slot
    stream whose worst gap is `gap_max_ms`. Everything else the suite needs is absent, so only
    tti_lead is exercised."""
    from ranbench.observers.nfapi import Msg
    msgs = [
        Msg(0.0, 0x82, "SLOT.indication", "pnf->vnf", "udp", _slotslot(0, 0)),
        Msg(0.9, 0x82, "SLOT.indication", "pnf->vnf", "udp", _slotslot(0, 10)),
        Msg(0.9, 0x80, "DL_TTI.request", "vnf->pnf", "udp", _slotslot(0, 7)),   # PHY at 10
    ]

    class _FakeNf:
        def messages(self, _p):    return msgs
        def slot_timing(self, _p): return {"gap_max_ms": gap_max_ms, "gap_p50_ms": 1.0}

    return {"ok": True, "pcaps": {"p7": "x"}, "nfapi": _FakeNf()}


def test_tti_lead_is_na_not_fail_when_the_host_stalled_the_slot_loop():
    """A late TTI on a non-isolated host, where the SLOT stream shows multi-hundred-ms stalls,
    is the OS descheduling the stack, not the VNF. It must record na, never fail, or the check
    blames the product for the rig."""
    from ranbench.suites import fapi_common as fc
    r = fc.tti_lead(_ctx("vnf", _late_tti_obs(835.0)), "VNF-P7-01", "x",
                    "DL_TTI.request", "SCF225 delay management")
    assert r.status == "na"
    assert "host stalls" in (r.notes or "")


def test_tti_lead_still_fails_a_late_tti_on_a_clean_slot_loop():
    """Same lateness, but no host stall (an isolated rig): the guard is a no-op and the late
    request is a real VNF fault that must fail."""
    from ranbench.suites import fapi_common as fc
    r = fc.tti_lead(_ctx("vnf", _late_tti_obs(1.2)), "VNF-P7-01", "x",
                    "DL_TTI.request", "SCF225 delay management")
    assert r.status == "fail"
    assert r.metrics.get("late") == 1
