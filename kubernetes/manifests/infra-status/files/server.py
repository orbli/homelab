#!/usr/bin/env python3
# infra-status — runs in-cluster, SERVES a sanitized snapshot over HTTP.
# Reached publicly via the cloudflared tunnel (outbound-only; home IP never exposed).
# Privacy rule: NEVER emit IPs, internal hostnames, datacenter/geo strings.
# Only counts, pretty labels, and aggregate metrics leave this process.
import json, os, ssl, time, threading, subprocess, urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TIMEOUT = 6
TTL     = int(os.environ.get("TTL", "30"))         # seconds to cache a snapshot
PORT    = int(os.environ.get("PORT", "8080"))
PROM    = "http://prometheus-kube-prometheus-prometheus.observability.svc:9090"
GLM     = "http://glm-spark1.networking.svc:8000"
NOMAD   = "http://nomad-hk2.networking.svc:4646"
KUBE    = "https://kubernetes.default.svc"
SA_DIR  = "/var/run/secrets/kubernetes.io/serviceaccount"


def http(url, ca=None, tok=None):
    req = urllib.request.Request(url)
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    ctx = None
    if url.startswith("https"):
        ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        return r.read().decode()


def jget(url, **kw):
    try:
        return json.loads(http(url, **kw))
    except Exception as e:
        print(f"  ! {url} -> {type(e).__name__}: {e}", flush=True)
        return None


def kube(path):
    try:
        tok = open(f"{SA_DIR}/token").read().strip()
        return json.loads(http(KUBE + path, ca=f"{SA_DIR}/ca.crt", tok=tok))
    except Exception as e:
        print(f"  ! kube {path} -> {type(e).__name__}: {e}", flush=True)
        return None


def prom(q):
    try:
        d = jget(PROM + "/api/v1/query?query=" + urllib.parse.quote(q))
        res = d["data"]["result"]
        return float(res[0]["value"][1]) if res else None
    except Exception:
        return None


def parse_prom_text(txt, key):
    for line in txt.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if parts and parts[0] == key:
            try:
                return float(parts[-1])
            except ValueError:
                return None
    return None


def age_days(ts):
    try:
        t = time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        return int((time.time() - time.mktime(t) + time.timezone) / 86400)
    except Exception:
        return None


def compute():
    c = {"model": None, "up": False, "apis": ["OpenAI", "Anthropic"],
         "params_b": None, "ctx": None, "vram_gb": None, "gpus": 2,
         "tok_s": None, "kv_cache_pct": None}
    m = jget(GLM + "/v1/models")
    if m and m.get("data"):
        d = m["data"][0]
        c["model"] = d.get("id")
        meta = d.get("meta", {})
        if meta.get("n_params"):
            c["params_b"] = round(meta["n_params"] / 1e9, 1)
        if meta.get("size"):
            c["vram_gb"] = round(meta["size"] / (1024**3))
        c["ctx"] = meta.get("n_ctx")
    try:
        http(GLM + "/health"); c["up"] = True
    except Exception:
        c["up"] = c["model"] is not None
    try:                                    # /metrics exists only after --metrics relaunch
        txt = http(GLM + "/metrics")
        tps = parse_prom_text(txt, "llamacpp:predicted_tokens_seconds")
        if tps is not None:
            c["tok_s"] = round(tps, 1)
        kv = parse_prom_text(txt, "llamacpp:kv_cache_usage_ratio")
        if kv is not None:
            c["kv_cache_pct"] = round(kv * 100)
    except Exception:
        pass
    return c


def k3s_node():
    nodes = kube("/api/v1/nodes")
    if not nodes:
        return None
    items = nodes.get("items", [])
    ready = sum(1 for n in items
                for cnd in n["status"].get("conditions", [])
                if cnd["type"] == "Ready" and cnd["status"] == "True")
    ages = [age_days(n["metadata"]["creationTimestamp"]) for n in items]
    ages = [a for a in ages if a is not None]
    pods = kube("/api/v1/pods")
    npods = len(pods.get("items", [])) if pods else None
    cpu = prom('100*(1-avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))')
    mem = prom('100*(1-sum(node_memory_MemAvailable_bytes)/sum(node_memory_MemTotal_bytes))')
    cores = prom('count(node_cpu_seconds_total{mode="idle"})')
    mets = [["uptime", f"{max(ages)}d"] if ages else ["nodes", str(len(items))]]
    if cpu is not None:
        mets.append(["cpu", f"{round(cpu)}%"])
    if mem is not None:
        mets.append(["mem", f"{round(mem)}%"])
    if npods is not None:
        mets.append(["pods", str(npods)])
    if cores is not None:
        mets.append(["cores", str(round(cores))])
    return {"name": "k3s cluster", "role": f"{len(items)}× Pi · 16 GB",
            "status": "ok" if ready == len(items) and items else "warn",
            "pill": f"{ready}/{len(items)} ready", "metrics": mets}


def argocd():
    apps = kube("/apis/argoproj.io/v1alpha1/applications")
    if not apps:
        return None
    items = apps.get("items", [])
    synced = sum(1 for a in items if a.get("status", {}).get("sync", {}).get("status") == "Synced")
    healthy = sum(1 for a in items if a.get("status", {}).get("health", {}).get("status") == "Healthy")
    return {"name": "ArgoCD", "role": "GitOps controller",
            "status": "ok" if healthy == len(items) else "warn",
            "pill": f"{synced}/{len(items)} synced",
            "metrics": [["apps", str(len(items))], ["healthy", str(healthy)]]}


