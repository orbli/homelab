#!/usr/bin/env python3
# infra-status — collects sanitized metrics on a background loop and streams them
# to the browser via Server-Sent Events (plus an /infra.json fallback). Reached
# publicly via the cloudflared tunnel (outbound-only; home IP never exposed).
# Privacy rule: NEVER emit IPs, internal hostnames, datacenter/geo strings.
import json, os, ssl, time, threading, subprocess, urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# INTERVAL is THE knob: per-source refresh + SSE push period, in seconds.
# Cloudflare drops idle proxied streams after ~100s, so keep INTERVAL well below
# that (we push every INTERVAL, which keeps the stream alive at any sane value).
INTERVAL = float(os.environ.get("INTERVAL", "1"))
PORT     = int(os.environ.get("PORT", "8080"))
HTTP_TIMEOUT = 5
PROM  = "http://prometheus-kube-prometheus-prometheus.observability.svc:9090"
GLM   = "http://glm-spark1.networking.svc:8000"
NOMAD = "http://nomad-hk2.networking.svc:4646"
KUBE  = "https://kubernetes.default.svc"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
SSH_KEY = "/keys/qnap/id"   # one key, authorized on the QNAP and both Sparks


def http(url, ca=None, tok=None, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url)
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    ctx = ssl.create_default_context(cafile=ca) if url.startswith("https") and ca else \
          (ssl.create_default_context() if url.startswith("https") else None)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode()


def jget(url, **kw):
    try:
        return json.loads(http(url, **kw))
    except Exception:
        return None


def kube(path):
    try:
        tok = open(f"{SA_DIR}/token").read().strip()
        return json.loads(http(KUBE + path, ca=f"{SA_DIR}/ca.crt", tok=tok))
    except Exception:
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
        p = line.split()
        if p and p[0] == key:
            try:
                return float(p[-1])
            except ValueError:
                return None
    return None


def age_days(ts):
    try:
        t = time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        return int((time.time() - time.mktime(t) + time.timezone) / 86400)
    except Exception:
        return None


def human_bps(v):
    if v is None:
        return "—"
    for u in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.0f} {u}/s"
        v /= 1024
    return f"{v:.0f} TB/s"


def ssh_run(host, remote, timeout):
    # ControlMaster keeps the (slow, tailnet) connection warm so 1 Hz polling is cheap.
    opts = ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=4",
            "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/cm-%h", "-o", "ControlPersist=90s"]
    return subprocess.run(["ssh"] + opts + [host, remote],
                          capture_output=True, text=True, timeout=timeout).stdout


# ─────────────────────────────────────────────────────────── sources ──────────
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
    try:
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


def k3s_panel():
    nodes = kube("/api/v1/nodes")
    if not nodes:
        return None
    items = nodes.get("items", [])
    ready = sum(1 for n in items for cnd in n["status"].get("conditions", [])
                if cnd["type"] == "Ready" and cnd["status"] == "True")
    ages = [a for a in (age_days(n["metadata"]["creationTimestamp"]) for n in items) if a is not None]
    pods = kube("/api/v1/pods")
    npods = len(pods.get("items", [])) if pods else None
    NODEFILT = '{device!~"lo|veth.*|cni.*|flannel.*|docker.*"}'
    q = {
        "cpu":  '100*(1-avg(rate(node_cpu_seconds_total{mode="idle"}[2m])))',
        "mem":  '100*(1-sum(node_memory_MemAvailable_bytes)/sum(node_memory_MemTotal_bytes))',
        "temp": 'avg(node_hwmon_temp_celsius)',
        "mhz":  'avg(node_cpu_scaling_frequency_hertz)/1e6',
        "fan":  'avg(node_hwmon_fan_rpm)',
        "load": 'sum(node_load1)',
        "dr":   'sum(rate(node_disk_read_bytes_total[2m]))',
        "dw":   'sum(rate(node_disk_written_bytes_total[2m]))',
        "nr":   'sum(rate(node_network_receive_bytes_total' + NODEFILT + '[2m]))',
        "nt":   'sum(rate(node_network_transmit_bytes_total' + NODEFILT + '[2m]))',
        "cores": 'count(node_cpu_seconds_total{mode="idle"})',
        "store": 'sum(node_filesystem_size_bytes{mountpoint="/"})/1e12',
    }
    v = {k: prom(expr) for k, expr in q.items()}
    mets = []
    if ages:
        mets.append(["uptime", f"{max(ages)}d"])
    if v["cpu"] is not None:  mets.append(["cpu", f"{round(v['cpu'])}%"])
    if v["mem"] is not None:  mets.append(["mem", f"{round(v['mem'])}%"])
    if v["temp"] is not None: mets.append(["temp", f"{round(v['temp'])}°C"])
    if v["mhz"] is not None:  mets.append(["freq", f"{round(v['mhz'])} MHz"])
    if v["fan"] is not None:  mets.append(["fan", f"{round(v['fan'])} rpm"])
    if v["load"] is not None: mets.append(["load", f"{v['load']:.1f}"])
    # separate cells (not "X · Y") so each short value fits its grid cell
    if v["dr"] is not None: mets.append(["disk ↓", human_bps(v["dr"])])
    if v["dw"] is not None: mets.append(["disk ↑", human_bps(v["dw"])])
    if v["nr"] is not None: mets.append(["net ↓", human_bps(v["nr"])])
    if v["nt"] is not None: mets.append(["net ↑", human_bps(v["nt"])])
    if npods is not None:      mets.append(["pods", str(npods)])
    if v["cores"] is not None: mets.append(["cores", str(round(v["cores"]))])
    if v["store"] is not None: mets.append(["storage", f"{v['store']:.1f} TB"])
    return {"name": "k3s cluster", "kind": "big", "role": f"{len(items)}× Pi 5 · 16 GB",
            "status": "ok" if ready == len(items) and items else "warn",
            "pill": f"{ready}/{len(items)} ready", "metrics": mets}


