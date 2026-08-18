# Deploying free5GC on Kubernetes for CNTC (reproducible runbook)

How the free5GC-on-Kubernetes rig under test was stood up, so it can be rebuilt. Target: a
single-node RKE2 cluster. Chart: `free5gc/free5gc-helm` at `~/free5gc-helm` (core + UERANSIM RAN).

> Prereqs on the node: `gtp5g` kernel module loaded (free5GC UPF), Multus present (RKE2 ships it),
> `kubectl` + `helm`, and the node's primary interface is `eth0` (the chart's `masterIf` default).

## 1. (If needed) tear down a conflicting core
This box previously ran **SD-Core** in namespace `aether-5gc` (OMEC images, *not* free5GC). It was
removed to free the N2 LoadBalancer / Multus attachments:
```bash
kubectl delete ns aether-5gc
```

## 2. Deploy the free5GC core
[`deploy/free5gc-k8s/values.yaml`](../deploy/free5gc-k8s/values.yaml) carries every delta this
node needs: `single` UPF, N6 `masterIf: eth0` (no `eth1`), **and a corrected mongo PV**: the
chart ships a `local` PV pinned to a node literally named `ubuntu` at `/home/ubuntu/mongodbdb`,
which never binds here (this node is `node1`). The override replaces it with a hostPath PV on
`node1`, so Helm creates the right PV itself.

First, prepare the mongo data dir (bitnami mongo runs as uid 1001; a root-owned hostPath →
CrashLoopBackOff), then install:
```bash
sudo mkdir -p /data/free5gc-mongo && sudo chown -R 1001:1001 /data/free5gc-mongo
cd ~/free5gc-helm
helm install free5gc-helm ./charts/free5gc -n free5gc --create-namespace \
  -f ~/control_cntc/deploy/free5gc-k8s/values.yaml
```
Wait until all 12 NFs + `mongodb-0` are `Running` (mongo binds and starts immediately now):
```bash
kubectl get pods -n free5gc
```

## 3. Deploy the in-cluster UERANSIM (gNB + UE)
```bash
helm install ueransim ~/free5gc-helm/charts/ueransim -n free5gc
```
The chart's UE (`imsi-208930000000001`, Ki `8baf47…`) matches the subscriber in
`configs/free5gc-k8s.yaml`, cpbench provisions it into the UDM/UDR via the WebUI on each run.

## 4. Point CNTC at it and run
The service naming (`free5gc-helm-free5gc-<nf>-service:8080`, `nrf-nnrf:8000`, WebUI NodePort 30500,
AMF N2 NodePort 31412, `ueransim-ue`/`ueransim-gnb` pods) is exactly what
[`configs/free5gc-k8s.yaml`](../configs/free5gc-k8s.yaml) expects.
```bash
export KUBECONFIG=$HOME/.kube/config
python3 -m cpbench.cli doctor --config configs/free5gc-k8s.yaml            # → READY
python3 -m cpbench.cli run   --config configs/free5gc-k8s.yaml --nf all --campaign VERIFY-K8S
```

## What a clean run shows (verified)
| NF | Result | Note |
|---|---|---|
| **AMF** | **PASS → certified** (9/9) | registration · 5G-AKA · NAS ciphering/integrity · deregistration · **wrong-key negative attach (SEC-04)** all pass, the in-cluster UE drives the full cycle |
| AUSF / UDM | INCOMPLETE | core auth/subscription **PASS** (transitive on registration); SBI-auth `na`, default free5GC doesn't enforce OAuth2 (400, not 401/403) |
| **NRF** | FAIL | real findings: SBI serves discovery **without a token** (HTTP 200) **and** no TLS |
| SMF | FAIL | in-cluster UPF data path (N6), PDU session doesn't complete |

**AMF certifies on Kubernetes** (`cpbench run --nf amf` → `cntc certify … --profile amf-conformance`).
The remaining gaps are honest: free5GC's default SBI posture (no TLS/OAuth2), an in-cluster UPF
data-path limitation, never a fake pass.

> **On enabling OAuth2:** free5GC's OAuth2 needs the NRF to write an access-token *signing cert*
> (`./cert/nrf.pem`) at startup and that cert distributed to every NF to verify tokens. In this
> chart the cert dir is read-only, so `oauth: true` makes the NRF CrashLoop, and the chart
> provisions no shared OAuth2 certs. Enabling it therefore needs a cert-bootstrap step the chart
> doesn't ship, the "no OAuth2 enforced" result CNTC reports is the correct finding for a
> default free5GC deployment.
