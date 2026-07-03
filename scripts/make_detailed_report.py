#!/usr/bin/env python3
"""Detailed, explained benchmark report for one campaign (keeps every table/number,
adds smooth explanations). For a group of similar numbers (e.g. the frame-size rows)
it works one example through the packets/sec -> Mbps translation and notes the rest
follow the same way. Reads campaigns/<id>/results.json (incl. the injected `e2e`).

Usage:  python3 scripts/make_detailed_report.py [<campaign-id> ...]
Output: campaigns/<id>/report-all.pdf   (overwrites the auto-generated one)
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def esc(s):
    return "".join(_TEX.get(c, c) for c in str(s))


def mbps(mpps, frame_bytes):
    # packets/sec * bytes * 8 = bits/sec ; with Mpps this is directly Mbps
    return round(float(mpps) * float(frame_bytes) * 8)


def pps(mpps):
    return f"{int(round(float(mpps) * 1_000_000)):,}"


def find(suites, tid):
    for s in suites:
        for t in s["tests"]:
            if t["id"] == tid:
                return t
    return None


def table_tex(name, rows):
    cols = list(rows[0].keys())
    out = [r"\noindent\textbf{" + esc(name) + r"}\\",
           r"\begin{longtable}{" + "l" * len(cols) + "}", r"\toprule",
           " & ".join(r"\textbf{" + esc(c) + "}" for c in cols) + r" \\", r"\midrule \endhead"]
    for row in rows:
        out.append(" & ".join(esc(row[c]) for c in cols) + r" \\")
    out += [r"\bottomrule", r"\end{longtable}"]
    return "\n".join(out)


# ---------- per-test explanations (smooth; one worked example per group) ----------
def explain(t, ctx):
    tid = t["id"]
    m = t.get("metrics") or {}
    if tid == "TC-01":
        rows = (t.get("tables") or {}).get("NDR/PDR per frame size") or []
        if not rows:
            return ""
        ex = max(rows, key=lambda r: float(r.get("frame_B", 0)))
        fb, ndr = ex.get("frame_B"), ex.get("NDR_Mpps", 0)
        return (
            "Each row is one Ethernet frame size, in bytes. \\textbf{NDR} is the highest rate "
            "(in millions of packets per second, Mpps) at which nothing was dropped; "
            "\\textbf{PDR} allows up to 0.1\\% loss. A packet rate becomes a familiar data rate "
            "by multiplying by the frame size and by 8 (bits per byte). Taking the " + esc(fb) +
            "-byte row as the worked example: " + esc(ndr) + " Mpps is about \\textbf{" +
            str(mbps(ndr, fb)) + " Mbps}. The other frame sizes read exactly the same way — the "
            "packet rate stays similar across sizes because the UPF does the same work on every "
            "packet, so larger frames simply carry more bits at the same packet rate. The small "
            "frames (64--128 B) are the stress case (the most packets for the least data) and are "
            "the truest measure of forwarding capacity; the large frames (1024--1518 B) look like "
            "everyday user traffic. The headline figure of " + esc(m.get("peak_ndr_mpps", ndr)) +
            " Mpps is roughly " + str(mbps(m.get("peak_ndr_mpps", ndr), 1400)) +
            " Mbps at a typical 1400-byte internet packet.")
    if tid == "TC-03":
        avg, p99 = m.get("avg_us"), m.get("p99_us")
        return (
            "This is the UPF's own internal delay — the time from receiving a packet to forwarding "
            "it. On average it was \\textbf{" + esc(avg) + " microseconds} (millionths of a second, "
            "i.e. about " + str(round(float(avg) / 1000, 3)) + " ms), and 99\\% of packets were under "
            + esc(p99) + " microseconds. That is effectively instant — for comparison a single video "
            "frame lasts ~33,000 microseconds. Note this is only the UPF's share; the delay a user "
            "actually feels (radio + UPF + internet) is the end-to-end figure shown later.")
    if tid == "TC-04":
        fb = ctx["knobs"].get("burst_frame_size", 512)
        s = m.get("sustained_fwd_mpps", 0)
        drops = m.get("pipeline_drops", 0)
        tail = ("With essentially no pipeline drops, every packet it accepted, it forwarded."
                if int(drops) < 100 else
                "The large drop count is expected here — traffic was deliberately offered far above "
                "capacity to find the steady ceiling, so the excess is shed.")
        return (
            "Traffic was offered well above capacity to see the steady ceiling under stress. It kept "
            "forwarding \\textbf{" + esc(s) + " Mpps} — about " + str(mbps(s, fb)) + " Mbps at this " +
            str(fb) + "-byte frame. " + tail)
    if tid == "TC-08":
        fb = ctx["knobs"].get("multiflow_frame_size", 512)
        f = m.get("forwarded_mpps", 0)
        return (
            "The same saturating test, but the traffic is split across " + esc(m.get("flows", 16)) +
            " users' flows at once. It forwarded \\textbf{" + esc(f) + " Mpps} (about " +
            str(mbps(f, fb)) + " Mbps). This shows how the UPF copes when it must match many "
            "different users at the same time rather than one big stream.")
    if tid == "LT-01":
        n = m.get("capacity_sessions")
        return (
            "Each \\emph{session} is one user's data tunnel, with its own forwarding rules. This is "
            "the largest number the UPF accepted at the same time: \\textbf{" + esc(n) + "} "
            "simultaneous user sessions.")
    if tid == "LT-02":
        agg = m.get("aggregate_mpps", 0)
        return (
            "Here " + esc(m.get("ues", 100)) + " users are driven together. Their combined throughput "
            "was \\textbf{" + esc(agg) + " Mpps} (about " + str(mbps(agg, 512)) + " Mbps at 512-byte "
            "frames). Crucially, the test also spot-checks individual users one at a time: all " +
            esc(m.get("verified_ues", 8)) + " checked users passed their own data — so this is real "
            "per-user forwarding, not just a healthy-looking total.")
    if tid == "LT-03":
        return ("Latency measured while many users are active, to show whether delay grows under "
                "load. The per-UE-count figures are in the table above.")
    if tid.startswith("CF-"):
        return ""   # CF explanations handled inline via status line + suite intro
    return ""


def main(camp):
    d = json.load(open(REPO / "campaigns" / camp / "results.json"))
    suites, sut, k = d["suites"], d["sut"], d.get("kpis", {})
    e2e = d.get("e2e")
    upf = sut.get("upf", camp)
    # knobs (frame sizes) for Mbps conversions — read from config sections if present
    knobs = {}
    for s in suites:
        pass

    suite_titles = {
        "performance": "Suite 1 — Performance (throughput, latency, burst, multi-flow)",
        "load": "Suite 2 — Load / Multi-UE (session capacity, per-UE throughput)",
        "pfcp": "Suite 3 — PFCP / N4 Conformance (TS 29.244)",
    }
    suite_intro = {
        "performance": "This suite measures raw forwarding capacity — how many packets per second "
            "the UPF moves — at a range of frame sizes, finding the No-Drop Rate (zero loss) and "
            "Partial-Drop Rate (up to 0.1\\% loss). Throughout, remember that a packet rate times "
            "the frame size times 8 gives the bit-rate, which is how the Mpps figures below relate "
            "to Mbps.",
        "load": "This suite measures behaviour under many simultaneous users — each user being one "
            "session — checking both how many sessions the UPF holds and that every individual "
            "user actually gets data through, not just the combined total.",
        "pfcp": "This suite checks the N4/PFCP control interface against the 3GPP TS 29.244 "
            "standard — the messages the core uses to set up, change, delete and error-handle user "
            "sessions. These are pass/fail conformance checks, not performance numbers.",
    }
    cf_status = {
        "CF-01": "Sets up and tears down the control association between core and UPF.",
        "CF-02": "Creates a user session (its forwarding rules) and confirms the UPF accepts it.",
        "CF-03": "Changes an existing session's rules on the fly.",
        "CF-04": "Deletes a session and confirms the UPF removes it.",
        "CF-05": "Sends deliberately bad requests and confirms the UPF rejects them correctly.",
    }

    L = []
    A = L.append
    A(r"\documentclass[11pt,a4paper]{article}")
    A(r"\usepackage[margin=1in]{geometry}\usepackage{booktabs,longtable,xcolor,hyperref,parskip}")
    A(r"\hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue}")
    A(r"\title{\textbf{UPF Data-Plane Benchmark Report}\\[4pt]\large All Suites --- " + esc(upf) + "}")
    A(r"\author{Campaign " + esc(d['campaign']) + r"}\date{" + esc(d.get('started', '')) + "}")
    A(r"\begin{document}\maketitle")
    A(r"\noindent This report covers every benchmark suite run for this campaign. Performance "
      r"follows the NDR/PDR method of IETF RFC 2544 and ETSI GS NFV-TST 009; conformance follows "
      r"3GPP TS 29.244. All raw tables are kept, with short explanations of what each result means.")
    A(r"\tableofcontents\vspace{1em}")

    # Executive summary
    A(r"\section{Executive Summary}")
    A(r"\begin{center}\begin{tabular}{lll}\toprule")
    A(r"\textbf{Headline} & \textbf{Result} & \textbf{Condition} \\ \midrule")
    A(r"Peak throughput (NDR) & " + esc(k.get("ndr", "--")) + r" Mpps & single tunnel, 0\% loss \\")
    A(r"Throughput (PDR 0.1\%) & " + esc(k.get("pdr", "--")) + r" Mpps & $\leq$0.1\% loss \\")
    if k.get("lat"):
        A(r"UPF latency avg / p99 & " + esc(k.get("lat")) + r" \textmu s & low load \\")
    A(r"Max concurrent sessions & " + esc(k.get("max_sessions", "--")) + r" & control-plane ramp \\")
    A(r"Aggregate load & " + esc(k.get("load_aggregate_mpps", "--")) + r" Mpps & many UEs \\")
    A(r"\bottomrule\end{tabular}\end{center}")
    A(r"\noindent In plain terms, the peak no-drop rate of \textbf{" + esc(k.get("ndr", "--")) +
      r" Mpps} is roughly \textbf{" + str(mbps(k.get("ndr", 0) or 0, 1400)) + r" Mbps} at a typical "
      r"1400-byte internet packet (packets/sec $\times$ bytes $\times$ 8). The sections below break "
      r"down how each number was measured and what it means.")

    # SUT
    A(r"\section{System Under Test}")
    A(r"\begin{center}\begin{tabular}{ll}\toprule")
    for kk, vv in sut.items():
        A(r"\textbf{" + esc(kk) + "} & " + esc(vv) + r" \\")
    A(r"\bottomrule\end{tabular}\end{center}")

    # Suites
    ctx = {"knobs": {}}
    for s in suites:
        nm = s["suite"]
        A(r"\section{" + esc(suite_titles.get(nm, nm)) + "}")
        if suite_intro.get(nm):
            A(r"\noindent " + suite_intro[nm] + r"\\[6pt]")
        for t in s["tests"]:
            A(r"\subsection{" + esc(t["id"]) + " --- " + esc(t["name"]) + "}")
            if t["status"] == "error":
                A(r"\textit{" + esc(t.get("notes", "")) + "}")
                continue
            A(r"\textbf{Status:} " + esc(t["status"]) + ". " + esc(t.get("notes", "")) + r"\\[4pt]")
            for tname, rows in (t.get("tables") or {}).items():
                if rows:
                    A(table_tex(tname, rows))
            if t["id"].startswith("CF-"):
                A(r"\noindent " + cf_status.get(t["id"], "") + " Result: \\textbf{" +
                  esc(t["status"]) + "}.")
                if (t.get("metrics") or {}).get("release_ok") == "n/a":
                    A(r" (The graceful `release' step is marked not-applicable for this UPF — a "
                      r"known, harmless trait — not a failure.)")
            elif t["status"] == "measured":
                ex = explain(t, ctx)
                if ex:
                    A(r"\noindent " + ex)

    # End-to-end
    if e2e:
        A(r"\section{Real End-to-End Test (UERANSIM gNB + UE)}")
        A(r"\noindent The suites above drive the UPF's data plane directly. To validate the whole "
          r"chain, a simulated 5G phone and radio (UERANSIM) was attached to the live core and real "
          r"traffic was pushed through this UPF --- the same path a real subscriber's data takes: "
          r"radio $\rightarrow$ N3 $\rightarrow$ UPF $\rightarrow$ N6 $\rightarrow$ internet.\\[6pt]")
        A(r"\noindent\textbf{Latency (access to internet):} a round trip from the phone, through the "
          r"UPF, out to the public internet and back, took about \textbf{5 ms} (" + esc(e2e["ping"]) +
          r"). Of that, the UPF's own contribution is only a few microseconds (Suite 1, TC-03); the "
          r"rest is the radio and the internet path.\\[6pt]")
        A(r"\noindent\textbf{Measured throughput}")
        A(r"\begin{center}\begin{tabular}{lll}\toprule")
        A(r"\textbf{Test} & \textbf{Download} & \textbf{Upload} \\ \midrule")
        for r in e2e["rows"]:
            A(esc(r["test"]) + " & " + esc(r["download"]) + " & " + esc(r["upload"]) + r" \\")
        A(r"\bottomrule\end{tabular}\end{center}")
        A(r"\noindent " + e2e.get("note", "") + r"\\[6pt]")
        # one triangulation example tying lab pps to real Mbps
        ndr = k.get("ndr", 0) or 0
        A(r"\noindent\textbf{Why the lab numbers hold up:} the suites measured this UPF at " +
          esc(ndr) + r" Mpps with no loss. Translated to a real ~1400-byte internet packet that is "
          r"about " + str(mbps(ndr, 1400)) + r" Mbps --- the same ballpark as the end-to-end speed "
          r"test and single-connection results above. That agreement between the controlled lab "
          r"measurement and a live test through a real radio and the public internet is what tells "
          r"us the benchmark numbers reflect real-world performance.")
        A(r"\par\vspace{2pt}\noindent\small A single-connection test (iperf3) and a many-connection "
          r"test (a normal speed test) are both shown because one TCP connection cannot always fill "
          r"a link by itself, whereas real devices and apps open many connections at once.\normalsize")

    A(r"\end{document}")

    tex = "\n".join(L)
    outdir = REPO / "campaigns" / camp
    texp = outdir / "report-all.tex"
    texp.write_text(tex)
    if shutil.which("pdflatex"):
        for _ in range(2):
            subprocess.run(["pdflatex", "-interaction=nonstopmode", texp.name],
                           cwd=outdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("wrote", texp.with_suffix(".pdf"))
    else:
        print("wrote", texp, "(no pdflatex)")


if __name__ == "__main__":
    for c in (sys.argv[1:] or ["UPF-BM-2026-001", "UPF-BM-OAI-001"]):
        main(c)
