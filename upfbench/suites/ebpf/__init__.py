"""eBPF/XDP Dataplane Assurance suite (the ``upf-ebpf`` certificate).

White-box checks that only an eBPF/XDP UPF (e.g. free5GC + eUPF) can satisfy: the XDP program is
attached and actually forwards, and the N4/PFCP rules bind into — and unbind from — the eBPF
maps (TS 29.244 §5.2). On any non-eBPF UPF every test grades 'na' (the adapter exposes no BPF
introspection) → the profile is INCOMPLETE → no eBPF certificate, which is the correct outcome.
"""
