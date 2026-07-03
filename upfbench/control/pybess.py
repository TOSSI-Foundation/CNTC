"""BESS white-box control (Suite 1 fast path).

Installs the minimal "forward everything" pipeline over the BESS gRPC API, exactly as in
the original benchmark methodology (the CNDP/AF_XDP reports, §12.b/§12.c):

  1. wildcard PDR (match any) + UL FAR (action=forward) + bypass QER, and
  2. a pipeline SHORT-CIRCUIT: rewire ``executeFAR:1`` straight into ``coreQSplit`` so
     forwarded packets reach the N6/core TX, bypassing ``coreRoutes``/``IPLookup`` —
     which otherwise drops synthetic test traffic (no real route/ARP for the made-up
     destination) into a Sink *before* the TX counter. This isolates the dataplane
     processing path (I/O backend + parse + PDR + QER + FAR) and is the exact technique
     the reference DPDK/CNDP/AF_XDP numbers were produced with. The reported metric is
     therefore "dataplane-processing throughput" (egress route-lookup/MAC-rewrite
     bypassed) — the right basis for comparing I/O modes, which share that egress stage.

BESS-specific; not portable — Suites 2/3 use the standardized pfcpsim path instead.

The BESS gRPC server (localhost:10514) lives inside the UPF pod, so pybess can't be
imported on the host; we ship a short script into the bessd container and run it with
``kubectl exec -i ... -- python3 -``. Config knobs (``upf.extra``): namespace / pod /
bessd_container / kubectl / kubeconfig, plus bess_grpc (localhost:10514) and
bess_path (/opt/bess).

Idempotent: the wildcard add + short-circuit happen once per control instance (one suite);
re-install calls are no-ops. This avoids the BESS WildcardMatch *tuple* table filling
(``clear`` frees rules but not mask tuples), which makes a second add fail with ENOSPC.
``teardown`` restores the original wiring and clears the rules.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from upfbench.control.base import ControlPlane

_HEADER = """\
import sys, json
sys.path.insert(0, {bess_path!r})
from pybess.bess import BESS
bess = BESS(); bess.connect(grpc_url={grpc!r})
def fdvs(vs): return [{{"value_int": int(v)}} for v in vs]
def ogate_target(mod, og):
    for g in bess.get_module_info(mod).ogates:
        if g.ogate == og: return [g.name, g.igate]
    return None
def feeder(dst_mod, dst_igate):
    # find [src_module, src_ogate] currently feeding dst_mod's input igate
    for m in bess.list_modules().modules:
        for g in bess.get_module_info(m.name).ogates:
            if g.name == dst_mod and g.igate == dst_igate:
                return [m.name, g.ogate]
    return None
"""

# Rules + short-circuit. Captures the originals so teardown can restore exactly.
_INSTALL = """\
bess.pause_all()
try:
    bess.run_module_command("pdrLookup","clear","EmptyArg",{})
    bess.run_module_command("farLookup","clear","EmptyArg",{})
    bess.run_module_command("appQERLookup","set_default_gate","QosCommandSetDefaultGateArg",{"gate":6})
    bess.run_module_command("sessionQERLookup","set_default_gate","QosCommandSetDefaultGateArg",{"gate":4})
    bess.run_module_command("farLookup","add","ExactMatchCommandAddArg",
        {"gate":0,"fields":fdvs([1,1]),"values":fdvs([1,0,0,0,0,0])})   # action=1 = UL forward
    bess.run_module_command("pdrLookup","add","WildcardMatchCommandAddArg",
        {"gate":0,"priority":1,"values":fdvs([0]*8),"masks":fdvs([0]*8),"valuesv":fdvs([1,1,1,1,1])})
    # SHORT-CIRCUIT executeFAR:1 -> [sink-MAC rewrite] -> coreQSplit.
    # Bypass coreRoutes/IPLookup (which Sinks synthetic dsts), AND rewrite the egress
    # dst-MAC to a MAC on no VF, so forwarded packets leave the NIC VEB instead of
    # re-circulating back into the UPF access VF (the VEB forwarding loop that otherwise
    # amplifies traffic and floods the generator). Matches the reference "loop-broken" run.
    ef = ogate_target("executeFAR", 1)          # original downstream of the forward gate
    cq_src = feeder("coreQSplit", 0)            # original feeder of coreQSplit:0 (normal path)
    try: bess.destroy_module("ubench_sink")
    except Exception: pass
    bess.create_module("Update","ubench_sink",
        arg={"fields":[{"offset":0,"size":6,"value":0x020000000099}]})  # dst-MAC -> sink
    bess.disconnect_modules("executeFAR", 1)
    if cq_src:
        bess.disconnect_modules(cq_src[0], cq_src[1])
    bess.connect_modules("executeFAR", "ubench_sink", ogate=1, igate=0)
    bess.connect_modules("ubench_sink", "coreQSplit", ogate=0, igate=0)
