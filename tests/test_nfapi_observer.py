"""The nFAPI observer, checked against real captures from the L1/L2 split rig.

The fixtures under ``tests/data/`` are unmodified tcpdump output from a live OAI PNF talking to
an OCUDU L2 across xFAPI:

  nfapi-p5-lifecycle.pcap   the whole P5 lifecycle, PNF device level and PHY instance level,
                            through to STOP. 71 KB.
  nfapi-p7-slice.pcap       a 2400-packet window of the P7 slot loop taken around a real
                            RACH.indication, so all ten P7 message types are present. 228 KB.

The single most important test here is ``test_nr_ids_are_not_named_as_their_lte_homonyms``.
5G NR nFAPI reuses the LTE message ids with different meanings, and Wireshark's nfapi dissector
carries only the LTE table, so it renames NR messages rather than failing on them. Anything that
regressed this module onto those names would produce a catalog that judges a PNF on procedures
it never performed, which is the worst failure mode this framework has.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from ranbench.observers.nfapi import MSG_NAMES, NfapiObserver, PNF_TO_VNF, VNF_TO_PNF

DATA = Path(__file__).parent / "data"
P5 = str(DATA / "nfapi-p5-lifecycle.pcap")
P7 = str(DATA / "nfapi-p7-slice.pcap")


@pytest.fixture
def obs():
    return NfapiObserver()


# --- the regression that matters most ---------------------------------------------
# id -> (correct SCF222 NR name, the LTE name Wireshark 3.6.2 reports for the same id)
_NR_VS_LTE = [
    (0x0080, "DL_TTI.request",     "DL_CONFIG.request"),
    (0x0081, "UL_TTI.request",     "UL_CONFIG.request"),
    (0x0082, "SLOT.indication",    "SUBFRAME_INDICATION"),
    (0x0083, "UL_DCI.request",     "HI_DCI0.request"),
    (0x0084, "TX_DATA.request",    "TX.request"),
    (0x0085, "RX_DATA.indication", "HARQ.indication"),
    (0x0087, "UCI.indication",     "RX_ULSCH.indication"),
    (0x0088, "SRS.indication",     "RACH.indication"),
    (0x0089, "RACH.indication",    "SRS.indication"),
]


@pytest.mark.parametrize("msg_id,nr_name,lte_name", _NR_VS_LTE)
def test_nr_ids_are_not_named_as_their_lte_homonyms(msg_id, nr_name, lte_name):
    """Each id must carry its SCF222 NR name, never the LTE message that shares the number."""
    assert MSG_NAMES[msg_id] == nr_name
    assert MSG_NAMES[msg_id] != lte_name


def test_start_response_uses_the_nfapi_v2_id():
    """START.response is 0x0108, not 0x05. Observed on the wire, and 0x0108 is what Wireshark's
    LTE table calls PARAM.request, so reading it from the LTE table is doubly wrong."""
    assert MSG_NAMES[0x0108] == "START.response"
    assert MSG_NAMES[0x0004] == "START.request"


# --- P5 ---------------------------------------------------------------------------
def test_p5_is_carried_on_sctp(obs):
    """SCF225 specifies SCTP for P5. This is evidence for PNF-P5-09 / VNF-P5-07."""
    assert obs.transport(P5) == {"sctp"}


def test_p5_setup_sequence_in_spec_order(obs):
    """The PNF device level completes, then each PHY instance is brought up, in that order."""
    order = ["PNF_PARAM.request", "PNF_PARAM.response",
             "PNF_CONFIG.request", "PNF_CONFIG.response",
             "PNF_START.request", "PNF_START.response",
             "PARAM.request", "PARAM.response",
             "CONFIG.request", "CONFIG.response",
             "START.request", "START.response"]
    times = [obs.first_time(P5, name) for name in order]
    assert all(t is not None for t in times), f"missing: {[n for n,t in zip(order,times) if t is None]}"
    assert times == sorted(times), "P5 setup did not occur in spec order"


def test_p5_direction_splits_requests_from_responses(obs):
    """Direction is what lets one capture judge two product classes. The VNF drives the
    procedure and the PNF answers, so every request is VNF->PNF and every response the reverse."""
    for name in ("PNF_PARAM.request", "PNF_CONFIG.request", "PNF_START.request",
                 "PARAM.request", "CONFIG.request", "START.request"):
        assert obs.first_time(P5, name, direction=VNF_TO_PNF) is not None, name
        assert obs.first_time(P5, name, direction=PNF_TO_VNF) is None, name
    for name in ("PNF_PARAM.response", "PNF_CONFIG.response", "PNF_START.response",
                 "PARAM.response", "CONFIG.response", "START.response"):
        assert obs.first_time(P5, name, direction=PNF_TO_VNF) is not None, name
        assert obs.first_time(P5, name, direction=VNF_TO_PNF) is None, name


def test_p5_teardown_observed(obs):
    """PNF-P5-08: an orderly stop is part of the lifecycle, not just tidiness."""
    procs = obs.procedures(P5)
    assert "STOP.request" in procs and "STOP.response" in procs


# --- P7 ---------------------------------------------------------------------------
def test_p7_is_carried_on_udp(obs):
    assert obs.transport(P7) == {"udp"}


def test_p7_slot_loop_message_set(obs):
    """All ten P7 messages the catalog names are present in the fixture window."""
    procs = obs.procedures(P7)
    for name in ("SLOT.indication", "DL_TTI.request", "UL_TTI.request", "UL_DCI.request",
                 "TX_DATA.request", "RX_DATA.indication", "CRC.indication",
                 "UCI.indication", "RACH.indication", "TIMING_INFO"):
        assert name in procs, f"{name} absent"


def test_p7_indications_come_from_the_pnf_and_requests_from_the_vnf(obs):
    """PNF-P7-* are judged on PNF->VNF messages and VNF-P7-* on the reverse. If this split were
    wrong, each product class would be judged on the other's behaviour."""
    pnf = obs.procedures(P7, direction=PNF_TO_VNF)
    vnf = obs.procedures(P7, direction=VNF_TO_PNF)
    assert {"SLOT.indication", "RACH.indication", "CRC.indication",
            "RX_DATA.indication", "UCI.indication"} <= pnf
    assert {"DL_TTI.request", "UL_TTI.request", "TX_DATA.request", "UL_DCI.request"} <= vnf
    assert not (pnf & vnf), f"a message appeared in both directions: {pnf & vnf}"


