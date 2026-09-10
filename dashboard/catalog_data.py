"""The test catalog, suites, test cases, and the standard each maps to.

Kept Dash-free so both the live page (pages/catalog.py) and the static export can import it
without instantiating a Dash app.

Two domains are shown, in release order:
  * control plane (cpbench), AMF/SMF/NRF/AUSF/UDM, built LIVE from cntc/standards/*-conformance.yaml
    (single source of truth, the same catalogs the verdict engine grades against, so this page
    can never drift from what actually runs).
  * user plane   (upfbench), the UPF suites, curated below.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import theme as T

# Standard → spec URL (opened in a new tab from the catalog). Combined strings like
# "RFC 2544 / ETSI TST009" are split on "/" and each token linked independently. Standards
# without a known URL render as a neutral (unlinked) pill.
STANDARD_URLS = {
    "RFC 2544": "https://www.rfc-editor.org/rfc/rfc2544",
    "RFC 8219": "https://www.rfc-editor.org/rfc/rfc8219",
    "RFC 9004": "https://www.rfc-editor.org/rfc/rfc9004",
    "ETSI TST009": "https://www.etsi.org/deliver/etsi_gs/NFV-TST/001_099/009/",
    "TS 29.244": "https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3111",
}
# suite key -> the cat-id--<suite> CSS modifier (colors the test id)
SUITE_CATID = {"Performance": "perf", "Multi-UE Load": "load",
               "PFCP Conformance": "pfcp", "N3 Robustness": "n3"}


def standard_links(standard: str):
    """Split a standard string into [(label, url_or_None), ...] for rendering as links."""
    out = []
    for tok in [t.strip() for t in standard.split("/") if t.strip()]:
        out.append((tok, STANDARD_URLS.get(tok)))
    return out


# --- control plane: built live from the requirement catalogs -----------------------------
# NF -> (section accent, section title). Order = the order NFs appear on the page.
_CP_NF = {
    "amf":  (T.ACCENT,   "AMF · Access & Mobility Management (N1/N2 + SBI)"),
    "smf":  (T.ACCENT2,  "SMF · Session Management (N4/PFCP + SBI)"),
    "ausf": (T.WARN,     "AUSF · Authentication Server (SBI)"),
    "udm":  ("#0e7490",  "UDM · Unified Data Management (SBI)"),
    "nrf":  (T.GOOD,     "NRF · NF Repository (SBI)"),
    "udr":  ("#7c3aed",  "UDR · Unified Data Repository (SBI)"),
    "pcf":  ("#b13a77",  "PCF · Policy Control (SBI)"),
}
# One-line "what it measures" per test id (supplements the catalog name; falls back to the
# name if an id ever appears here without an entry: so a new test still shows, never hides).
_CP_WHAT = {
    # UDR, driven over Nudr; assertions taken from the published schemas
    "UDR-DR-01": "serves access and mobility subscription data",
    "UDR-DR-02": "serves session management subscription data (singleNssai required)",
    "UDR-DR-03": "serves SMF selection subscription data",
    "UDR-DR-04": "serves the 5G-AKA authentication subscription",
    "UDR-DR-05": "serves access and mobility policy data",
    "UDR-SEC-01": "Nudr rejects a request with no valid token",
    "UDR-NEG-01": "unknown SUPI is refused, repository stays up",
    # PCF, the policy association lifecycle over Npcf
    "PCF-AM-01": "creates an association from the mandatory members alone",
    "PCF-AM-02": "the association is retrievable where it said it would be",
    "PCF-AM-03": "release frees the association, and it is then gone",
    "PCF-SM-01": "creates an SM policy association for a PDU session",
    "PCF-SEC-01": "Npcf rejects a request with no valid token",
    "PCF-NEG-01": "malformed policy request refused, PCF stays up",
    "AMF-REG-01": "UE completes initial registration",
    "AMF-AUTH-01": "AMF runs 5G-AKA with the UE",
    "AMF-AUTH-02": "NAS security-mode command / complete",
    "AMF-DEREG-01": "UE-initiated deregistration handled",
    "AMF-NGAP-01": "gNB↔AMF NGAP association (NG Setup)",
    "AMF-NGAP-02": "Initial UE message / NAS transport",
    "AMF-NGAP-03": "Initial context setup toward the gNB",
    "AMF-NGAP-04": "UE context release",
    "AMF-SEC-01": "NAS integrity-protected after SMC (no cleartext)",
    "AMF-SEC-02": "NAS ciphered after SMC",
    "AMF-SEC-04": "Wrong RES* rejected, no auth bypass",
    "AMF-SEC-06": "SUPI concealed as SUCI on N2",
    "AMF-SEC-07": "Namf SBI requires TLS + OAuth2",
    "AMF-NEG-01": "Malformed NGAP rejected, no crash",
    "AUSF-AUTH-01": "Initiates 5G-AKA authentication",
    "AUSF-AUTH-02": "Confirms RES* on success",
    "AUSF-SEC-02": "Nausf SBI requires TLS + OAuth2",
    "AUSF-NEG-01": "Malformed auth request rejected, no crash",
    "NRF-REG-01": "An NF registers its profile",
    "NRF-DISC-01": "Discovery by NF type returns profiles",
    "NRF-DISC-02": "Discovery by service name",
    "NRF-SEC-01": "OAuth2 access-token grant",
    "NRF-SEC-02": "No token → 403 (authorization enforced)",
    "NRF-SEC-03": "SBI endpoint requires TLS",
    "NRF-NEG-01": "Malformed register → 400, no crash",
    "NRF-NEG-02": "Unknown NF type → empty / 404",
    "SMF-SESS-01": "PDU session established",
    "SMF-SESS-03": "PDU session released",
    "SMF-SESS-04": "Multiple sessions / multi-DNN",
    "SMF-SESS-05": "UE IP address allocated",
    "SMF-N4-01": "SMF programs the UPF over N4 (PFCP)",
    "SMF-N4-03": "N4 session deleted on release",
    "SMF-DP-01": "Traffic forwards through the programmed UPF",
    "SMF-SEC-01": "Nsmf SBI requires TLS + OAuth2",
    "SMF-SEC-02": "Missing / invalid mandatory IE rejected",
    "SMF-SEC-03": "Session-event logging present",
    "SMF-NEG-01": "Invalid session request rejected, no crash",
    "UDM-SDM-01": "Subscription data retrieved (SDM_Get)",
    "UDM-SDM-02": "Session-management subscription data",
    "UDM-UECM-01": "AMF registration recorded (UECM)",
    "UDM-AUTH-01": "Generates authentication vectors",
    "UDM-SEC-01": "SUCI de-concealment (SIDF) authorized",
    "UDM-SEC-02": "Nudm SBI requires TLS + OAuth2",
    "UDM-NEG-01": "Unknown SUPI → 404, no crash",
}

# --- RAN: built live from the per-product-class conformance catalogs ----------------------
# The split-gNB product classes of 3GPP TS 33.523, in pipeline order (DU -> CU-CP -> CU-UP).
_RAN_TARGET = {
    "du":   (T.ACCENT2, "O-DU · Distributed Unit (F1-C / F1-U + the cell)"),
    "cucp": (T.ACCENT,  "O-CU-CP · Centralised Unit, control plane (N2 · F1-C · E1 · RRC)"),
    "cuup": ("#0e7490", "O-CU-UP · Centralised Unit, user plane (E1 · F1-U · N3)"),
    # The L1/L2 split over nFAPI (SCF222/SCF225), a different cut and not a 3GPP SCAS class.
    "pnf":  ("#7c3aed", "PNF · L1 / PHY over nFAPI (P5 · P7), SCF interface conformance"),
    "vnf":  ("#b45309", "VNF · L2 driver over nFAPI (P5 · P7), SCF interface conformance"),
}
# One-line "what it proves" per RAN test id (falls back to the catalog name).
_RAN_WHAT = {
    "CUCP-NGAP-01": "NG Setup with the AMF succeeds",
    "CUCP-NGAP-02": "Initial UE Message carries the UE's NAS",
    "CUCP-NGAP-03": "Initial Context Setup accepted",
    "CUCP-NGAP-04": "PDU Session Resource Setup returns the DL NG-U TEID",
    "CUCP-NGAP-05": "UL/DL NAS transport is transparent",
    "CUCP-NGAP-06": "UE Context Release completes",
    "CUCP-NGAP-07": "PDU Session Resource Release completes",
    "CUCP-NGAP-08": "NG association recovers after an AMF restart",
    "CUCP-F1-01": "F1 Setup from the O-DU accepted, cells activated",
    "CUCP-F1-02": "UE Context Setup toward the O-DU",
    "CUCP-F1-03": "DL RRC Message Transfer delivers RRC",
    "CUCP-F1-04": "UE Context Modification on session add",
    "CUCP-F1-05": "CU-initiated UE Context Release",
    "CUCP-F1-06": "gNB-DU Configuration Update acknowledged",
    "CUCP-E1-01": "E1 Setup from the O-CU-UP accepted",
    "CUCP-E1-02": "Bearer Context Setup on session establishment",
    "CUCP-E1-03": "Bearer Context Modification carries the DL F1-U TNL",
    "CUCP-E1-04": "Bearer Context Release on session release",
    "CUCP-RRC-01": "RRC Setup / Complete, SRB1 up",
    "CUCP-RRC-02": "AS Security Mode Command / Complete",
    "CUCP-RRC-03": "RRC Reconfiguration establishes the DRB",
    "CUCP-RRC-04": "RRC Release on deregistration",
    "CUCP-SEC-01": "RRC integrity-protected after AS SMC",
    "CUCP-SEC-02": "RRC ciphered after AS SMC",
    "CUCP-SEC-03": "Highest-priority AS algorithm chosen, no NIA0/NEA0 downgrade",
    "CUCP-SEC-04": "SMF's UP security policy reaches the CU-UP over E1",
    "CUCP-SEC-05": "F1-C / E1 transport protected (IPsec / DTLS)",
    "CUCP-SEC-06": "N2 transport protected (IPsec / DTLS)",
    "CUCP-NEG-01": "Malformed F1-C / E1 input rejected, no crash",
    "CUCP-NEG-02": "Unknown F1AP procedure → Error Indication",
    "CUUP-E1-01": "E1 Setup advertises supported PLMNs / slices",
    "CUUP-E1-02": "Bearer Context Setup allocates F1-U + NG-U TEIDs",
    "CUUP-E1-03": "Bearer Context Modification applies the DL F1-U TNL",
    "CUUP-E1-04": "Bearer Context Release frees the TEIDs",
    "CUUP-E1-05": "gNB-CU-UP Configuration Update acknowledged",
    "CUUP-UP-01": "F1-U tunnel to the O-DU on the signalled TEID",
    "CUUP-UP-02": "N3 tunnel to the UPF, QFI marked",
    "CUUP-UP-03": "End-to-end data path reaches the DN",
    "CUUP-UP-04": "Unknown TEID dropped, no crash",
    "CUUP-SEC-01": "User plane ciphered per the E1 security policy",
    "CUUP-SEC-02": "User plane integrity when the policy requires it",
    "CUUP-SEC-03": "E1 transport protected",
    "CUUP-SEC-04": "F1-U / N3 transport protected",
    "CUUP-NEG-01": "Malformed GTP-U dropped, no crash",
    "DU-F1-01": "F1 Setup with a well-formed served-cell list",
    "DU-F1-02": "Initial UL RRC Message Transfer on UE access",
    "DU-F1-03": "UE Context Setup admits SRB/DRB, returns the F1-U TNL",
    "DU-F1-04": "UL RRC Message Transfer carries the UE's RRC",
    "DU-F1-05": "UE Context Release completes",
    "DU-F1-06": "gNB-DU Configuration Update acknowledged",
    "DU-F1-07": "F1 recovers after a CU restart",
    "DU-CELL-01": "MIB/SIB1 match the F1 Setup served-cell info",
    "DU-CELL-02": "Random access Msg1→Msg4 completes",
    "DU-CELL-03": "DRB scheduling sustains UL and DL",
    "DU-UP-01": "F1-U tunnel with the O-CU-UP forwards uplink",
    "DU-SEC-01": "F1-C transport protected",
    "DU-SEC-02": "F1-U transport protected",
    "DU-NEG-01": "Malformed GTP-U dropped, no crash",
}

_SPEC_RE = re.compile(r"\b(\d{2}\.\d{3})\b")
_SCF_RE = re.compile(r"\b(SCF\s?\d{3})")  # the nFAPI split anchors to SCF, not 3GPP
_CLAUSE_RE = re.compile(r"\s*\((?:[^)]*(?:§|\d{2}\.\d{3})[^)]*)\)\s*$")  # trailing spec clause only


def _clean_name(name: str) -> str:
    """Drop a trailing spec clause like '(24.501 §5.5.1.2)', but keep descriptive parens."""
    return _CLAUSE_RE.sub("", name).strip()


def _standard_for(name: str, primary: str) -> str:
    """Per-test standard string: spec numbers mentioned in the name, else the NF's primary spec."""
    specs = []
    for s in _SCF_RE.findall(name):          # SCF first: the nFAPI names cite it explicitly
        tok = s.replace(" ", "")
        if tok not in specs:
            specs.append(tok)
    for s in _SPEC_RE.findall(name):
        tok = f"TS {s}"
        if tok not in specs:
            specs.append(tok)
    return " / ".join(specs) if specs else primary


