# Learning Guide: understand every piece before the interview

Read this top to bottom, run each part yourself, and break things on purpose.
If you can answer the questions at the end out loud, you're ready to talk about this project.

---

## 1. The app (`app/orbitwatch/`)

**What it is:** A FastAPI web service that tracks satellites from real orbital data and
predicts when they'll be visible from a ground station.

| File | Job |
|---|---|
| `catalog.py` | Loads TLEs and runs **SGP4** to get a satellite's position at any time |
| `geo.py` | Coordinate conversions: TEME → ECEF → latitude/longitude/altitude, plus antenna look angles |
| `passes.py` | Finds every pass over a ground station (AOS, TCA, LOS) |
| `stations.py` | Ground stations, loadable from a JSON file |
| `main.py` | The HTTP endpoints and Prometheus metrics |

Two endpoints are special for Kubernetes:
- `/healthz` = **liveness**: "is the process alive?" If it fails, Kubernetes restarts the pod.
- `/readyz` = **readiness**: "can it take traffic?" If it fails, Kubernetes stops sending requests to it, but doesn't restart it.

**Try it:** run `pytest -q`, start the app, and `curl` each endpoint.

## 1a. Orbits: TLE, SGP4, and coordinate frames

- A **TLE (Two-Line Element set)** is the standard text format for a satellite's orbit at one
  moment, its **epoch**. U.S. Space Force publishes them; CelesTrak redistributes them.
- **SGP4** is the standard model that takes a TLE and computes where the satellite will be at any
  other time, accounting for Earth's bulge and atmospheric drag.
- **TLEs go stale.** Error grows roughly a kilometer or more per day, so operators refresh them often.
  OrbitWatch exports `orbitwatch_tle_age_days` so monitoring can flag stale data.
- **Coordinate frames (good interview depth):**
  1. SGP4 outputs **TEME**, an Earth-centered frame that does *not* spin with the Earth.
  2. Rotate by **GMST** (how far Earth has turned) to get **ECEF**, a frame fixed to the ground.
  3. Convert ECEF to **latitude/longitude/altitude** on the **WGS-84** ellipsoid, since the Earth isn't a perfect sphere.
- **Demo vs. real data:** the bundled TLEs (99901–99904) are generated placeholders, labeled as
  such, so it works offline. `scripts/update-tle.sh` pulls real ones from CelesTrak.

## 1b. Ground-station passes

A ground antenna can only talk to a satellite while it's above the horizon, and in practice
above an **elevation mask** (10° here), because buildings, terrain, and atmosphere block low angles.

- **AOS** (Acquisition of Signal): the satellite rises above the mask
- **TCA** (Time of Closest Approach): its highest point in the pass
- **LOS** (Loss of Signal): it drops below the mask
- **Azimuth** = compass direction to point the antenna; **elevation** = angle above the horizon

**How the algorithm works:** step through the window every 30 s computing elevation. When it
crosses the mask, **bisect** between the two samples to pin the crossing to ~1 s. The peak is
found with a **golden-section search**. A LEO satellite is overhead for only about 5–10 minutes,
so a 30 s step can't skip a pass above 10°.

**How it's verified:** `tests/test_orbits.py` checks positions and passes against **Skyfield**, an
independent, widely used astronomy library. Positions match within 0.02° and 0.5 km, and every
AOS/LOS is a real crossing within ±2 s.

**A real debugging story from building this:** the first version of the test compared our pass
times to Skyfield's `find_events()` and failed by 7 seconds on one pass. Instead of loosening the
test, I compared elevation second by second. Our elevation matched Skyfield's own `altaz()` to
0.0005°, and both crossed 10° at 17:31:06.5, so the 7-second gap came from `find_events()` in that
environment (it also printed a NumPy deprecation warning), not our math. The test now checks crossings
against Skyfield's direct elevation values. *(Re-run it yourself so you can tell this in your own words.)*

## 2. The container (`app/Dockerfile`)

- **Multi-stage build:** stage 1 installs packages, stage 2 copies only the result.
  The final image is smaller and has no build tools an attacker could use.
- **Non-root user (UID 10001):** if someone breaks into the app, they aren't root inside the container.
- **`.dockerignore`:** keeps tests and caches out of the image.

## 3. Kubernetes + Helm (`charts/orbitwatch/`)

Helm is a templating and packaging tool for Kubernetes YAML. `values.yaml` holds the settings;
`templates/` turns them into real Kubernetes objects.

| File | Kubernetes object | Why it matters |
|---|---|---|
| `deployment.yaml` | Deployment | Runs the pods, rolling updates with `maxUnavailable: 0` (no downtime) |
| `service.yaml` | Service | A stable internal address that load-balances across pods |
| `hpa.yaml` | HorizontalPodAutoscaler | Adds pods when CPU > 70%, up to 6 |
| `pdb.yaml` | PodDisruptionBudget | During node maintenance, at least 1 pod stays up |
| `servicemonitor.yaml` | ServiceMonitor | Tells Prometheus to scrape `/metrics` |
| `networkpolicy.yaml` | NetworkPolicy | Default-deny firewall: only allowed callers get in, nothing gets out |
| `configmap.yaml` | ConfigMap | Delivers TLE data and ground stations without rebuilding the image |