def nomad():
    # Nomad is federated across regions (hk/jp/us); /v1/nodes only returns the
    # queried server's region, so aggregate over every region. Region names are
    # intentionally NOT emitted — only counts (no geo).
    regions = jget(NOMAD + "/v1/regions")
    if not regions:
        return None
    total = ready = njobs = running = 0
    for r in regions:
        nodes = jget(NOMAD + "/v1/nodes?region=" + urllib.parse.quote(r)) or []
        total += len(nodes)
        ready += sum(1 for n in nodes if n.get("Status") == "ready")
        jobs = jget(NOMAD + "/v1/jobs?region=" + urllib.parse.quote(r)) or []
        njobs += len(jobs)
        running += sum(1 for j in jobs if j.get("Status") == "running")
    return {"name": "Nomad", "role": "federation",
            "status": "ok" if ready == total and total else "warn",
            "pill": f"{ready}/{total} ready",
            "metrics": [["regions", str(len(regions))], ["nodes", str(total)],
                        ["running", str(running)]]}


def qnap():
    # The QNAP has no HTTP stats surface and no python, so this is the one tile
    # gathered over SSH (key mounted at /keys/qnap/id, reached via the nas-qnap
    # egress). Best-effort: any failure just drops the tile. Emits only
    # aggregate numbers — no hostname/model/IP.
    key = "/keys/qnap/id"
    if not os.path.exists(key):
        return None
    host = "o@nas-qnap.networking.svc.home-hk1-cluster.orbb.li"
    remote = ('cat /proc/loadavg; grep -E "MemTotal|MemAvailable" /proc/meminfo; '
              'grep -c ^processor /proc/cpuinfo; cut -d. -f1 /proc/uptime; '
              'df -P /share/CACHEDEV2_DATA 2>/dev/null | tail -1')
    try:
        r = subprocess.run(
            ["ssh", "-i", key, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=8", host, remote],
            capture_output=True, text=True, timeout=15)
        out = r.stdout.splitlines()
        if len(out) < 6:
            return None
        load = out[0].split()[0]
        mt = int(out[1].split()[1]); ma = int(out[2].split()[1])
        mem = round((mt - ma) / mt * 100)
        cores = out[3].strip()
        up_d = int(out[4].strip()) // 86400
        p = out[5].split()
        used_tb = int(p[2]) / 1073741824
        tot_tb = int(p[1]) / 1073741824
        disk = f"{used_tb:.1f}/{round(tot_tb)} TB"
    except Exception as e:
        print("qnap: %s" % e, flush=True)
        return None
    return {"name": "QNAP NAS", "role": f"QTS · {cores}-core array",
            "status": "ok", "pill": "online",
            "metrics": [["uptime", f"{up_d}d"], ["load", load],
                        ["mem", f"{mem}%"], ["disk", disk]]}


def platform():
    out = []
    obs = kube("/api/v1/namespaces/observability/pods")
    if obs:
        run = sum(1 for p in obs.get("items", []) if p["status"].get("phase") == "Running")
        out.append({"name": "Observability", "role": "Prometheus · Loki · Tempo · Pyroscope",
                    "status": "ok", "pill": "scraping",
                    "metrics": [["pods", str(run)], ["retain", "30d"]]})
    iam = kube("/api/v1/namespaces/iam/pods")
    if iam:
        run = sum(1 for p in iam.get("items", []) if p["status"].get("phase") == "Running")
        out.append({"name": "Keycloak", "role": "identity · OAuth/OIDC",
                    "status": "ok", "pill": "up", "metrics": [["pods", str(run)]]})
    return out


def cloud():
    return [
        {"name": "Homelab", "sub": "primary", "big": "2", "unit": "sites", "st": "ok"},
        {"name": "GCP", "sub": "projects", "big": "8", "unit": "projects", "st": "ok"},
        {"name": "Oracle", "sub": "cloud VM", "big": "1", "unit": "VM", "st": "ok"},
        {"name": "Firebase", "sub": "static hosting", "big": "3", "unit": "apps", "st": "ok"},
    ]


def build_snapshot():
    print("building snapshot...", flush=True)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "compute": compute(),
        "nodes": [x for x in (k3s_node(), nomad(), qnap()) if x],
        "platform": [x for x in ([argocd()] + platform()) if x],
        "cloud": cloud(),
    }


_lock = threading.Lock()
_cache = {"at": 0.0, "body": None}


def snapshot():
    with _lock:
        if _cache["body"] is None or (time.time() - _cache["at"]) > TTL:
            try:
                _cache["body"] = json.dumps(build_snapshot(), indent=2)
                _cache["at"] = time.time()
            except Exception as e:
                print(f"build failed: {e}", flush=True)
                if _cache["body"] is None:
                    _cache["body"] = json.dumps({"error": "collecting", "generated": None})
        return _cache["body"]


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", f"public, max-age={TTL}")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/healthz", "/ready"):
            return self._send(200, '{"ok":true}')
        if path in ("/", "/infra.json"):
            return self._send(200, snapshot())
        self._send(404, '{"error":"not found"}')

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    print(f"infra-status serving on :{PORT} (TTL={TTL}s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
