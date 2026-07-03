"""Issue a formal CNTC conformance certificate from a graded verdict.

This is the automated analog of CNTI's "Certified CNF / Certified CNTi" program: where CNTI
grants a certificate after a vendor self-certifies and a reviewer verifies the test results,
CNTC issues a **technical conformance certificate** automatically **iff every essential test
in the profile passed** (verdict.result == "PASS"). A FAIL or INCOMPLETE verdict yields no
certificate — you cannot certify a UPF that failed an essential test (e.g. one that crashes
on malformed input).

NOTE ON SCOPE: this certifies *technical conformance to a versioned CNTC profile on a stated
rig*. It is NOT a governance-backed brand like "Certified CNTi" (which requires an LFN review
board + terms). The certificate records exactly what was verified so it is auditable.
"""
from __future__ import annotations

import hashlib
from typing import Any


def _certificate_id(subject: str, profile: str, catalog_version: str, stamp: str) -> str:
    seed = f"{subject}|{profile}|{catalog_version}|{stamp}".encode()
    return f"CNTC-{profile.upper()[:4]}-{hashlib.sha256(seed).hexdigest()[:10].upper()}"


def issue(verdict: dict[str, Any], sut: dict[str, Any], stamp: str) -> dict[str, Any] | None:
    """Return a certificate dict iff the verdict is a PASS, else None."""
    if verdict.get("result") != "PASS":
        return None
    subject = str(sut.get("upf") or sut.get("upf_image") or "Unknown UPF")
    cid = _certificate_id(subject, verdict.get("profile", "?"),
                          str(verdict.get("catalog_version", "?")), stamp)
    return {
        "certificate_id": cid,
        "framework": "CNTC",
        "grade": "PASS",
        "subject": subject,
        "upf_image": sut.get("upf_image", ""),
        "profile": verdict.get("profile", "?"),
        "catalog_version": verdict.get("catalog_version", "?"),
        "title": verdict.get("title", ""),
        "standards": verdict.get("standards", []),
        "rig": verdict.get("rig", {}),
        "essential": verdict.get("essential", {}),
        "categories": verdict.get("categories", {}),
        "issued": stamp or "(unstamped)",
        "scope": "Technical conformance to the stated CNTC profile on the stated rig. "
                 "Not an LFN-governed brand.",
    }


def refusal_reason(verdict: dict[str, Any]) -> str:
    """Human-readable reason a certificate cannot be issued."""
    r = verdict.get("result")
    if r == "FAIL":
        fe = ", ".join(verdict.get("failed_essentials", [])) or "(unknown)"
        return f"verdict is FAIL — failed essential test(s): {fe}"
    if r == "INCOMPLETE":
        nr = ", ".join(verdict.get("not_run_essentials", [])) or "(unknown)"
        return f"verdict is INCOMPLETE — essential test(s) did not run: {nr}"
    return f"verdict is {r!r}, not PASS"


def render_markdown(cert: dict[str, Any]) -> str:
    rig = "  ·  ".join(f"{k}={v}" for k, v in (cert.get("rig") or {}).items() if v)
    std = ", ".join(f"`{s}`" for s in cert.get("standards", []))
    e = cert.get("essential", {})
    out = [
        "```",
        "════════════════════════════════════════════════════════════════════",
        "                    CNTC CONFORMANCE CERTIFICATE",
        "         Cloud Native Telecom Certification Framework — PASS",
        "════════════════════════════════════════════════════════════════════",
        "```",
        "",
        f"**Certificate ID:** `{cert['certificate_id']}`",
        "",
        f"This certifies that the network function below passed **every essential test** of "
        f"the CNTC **{cert['profile']}** profile (catalog v{cert['catalog_version']}).",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Subject (UPF) | **{cert['subject']}** |",
        f"| UPF image | `{cert.get('upf_image','') or '—'}` |",
        f"| Profile | {cert['profile']} — {cert.get('title','')} |",
        f"| Catalog version | v{cert['catalog_version']} |",
        f"| Standards | {std or '—'} |",
        f"| Rig | {rig or '—'} |",
        f"| Essential gate | {e.get('passed',0)}/{e.get('total',0)} passed, 0 failed |",
        f"| Grade | **PASS** |",
        f"| Issued | {cert['issued']} |",
        "",
        f"> **Scope.** {cert['scope']}",
        "",
        "*Issued automatically by the CNTC verdict layer. Verify against the campaign "
        "`results.json` (the `verdict` block) that produced it.*",
        "",
    ]
    return "\n".join(out)


def render_html(cert: dict[str, Any]) -> str:
    def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    rig = "  ·  ".join(f"{k}={esc(v)}" for k, v in (cert.get("rig") or {}).items() if v)
    std = ", ".join(esc(s) for s in cert.get("standards", []))
    e = cert.get("essential", {})
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>CNTC Certificate {esc(cert['certificate_id'])}</title>
<style>
 body{{font:15px/1.6 Georgia,serif;color:#1f2328;max-width:760px;margin:3rem auto;padding:2rem;
      border:3px double #1a7f37;border-radius:10px;background:#fffdf7}}
 h1{{text-align:center;color:#1a7f37;font-size:22px;letter-spacing:1px;margin:0}}
 .sub{{text-align:center;color:#57606a;margin:.2rem 0 1.4rem}}
 .id{{text-align:center;font-family:monospace;background:#eef7f0;padding:.4rem;border-radius:6px}}
 table{{width:100%;border-collapse:collapse;margin:1.3rem 0}}
 td{{padding:.4rem .6rem;border-bottom:1px solid #e5e7eb}} td:first-child{{color:#57606a;width:34%}}
 .grade{{color:#1a7f37;font-weight:bold}} .scope{{color:#57606a;font-size:13px;font-style:italic}}
</style></head><body>
<h1>CNTC CONFORMANCE CERTIFICATE</h1>
<div class="sub">Cloud Native Telecom Certification Framework — <span class="grade">PASS</span></div>
<div class="id">{esc(cert['certificate_id'])}</div>
<p>This certifies that the network function below passed <b>every essential test</b> of the
CNTC <b>{esc(cert['profile'])}</b> profile (catalog v{esc(cert['catalog_version'])}).</p>
<table>
 <tr><td>Subject (UPF)</td><td><b>{esc(cert['subject'])}</b></td></tr>
 <tr><td>UPF image</td><td><code>{esc(cert.get('upf_image','') or '—')}</code></td></tr>
 <tr><td>Profile</td><td>{esc(cert['profile'])} — {esc(cert.get('title',''))}</td></tr>
 <tr><td>Catalog version</td><td>v{esc(cert['catalog_version'])}</td></tr>
 <tr><td>Standards</td><td>{std or '—'}</td></tr>
 <tr><td>Rig</td><td>{rig or '—'}</td></tr>
 <tr><td>Essential gate</td><td>{e.get('passed',0)}/{e.get('total',0)} passed, 0 failed</td></tr>
 <tr><td>Grade</td><td class="grade">PASS</td></tr>
 <tr><td>Issued</td><td>{esc(cert['issued'])}</td></tr>
</table>
<p class="scope">Scope: {esc(cert['scope'])} Issued automatically by the CNTC verdict layer.</p>
</body></html>"""