def _gpu(line):
    # "temp, util, clock, power" (csv nounits) -> dict
    try:
        t, u, c, p = [x.strip() for x in line.split(",")]
        return {"temp": int(float(t)), "util": int(float(u)),
                "mhz": int(float(c)), "power": round(float(p))}
    except Exception:
        return None


def sparks_panel():
    # Two GB10 boxes. spark2 is reachable over its egress (real sshd); spark1 runs
    # Tailscale-SSH which blocks the egress proxy, so we hop to it from spark2 over
    # the RoCE link. One SSH round-trip returns both GPUs + host memory.
    if not os.path.exists(SSH_KEY):
        return None
    host = "o@glm-spark2.networking.svc.home-hk1-cluster.orbb.li"
    Q = "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,clocks.sm,power.draw --format=csv,noheader,nounits"
    remote = (
        f'echo S2; {Q}; '
        'echo H; grep -c ^processor /proc/cpuinfo; '
        "awk '/MemTotal:/{t=$2}/MemAvailable:/{a=$2}END{print t, a}' /proc/meminfo; "
        'cut -d. -f1 /proc/uptime; '
        f'echo S1; ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=4 192.168.100.10 "{Q}"'
    )
    try:
        out = ssh_run(host, remote, timeout=8).splitlines()
    except Exception as e:
        print("sparks: %s" % e, flush=True)
        return None
    gpus, host_m = {}, {}
    sect = None
    for ln in out:
        ln = ln.strip()
        if ln in ("S1", "S2", "H"):
            sect = ln; host_m.setdefault("_h", [])
            continue
        if sect == "S2":
            g = _gpu(ln)
            if g:
                g["label"] = "β"; gpus["β"] = g
        elif sect == "S1":
            g = _gpu(ln)
            if g:
                g["label"] = "α"; gpus["α"] = g
        elif sect == "H" and ln:
            host_m["_h"].append(ln)
    if not gpus:
        return None
    mets = []
    h = host_m.get("_h", [])
    try:
        cores = h[0].strip()
        mt, ma = [int(x) for x in h[1].split()]
        resident_gb = round((mt - ma) / 1024 / 1024)
        total_gb = round(mt / 1024 / 1024)
        up_d = int(h[2].strip()) // 86400
        mets.append(["model resident", f"{resident_gb} / {total_gb} GB"])
        mets.append(["cpu cores", cores])
        mets.append(["uptime", f"{up_d}d"])
    except Exception:
        pass
    ordered = [gpus[k] for k in ("α", "β") if k in gpus]
    return {"name": "GB10 Sparks", "kind": "big", "role": "2× GB10 · GLM tensor-split",
            "status": "ok", "pill": f"{len(ordered)}/2 GPU", "gpus": ordered, "metrics": mets}


