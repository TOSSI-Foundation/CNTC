# Deploying free5GC on Kubernetes for CNTC (reproducible runbook)

How the free5GC-on-Kubernetes rig under test was stood up, so it can be rebuilt. Target: a
single-node RKE2 cluster. Chart: `free5gc/free5gc-helm` at `~/free5gc-helm` (core + UERANSIM RAN).

> Prereqs on the node: `gtp5g` kernel module loaded (free5GC UPF), Multus present (RKE2 ships it),
> `kubectl` + `helm`, and the node's primary interface is `eth0` (the chart's `masterIf` default).

## 1. (If needed) tear down a conflicting core
This box previously ran **SD-Core** in namespace `aether-5gc` (OMEC images — *not* free5GC). It was
removed to free the N2 LoadBalancer / Multus attachments:
```bash
kubectl delete ns aether-5gc
```

## 2. Deploy the free5GC core
Overrides live in [`deploy/free5gc-k8s/values.yaml`](../deploy/free5gc-k8s/values.yaml): `single`
UPF (simpler than ULCL) and N6 `masterIf: eth0` (this node has no `eth1`).
```bash
cd ~/free5gc-helm
helm install free5gc-helm ./charts/free5gc -n free5gc --create-namespace \
  -f ~/control_cntc/deploy/free5gc-k8s/values.yaml
```

### 2a. Storage fix (this cluster has no dynamic provisioner)
A stale `free5gc-pv-mongo` PV from an old microk8s setup (wrong `storageClassName` + node affinity
for a non-existent node `ubuntu`) leaves `mongodb-0` **Pending**. Replace it with a node-local PV:
```bash
kubectl delete pvc datadir-mongodb-0 -n free5gc ; kubectl delete pv free5gc-pv-mongo
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: PersistentVolume
metadata: { name: free5gc-pv-mongo }
spec:
  capacity: { storage: 8Gi }
  accessModes: ["ReadWriteOnce"]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: microk8s-hostpath
  hostPath: { path: /data/free5gc-mongo, type: DirectoryOrCreate }
  nodeAffinity:
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - { key: kubernetes.io/hostname, operator: In, values: ["node1"] }
YAML
kubectl delete pod mongodb-0 -n free5gc          # statefulset recreates the PVC → binds to the new PV
```

### 2b. Mongo permission fix (bitnami runs as uid 1001)
`mongodb-0` CrashLoopBackOff on a fresh hostPath → the dir is root-owned:
```bash
sudo chown -R 1001:1001 /data/free5gc-mongo && sudo chmod -R 775 /data/free5gc-mongo
kubectl delete pod mongodb-0 -n free5gc
```
Wait until all 12 NFs + `mongodb-0` are `Running`:
```bash
kubectl get pods -n free5gc
```

## 3. Deploy the in-cluster UERANSIM (gNB + UE)
```bash
helm install ueransim ~/free5gc-helm/charts/ueransim -n free5gc
```
The chart's UE (`imsi-208930000000001`, Ki `8baf47…`) matches the subscriber in
`configs/free5gc-k8s.yaml` — cpbench provisions it into the UDM/UDR via the WebUI on each run.

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
| **AMF** | INCOMPLETE (8/9) | registration · 5G-AKA · NAS ciphering/integrity · deregistration all **PASS**; `SEC-04` (wrong-key negative attach) `na` in-cluster |
| AUSF / UDM | INCOMPLETE | core auth/subscription **PASS** (transitive on registration); SBI-auth `na` — default free5GC doesn't enforce OAuth2 (400, not 401/403) |
| **NRF** | FAIL | real findings: SBI serves discovery **without a token** (HTTP 200) **and** no TLS |
| SMF | FAIL | in-cluster UPF data path (N6) — PDU session doesn't complete |

**Registration and 5G-AKA run fully in-cluster.** The gaps are honest: free5GC's default SBI
posture (no TLS/OAuth2), an in-cluster UPF data-path limitation, and one not-yet-driven negative
test — never a fake pass.