def test_slot_sequence_is_intact(obs):
    """PNF-P7-02. SFN wraps 0..1023 and slot runs 0..19 at numerology 1, with no gaps.

    This is the timing property that IS certifiable on a simulated radio, unlike absolute rate.
    """
    st = obs.slot_timing(P7)
    assert st["frames"] > 1000
    assert 0 <= st["sfn_min"] and st["sfn_max"] <= 1023
    assert st["slot_min"] >= 0 and st["slot_max"] == 19
    assert st["breaks"] == 0, f"{st['breaks']} of {st['transitions']} slot transitions broken"


def test_rach_indication_is_a_single_event(obs):
    """One preamble in this window, and it must not be confused with SRS.indication, which is
    the adjacent id and the name Wireshark gives it."""
    counts = obs.counts(P7)
    assert counts["RACH.indication"] == 1
    assert "SRS.indication" not in counts


# --- header layouts ----------------------------------------------------------------
def _p7_packet(msg: bytes) -> bytes:
    """One SLL2 + IPv4 + UDP frame carrying ``msg``, as `tcpdump -i any` would record it."""
    udp = struct.pack(">HHHH", 50010, 50011, 8 + len(msg), 0) + msg
    ip = (struct.pack(">BBHHHBBH", 0x45, 0, 20 + len(udp), 1, 0, 64, 17, 0)
          + bytes([127, 0, 0, 1]) + bytes([127, 0, 0, 1]))
    sll2 = struct.pack(">HHIHBB", 0x0800, 0, 1, 0x0304, 0, 6) + b"\x00" * 8
    return sll2 + ip + udp


def _pcap(frames: list[bytes], linktype: int = 276) -> bytes:
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype)
    for i, f in enumerate(frames):
        out += struct.pack("<IIII", 1700000000 + i, 0, len(f), len(f)) + f
    return out