**Requests vs. limits** (their application asks about this):
- **Request** = what the scheduler *reserves*. It decides which node the pod fits on.
- **Limit** = the *maximum*. Over the CPU limit, the pod gets slowed (throttled).
  Over the memory limit, it gets killed (OOMKilled).
- The HPA measures CPU **as a percentage of the request**, which is why requests must be set.

**Security context:** non-root, read-only filesystem (with a writable `/tmp`), all Linux
capabilities dropped, seccomp on. This passes Kubernetes' "restricted" Pod Security level.
The pod also gets **no service account token**, since the app never calls the Kubernetes API.
If someone broke in, there'd be no credentials to steal.

**Availability extras:** pods are spread across nodes (topology spread), and the PodDisruptionBudget
is only created when there are 2+ pods. With 1 pod, "keep at least 1 running" would block node
maintenance forever.

**Zero-trust networking (NetworkPolicy):** the first policy blocks *all* traffic in and out of the
pods. The second allows only clients in the same namespace and Prometheus. The app gets **no egress
at all**, not even DNS, because it never needs to call out: orbital data is mounted as a file.
If an attacker got in, they couldn't reach anything else or send data out. (Kubernetes health
probes come from the node itself, and most network plugins let node-to-pod traffic through
regardless. Still, confirm the pods go Ready after enabling it; if a plugin blocks probes,
add your node CIDR under `networkPolicy.extraIngress`.)

**Config without rebuilds:** `helm upgrade ... --set-file tle.data=data/current.tle` puts fresh TLEs
in a ConfigMap. A checksum annotation on the pod template means changing the data automatically
rolls the pods so they load it.

**Try it:** `helm template orbitwatch charts/orbitwatch` prints the final YAML. Change a value and watch it change.

## 4. Terraform (`terraform/`)

Terraform describes infrastructure as code. `plan` shows what will change; `apply` does it.

- `cluster/main.tf` creates:
  - a **Namespace** with Pod Security set to `restricted`
  - a **ResourceQuota**: the whole namespace can't use more than 2 CPU / 2 GiB
  - a **LimitRange**: default requests/limits for containers that forget to set them
  - **kube-prometheus-stack** (Prometheus + Grafana) via the Helm provider
  - the **Grafana dashboard** as a ConfigMap that Grafana loads automatically
  - optionally, **Kyverno** plus the image-signature policy (see section 5a)
  - the **OrbitWatch** Helm release
- The Grafana password is a **sensitive variable**, or Terraform generates a random one.
  It never appears in the repo or in `plan` output. Read it with `terraform output -raw grafana_admin_password`.
