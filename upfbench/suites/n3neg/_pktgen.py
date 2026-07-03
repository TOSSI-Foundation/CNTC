"""Out-of-process GTP-U packet builder for the N3 negative suite.

Why a subprocess: the TRex generator imports its *bundled* scapy-2.4.3, but the 5G PSC
ext-header (``GTPPDUSessionContainer``) only exists in system scapy >= 2.4.4 — and loading
the system scapy into the TRex process crashes TRex's import (mixed 2.4.3/2.4.4 ->
``conf.use_dnet``). So we craft every negative/valid variant here, in a clean process that
uses ONLY the system scapy, and hand the parent raw bytes (base64). No scapy object ever
crosses into the TRex process; it just transmits the bytes.

Reads one JSON object from stdin: {teid, ue_ip, dst_mac, src_mac, gnb_ip, remote_ip, frame}
Writes one JSON object to stdout: {variant_name: base64(raw_frame), ...}

GTP-U message types (TS 29.281): 0xFF (255) = G-PDU (the NORMAL user-data type — so a
G-PDU is *valid*, not malformed); 1 = Echo Request; 26 = Error Indication; 254 = End
Marker. Anything else is reserved/undefined. The negative variants below exercise the
decap path with control/reserved types, a bad version, and three truncation cases that
make a fixed-offset inner-header parser read past the buffer.
"""
import base64
import json
import sys

_HDR = 78   # Eth14+IP20+UDP8+GTPU8 + innerIP20+innerUDP8


def _b64(pkt) -> str:
    return base64.b64encode(bytes(pkt)).decode()


def build(p) -> dict:
    # scapy is imported HERE (not at module top) so the registry can import this module to
    # scan for TESTS without pulling the system scapy into the TRex process — that import
    # would collide with TRex's bundled scapy-2.4.3 (conf.use_dnet). This module only ever
    # runs scapy as a subprocess (__main__), where the import is harmless.
    from scapy.contrib.gtp import GTP_U_Header, GTPPDUSessionContainer
    from scapy.layers.l2 import Ether
    from scapy.layers.inet import IP, UDP
    teid = int(p["teid"])
    frame = int(p.get("frame", 256))
    eth = (Ether(dst=p["dst_mac"], src=p["src_mac"]) /
           IP(src=p["gnb_ip"], dst=p["remote_ip"]) / UDP(sport=2152, dport=2152))
    inner = IP(src=p["ue_ip"], dst="8.8.8.8") / UDP(sport=1234, dport=80) / (b"\x00" * max(0, frame - _HDR))

    out = {}
    # valid G-PDU on the known TEID -> must forward (liveness reference)
    out["valid"] = _b64(eth / GTP_U_Header(teid=teid) / inner)
    # unknown TEID (no matching PDR) -> must drop, must not forward
    out["unknown_teid"] = _b64(eth / GTP_U_Header(teid=teid + 999999) / inner)
    # control message type carrying a T-PDU payload -> not user data; must not be forwarded
    out["echo_request"] = _b64(eth / GTP_U_Header(gtp_type=1, teid=teid) / inner)
    # reserved/undefined message type -> must not be forwarded as user data
    out["reserved_type"] = _b64(eth / GTP_U_Header(gtp_type=0x07, teid=teid) / inner)
    # GTPv0 in a GTPv1-U tunnel (wrong version) -> non-compliant
    out["bad_version"] = _b64(eth / GTP_U_Header(version=0, teid=teid) / inner)
    # --- truncation cases: a fixed-offset decap that trusts the header can read past
    #     the buffer here. These are the prime crash candidates. ---
    # (a) GTP header itself truncated to 4 bytes
    out["truncated_hdr"] = _b64(eth / bytes(GTP_U_Header(teid=teid))[:4])
    # (b) full 8-byte G-PDU header but ZERO inner bytes (length=0, nothing to decap)
    out["gpdu_no_inner"] = _b64(eth / GTP_U_Header(teid=teid, length=0))
    # (c) header length field claims a large inner payload that isn't present
    out["len_overflow"] = _b64(eth / GTP_U_Header(teid=teid, length=0xFFFF) / b"\x45\x00\x00\x14")
    # 5G PSC (ext-header 0x85): a well-formed container should be handled; a bogus
    # ext-header (bad length/content) must be dropped without crashing.
    gtp = GTP_U_Header(teid=teid, E=1, next_ex=0x85)
    psc_inner = IP(src=p["ue_ip"], dst="8.8.8.8") / UDP() / (b"\x00" * max(0, frame - _HDR - 12))
    out["psc_valid"] = _b64(eth / gtp / GTPPDUSessionContainer() / psc_inner)
    out["psc_malformed"] = _b64(eth / gtp / b"\xff\x85\x00")
    return out


if __name__ == "__main__":
    params = json.load(sys.stdin)
    json.dump(build(params), sys.stdout)