def _primary_spec(standards: list[str]) -> str:
    """The catalog's headline spec token. SCF for the nFAPI split, else a 'TS xx.xxx'.

    SCF is checked first and deliberately: labelling a PNF or VNF requirement '3GPP' would be
    exactly the misattribution these catalogs exist to avoid, since 3GPP defines no product
    class for this split.
    """
    for s in standards:
        m = _SCF_RE.search(s)
        if m:
            return m.group(1).replace(" ", "")
    for s in standards:
        m = _SPEC_RE.search(s)
        if m:
            return f"TS {m.group(1)}"
    return "3GPP"


def _standards_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "cntc" / "standards"


def _build_catalog(sections: dict, whats: dict):
    """Read a domain's per-target conformance catalogs and shape them like the UPF CATALOG
    sections: ``[(title, accent, [(id, name, what it measures, standard)])]``.

    Shared by the control plane and the RAN so both stay a live view of the same YAML the
    verdict engine grades against, a new test appears here the moment it is catalogued.
    """
    out = []
    d = _standards_dir()
    for key, (accent, title) in sections.items():
        f = d / f"{key}-conformance.yaml"
        if not f.exists():
            continue
        cat = yaml.safe_load(f.read_text()) or {}
        primary = _primary_spec(cat.get("standards", []))
        rows = []
        for t in cat.get("tests", []):
            tid = t["id"]
            what = whats.get(tid) or _clean_name(t["name"])
            if t.get("class") == "essential":
                what += "  · essential"
            rows.append((tid, _clean_name(t["name"]), what, _standard_for(t["name"], primary)))
        if rows:
            out.append((title, accent, rows))
    return out


