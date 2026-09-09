"""Capture the signalling off the wire, for stacks that do not capture it themselves.

OCUDU writes a pcap per interface and the adapter reports the files it wrote. OAI writes none:
its only pcap support is MAC-layer OPT, so there is no NGAP, F1AP or E1AP capture to point at.
Rather than judge OAI on its logs, which the framework refuses to do, ranbench captures the
interfaces itself and hands the files to the same observer and the same test cases.

An independent capture is the stronger evidence of the two. A product's own pcap is its record
of what it believes it sent; a capture off the wire is what actually crossed. The cost is that
correctness moves to us: a filter that matches nothing yields an empty file, which grades 'na'.
That fails safe, but silently, so ``empty()`` names the captures that stayed empty and the
adapter reports them rather than letting them pass as ordinary missing evidence.

One capture serves both ends of a link. F1-C is one wire, so the CU-CP's view and the DU's view
of it are the same packets; capturing once and reporting the file for both is not an assumption,
it is the same observation read twice.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path


class WireCapture:
    """tcpdump per interface, started before the products and stopped after them."""

    def __init__(self, outdir: Path, store, use_sudo: bool = True):
        self.outdir = Path(outdir)
        self.store = store
        self.sudo = ["sudo", "-n"] if use_sudo else []
        self._procs: dict[str, subprocess.Popen] = {}
        self._files: dict[str, Path] = {}
        self._filters: dict[str, str] = {}

    def available(self) -> bool:
        return bool(shutil.which("tcpdump"))

    def start(self, filters: dict[str, str], settle: float = 1.5) -> None:
        """Start one capture per named interface. Never raises: a capture that cannot be
        started is missing evidence, and the tests that need it grade 'na'."""
        if not self.available():
            return
        self.stop()
        self.outdir.mkdir(parents=True, exist_ok=True)
        for name, bpf in filters.items():
            path = self.outdir / f"{name}.pcap"
            try:
                path.unlink()
            except OSError:
                pass
            # -U writes each packet through rather than holding a block, so a capture stopped
            # early still has everything up to that point rather than a truncated last buffer.
            cmd = [*self.sudo, "tcpdump", "-i", "any", "-U", "-s", "0", "-w", str(path), bpf]
            self.store.record_command(" ".join(cmd))
            try:
                # Own session, so stopping means signalling the group: tcpdump is sudo's child
                # and would otherwise outlive a signal aimed at sudo.
                p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                continue
            self._procs[name] = p
            self._files[name] = path
            self._filters[name] = bpf
        # tcpdump binds its filter asynchronously; returning before that races the first
        # packets of NG Setup, which is exactly the evidence the setup cases need.
        if self._procs:
            time.sleep(settle)

    def stop(self, drain: float = 1.5) -> None:
        """Stop every capture, letting tcpdump flush. Safe to call when nothing is running.

        Signalling is done with ``os.killpg`` rather than by shelling out to ``kill`` with a
        negative pid. That looked equivalent and was not: ``sudo -n kill -TERM -<pgid>`` was
        observed terminating *this* process instead of the capture's group, even with the two
        groups verifiably distinct, and it took the whole campaign down with it. Passing a
        process group as a negative number through an argument parser is ambiguous by nature;
        ``killpg`` says what it means and cannot be re-read as a signal number.

        The guard below is the belt to that brace: whatever the pgid lookup returns, this must
        never signal the group the campaign itself is running in.
        """
        if not self._procs:
            return
        time.sleep(drain)          # let the last packets of the teardown land
        ours = os.getpgid(0)
        for p in self._procs.values():
            try:
                pg = os.getpgid(p.pid)
            except OSError:
                continue           # already gone
            try:
                if pg == ours:
                    # Never signal our own group. Fall back to the one process we know.
                    p.terminate()
                else:
                    # SIGTERM, not SIGKILL: tcpdump flushes and closes the file on it, and
                    # SIGKILL would leave the tail of the capture unwritten.
                    os.killpg(pg, signal.SIGTERM)
            except OSError:
                continue
        deadline = time.time() + 10
        for p in self._procs.values():
            try:
                p.wait(timeout=max(0.1, deadline - time.time()))
            except (subprocess.TimeoutExpired, OSError):
                pass
        self._procs.clear()

    def paths(self) -> dict[str, str]:
        """``{interface: path}`` for captures that exist and hold at least one packet.

        An empty file is not evidence. Reporting it would let a test read "no procedures seen"
        as "the product did not perform them", when the truth is that nothing was captured.
        """
        out: dict[str, str] = {}
        for name, path in self._files.items():
            try:
                if path.exists() and path.stat().st_size > 24:   # 24 = pcap header alone
                    out[name] = str(path)
            except OSError:
                continue
        return out

    def empty(self) -> dict[str, str]:
        """``{interface: filter}`` for captures that ran but caught nothing.

        Worth surfacing separately. A capture that stayed empty while its link was established
        means the filter is wrong, which is our fault, not the product's.
        """
        got = self.paths()
        return {n: self._filters.get(n, "") for n in self._files if n not in got}