finally:
    bess.resume_all()
print("RESTORE " + json.dumps({"ef": ef, "cq_src": cq_src}))
print("INSTALL_OK")
"""

# Restore the original wiring (parameterised with the captured RESTORE json), then clear.
_RESTORE = """\
ef = {ef}; cq_src = {cq_src}
bess.pause_all()
try:
    try: bess.disconnect_modules("executeFAR", 1)
    except Exception: pass
    try: bess.destroy_module("ubench_sink")
    except Exception: pass
    if cq_src:
        try: bess.connect_modules(cq_src[0], "coreQSplit", ogate=cq_src[1], igate=0)
        except Exception: pass
    if ef:
        try: bess.connect_modules("executeFAR", ef[0], ogate=1, igate=ef[1])
        except Exception: pass
    try: bess.run_module_command("pdrLookup","clear","EmptyArg",{{}})
    except Exception: pass
    try: bess.run_module_command("farLookup","clear","EmptyArg",{{}})
    except Exception: pass
finally:
    bess.resume_all()
print("RESTORE_OK")
"""


class Control(ControlPlane):
    name = "pybess"

    def __init__(self, cfg, store):
        super().__init__(cfg, store)
        e = cfg.extra
        self.namespace = e.get("namespace", "aether-5gc")
        self.pod = e.get("pod", "upf-0")
        self.container = e.get("bessd_container", "bessd")
        self.kubectl = e.get("kubectl", "kubectl")
        self.kubeconfig = e.get("kubeconfig", "")
        self.grpc = e.get("bess_grpc", "localhost:10514")
        self.bess_path = e.get("bess_path", "/opt/bess")
        self._script_n = 0
        self._installed = False
        self._restore: dict | None = None

    def _run(self, body: str, label: str) -> str:
        script = _HEADER.format(bess_path=self.bess_path, grpc=self.grpc) + body
        cmd = [self.kubectl]
        if self.kubeconfig:
            cmd += ["--kubeconfig", self.kubeconfig]
        cmd += ["exec", "-i", "-n", self.namespace, self.pod, "-c", self.container,
                "--", "python3", "-"]
        self._script_n += 1
        (self.store.raw / f"pybess_{self._script_n:02d}_{label}.py").write_text(script)
        self.store.record_command(
            f"{' '.join(cmd)}  < raw/pybess_{self._script_n:02d}_{label}.py")
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"pybess script '{label}' failed ({proc.returncode}):\n"
                               f"{proc.stderr.strip()}")
        return proc.stdout

    def setup(self) -> None:
        out = self._run('print("CONNECT_OK", len(bess.list_modules().modules), "modules")',
                        "connect")
        if "CONNECT_OK" not in out:
            raise RuntimeError(f"pybess connect check failed: {out!r}")

    def install_sessions(self, count: int = 1, base_id: int = 1, **kw) -> dict[str, Any]:
        """Forward-all rules + executeFAR->coreQSplit short-circuit. Idempotent: only the
        first call touches BESS (avoids WildcardMatch tuple ENOSPC on re-install)."""
        if self._installed:
            return {"mode": "wildcard-forward-all", "reused": True}
        out = self._run(_INSTALL, "install")
        if "INSTALL_OK" not in out:
            raise RuntimeError(f"rule install did not confirm: {out!r}")
        m = re.search(r"RESTORE (\{.*\})", out)
        self._restore = json.loads(m.group(1)) if m else None
        self._installed = True
        return {"mode": "wildcard-forward-all", "pdr": 1, "far": 1,
                "short_circuit": "executeFAR:1->coreQSplit"}

    def delete_sessions(self, count: int = 1, base_id: int = 1) -> dict[str, Any]:
        return self._restore_pipeline()

    def teardown(self) -> None:
        try:
            self._restore_pipeline()
        except Exception as e:  # noqa: BLE001
            print(f"[upfbench] warning: pybess teardown restore failed: {e}")

    def _restore_pipeline(self) -> dict[str, Any]:
        if not self._installed:
            return {"restored": False}
        r = self._restore or {"ef": None, "cq_src": None}
        body = _RESTORE.format(ef=r.get("ef"), cq_src=r.get("cq_src"))
        out = self._run(body, "restore")
        self._installed = False
        self._restore = None
        return {"restored": "RESTORE_OK" in out}

    # aligned_flows: the wildcard PDR matches any TEID/UE-IP, so the generator's default
    # single flow is fine (no specific F-TEID to align to, unlike the pfcpsim path).
    def aligned_flows(self, *args, **kwargs):
        return None, None