def _target_counts(sections: dict) -> dict[str, tuple[int, int]]:
    """{target: (total_tests, essential_tests)} from that domain's conformance catalogs."""
    out: dict[str, tuple[int, int]] = {}
    d = _standards_dir()
    for key in sections:
        f = d / f"{key}-conformance.yaml"
        if not f.exists():
            continue
        tests = (yaml.safe_load(f.read_text()) or {}).get("tests", [])
        out[key] = (len(tests), sum(1 for t in tests if t.get("class") == "essential"))
    return out


# (suite, accent, [(id, name, what it measures, standard)])
_UPF_CATALOG = [
    ("Performance", T.ACCENT, [
        ("TC-01", "Throughput vs frame size", "NDR (0% loss) + PDR (0.1% loss) per frame size via TST009 binary search", "RFC 2544 / ETSI TST009"),
        ("TC-02", "Bidirectional throughput", "Simultaneous UL+DL throughput", "RFC 2544"),
        ("TC-03", "Latency / jitter", "In-pipeline latency distribution (min/p50/p90/p99/p99.9/max) + jitter", "RFC 8219"),
        ("TC-04", "Burst / back-to-back", "Saturation absorb/forward + drain tail, pipeline drops", "RFC 9004"),
        ("TC-08", "Multi-flow (RSS)", "Aggregate throughput with N flows spread across worker queues", "RFC 2544"),
    ]),
    ("Multi-UE Load", T.ACCENT2, [
        ("LT-01", "Max concurrent sessions", "Capacity ceiling, install N UE sessions, measure rate & ceiling", ", "),
        ("LT-02", "Aggregate + per-UE throughput", "Throughput under N UEs and per-UE forwarding fairness", ", "),
        ("LT-03", "Latency vs UE count", "In-pipeline latency as session count & offered load scale", "RFC 8219"),
    ]),
    ("PFCP Conformance", T.GOOD, [
        ("CF-01", "Association setup / release", "PFCP node association lifecycle", "TS 29.244"),
        ("CF-02", "Session establishment", "PDR/FAR/QER accepted on establish", "TS 29.244"),
        ("CF-03", "Session modification", "Modify an established session", "TS 29.244"),
        ("CF-04", "Session deletion", "Tear down a session cleanly", "TS 29.244"),
        ("CF-05", "Error handling", "Unknown SEID / missing IE → correct cause", "TS 29.244"),
    ]),
    ("N3 Robustness", T.BAD, [
        ("NT-01", "Unknown TEID", "GTP-U on a TEID with no PDR must be dropped, not leaked", "robustness"),
        ("NT-02", "Malformed GTP-U", "6 variants (bad type/version, 3 truncation cases), must not crash", "robustness"),
        ("NT-03", "PSC (0x85) ext-header", "Valid vs malformed 5G PDU-Session-Container handling", "robustness"),
    ]),
]

