"""nFAPI observer: decode the P5 and P7 interface without Wireshark's nfapi dissector.

Every other RAN interface in ranbench is decoded with tshark, because tshark knows F1AP, E1AP
and NGAP and its dissectors are the reference. **nFAPI is the one interface where that is not
true, and using tshark there would produce confidently wrong evidence.**

5G NR nFAPI reuses the LTE nFAPI message ids with different meanings. Wireshark 3.6.2 ships
only the LTE table and registers the dissector on no port, so it stays silent by default; the
moment anyone forces it with ``-d udp.port==50011,nfapi`` it renames every NR message to its LTE
homonym and reports the body as malformed. Measured on a real capture from this rig, seven of
nine message types came back with the wrong name and identical counts:

    on the wire (NR)          tshark 3.6.2 said (LTE)
    SLOT.indication   0x82 -> SUBFRAME_INDICATION
    UL_TTI.request    0x81 -> UL_CONFIG.request
    DL_TTI.request    0x80 -> DL_CONFIG.request
    TX_DATA.request   0x84 -> TX.request
    RX_DATA.indication 0x85 -> HARQ.indication
    UL_DCI.request    0x83 -> HI_DCI0.request
    RACH.indication   0x89 -> SRS.indication

A catalog written against that output would judge a PNF on procedures it never performed. So
this module reads the message id out of the header bytes and names it from the SCF222 NR message
set, which is the same table the implementations compile against.

**Message id is always at offset 2:4.** Two header layouts exist in the wild and this observer
has met both on this rig:

    16 bytes  phy_id(2) message_id(2) length(2)  m_seg(2) checksum(4) transmit_timestamp(4)
    18 bytes  phy_id(2) message_id(2) length(4)  m_seg(2) checksum(4) transmit_timestamp(4)

The nFAPI v2 form widened ``length`` to 32 bits. Message identity is unaffected either way,
which is why procedure evidence is robust; only the body offset moves, so the header length is
detected per message by asking which reading of ``length`` matches the bytes actually present.
Guessing wrong yields a silent zero-message decode, which is why this is detected rather than
configured.

Nothing here shells out. Reading the pcap directly is less code than driving tshark and it
removes the dissector from the evidence path entirely, which is the whole point.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, NamedTuple

# SCF222 (5G FAPI: PHY API Specification, 10th edition) NR message ids, as implemented in
# OAI's nfapi_nr_interface_scf.h. START.response is 0x0108 rather than 0x05 as of nFAPI v2
# (the header cites "SCF 222.10.04 Section 3.2"), which is why it is listed out of sequence.
MSG_NAMES: dict[int, str] = {
    # P5, PHY instance level
    0x0000: "PARAM.request",       0x0001: "PARAM.response",
    0x0002: "CONFIG.request",      0x0003: "CONFIG.response",
    0x0004: "START.request",       0x0005: "STOP.request",
    0x0006: "STOP.indication",     0x0007: "ERROR.indication",
    0x0108: "START.response",      0x010F: "STOP.response",
    # P5, PNF device level
    0x0100: "PNF_PARAM.request",   0x0101: "PNF_PARAM.response",
    0x0102: "PNF_CONFIG.request",  0x0103: "PNF_CONFIG.response",
    0x0104: "PNF_START.request",   0x0105: "PNF_START.response",
    0x0106: "PNF_STOP.request",    0x0107: "PNF_STOP.response",
    # P7, slot loop
    0x0080: "DL_TTI.request",      0x0081: "UL_TTI.request",
    0x0082: "SLOT.indication",     0x0083: "UL_DCI.request",
    0x0084: "TX_DATA.request",     0x0085: "RX_DATA.indication",
    0x0086: "CRC.indication",      0x0087: "UCI.indication",
    0x0088: "SRS.indication",      0x0089: "RACH.indication",
    # P7, delay management
    0x0180: "UL_NODE_SYNC",        0x0181: "DL_NODE_SYNC",
    0x0182: "TIMING_INFO",
}

PNF_TO_VNF = "pnf->vnf"
VNF_TO_PNF = "vnf->pnf"


class Msg(NamedTuple):
    t: float             # capture-relative seconds
    msg_id: int
    name: str            # SCF222 name, or "UNKNOWN(0x....)"
    direction: str       # PNF_TO_VNF | VNF_TO_PNF | "" when it cannot be told
    proto: str           # "sctp" (P5) or "udp" (P7)
    body: bytes          # everything after the header, for the cases that need an IE


class NfapiObserver:
    """Decode one nFAPI capture into the facts the PNF and VNF suites assert on.

    Direction is decided by port, and it is what lets a single capture judge two product
    classes: PNF->VNF messages are the PNF's behaviour, VNF->PNF messages are the VNF's. This is
    the same arrangement F1-C already uses, where one wire is read once and judged twice for the
    O-DU and the O-CU-CP.
    """

    def __init__(self, cfg=None, store=None, p5_vnf_port: int = 50001,
                 p7_pnf_port: int = 50010, p7_vnf_port: int = 50011):
        self.cfg = cfg
        self.store = store
        # The VNF is the P5 listener; the PNF dials it from an ephemeral port, so P5 direction
        # is decided by which end the well-known port is on, never by assuming a source port.
        self.p5_vnf_port = p5_vnf_port
        self.p7_pnf_port = p7_pnf_port
        self.p7_vnf_port = p7_vnf_port
        self._cache: dict[str, list[Msg]] = {}

    # --- pcap plumbing --------------------------------------------------------
    @staticmethod
    def _link_payload(pkt: bytes, linktype: int) -> bytes | None:
        """The IPv4 payload of one captured frame, or None if it is not IPv4.

        ``tcpdump -i any`` emits SLL2 (linktype 276, 20-byte header) on a current libpcap and
        classic SLL (113, 16-byte header) on an older one. Both appear on rigs in use, and
        assuming the wrong one decodes zero messages without erroring, so both are handled.
        """
        if linktype == 276:                        # LINUX_SLL2
            return pkt[20:] if len(pkt) > 20 and struct.unpack(">H", pkt[0:2])[0] == 0x0800 else None
        if linktype == 113:                        # LINUX_SLL
            return pkt[16:] if len(pkt) > 16 and struct.unpack(">H", pkt[14:16])[0] == 0x0800 else None
        if linktype == 1:                          # Ethernet
            return pkt[14:] if len(pkt) > 14 and struct.unpack(">H", pkt[12:14])[0] == 0x0800 else None
        return None

    @staticmethod
    def _header_len(msg: bytes) -> int | None:
        """The header size, decided by which reading of ``length`` matches the bytes present.

        Three layouts occur, and all three were met on this rig. They differ in the width of
        ``length`` and, awkwardly, in whether it counts the whole message or only the body:

            10  P5   phy_id(2) message_id(2) length(4) spare(2)        length = body
            16  P7   phy_id(2) message_id(2) length(2) m_seg(2) checksum(4) timestamp(4)  = total
            18  P7   as above with length widened to 32 bits (nFAPI v2)                   = total

        Detected per message rather than configured, because a wrong guess does not error, it
        shifts every body field silently. The three readings cannot collide: a P5 length equals
        ``len(msg) - 10`` and a P7 length equals ``len(msg)``, and no message can satisfy both.
        """
        n = len(msg)
        if n < 8:
            return None
        if struct.unpack(">H", msg[4:6])[0] == n:
            return 16
        if struct.unpack(">I", msg[4:8])[0] == n:
            return 18
        if n >= 10 and struct.unpack(">I", msg[4:8])[0] == n - 10:
            return 10
        return None

    def _decode_one(self, t: float, sp: int, dp: int, proto: str,
                    msg: bytes) -> Msg | None:
        if len(msg) < 4:
            return None
        msg_id = struct.unpack(">H", msg[2:4])[0]
        hl = self._header_len(msg)
        body = msg[hl:] if hl else b""
        if proto == "sctp":
            direction = (PNF_TO_VNF if dp == self.p5_vnf_port else
                         VNF_TO_PNF if sp == self.p5_vnf_port else "")
        else:
            direction = (PNF_TO_VNF if sp == self.p7_pnf_port else
                         VNF_TO_PNF if sp == self.p7_vnf_port else "")
        return Msg(t, msg_id, MSG_NAMES.get(msg_id, f"UNKNOWN(0x{msg_id:04x})"),
                   direction, proto, body)

    def messages(self, pcap: str) -> list[Msg] | None:
        """Every nFAPI message in a capture, in capture order.

        None when the file is missing, empty or unreadable, so the test grades 'na' with a real
        reason rather than reading an absent capture as an absent procedure.
        """
        key = str(pcap)
        if key in self._cache:
            return self._cache[key] or None
        try:
            raw = Path(pcap).read_bytes()
        except OSError:
            return None
        if len(raw) < 24:
            return None
        magic = struct.unpack("<I", raw[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            endian = "<"
        elif magic in (0xD4C3B2A1, 0x4D3CB2A1):
            endian = ">"
        else:
            return None                            # not a classic pcap (pcapng is not produced here)
        linktype = struct.unpack(endian + "I", raw[20:24])[0]

        out: list[Msg] = []
        # partially received P5 messages, keyed by (src, dst, stream)
        pending: dict[tuple[int, int, int], list] = {}
        off, t0 = 24, None
        while off + 16 <= len(raw):
            ts, tus, caplen, _ = struct.unpack(endian + "IIII", raw[off:off + 16])
            off += 16
            pkt, off = raw[off:off + caplen], off + caplen
            t = ts + tus / 1e6
            if t0 is None:
                t0 = t
            ip = self._link_payload(pkt, linktype)
            if not ip or len(ip) < 20:
                continue
            payload = ip[(ip[0] & 0x0F) * 4:]
            proto = ip[9]
            if proto == 17 and len(payload) >= 8:                      # UDP carries P7
                sp, dp = struct.unpack(">HH", payload[:4])
                m = self._decode_one(t - t0, sp, dp, "udp", payload[8:])
                if m:
                    out.append(m)
            elif proto == 132 and len(payload) >= 12:                   # SCTP carries P5
                sp, dp = struct.unpack(">HH", payload[:4])
                o = 12
                while o + 4 <= len(payload):
                    ctype = payload[o]
                    flags = payload[o + 1]
                    clen = struct.unpack(">H", payload[o + 2:o + 4])[0]
                    if clen < 4:
                        break
                    if ctype == 0 and o + 16 < len(payload):            # DATA chunk
                        # A large P5 message is split across several DATA chunks, and only the
                        # one with the B (beginning) flag starts it. Decoding every chunk
                        # independently reads the middle of a message as a new header and
                        # invents a procedure that never happened: a 1436-byte CONFIG.request
                        # produced a phantom PNF_PARAM.response from its continuation on this
                        # rig. Fragments are therefore reassembled B through E (RFC 4960).
                        beginning, ending = bool(flags & 0x02), bool(flags & 0x01)
                        stream = struct.unpack(">H", payload[o + 8:o + 10])[0]
                        key = (sp, dp, stream)
                        chunk = payload[o + 16:o + clen]
                        if beginning:
                            pending[key] = [t - t0, bytearray(chunk)]
                        elif key in pending:
                            pending[key][1] += chunk
                        if ending and key in pending:
                            started, buf = pending.pop(key)
                            m = self._decode_one(started, sp, dp, "sctp", bytes(buf))
                            if m:
                                out.append(m)
                    o += (clen + 3) & ~3
        self._cache[key] = out
        return out or None

    # --- procedure presence ---------------------------------------------------
    def procedures(self, pcap: str, direction: str | None = None) -> set[str] | None:
        """The distinct SCF222 messages seen, optionally only in one direction."""
        msgs = self.messages(pcap)
        if msgs is None:
            return None
        return {m.name for m in msgs if direction is None or m.direction == direction}

    def counts(self, pcap: str, direction: str | None = None) -> dict[str, int] | None:
        """Per-message counts, for the cases that care how many rather than whether."""
        msgs = self.messages(pcap)
        if msgs is None:
            return None
        out: dict[str, int] = {}
        for m in msgs:
            if direction is None or m.direction == direction:
                out[m.name] = out.get(m.name, 0) + 1
        return out

    def first_time(self, pcap: str, name: str,
                   direction: str | None = None) -> float | None:
        """Capture-relative time of the first message with this name.

        Used for the ordering requirements, which are about *when* rather than whether: a PHY
        that emits P7 before it answered START.request is not in the state it claims.
        """
        msgs = self.messages(pcap)
        if msgs is None:
            return None
        for m in msgs:
            if m.name == name and (direction is None or m.direction == direction):
                return m.t
        return None

    def epoch0(self, pcap: str) -> float | None:
        """Absolute epoch time of the first frame, so two captures can be compared.

        Times on :class:`Msg` are capture-relative, which is what a reader wants inside one
        file. The ordering requirements span two: "no P7 before START.response" asks whether a
        message in the P7 capture preceded one in the P5 capture, and relative times from
        different files cannot answer that. Callers add this to a relative time to get a common
        clock, rather than the observer guessing which pairs of files belong together.
        """
        try:
            raw = Path(pcap).read_bytes()
        except OSError:
            return None
        if len(raw) < 40:
            return None
        magic = struct.unpack("<I", raw[:4])[0]
        if magic in (0xA1B2C3D4, 0xA1B23C4D):
            endian = "<"
        elif magic in (0xD4C3B2A1, 0x4D3CB2A1):
            endian = ">"
        else:
            return None
        ts, tus = struct.unpack(endian + "II", raw[24:32])
        return ts + tus / 1e6

    def transport(self, pcap: str) -> set[str] | None:
        """Which transports actually carried nFAPI here.

        SCF225 specifies SCTP for P5 and UDP for P7. A deployment that carries P5 on something
        else is reported as observed rather than quietly accepted.
        """
        msgs = self.messages(pcap)
        if msgs is None:
            return None
        return {m.proto for m in msgs}

    # --- slot loop ------------------------------------------------------------
    def slot_timing(self, pcap: str, slots_per_frame: int = 20) -> dict[str, Any] | None:
        """Facts about the SLOT.indication stream: ranges, sequence integrity and cadence.

        ``breaks`` is the requirement that can actually be certified on a simulated radio.
        Absolute rate cannot: under a radio simulator the slot clock is driven by how fast
        samples are produced, which is a property of the simulation rather than of the PHY, and
        it was measured moving with UE presence on this rig. Sequence integrity is a genuine
        conformance property either way, so both are reported and the case decides.
        """
        msgs = self.messages(pcap)
        if msgs is None:
            return None
        rows = [(m.t, m.body) for m in msgs
                if m.name == "SLOT.indication" and len(m.body) >= 4]
        if not rows:
            return {"frames": 0}
        sfn_slot = [(struct.unpack(">H", b[0:2])[0], struct.unpack(">H", b[2:4])[0])
                    for _, b in rows]
        times = [t for t, _ in rows]
        span = times[-1] - times[0]
        breaks = 0
        for i in range(1, len(sfn_slot)):
            psfn, pslot = sfn_slot[i - 1]
            expected = ((psfn, pslot + 1) if pslot < slots_per_frame - 1
                        else ((psfn + 1) % 1024, 0))
            if sfn_slot[i] != expected:
                breaks += 1
        gaps = sorted(times[i] - times[i - 1] for i in range(1, len(times)))
        def pct(p):
            return gaps[min(len(gaps) - 1, int(len(gaps) * p))] if gaps else None
        return {
            "frames": len(rows),
            "span_s": round(span, 3),
            "rate_hz": round(len(rows) / span, 1) if span > 0 else None,
            "sfn_min": min(s for s, _ in sfn_slot), "sfn_max": max(s for s, _ in sfn_slot),
            "slot_min": min(l for _, l in sfn_slot), "slot_max": max(l for _, l in sfn_slot),
            "breaks": breaks, "transitions": len(sfn_slot) - 1,
            "gap_p50_ms": round(pct(0.50) * 1e3, 3) if gaps else None,
            "gap_p99_ms": round(pct(0.99) * 1e3, 3) if gaps else None,
            "gap_max_ms": round(gaps[-1] * 1e3, 3) if gaps else None,
        }