def nomad():
    regions = jget(NOMAD + "/v1/regions")
    if not regions:
        return None
    total = ready = running = cores = memmb = 0
    for r in regions:
        rq = urllib.parse.quote(r)
        ns = jget(NOMAD + "/v1/nodes?region=" + rq) or []
        total += len(ns)
        ready += sum(1 for n in ns if n.get("Status") == "ready")
        for n in ns:
            det = jget(NOMAD + "/v1/node/%s?region=%s" % (n.get("ID", ""), rq))
            nr = (det or {}).get("NodeResources", {})
            cores += (nr.get("Cpu") or {}).get("TotalCpuCores") or 0
            memmb += (nr.get("Memory") or {}).get("MemoryMB") or 0
        jobs = jget(NOMAD + "/v1/jobs?region=" + rq) or []
        running += sum(1 for j in jobs if j.get("Status") == "running")
    mets = [["regions", str(len(regions))], ["nodes", str(total)]]
    if cores:
        mets.append(["cores", str(cores)])
    if memmb:
        mets.append(["mem", f"{round(memmb/1024)} GB"])
    mets.append(["running", str(running)])
    return {"name": "Nomad", "kind": "card", "role": "federation",
            "status": "ok" if ready == total and total else "warn",
            "pill": f"{ready}/{total} ready", "metrics": mets}


def qnap():
    if not os.path.exists(SSH_KEY):
        return None
    host = "o@nas-qnap.networking.svc.home-hk1-cluster.orbb.li"
    remote = ('cat /proc/loadavg; grep -E "MemTotal|MemAvailable" /proc/meminfo; '
              'grep -c ^processor /proc/cpuinfo; cut -d. -f1 /proc/uptime; '
              'df -P /share/CACHEDEV2_DATA 2>/dev/null | tail -1')
    try:
        out = ssh_run(host, remote, timeout=8).splitlines()
        if len(out) < 6:
            return None
        load = out[0].split()[0]
        mt = int(out[1].split()[1]); ma = int(out[2].split()[1])
        mem = round((mt - ma) / mt * 100)
        cores = out[3].strip()
        up_d = int(out[4].strip()) // 86400
        p = out[5].split()
        disk = f"{int(p[2]) / 1073741824:.1f}/{round(int(p[1]) / 1073741824)} TB"
    except Exception as e:
        print("qnap: %s" % e, flush=True)
        return None
    return {"name": "QNAP NAS", "kind": "card", "role": f"QTS · {cores}-core array",
            "status": "ok", "pill": "online",
            "metrics": [["uptime", f"{up_d}d"], ["load", load], ["mem", f"{mem}%"], ["disk", disk]]}


def platform():
    out = []
    apps = kube("/apis/argoproj.io/v1alpha1/applications")
    if apps:
        items = apps.get("items", [])
        synced = sum(1 for a in items if a.get("status", {}).get("sync", {}).get("status") == "Synced")
        healthy = sum(1 for a in items if a.get("status", {}).get("health", {}).get("status") == "Healthy")
        out.append({"name": "ArgoCD", "role": "GitOps controller",
                    "status": "ok" if healthy == len(items) else "warn",
                    "pill": f"{synced}/{len(items)} synced",
                    "metrics": [["apps", str(len(items))], ["healthy", str(healthy)]]})
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


# ─────────────────────────────────────── background collection + assembly ──────
PARTS = {}
PLOCK = threading.Lock()
SOURCES = {"compute": compute, "k3s": k3s_panel, "sparks": sparks_panel,
           "nomad": nomad, "qnap": qnap, "platform": platform}


def worker(name, fn):
    while True:
        t = time.time()
        try:
            v = fn()
            with PLOCK:
                PARTS[name] = v
        except Exception as e:
            print(f"src {name}: {e}", flush=True)
        time.sleep(max(0.05, INTERVAL - (time.time() - t)))


def assemble():
    with PLOCK:
        p = dict(PARTS)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "interval": INTERVAL,
        "compute": p.get("compute"),
        "nodes": [x for x in (p.get("k3s"), p.get("sparks"), p.get("nomad"), p.get("qnap")) if x],
        "platform": p.get("platform") or [],
        "cloud": cloud(),
    }


# ───────────────────────────────────────────────────────────── serving ────────
class H(BaseHTTPRequestHandler):
    def _headers(self, code, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/healthz", "/ready"):
            self._headers(200, "application/json"); self.wfile.write(b'{"ok":true}'); return
        if path == "/events":                      # Server-Sent Events stream
            self._headers(200, "text/event-stream",
                          {"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                           "Connection": "keep-alive"})
            try:
                while True:
                    body = json.dumps(assemble())
                    self.wfile.write(("data: " + body + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(INTERVAL)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
        if path in ("/", "/infra.json"):           # snapshot fallback
            self._headers(200, "application/json", {"Cache-Control": "no-cache"})
            self.wfile.write(json.dumps(assemble()).encode()); return
        self._headers(404, "application/json"); self.wfile.write(b'{"error":"not found"}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    for _n, _fn in SOURCES.items():
        threading.Thread(target=worker, args=(_n, _fn), daemon=True).start()
    print(f"infra-status streaming on :{PORT} (INTERVAL={INTERVAL}s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
