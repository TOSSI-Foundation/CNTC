"""Root-cause diagnosis: turn "this did not happen" into "here is why".

A conformance result says what was observed. That is the correct basis for a verdict, but on its
own it is a poor tool: "missing NGSetupRequest on cucp.ngap" is true, and useless to whoever has
to fix it. The message is missing because the AMF was unreachable, or the SCTP port was already
held by another process, or the product exited on a bad config, and none of that is visible in
the capture, precisely because the capture is empty.

So a run also collects environmental facts, and each failing or unevaluated result is matched
against them to produce a probable cause. The distinction is kept deliberately:

    outcome   what the specification says, decided only by observation
    cause     why it probably happened, decided by the surrounding evidence

A cause never changes a verdict. A test that failed still failed if the reason was a port
conflict; the deployment did not do what the requirement asks. The cause tells the operator where
to look, and it is always phrased as "probable", because the engine is inferring rather than
observing.
"""
from __future__ import annotations

import re
import socket
import subprocess
from typing import Any

# Product class -> the log phrases that mean it could not reach its peer.
_PEER_ERRORS = {
    "cucp": ["failed to connect to amf", "amf connection", "ng setup failure",
             "unable to connect", "connection refused"],
    "cuup": ["failed to connect", "e1 setup failure", "connection refused"],
    "du":   ["failed to connect", "f1 setup failure", "connection refused"],
}
_FATAL = ["error", "fatal", "abort", "could not", "failed to start", "invalid"]