# Built at import time. Exposed per-domain so the catalog page can group them under headers,
# and combined (control plane first, this release) for back-compatible callers.
CP_CATALOG = _build_catalog(_CP_NF, _CP_WHAT)
RAN_CATALOG = _build_catalog(_RAN_TARGET, _RAN_WHAT)
UPF_CATALOG = _UPF_CATALOG
CATALOG = CP_CATALOG + RAN_CATALOG + UPF_CATALOG

# The three domains, each with a heading + one-line blurb, for a sectioned catalog page.
DOMAINS = [
    ("Control plane", "5G core network functions, driven over N1/N2 (NAS/NGAP) and the "
     "Service-Based Interface, graded against each NF's protocol + SCAS spec.", CP_CATALOG),
    ("RAN", "The split gNB, each 3GPP TS 33.523 product class (O-CU-CP, O-CU-UP, O-DU) driven "
     "over its own interfaces (N2 · F1-C · E1 · F1-U · N3) with RRC read out of the F1AP "
     "containers, graded against its protocol + SCAS spec.", RAN_CATALOG),
    ("User plane", "The UPF, black-box over N3 (GTP-U) and N4 (PFCP), plus optional white-box "
     "eBPF/XDP dataplane assurance.", UPF_CATALOG),
]


def domain_summary(catalog) -> str:
    """'N suites · M test cases (K essential)' for a domain's catalog list."""
    n_suites = len(catalog)
    n_tests = sum(len(tests) for _, _, tests in catalog)
    n_ess = sum(1 for _, _, tests in catalog for t in tests if "· essential" in t[2])
    ess = f" ({n_ess} essential)" if n_ess else ""
    return f"{n_suites} suites · {n_tests} test cases{ess}"


def catalog_summary() -> str:
    """One-line header sub: suite + test totals across all domains."""
    n_suites = len(CATALOG)
    n_tests = sum(len(tests) for _, _, tests in CATALOG)
    return (f"{n_suites} suites · {n_tests} test cases · "
            "5G control plane (N1/N2 + SBI), RAN (N2 · F1 · E1) and user plane (N3/N4)")


def cp_nf_counts() -> dict[str, tuple[int, int]]:
    """{nf: (total_tests, essential_tests)} from the per-NF conformance catalogs."""
    return _target_counts(_CP_NF)


def ran_target_counts() -> dict[str, tuple[int, int]]:
    """{target: (total_tests, essential_tests)} from the per-product-class RAN catalogs."""
    return _target_counts(_RAN_TARGET)