def test_both_p7_header_layouts_decode(tmp_path, obs):
    """The nFAPI v2 header widened `length` from 16 to 32 bits, and both forms were met on this
    rig. Message id sits at 2:4 either way, so identity must survive, and the body offset must
    be detected rather than assumed: guessing wrong decodes zero messages silently."""
    # 18-byte header (length as uint32), SFN 7 slot 3
    new = struct.pack(">HHIH", 1, 0x0082, 22, 0) + b"\x00" * 8 + struct.pack(">HH", 7, 3)
    # 16-byte header (length as uint16), same message
    old = struct.pack(">HHHH", 1, 0x0082, 20, 0) + b"\x00" * 8 + struct.pack(">HH", 7, 4)
    assert len(new) == 22 and len(old) == 20
    p = tmp_path / "mixed.pcap"
    p.write_bytes(_pcap([_p7_packet(new), _p7_packet(old)]))
    msgs = obs.messages(str(p))
    assert msgs is not None and len(msgs) == 2
    assert all(m.name == "SLOT.indication" for m in msgs)
    # the body must be found in both, which is what proves the header length was detected
    assert struct.unpack(">HH", msgs[0].body[:4]) == (7, 3)
    assert struct.unpack(">HH", msgs[1].body[:4]) == (7, 4)


def test_unknown_id_is_named_not_guessed(tmp_path, obs):
    """An id outside SCF222 must be reported as unknown, never mapped to a near neighbour."""
    msg = struct.pack(">HHIH", 1, 0x00FE, 18, 0) + b"\x00" * 8
    p = tmp_path / "unknown.pcap"
    p.write_bytes(_pcap([_p7_packet(msg)]))
    msgs = obs.messages(str(p))
    assert msgs is not None and msgs[0].name == "UNKNOWN(0x00fe)"


# --- honest failure ----------------------------------------------------------------
@pytest.mark.parametrize("content", [b"", b"not a pcap at all", b"\xa1\xb2\xc3\xd4short"])
def test_unreadable_capture_yields_none_not_an_empty_result(tmp_path, obs, content):
    """None means 'could not be judged' and grades 'na'. An empty set would mean 'the product
    performed no procedures', which is a false accusation."""
    p = tmp_path / "bad.pcap"
    p.write_bytes(content)
    assert obs.messages(str(p)) is None
    assert obs.procedures(str(p)) is None
    assert obs.slot_timing(str(p)) is None


def test_missing_capture_yields_none(obs):
    assert obs.messages("/nonexistent/nope.pcap") is None


# --- P5 header and SCTP reassembly ---------------------------------------------------
def test_p5_bodies_are_extracted(obs):
    """The P5 header is 10 bytes and its length field counts the body, not the whole message.
    Without that, every P5 body decodes as empty and the capability and error-code cases below
    have nothing to read."""
    bodies = {m.name: len(m.body) for m in obs.messages(P5)}
    assert bodies["PARAM.response"] > 100, "PARAM.response carries the capability TLVs"
    assert bodies["CONFIG.response"] > 0, "CONFIG.response carries an error code"
    assert bodies["PNF_PARAM.request"] == 0, "a bare request has no body"


def test_large_p5_message_is_reassembled_across_sctp_chunks(obs):
    """CONFIG.request does not fit one SCTP DATA chunk and is split B..E.

    Decoding each chunk independently reads the middle of the message as a fresh header and
    invents a procedure: on this capture the continuation produced a phantom
    PNF_PARAM.response in the wrong direction. The reassembled body must therefore be larger
    than a single chunk, and the phantom must be absent.
    """
    msgs = obs.messages(P5)
    cfg = next(m for m in msgs if m.name == "CONFIG.request")
    assert len(cfg.body) > 1500, "CONFIG.request was not reassembled across chunks"
    # the phantom appeared as a PNF_PARAM.response travelling vnf->pnf, which cannot happen
    assert obs.first_time(P5, "PNF_PARAM.response", direction=VNF_TO_PNF) is None
    assert [m.name for m in msgs].count("PNF_PARAM.response") == 1


def test_epoch0_puts_two_captures_on_one_clock(obs):
    """PNF-P5-07 compares a P7 message against a P5 one, and they live in different files.
    Capture-relative times cannot be compared across files, so the absolute origin is exposed."""
    e5, e7 = obs.epoch0(P5), obs.epoch0(P7)
    assert e5 and e7 and e5 > 1_600_000_000, "epoch0 must be an absolute unix time"
    start_rsp = obs.epoch0(P5) + obs.first_time(P5, "START.response")
    first_p7 = obs.epoch0(P7) + obs.messages(P7)[0].t
    assert isinstance(start_rsp - first_p7, float)
    assert obs.epoch0("/nonexistent/x.pcap") is None