def _run(*args: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(["sudo", "-n", *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def port_holder(port: int, proto: str = "sctp") -> dict | None:
    """Which process is holding a port, if any. This is what turns "the message never
    appeared" into "something else is already on 38412"."""
    out = _run("ss", "-lanp", f"--{proto}" if proto in ("tcp", "udp") else "--sctp")
    if not out:
        return None
    for line in out.splitlines():
        if f":{port}" not in line:
            continue
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if m:
            return {"port": port, "process": m.group(1), "pid": int(m.group(2))}
        return {"port": port, "process": "unknown", "pid": None}
    return None


def sctp_reachable(host: str, port: int, timeout: float = 4.0) -> bool | None:
    """Whether an SCTP peer accepts an association. None when the address is unusable."""
    if not host:
        return None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 132)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False
    except Exception:  # noqa: BLE001
        return None


def _log_errors(ran, target: str, limit: int = 3) -> list[str]:
    """The most recent lines from a product's log that look like a failure."""
    try:
        path = ran._cfg_yaml(target).get("log", {}).get("filename")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return []
    if not path:
        return []
    out = _run("tail", "-n", "400", str(path), timeout=15)
    hits = [l.strip() for l in out.splitlines()
            if any(w in l.lower() for w in _FATAL)]
    # keep the last few, trimmed, and drop the timestamp prefix for readability
    return [re.sub(r"^\S+\s+", "", h)[:160] for h in hits[-limit:]]


def collect(cfg, ran, obs: dict | None = None) -> dict[str, Any]:
    """Environmental facts gathered around a run, used to explain failures."""
    facts: dict[str, Any] = {"ports": {}, "logs": {}, "products": {}}

    # the AMF the CU-CP is configured to reach, and whether it answers
    amf = ""
    try:
        amf_cfg = (ran._cfg_yaml("cucp").get("cu_cp") or {}).get("amf") or {}  # noqa: SLF001
        addrs = amf_cfg.get("addrs")
        amf = str(addrs[0] if isinstance(addrs, (list, tuple)) and addrs else (addrs or ""))
    except Exception:  # noqa: BLE001
        pass
    if amf:
        facts["amf_addr"] = amf
        facts["amf_reachable"] = sctp_reachable(amf, 38412)

    # who holds the interfaces the RAN needs
    for name, port in (("f1c", 38472), ("e1", 38462)):
        holder = port_holder(port)
        if holder:
            facts["ports"][name] = holder

    for tgt in cfg.targets:
        facts["products"][tgt] = {"alive": ran.node_alive(tgt)}
        errs = _log_errors(ran, tgt)
        if errs:
            facts["logs"][tgt] = errs

    if obs:
        facts["attach"] = {k: obs.get(k) for k in
                           ("synchronized", "rrc_connected", "registration_accept",
                            "pdu_session", "ping_ok") if k in obs}
        if obs.get("error"):
            facts["attach_error"] = obs["error"]
    return facts


# Which product and which peer each family of tests depends on.
_DEPENDS = {
    "CUCP-NGAP": ("cucp", "the AMF over N2"),
    "CUCP-F1":   ("cucp", "the O-DU over F1-C"),
    "CUCP-E1":   ("cucp", "the O-CU-UP over E1"),
    "CUCP-RRC":  ("cucp", "the UE over RRC"),
    "DU-F1":     ("du",   "the O-CU-CP over F1-C"),
    "DU-CELL":   ("du",   "the UE on the cell"),
    "DU-UP":     ("du",   "the O-CU-UP over F1-U"),
    "CUUP-E1":   ("cuup", "the O-CU-CP over E1"),
    "CUUP-UP":   ("cuup", "the UPF over N3"),
}


def probable_cause(test_id: str, status: str, notes: str, facts: dict) -> str | None:
    """A likely explanation for a failing or unevaluated result, or None.

    Ordered from the most specific and most actionable to the most general, so the operator is
    told about a port conflict rather than a generic "the peer was unreachable".
    """
    if status not in ("fail", "na", "error"):
        return None
    # results that already state their own cause need no help
    if any(k in notes.lower() for k in ("ipsec", "nea0", "nia0", "not implemented",
                                        "not exercised", "cannot observe")):
        return None

    family = next((k for k in _DEPENDS if test_id.startswith(k)), None)
    product, peer = _DEPENDS.get(family, (None, None))

    # 1. the product died or never started: everything about it is unexplained until that is
    if product and (facts.get("products", {}).get(product, {}).get("alive") is False):
        errs = facts.get("logs", {}).get(product)
        detail = f" Last error: {errs[-1]}" if errs else ""
        return f"the {product} was not running when this was judged.{detail}"

    # 2. a port the RAN needs is held by something else
    port_map = {"CUCP-F1": "f1c", "DU-F1": "f1c", "CUCP-E1": "e1", "CUUP-E1": "e1"}
    holder = facts.get("ports", {}).get(port_map.get(family, ""))
    if holder and holder.get("process") not in ("ocucp", "unknown", None):
        return (f"port {holder['port']} is held by {holder['process']} "
                f"(pid {holder.get('pid')}), not by the O-CU-CP, so the association "
                f"could not be established.")

    # 3. the peer this family depends on is unreachable
    if family and family.startswith("CUCP-NGAP") and facts.get("amf_reachable") is False:
        return (f"the AMF at {facts.get('amf_addr')} did not accept an SCTP association on "
                f"38412, so no N2 signalling could occur. Check the address in the CU-CP "
                f"config and that the core is reachable from this host.")

    # 4. the attach stopped before this procedure could happen
    attach = facts.get("attach") or {}
    if facts.get("attach_error"):
        return f"the UE attach did not run: {facts['attach_error']}"
    for key, why in (("synchronized", "the UE never synchronised to the cell"),
                     ("rrc_connected", "the UE never reached RRC_CONNECTED"),
                     ("registration_accept", "the UE never completed registration"),
                     ("pdu_session", "the UE never obtained a PDU session")):
        if key in attach and attach[key] is not True:
            errs = facts.get("logs", {}).get(product or "du")
            detail = f" Last error from the {product or 'du'}: {errs[-1]}" if errs else ""
            return f"{why}, so this procedure was never reached.{detail}"

    # 5. the product logged something that looks like the reason
    if product:
        errs = facts.get("logs", {}).get(product)
        if errs:
            return f"the {product} logged: {errs[-1]}"
    return None


# One underlying problem usually breaks several requirements, and the wording differs per
# interface. Collapse those to a single problem statement so the operator is shown the fault
# rather than each of its symptoms.
_PROBLEM_CLASSES = [
    (r"no ipsec sa on the host",
     "no IPsec on any RAN interface, so F1, E1, N2 and N3 traffic is unprotected in transit"),
    (r"(nea0|null ciphering)",
     "the null ciphering algorithm NEA0 is in use, so nothing is encrypted"),
    (r"(nia0|null integrity)",
     "the null integrity algorithm NIA0 is in use, so signalling is not authenticated"),
    (r"the (\w+) was not running",
     "a product class was not running when it was judged"),
    (r"alive after = false|crashed",
     "a product class did not survive malformed input"),
    (r"no f1 setup seen after",
     "the O-DU does not re-establish F1 when the Central Unit is lost"),
    (r"no ng re-establishment",
     "the O-CU-CP does not re-establish NG after losing the AMF"),
    (r"did not accept an sctp association",
     "the AMF was unreachable, so no N2 signalling could occur"),
]


def _problem_class(cause: str) -> str:
    """Collapse a per-test reason into the underlying problem it is a symptom of."""
    low = (cause or "").lower()
    for pattern, label in _PROBLEM_CLASSES:
        if re.search(pattern, low):
            return label
    return (cause or "unexplained").split("  |  ")[0][:110]


def summary(facts: dict, rows: list[dict]) -> list[str]:
    """The headline problems with this RAN: what is wrong, and which requirements it breaks."""
    out: list[str] = []
    fails = [r for r in rows if r.get("outcome") == "fail"]
    if not fails:
        return out
    by_problem: dict[str, list[str]] = {}
    for r in fails:
        by_problem.setdefault(_problem_class(r.get("cause") or ""), []).append(r["id"])
    for problem, ids in sorted(by_problem.items(), key=lambda kv: -len(kv[1])):
        shown = ", ".join(ids[:6]) + (f", +{len(ids) - 6} more" if len(ids) > 6 else "")
        out.append(f"{problem}  ({len(ids)} requirement(s): {shown})")
    return out
