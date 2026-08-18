"""Wire observer, turn captured signalling into the facts the test cases assert on.

Evidence comes from the per-interface pcaps the RAN writes itself (NGAP, F1AP, E1AP, F1-U, N3),
located by the adapter. Everything is decoded with ``tshark``; nothing is inferred from logs
that could be reworded by a release, and nothing is assumed when a capture is missing, an
absent or unreadable pcap yields ``None`` so the test grades 'na' rather than guessing.

Two decodes carry most of the catalog:

* **F1AP**: in a split gNB every RRC message crosses F1 inside an F1AP container (TS 38.473
  clause 8.4), so one decode gives the F1AP procedures *and* the RRC procedures. Wireshark's
  info column also exposes the PDCP ``MAC=`` of each RRC container, which is how AS-security
  activation becomes observable: null MAC before the AS Security Mode Command, a real MAC after,
  and ciphered payloads no longer render their NAS content.
* **NGAP**: the N2 procedures, plus the NAS message names carried in them.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class WireObserver:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        # pcaps written by a root-run RAN are root-owned; tshark needs the same privilege
        self.sudo = ["sudo", "-n"] if bool(cfg.ran.extra.get("sudo", True)) else []
        self._cache: dict[str, list[str]] = {}

    # --- plumbing -------------------------------------------------------------
    def available(self) -> bool:
        return bool(shutil.which("tshark"))

    def _tshark(self, pcap: str, *args: str, timeout: int = 90) -> str | None:
        """Run tshark over a pcap; None when it cannot be read (missing/empty/no tool)."""
        if not self.available():
            return None
        p = Path(pcap)
        try:
            if not p.exists():
                return None
        except OSError:
            return None
        cmd = [*self.sudo, "tshark", "-r", str(p), *args]
        self.store.record_command(" ".join(cmd))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return None
        return r.stdout if r.returncode == 0 else None

    def info_lines(self, pcap: str) -> list[str] | None:
        """The Wireshark info column, one entry per frame, the summary the catalog reads."""
        if pcap in self._cache:
            return self._cache[pcap] or None
        out = self._tshark(pcap, "-T", "fields", "-e", "_ws.col.Info")
        if out is None:
            return None
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        self._cache[pcap] = lines
        return lines or None

    # --- procedure presence ---------------------------------------------------
    def procedures(self, pcap: str) -> set[str] | None:
        """The distinct protocol messages seen, e.g. {'NGSetupRequest', 'InitialUEMessage'}.

        The info column is ``Message[, detail[, detail]]``; the leading token is the ASN.1
        procedure name, which is what the catalog is written against."""
        lines = self.info_lines(pcap)
        if lines is None:
            return None
        return {l.split(",")[0].strip() for l in lines}

    def has(self, pcap: str, *names: str) -> bool | None:
        """True iff every named procedure appears in the capture."""
        procs = self.procedures(pcap)
        if procs is None:
            return None
        return all(n in procs for n in names)

    def detail_lines(self, pcap: str, needle: str) -> list[str] | None:
        """Info lines mentioning ``needle`` (case-insensitive), for the NAS names and RRC
        messages that ride as details inside an F1AP/NGAP container."""
        lines = self.info_lines(pcap)
        if lines is None:
            return None
        low = needle.lower()
        return [l for l in lines if low in l.lower()]

    # --- AS security, read out of the F1AP RRC containers ---------------------
    _MAC_RE = re.compile(r"MAC=0x([0-9a-fA-F]{8})")

    def as_security(self, f1ap_pcap: str) -> dict | None:
        """Whether AS security actually activated, judged on the PDCP MAC of RRC containers.

        Before the AS Security Mode Command completes, RRC is unprotected and the MAC field is
        all zeros. Once Security Mode Complete is sent, every subsequent RRC container carries a
        real MAC, and ciphered containers stop rendering their inner NAS message.

        Returns ``{smc, smp, pre_smc_macs, post_smc_macs, integrity_activated, ciphered}`` or
        None when the capture cannot be read.
        """
        lines = self.info_lines(f1ap_pcap)
        if lines is None:
            return None
        smp_idx = next((i for i, l in enumerate(lines)
                        if "security mode complete" in l.lower()), None)
        smc_idx = next((i for i, l in enumerate(lines)
                        if "security mode command" in l.lower()), None)
        pre = [m.group(1) for i, l in enumerate(lines)
               if (smp_idx is None or i < smp_idx) for m in [self._MAC_RE.search(l)] if m]
        post = [m.group(1) for i, l in enumerate(lines)
                if smp_idx is not None and i >= smp_idx
                for m in [self._MAC_RE.search(l)] if m]
        nonzero_post = [m for m in post if m != "00000000"]
        # A ciphered RRC container no longer renders the NAS message name it carries: after the
        # SMC we expect bare "DL/UL Information Transfer" rather than "... , <NAS message>".
        after = lines[smp_idx + 1:] if smp_idx is not None else []
        transfers = [l for l in after if "information transfer" in l.lower()]
        opaque = [l for l in transfers if l.lower().split("mac=")[0].count(",") <= 1]
        return {
            "smc": smc_idx is not None,
            "smp": smp_idx is not None,
            "pre_smc_macs": sorted(set(pre)),
            "post_smc_macs": sorted(set(nonzero_post)),
            "integrity_activated": bool(nonzero_post) and all(m == "00000000" for m in pre),
            "ciphered": bool(transfers) and len(opaque) == len(transfers),
        }

    # --- user plane -----------------------------------------------------------
    def gtpu(self, pcap: str) -> dict | None:
        """GTP-U facts for an F1-U or N3 capture: whether user PDUs flow, the TEIDs seen, and
        whether the 5G PDU Session Container (QFI) is present (TS 38.415)."""
        out = self._tshark(pcap, "-Y", "gtp", "-T", "fields",
                           "-e", "gtp.teid", "-e", "gtp.ext_hdr.pdu_ses_con.qos_flow_id")
        if out is None:
            return None
        teids, qfis, frames = set(), set(), 0
        for line in out.splitlines():
            if not line.strip():
                continue
            frames += 1
            teid, _, qfi = line.partition("\t")
            for t in teid.replace(",", " ").split():
                teids.add(t)
            for q in qfi.replace(",", " ").split():
                qfis.add(q)
        return {"frames": frames, "teids": sorted(teids), "qfis": sorted(qfis),
                "has_traffic": frames > 0}

    def gtpu_payload_visible(self, pcap: str) -> bool | None:
        """Whether an inner IP payload is readable inside the GTP-U tunnel.

        Used for the user-plane confidentiality case: if PDCP ciphering is applied the inner
        packet is opaque, so a decodable inner IP header means the user plane is in the clear.
        """
        out = self._tshark(pcap, "-Y", "gtp", "-T", "fields", "-e", "ip.proto")
        if out is None:
            return None
        # An encapsulated packet whose inner IP header is readable yields two ip.proto values
        # (outer UDP + inner protocol); a ciphered payload yields only the outer one.
        for line in out.splitlines():
            if len([v for v in line.replace(",", " ").split() if v]) >= 2:
                return True
        return False

    # --- field extraction -----------------------------------------------------
    def fields(self, pcap: str, display_filter: str, *names: str) -> list[list[str]] | None:
        """Raw per-frame field values for a filter, for the cases that need an IE, not just a
        message name. None when the capture cannot be read."""
        args = ["-Y", display_filter, "-T", "fields"]
        for n in names:
            args += ["-e", n]
        out = self._tshark(pcap, *args)
        if out is None:
            return None
        return [line.split("\t") for line in out.splitlines() if line.strip()]

    def rrc_security_algorithms(self, f1ap_pcap: str) -> dict | None:
        """The AS algorithms the gNB selected, from the RRC Security Mode Command (TS 38.331
        §5.3.4). NIA0/NEA0 are the null algorithms, selecting them when a real one is
        available is the bidding-down failure TS 33.511 §4.2.2.1.12 is about."""
        rows = self.fields(f1ap_pcap, "nr-rrc.securityModeCommand_element",
                           "nr-rrc.cipheringAlgorithm", "nr-rrc.integrityProtAlgorithm")
        if rows is None:
            return None
        ciph = [r[0] for r in rows if r and r[0]]
        integ = [r[1] for r in rows if len(r) > 1 and r[1]]
        if not ciph and not integ:
            return {"seen": False}
        return {"seen": True, "ciphering": ciph[0] if ciph else "",
                "integrity": integ[0] if integ else ""}
