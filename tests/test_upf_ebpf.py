"""UPF eBPF/XDP Dataplane Assurance, catalog grading + eUPF adapter logic.

Proves the optional ``upf-ebpf`` certificate gate is real (all-pass -> PASS -> certificate;
non-eBPF -> every test 'na' -> INCOMPLETE -> no certificate; one essential FAIL -> FAIL), and
that the eUPF adapter synthesizes its N3/N6 counters and BPF introspection correctly from the
eUPF REST API shape, the same ``status_pass`` discipline as the UPF conformance profile.
"""
from __future__ import annotations

import dataclasses

import pytest

from cntc.standards import load_catalog, lint_catalog
from cntc.verdict import evaluate


def _suite(cat, status="pass"):
    return [{"suite": "ebpf",
             "tests": [{"id": t["id"], "name": t["name"], "status": status, "metrics": {}}
                       for t in cat["tests"]]}]


def test_upf_ebpf_catalog_lints_clean():
    assert lint_catalog(load_catalog("upf-ebpf")) == []


def test_all_pass_is_pass():
    cat = load_catalog("upf-ebpf")
    v = evaluate(_suite(cat, "pass"), cat, rig={"adapter": "eupf", "mode": "ebpf_xdp"})
    assert v["result"] == "PASS"
    assert v["essential"]["failed"] == 0 and v["essential"]["na"] == 0


def test_non_ebpf_is_incomplete_no_cert():
    cat = load_catalog("upf-ebpf")
    v = evaluate(_suite(cat, "na"), cat, rig={"adapter": "sdcore_bess", "mode": "af_packet"})
    assert v["result"] == "INCOMPLETE"           # a DPDK/af_packet UPF is not an eBPF UPF


def test_single_essential_fail_is_fail():
    cat = load_catalog("upf-ebpf")
    suites = _suite(cat, "pass")
    essential = next(t["id"] for t in cat["tests"] if t.get("class") == "essential")
    for t in suites[0]["tests"]:
        if t["id"] == essential:
            t["status"] = "fail"
    v = evaluate(suites, cat, rig={"adapter": "eupf", "mode": "ebpf_xdp"})
    assert v["result"] == "FAIL"


# --- eUPF adapter pure logic (mocked REST API) --------------------------------
class _FakeStore:
    def record_command(self, *_a):
        pass


@dataclasses.dataclass
class _Cfg:
    adapter: str = "eupf"
    n3_iface: str = "access"
    n6_iface: str = "core"
    n4_addr: str = ""
    mode: str = "ebpf_xdp"
    extra: dict = dataclasses.field(default_factory=dict)


_API = {
    "config": {"interface_name": ["eth0"], "xdp_attach_mode": "generic",
               "pdr_map_size": 131070, "far_map_size": 131070, "qer_map_size": 65535,
               "max_sessions": 65535},
    "xdp_stats": {"aborted": 0, "drop": 3, "pass": 100, "tx": 500, "redirect": 0},
    "packet_stats": {"rx_gtp_pdu": 502, "rx_ip4": 502},
    "pfcp_sessions": [{"PDRs": {"1": {"Teid": 100}, "2": {"Teid": 0, "Ipv4": "10.250.0.1"}},
                       "FARs": {"1": {}}, "QERs": {"1": {}}}],
}


def _adapter(monkeypatch):
    from upfbench.adapters.eupf import Adapter
    a = Adapter(_Cfg(), _FakeStore())
    monkeypatch.setattr(a, "_api", lambda p: _API[p])
    monkeypatch.setattr(a, "_bpffs_objects", lambda: ["upf_pipeline"])
    return a


def test_eupf_counters_and_fwd_field(monkeypatch):
    a = _adapter(monkeypatch)
    assert a.dataplane_kind() == "ebpf"
    assert a.fwd_field() == "tx_pkts"
    pc = a.port_counters()
    # N3 rx == GTP-U PDUs seen; N6 tx == XDP forwarded (tx + redirect)
    assert pc["access"]["rx_pkts"] == 502 and pc["access"]["rx_drops"] == 3
    assert pc["core"]["tx_pkts"] == 500


def test_eupf_bpf_introspect_shape(monkeypatch):
    a = _adapter(monkeypatch)
    bi = a.bpf_introspect()
    assert bi["xdp"]["eth0"]["attached"] is True
    assert bi["xdp"]["eth0"]["mode"] == "generic"
    assert 100 in bi["teids"] and bi["maps"]["pdr"]["entries"] == 2   # 2 PDRs installed
    assert bi["maps"]["session"]["entries"] == 1 and bi["maps"]["pdr"]["pinned"] is True


def test_eupf_bpf_introspect_none_when_api_down(monkeypatch):
    from upfbench.adapters.eupf import Adapter

    def boom(_p):
        raise RuntimeError("unreachable")

    a = Adapter(_Cfg(), _FakeStore())
    monkeypatch.setattr(a, "_api", boom)
    assert a.bpf_introspect() is None     # -> XDP tests grade 'na', never a false pass