- `depends_on` makes sure the guardrails exist before the app is installed.
- **State** is Terraform's record of what it created. Locally it's a file; in CI, GitLab stores it
  (with locking, so two pipelines can't apply at once).
- `aws-k3s/` builds a one-node cluster on EC2. SSH and the Kubernetes API are open **only to your IP**,
  the disk is encrypted, and IMDSv2 is required. **It costs money. Run `terraform destroy` after.**

## 5. CI/CD (`.gitlab-ci.yml`)

Every push runs: **lint → test → build → scan/SBOM/sign → plan → deploy → package (Zarf + bundle)**.

- **Kaniko** builds the image without needing Docker or privileged access on the runner.
- Images are tagged with the **commit SHA**, so you always know exactly what code is running.
- `terraform plan` saves its output; `apply` uses **that exact plan** and needs a manual click.
- **Trivy** scans the image for known vulnerabilities (CVEs) and saves a report.
- The kubeconfig and Grafana password are **masked, protected CI variables**, never in the repo.
- **Runner reachability:** the deploy jobs connect to your cluster from a GitLab runner. Shared
  runners live on the public internet, so a cluster that only allows your IP needs a runner you
  host yourself. Knowing this is a real-world gotcha interviewers like to hear.

## 5a. Supply-chain security (SBOM, signing, admission policy)

Defense customers need to know **exactly what's in the software** and **that nobody tampered with it**.

1. **SBOM (Software Bill of Materials):** the `sbom` job uses **Syft** to list every package in the image
   as a CycloneDX JSON file. When a new vulnerability is announced, you can instantly see whether you're affected.
2. **Signing:** the `sign-image` job uses **cosign** to sign the image **by digest** (its exact content
   hash, not a tag that could be moved), and attaches the SBOM as a **signed attestation**.
3. **Enforcement:** **Kyverno** checks every new pod. If the OrbitWatch image isn't signed with our key
   and doesn't carry the signed SBOM, the pod is **rejected**. It also pins the pod to the verified digest.
4. **Why a key pair instead of "keyless" signing:** keyless signing relies on public internet services
   (Fulcio and the Rekor transparency log). A classified network can't reach them, so we sign with a key
   and skip the public log, which lets verification work fully offline.

## 6. Air-gapped deployment

Classified networks have **no internet**, so you can't `docker pull` or add a Helm repo.

### 6a. Zarf (what defense teams actually use)

**Zarf** is an open-source tool built for exactly this: delivering Kubernetes apps into
disconnected and classified environments.

1. Connected side: `scripts/zarf-package.sh` runs `zarf package create`, which pulls the image and
   the Helm chart (plus optional fresh TLE data) into **one signed `.tar.zst` file**.
2. The file is carried across the air gap.
3. Disconnected side: `zarf init` sets up Zarf's **in-cluster registry**, and `zarf package deploy`
   verifies the signature, pushes the image into that registry, and installs the chart.
4. Zarf's admission agent **rewrites image references** to the internal registry automatically,
   so the same Helm chart works on both sides with no edits.

### 6b. The same thing by hand (`scripts/airgap-*.sh`)

This shows the mechanics that Zarf automates:

1. On the connected side, `airgap-bundle.sh` saves the image to a `.tar` file, packages
   the Helm chart into a `.tgz`, writes **SHA-256 checksums** of everything, and can **sign**
   the checksum list with GPG.
2. The bundle gets physically transferred (USB, approved data diode, etc.).
3. On the offline side, `airgap-install.sh` **verifies the signature and checksums first**, imports
   the image into k3s's containerd (or an internal registry), and installs the chart from the local
   file with `pullPolicy: Never`, so nothing tries to reach the internet.

**Checksums vs. signatures (good interview point):** a checksum only proves a file wasn't
*corrupted*. Someone who swaps a file could also swap the checksum list. A **signature** made with
the builder's private key proves *who* made the bundle, which is what actually detects tampering.
Set `REQUIRE_SIGNATURE=1` to refuse unsigned bundles.

## 7. Monitoring (`monitoring/`)

- Prometheus **scrapes** `/metrics` every 15s, found through the ServiceMonitor.
- The Grafana dashboard shows request rate, **p95 latency**, error rate, reads per satellite, TLE age, and pass-prediction latency.
- **TLE age** per satellite is on the dashboard (green < 3 days, orange, red > 7 days), and pass
  prediction latency gets its own panel, since it's the most CPU-heavy endpoint.
- Metrics use the route template `/telemetry/{norad_id}` instead of each real URL. Otherwise every
  new ID creates a new time series (**high cardinality**), which can overload Prometheus.
- URLs that don't match any route all share one `unmatched` label. Without that, a bot scanning
  thousands of random paths would create thousands of series. (The audit caught this. See `AUDIT.md`.)
- Kubernetes health-probe requests are left out of the stats so they don't drown out real traffic.
- If a handler crashes, the request is still counted as a `500`, so the error-rate panel is honest.

---

## Practice interview questions

Answer these out loud, in your own words.

1. Walk me through what happens from `git push` to a running pod.
2. What's the difference between a liveness and a readiness probe? What goes wrong if you mix them up?
3. Requests vs. limits: what happens when a container goes over its memory limit? Its CPU limit?
4. Why does the HPA need CPU requests to be set?
5. Why save the Terraform plan and apply that file, instead of just running `apply`?
6. Where is Terraform state stored, and why does locking matter?
7. How would you deploy this to a network with no internet? What's the difference between a checksum and a signature?
8. Why Kaniko instead of Docker-in-Docker?
9. What does the ResourceQuota protect against?
10. What's high cardinality in Prometheus, and how did you avoid it?
11. What would you add next? (GitOps with Argo CD/Flux, alert rules, antenna scheduling across satellites.)
12. Why can't GitLab's shared runners deploy to a cluster locked to your IP, and how would you fix it?
13. Your Trivy scan finds a critical CVE in the base image. What do you do?
14. What's a TLE, and why does its age matter? How does your system surface stale data?
15. Walk me through how you turn an SGP4 output into latitude/longitude. Why can't you skip the frame rotation?
16. What are AOS, TCA, and LOS? Why use an elevation mask instead of the horizon?
17. How did you verify your orbit math is correct?
18. What's an SBOM, and why would a defense customer require one?
19. Why sign by digest instead of by tag? Why didn't you use keyless signing?
20. What does your NetworkPolicy allow, and why does the app need no egress at all?
21. What does Zarf do that a plain `docker save` + `helm install` doesn't?
22. Tell me about something that broke while you built this, and how you fixed it.
    *(The Skyfield `find_events` story in section 1b is a real one. Rerun it so you can tell it in your own words.)*
