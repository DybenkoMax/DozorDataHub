#!/usr/bin/env python3
"""
VARTA AI HUB — Full Platform
Zero dependencies: pure Python 3.12 + SQLite in-memory.
All modules: Auth, Data Lake, Model Zoo, Training, Leaderboard,
             Evaluation Dashboard, Annotations, Analytics, Edge.
"""
import sqlite3, uuid, json, hashlib, time, threading, math
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8767

# ══════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════
DB = sqlite3.connect(":memory:", check_same_thread=False)
DB.row_factory = sqlite3.Row
LK = threading.Lock()

def sql(q, p=()):
    with LK:
        c = DB.execute(q, p); DB.commit(); return c

def rows(q, p=()):  return [dict(r) for r in sql(q, p).fetchall()]
def row(q, p=()):   r = sql(q, p).fetchone(); return dict(r) if r else None
def one(q, p=()):   return sql(q, p).fetchone()[0]

# ── Schema ────────────────────────────────────────────────────────────────
for s in [
"""CREATE TABLE users(
    id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT,
    pw_hash TEXT, role TEXT DEFAULT 'annotator',
    is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id TEXT, created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE datasets(
    id TEXT PRIMARY KEY, name TEXT, description TEXT DEFAULT '',
    source_type TEXT, storage_path TEXT,
    object_types TEXT DEFAULT '[]', tags TEXT DEFAULT '{}',
    is_synthetic INTEGER DEFAULT 0,
    total_files INTEGER DEFAULT 0, total_size_bytes INTEGER DEFAULT 0,
    created_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE ai_models(
    id TEXT PRIMARY KEY, name TEXT, version TEXT, architecture TEXT,
    author TEXT, storage_path TEXT DEFAULT '', format TEXT DEFAULT 'pt',
    size_mb REAL DEFAULT 0, input_size TEXT DEFAULT '640x640',
    class_names TEXT DEFAULT '[]', description TEXT DEFAULT '',
    tags TEXT DEFAULT '{}', is_active INTEGER DEFAULT 1,
    created_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE model_benchmarks(
    id TEXT PRIMARY KEY, model_id TEXT, dataset_id TEXT,
    precision REAL, recall REAL, map50 REAL, map50_95 REAL,
    inference_ms REAL, fps REAL, hardware TEXT DEFAULT '',
    notes TEXT DEFAULT '', created_by TEXT,
    created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE annotations(
    id TEXT PRIMARY KEY, media_file_id TEXT, dataset_id TEXT,
    frame_number INTEGER, annotator_id TEXT, status TEXT DEFAULT 'pending',
    boxes TEXT DEFAULT '[]', from_mission INTEGER DEFAULT 0,
    created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE edge_deployments(
    id TEXT PRIMARY KEY, device_id TEXT, device_name TEXT,
    model_id TEXT, status TEXT DEFAULT 'active',
    deployed_at TEXT DEFAULT(datetime('now')),
    deployed_by TEXT, config TEXT DEFAULT '{}')""",
"""CREATE TABLE mission_results(
    id TEXT PRIMARY KEY, mission_id TEXT, device_id TEXT,
    model_id TEXT, detections TEXT DEFAULT '[]',
    hard_frames TEXT DEFAULT '[]', telemetry TEXT DEFAULT '{}',
    created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE training_jobs(
    id TEXT PRIMARY KEY, job_id TEXT, display_name TEXT,
    experiment TEXT, status TEXT DEFAULT 'Queued',
    model_arch TEXT, epochs INTEGER DEFAULT 100,
    dataset_path TEXT DEFAULT '', hardware TEXT DEFAULT '',
    submitted_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE evaluation_runs(
    id TEXT PRIMARY KEY, model_id TEXT, dataset_id TEXT,
    name TEXT, description TEXT DEFAULT '', hardware TEXT DEFAULT '',
    notes TEXT DEFAULT '', created_by TEXT,
    created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE class_metrics(
    id TEXT PRIMARY KEY, run_id TEXT, background_type TEXT, class_name TEXT,
    n INTEGER DEFAULT 0, tp INTEGER DEFAULT 0,
    fp INTEGER DEFAULT 0, fn INTEGER DEFAULT 0,
    precision REAL, recall REAL, f1 REAL, fppi REAL,
    FOREIGN KEY(run_id) REFERENCES evaluation_runs(id) ON DELETE CASCADE)""",
]: sql(s)

# ══════════════════════════════════════════════════════════════════════════
# SEED DATA
# ══════════════════════════════════════════════════════════════════════════
def h(pw): return hashlib.sha256(pw.encode()).hexdigest()
def uid(): return uuid.uuid4().hex[:12]

# Users
for u in [
    (uid(),'admin@varta.ai',    'System Admin',  h('ChangeMe123!'),   'admin'),
    (uid(),'ml@varta.ai',       'ML Engineer',   h('MLEngineer123!'), 'ml_engineer'),
    (uid(),'annotator@varta.ai','Annotator 1',   h('Annotator123!'),  'annotator'),
    (uid(),'analyst@varta.ai',  'Analyst 1',     h('Analyst123!'),    'analyst'),
]:
    sql("INSERT INTO users(id,email,name,pw_hash,role) VALUES(?,?,?,?,?)", u)

ADMIN_ID = row("SELECT id FROM users WHERE role='admin'")["id"]
ML_ID    = row("SELECT id FROM users WHERE role='ml_engineer'")["id"]

# Datasets
DS_IDS = []
for d in [
    ('Shahed-136 Training Set',    'azure_blob','raw-data/shahed-136/2024',    '["shahed"]',             0, 4821,  94000000000),
    ('FPV Combat Synthetic',       'azure_blob','raw-data/fpv-synthetic/v1',   '["fpv"]',                1, 12300, 21000000000),
    ('Ground-Air Benchmark v2',    'azure_blob','datasets/ground-air-bm-v2',   '["ground_air"]',         0, 890,   3200000000),
    ('Multi-Target Mixed',         's3',        's3-bucket/mixed-targets',     '["shahed","air_air","fpv"]',0,7650,158000000000),
]:
    did = uid()
    DS_IDS.append(did)
    sql("INSERT INTO datasets(id,name,source_type,storage_path,object_types,is_synthetic,total_files,total_size_bytes,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
        (did, d[0], d[1], d[2], d[3], d[4], d[5], d[6], ADMIN_ID))

# Models
M_IDS = []
for m in [
    ('ShahedNet',  '3.1','yolov8m', 'Олег', 52.3,  '["shahed","fpv","bird"]',          'Основна модель детекції Шахедів'),
    ('FastEye',    '1.4','yolov8n', 'Петя', 6.2,   '["shahed","fpv"]',                 'Легка модель для edge — Jetson Nano'),
    ('AirTracker', '2.0','yolov9c', 'Вася', 103.7, '["air_air","ground_air"]',          'Повітря-повітря та земля-повітря'),
    ('MultiStrike','4.0','yolov10n','Олег', 4.8,   '["shahed","fpv","air_air","ground_air"]','Multi-class YOLOv10'),
    ('RTDetector', '1.0','rt_detr', 'Петя', 149.1, '["shahed","fpv","air_air"]',        'RT-DETR трансформер — найвища mAP'),
]:
    mid = uid()
    M_IDS.append(mid)
    sql("INSERT INTO ai_models(id,name,version,architecture,author,size_mb,class_names,description,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
        (mid,)+m+(ADMIN_ID,))

# Benchmarks
for b in [
    (M_IDS[0],DS_IDS[0],0.934,0.912,0.941,0.712,18.4,54.3,'RTX 3080'),
    (M_IDS[0],DS_IDS[2],0.921,0.905,0.928,0.698,18.4,54.3,'RTX 3080'),
    (M_IDS[1],DS_IDS[0],0.878,0.861,0.882,0.634,6.1, 163.9,'Jetson Nano'),
    (M_IDS[2],DS_IDS[2],0.956,0.944,0.961,0.741,22.1,45.2,'RTX 3080'),
    (M_IDS[3],DS_IDS[0],0.963,0.951,0.968,0.753,7.3, 136.9,'RTX 4090'),
    (M_IDS[4],DS_IDS[0],0.978,0.967,0.981,0.789,31.2,32.1,'RTX 4090'),
    (M_IDS[3],DS_IDS[2],0.959,0.947,0.964,0.748,7.3, 136.9,'RTX 4090'),
]:
    sql("INSERT INTO model_benchmarks(id,model_id,dataset_id,precision,recall,map50,map50_95,inference_ms,fps,hardware,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (uid(),)+b+(ML_ID,))

# Edge deployments
for e in [
    ('drone-007', 'Дрон «Сокіл-7»',         M_IDS[1], '{"conf":0.45}'),
    ('drone-012', 'Дрон «Беркут-12»',        M_IDS[3], '{"conf":0.50}'),
    ('station-3', 'Пост спостереження №3',   M_IDS[0], '{"conf":0.40}'),
]:
    sql("INSERT INTO edge_deployments(id,device_id,device_name,model_id,config,deployed_by) VALUES(?,?,?,?,?,?)",
        (uid(),)+e+(ML_ID,))

# Missions
for m in [
    ('OPS-2025-001','drone-007', M_IDS[1],'[{"fr":10,"lbl":"shahed","c":0.91}]','[88,134]','{"lat":50.45}'),
    ('OPS-2025-002','drone-012', M_IDS[3],'[{"fr":23,"lbl":"shahed","c":0.96}]','[]',       '{"lat":49.22}'),
    ('OPS-2025-003','station-3', M_IDS[0],'[{"fr":5,"lbl":"air_air","c":0.89}]','[102,211,289]','{"lat":51.10}'),
    ('OPS-2025-004','drone-007', M_IDS[1],'[]',                                  '[12,33,67,91]','{"lat":50.60}'),
]:
    sql("INSERT INTO mission_results(id,mission_id,device_id,model_id,detections,hard_frames,telemetry) VALUES(?,?,?,?,?,?,?)",
        (uid(),)+m)

# Training jobs
for j in [
    ('azml-run-001','shahed-yolov8m-v3', 'shahed-detection','Completed','yolov8m', 100,'RTX 3080'),
    ('azml-run-002','multi-yolov10n-v4', 'multi-target',    'Completed','yolov10n',150,'RTX 4090'),
    ('azml-run-003','rtdetr-baseline-v1','rt-detr-exp',      'Running',  'rt_detr', 200,'RTX 4090'),
]:
    sql("INSERT INTO training_jobs(id,job_id,display_name,experiment,status,model_arch,epochs,hardware,submitted_by) VALUES(?,?,?,?,?,?,?,?,?)",
        (uid(),)+j+(ML_ID,))

# Evaluation run (reference from screenshot)
EVAL_RUN_ID = uid()
sql("INSERT INTO evaluation_runs(id,model_id,dataset_id,name,hardware,created_by) VALUES(?,?,?,?,?,?)",
    (EVAL_RUN_ID, M_IDS[0], DS_IDS[0], 'Field Evaluation — Reference', 'RTX 3080', ML_ID))

EVAL_METRICS = [
    ('frame_background', 'BUILDING', 4,    3,    0,   1,   1.000, 0.750, 0.857, 0.000),
    ('frame_background', 'FIELD',    1990, 2046, 116, 119, 0.946, 0.945, 0.946, 0.058),
    ('frame_background', 'FOREST',   406,  398,  7,   8,   0.983, 0.980, 0.982, 0.017),
    ('frame_background', 'SKY',      456,  479,  48,  100, 0.909, 0.827, 0.866, 0.105),
    ('frame_background', 'UNKNOWN',  1,    0,    0,   1,   None,  0.000, None,  0.000),
    ('object_background','BUILDING', 7,    5,    1,   2,   0.833, 0.714, 0.769, 0.143),
    ('object_background','CLOUDS',   427,  455,  40,  95,  0.919, 0.827, 0.871, 0.094),
    ('object_background','FIELD',    221,  209,  14,  12,  0.937, 0.946, 0.941, 0.063),
    ('object_background','FOREST',   408,  398,  8,   10,  0.980, 0.975, 0.978, 0.020),
    ('object_background','SKY',      1794, 1859, 108, 110, 0.945, 0.944, 0.945, 0.060),
]
for m in EVAL_METRICS:
    sql("INSERT INTO class_metrics(id,run_id,background_type,class_name,n,tp,fp,fn,precision,recall,f1,fppi) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid(), EVAL_RUN_ID)+m)

# ══════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════
def login(email, pw):
    u = row("SELECT * FROM users WHERE email=? AND pw_hash=? AND is_active=1",
            (email, h(pw)))
    if not u: return None
    tok = hashlib.sha256(f"{u['id']}{time.time()}".encode()).hexdigest()
    sql("INSERT INTO sessions VALUES(?,?,datetime('now'))", (tok, u['id']))
    return tok, u

def get_user(token):
    if not token: return None
    s = row("SELECT user_id FROM sessions WHERE token=?", (token,))
    if not s: return None
    return row("SELECT * FROM users WHERE id=?", (s['user_id'],))

def weighted_avg(metrics, field):
    valid = [(r[field], r['n']) for r in metrics if r.get(field) is not None and r['n'] > 0]
    total = sum(n for _, n in valid)
    return sum(v*n for v,n in valid)/total if total else None

# ══════════════════════════════════════════════════════════════════════════
# HTTP HANDLER
# ══════════════════════════════════════════════════════════════════════════
class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a): print(f"  {self.command:6s} {self.path.split('?')[0]} → {a[1]}")

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def rbody(self):
        n = int(self.headers.get("Content-Length",0))
        return json.loads(self.rfile.read(n)) if n else {}

    def token(self):
        a = self.headers.get("Authorization","")
        return a[7:] if a.startswith("Bearer ") else None

    def do_OPTIONS(self):
        self.send_response(204)
        for k,v in [("Access-Control-Allow-Origin","*"),
                    ("Access-Control-Allow-Methods","GET,POST,DELETE,PUT,OPTIONS"),
                    ("Access-Control-Allow-Headers","Authorization,Content-Type")]:
            self.send_header(k,v)
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)
        path, qs = p.path.rstrip("/"), parse_qs(p.query)
        g = lambda k,d=None: qs.get(k,[d])[0]

        if path in ("","/"): return self.send_html(HTML)
        if path == "/health": return self.send_json({"status":"healthy","version":"1.0.0"})

        user = get_user(self.token())

        # ── Datasets ──────────────────────────────────────────────────────
        if path == "/api/v1/datasets":
            return self.send_json(rows("SELECT * FROM datasets ORDER BY created_at DESC"))

        # ── Models ────────────────────────────────────────────────────────
        if path == "/api/v1/models":
            ms = rows("SELECT * FROM ai_models WHERE is_active=1 ORDER BY created_at DESC")
            for m in ms: m['class_names'] = json.loads(m['class_names'])
            return self.send_json(ms)

        if path == "/api/v1/models/leaderboard":
            metric = g("metric","map50")
            did = g("dataset_id")
            q = "SELECT b.*,m.name as mname,m.version as ver,m.author,m.architecture as arch FROM model_benchmarks b JOIN ai_models m ON m.id=b.model_id WHERE 1=1"
            pr = []
            if did: q += " AND b.dataset_id=?"; pr.append(did)
            brs = rows(q, pr)
            col_map = {"map50":"map50","map50_95":"map50_95","precision":"precision","recall":"recall","fps":"fps"}
            col = col_map.get(metric,"map50")
            brs = [b for b in brs if b.get(col) is not None]
            brs.sort(key=lambda b: b[col], reverse=True)
            for i,b in enumerate(brs): b["rank"] = i+1; b["model_name"] = f"{b['mname']} v{b['ver']}"
            return self.send_json({"metric":metric,"dataset_id":did,"rankings":brs})

        # ── Analytics ─────────────────────────────────────────────────────
        if path == "/api/v1/analytics/overview":
            ms = rows("SELECT hard_frames FROM mission_results")
            hard = sum(len(json.loads(m["hard_frames"])) for m in ms)
            return self.send_json({
                "total_datasets":    one("SELECT COUNT(*) FROM datasets"),
                "total_models":      one("SELECT COUNT(*) FROM ai_models WHERE is_active=1"),
                "total_annotations": one("SELECT COUNT(*) FROM annotations"),
                "total_missions":    one("SELECT COUNT(*) FROM mission_results"),
                "hard_frames_total": hard,
            })

        if path == "/api/v1/analytics/feedback-loop":
            ms = rows("SELECT * FROM mission_results ORDER BY created_at DESC")
            with_hard = [m for m in ms if json.loads(m["hard_frames"])]
            total_hard = sum(len(json.loads(m["hard_frames"])) for m in ms)
            return self.send_json({
                "missions_with_hard_frames": len(with_hard),
                "total_hard_frames": total_hard,
                "recent": [{"mission_id":m["mission_id"],"device_id":m["device_id"],
                             "hard_frames":len(json.loads(m["hard_frames"])),"date":m["created_at"]}
                            for m in with_hard[:10]],
            })

        # ── Edge ──────────────────────────────────────────────────────────
        if path == "/api/v1/edge/devices":
            deps = rows("SELECT e.*,m.name as mname,m.version as ver FROM edge_deployments e JOIN ai_models m ON m.id=e.model_id WHERE e.status='active'")
            return self.send_json({"devices":[{
                "device_id":d["device_id"],"device_name":d["device_name"],
                "model":f"{d['mname']} v{d['ver']}","model_id":d["model_id"],
                "deployed_at":d["deployed_at"],"config":json.loads(d["config"])}
                for d in deps]})

        # ── Training ──────────────────────────────────────────────────────
        if path == "/api/v1/training/jobs":
            return self.send_json({"jobs": rows("SELECT * FROM training_jobs ORDER BY created_at DESC")})

        # ── Evaluation ────────────────────────────────────────────────────
        if path == "/api/v1/eval/runs":
            mid, did = g("model_id"), g("dataset_id")
            q = "SELECT * FROM evaluation_runs WHERE 1=1"
            pr = []
            if mid: q += " AND model_id=?"; pr.append(mid)
            if did: q += " AND dataset_id=?"; pr.append(did)
            q += " ORDER BY created_at DESC"
            return self.send_json(rows(q, pr))

        if path.startswith("/api/v1/eval/runs/") and path.endswith("/summary"):
            rid = path.split("/")[5]
            run = row("SELECT * FROM evaluation_runs WHERE id=?", (rid,))
            if not run: return self.send_json({"detail":"Not found"},404)
            ms = rows("SELECT * FROM class_metrics WHERE run_id=?", (rid,))
            def summarize(mlist):
                if not mlist: return {}
                return {
                    "weighted_precision": round(weighted_avg(mlist,"precision") or 0, 4),
                    "weighted_recall":    round(weighted_avg(mlist,"recall") or 0, 4),
                    "weighted_f1":        round(weighted_avg(mlist,"f1") or 0, 4),
                    "avg_fppi":           round(sum(m["fppi"] or 0 for m in mlist)/len(mlist), 4),
                    "total_tp": sum(m["tp"] for m in mlist),
                    "total_fp": sum(m["fp"] for m in mlist),
                    "total_fn": sum(m["fn"] for m in mlist),
                }
            return self.send_json({
                "run_id": rid, "name": run["name"],
                "frame_background":  summarize([m for m in ms if m["background_type"]=="frame_background"]),
                "object_background": summarize([m for m in ms if m["background_type"]=="object_background"]),
            })

        if path.startswith("/api/v1/eval/runs/"):
            rid = path.split("/")[5]
            run = row("SELECT * FROM evaluation_runs WHERE id=?", (rid,))
            if not run: return self.send_json({"detail":"Not found"},404)
            ms = rows("SELECT * FROM class_metrics WHERE run_id=?", (rid,))
            return self.send_json({
                **dict(run),
                "frame_background":  [m for m in ms if m["background_type"]=="frame_background"],
                "object_background": [m for m in ms if m["background_type"]=="object_background"],
            })

        if path == "/api/v1/eval/compare":
            ra, rb = g("run_a"), g("run_b")
            if not ra or not rb: return self.send_json({"detail":"run_a and run_b required"},400)
            def by_key(rid):
                return {f"{r['background_type']}:{r['class_name']}": r
                        for r in rows("SELECT * FROM class_metrics WHERE run_id=?", (rid,))}
            ma, mb = by_key(ra), by_key(rb)
            diffs = []
            for key in sorted(set(ma)|set(mb)):
                bg, cls = key.split(":",1)
                for metric in ("f1","precision","recall","fppi"):
                    av = ma.get(key,{}).get(metric)
                    bv = mb.get(key,{}).get(metric)
                    if av is not None and bv is not None:
                        delta = round(bv-av,4)
                        diffs.append({"background":bg,"class":cls,"metric":metric,
                                      "run_a":round(av,4),"run_b":round(bv,4),
                                      "delta":delta,"improved":delta>0 if metric!="fppi" else delta<0})
            runa = row("SELECT name FROM evaluation_runs WHERE id=?", (ra,))
            runb = row("SELECT name FROM evaluation_runs WHERE id=?", (rb,))
            return self.send_json({"run_a":{"id":ra,"name":runa["name"] if runa else "?"},
                                   "run_b":{"id":rb,"name":runb["name"] if runb else "?"},
                                   "comparisons":diffs})

        if path == "/api/v1/eval/leaderboard":
            metric = g("metric","f1")
            bg = g("background","frame_background")
            runs = rows("SELECT * FROM evaluation_runs ORDER BY created_at DESC")
            result = []
            for run in runs:
                ms = rows("SELECT * FROM class_metrics WHERE run_id=? AND background_type=?", (run["id"],bg))
                if not ms: continue
                val = weighted_avg(ms, metric) if metric != "fppi" else sum(m.get("fppi") or 0 for m in ms)/len(ms)
                if val is None: continue
                result.append({"run_id":run["id"],"run_name":run["name"],
                                "model_id":run["model_id"],"hardware":run["hardware"],
                                "value":round(val,4),"created_at":run["created_at"]})
            result.sort(key=lambda r: r["value"], reverse=(metric!="fppi"))
            for i,r in enumerate(result): r["rank"] = i+1
            return self.send_json({"metric":metric,"background":bg,"rankings":result})

        # ── Annotations ───────────────────────────────────────────────────
        if path == "/api/v1/annotations/queue":
            return self.send_json({"queue": rows("SELECT * FROM annotations WHERE status='pending' LIMIT 20")})

        self.send_json({"detail":"Not found"},404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        body = self.rbody()
        user = get_user(self.token())

        # ── Auth ──────────────────────────────────────────────────────────
        if path == "/api/v1/auth/login":
            r = login(body.get("email",""), body.get("password",""))
            if not r: return self.send_json({"detail":"Invalid credentials"},401)
            tok, u = r
            return self.send_json({"access_token":tok,"token_type":"bearer","role":u["role"],"name":u["name"]})

        # ── Datasets ──────────────────────────────────────────────────────
        if path == "/api/v1/datasets":
            did = uid()
            sql("INSERT INTO datasets(id,name,source_type,storage_path,object_types,is_synthetic,created_by) VALUES(?,?,?,?,?,?,?)",
                (did, body.get("name","New Dataset"), body.get("source_type","azure_blob"),
                 body.get("storage_path",""), json.dumps(body.get("object_types",[])),
                 1 if body.get("is_synthetic") else 0,
                 user["id"] if user else ADMIN_ID))
            return self.send_json(row("SELECT * FROM datasets WHERE id=?", (did,)), 201)

        # ── Models benchmark ──────────────────────────────────────────────
        if "/benchmark" in path:
            mid = path.split("/")[4]
            bid = uid()
            sql("INSERT INTO model_benchmarks(id,model_id,dataset_id,precision,recall,map50,map50_95,inference_ms,fps,hardware,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (bid, mid, body.get("dataset_id",""), body.get("precision"), body.get("recall"),
                 body.get("map50"), body.get("map50_95"), body.get("inference_ms"), body.get("fps"),
                 body.get("hardware",""), user["id"] if user else ADMIN_ID))
            return self.send_json({"id":bid},201)

        # ── Annotations ───────────────────────────────────────────────────
        if path == "/api/v1/annotations":
            aid = uid()
            sql("INSERT INTO annotations(id,media_file_id,dataset_id,frame_number,annotator_id,boxes,status) VALUES(?,?,?,?,?,?,?)",
                (aid, body.get("media_file_id",""), body.get("dataset_id",""),
                 body.get("frame_number"), user["id"] if user else ADMIN_ID,
                 json.dumps(body.get("boxes",[])), "in_progress"))
            return self.send_json({"id":aid,"status":"in_progress"},201)

        # ── Edge deploy ───────────────────────────────────────────────────
        if path == "/api/v1/edge/deploy-model":
            sql("UPDATE edge_deployments SET status='superseded' WHERE device_id=?", (body.get("device_id"),))
            eid = uid()
            sql("INSERT INTO edge_deployments(id,device_id,device_name,model_id,config,deployed_by) VALUES(?,?,?,?,?,?)",
                (eid, body.get("device_id",""), body.get("device_name",""),
                 body.get("model_id",""), json.dumps(body.get("config",{})),
                 user["id"] if user else ADMIN_ID))
            return self.send_json({"deployment_id":eid,"status":"active"},201)

        # ── Mission results ───────────────────────────────────────────────
        if path == "/api/v1/edge/mission-results":
            mis_id = uid()
            sql("INSERT INTO mission_results(id,mission_id,device_id,model_id,detections,hard_frames,telemetry) VALUES(?,?,?,?,?,?,?)",
                (mis_id, body.get("mission_id",f"OPS-{int(time.time())}"),
                 body.get("device_id",""), body.get("model_id",""),
                 json.dumps(body.get("detections",[])),
                 json.dumps(body.get("hard_frames",[])),
                 json.dumps(body.get("telemetry",{}))))
            return self.send_json({"mission_result_id":mis_id,
                                   "hard_frames_queued":len(body.get("hard_frames",[]))},201)

        # ── Training job ──────────────────────────────────────────────────
        if path == "/api/v1/training/jobs":
            jid = uid()
            n = one("SELECT COUNT(*) FROM training_jobs")+1
            job_ref = f"azml-run-{n:03d}"
            sql("INSERT INTO training_jobs(id,job_id,display_name,experiment,status,model_arch,epochs,dataset_path,hardware,submitted_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (jid, job_ref,
                 f"{body.get('model_arch','yolo')}-{body.get('experiment_name','exp')}",
                 body.get("experiment_name","varta-training"),
                 "Queued", body.get("model_arch","yolov8n"),
                 body.get("epochs",100), body.get("dataset_path",""),
                 body.get("hardware",""),
                 user["id"] if user else ML_ID))
            j = row("SELECT * FROM training_jobs WHERE id=?", (jid,))
            def start(): time.sleep(2); sql("UPDATE training_jobs SET status='Running' WHERE id=?", (jid,))
            threading.Thread(target=start, daemon=True).start()
            return self.send_json(j, 201)

        # ── Evaluation run ────────────────────────────────────────────────
        if path == "/api/v1/eval/runs":
            rid = uid()
            sql("INSERT INTO evaluation_runs(id,model_id,dataset_id,name,description,hardware,notes,created_by) VALUES(?,?,?,?,?,?,?,?)",
                (rid, body.get("model_id",""), body.get("dataset_id",""),
                 body.get("name","Unnamed"), body.get("description",""),
                 body.get("hardware",""), body.get("notes",""),
                 user["id"] if user else ML_ID))
            count = 0
            for m in body.get("metrics",[]):
                pre = m.get("precision") or m.get("pre")
                rec = m.get("recall") or m.get("rec")
                f1  = m.get("f1")
                if pre is None and m.get("tp",0)+m.get("fp",0)>0:
                    pre = m["tp"]/(m["tp"]+m["fp"])
                if rec is None and m.get("tp",0)+m.get("fn",0)>0:
                    rec = m["tp"]/(m["tp"]+m["fn"])
                if f1 is None and pre and rec and pre+rec>0:
                    f1 = 2*pre*rec/(pre+rec)
                sql("INSERT INTO class_metrics(id,run_id,background_type,class_name,n,tp,fp,fn,precision,recall,f1,fppi) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid(), rid,
                     m.get("background_type","frame_background"),
                     m.get("class_name","?").upper(),
                     m.get("n",0),m.get("tp",0),m.get("fp",0),m.get("fn",0),
                     pre,rec,f1,m.get("fppi")))
                count += 1
            return self.send_json({"id":rid,"name":body.get("name"),"metrics_count":count},201)

        self.send_json({"detail":"Not found"},404)

    def do_DELETE(self):
        path = urlparse(self.path).path.rstrip("/")
        if path.startswith("/api/v1/eval/runs/"):
            rid = path.split("/")[5]
            sql("DELETE FROM class_metrics WHERE run_id=?", (rid,))
            sql("DELETE FROM evaluation_runs WHERE id=?", (rid,))
            return self.send_json({"deleted":rid})
        if path.startswith("/api/v1/training/jobs/"):
            jid = path.split("/")[5]
            sql("UPDATE training_jobs SET status='Canceled' WHERE job_id=?", (jid,))
            return self.send_json({"cancelled":True})
        self.send_json({"detail":"Not found"},404)

# ══════════════════════════════════════════════════════════════════════════
# FRONTEND
# ══════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VARTA AI HUB</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#030712;--s1:#0f1724;--s2:#162033;--brd:#1e2d40;--brd2:#253348;
  --acc:#fbbf24;--a2:rgba(251,191,36,.12);--a3:rgba(251,191,36,.24);
  --txt:#f1f5f9;--t2:#94a3b8;--t3:#64748b;
  --grn:#22c55e;--g2:rgba(34,197,94,.12);--g3:rgba(34,197,94,.22);
  --red:#ef4444;--r2:rgba(239,68,68,.12);
  --blu:#3b82f6;--b2:rgba(59,130,246,.12);--b3:rgba(59,130,246,.22);
  --pur:#a855f7;--p2:rgba(168,85,247,.1);
  --ora:#f97316;--o2:rgba(249,115,22,.1);
}
html,body,#root{height:100%;font-family:'JetBrains Mono',monospace;background:var(--bg);color:var(--txt);font-size:11px}
::-webkit-scrollbar{width:3px;height:3px}::-webkit-scrollbar-thumb{background:#253348;border-radius:2px}
#root{display:flex;flex-direction:column;height:100vh}

/* LOGIN */
#login{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:99;background-image:linear-gradient(rgba(251,191,36,.03)1px,transparent 1px),linear-gradient(90deg,rgba(251,191,36,.03)1px,transparent 1px);background-size:60px 60px}
.lbox{width:320px}
.lico{width:54px;height:54px;background:var(--a2);border:1px solid var(--a3);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:22px;margin:0 auto 12px}
.lt{text-align:center;font-size:13px;font-weight:700;letter-spacing:.2em;color:var(--acc);margin-bottom:2px}
.ls{text-align:center;font-size:9px;color:var(--t3);letter-spacing:.15em;margin-bottom:20px}
.lcard{background:var(--s1);border:1px solid var(--brd);border-radius:8px;padding:20px}
lbl,label.lbl{display:block;font-size:9px;color:var(--t3);letter-spacing:.1em;text-transform:uppercase;margin-bottom:5px}
.inp{width:100%;background:var(--bg);border:1px solid var(--brd);border-radius:4px;padding:8px 10px;font-size:11px;font-family:inherit;color:var(--txt);outline:none;margin-bottom:12px;transition:border .15s}
.inp:focus{border-color:var(--a3)} .sel{width:100%;background:var(--bg);border:1px solid var(--brd);border-radius:4px;padding:8px 10px;font-size:11px;font-family:inherit;color:var(--txt);outline:none;margin-bottom:12px}
.btn-pri{width:100%;padding:10px;background:var(--acc);color:#0f1724;font-family:inherit;font-size:10px;font-weight:700;letter-spacing:.15em;border:none;border-radius:4px;cursor:pointer;text-transform:uppercase;transition:background .15s}
.btn-pri:hover{background:#fcd34d} .btn-pri:disabled{background:rgba(251,191,36,.3);cursor:not-allowed}
.lhint{font-size:9px;color:var(--t3);text-align:center;margin-top:10px;line-height:1.9}
.emsg{background:var(--r2);border:1px solid rgba(239,68,68,.2);border-radius:4px;padding:7px 10px;font-size:10px;color:var(--red);margin-bottom:10px}

/* LAYOUT */
#app{display:none;flex:1;overflow:hidden;flex-direction:row}
#sb{width:204px;flex-shrink:0;background:var(--s1);border-right:1px solid var(--brd);display:flex;flex-direction:column}
#main{flex:1;overflow-y:auto;padding:22px 26px}
.logo{padding:16px 16px 14px;border-bottom:1px solid var(--brd)}
.logo-t{font-size:12px;font-weight:700;letter-spacing:.2em;color:var(--acc)}
.logo-s{font-size:9px;color:var(--t3);letter-spacing:.12em;margin-top:2px}
nav{flex:1;padding:10px 8px;overflow-y:auto}
.ni{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:4px;font-size:10px;letter-spacing:.1em;cursor:pointer;color:var(--t3);transition:all .12s;border:1px solid transparent;margin-bottom:1px;user-select:none}
.ni:hover{color:var(--txt);background:var(--s2)} .ni.on{background:var(--a2);color:var(--acc);border-color:var(--a3)}
.ni-ico{font-size:12px;width:14px;text-align:center;flex-shrink:0}
.usr{padding:10px;border-top:1px solid var(--brd)}
.un{font-size:10px;color:var(--txt);padding:4px 8px} .ur{font-size:9px;color:var(--t3);letter-spacing:.1em;padding:0 8px 6px}
.btnlo{width:100%;padding:6px 8px;background:none;border:1px solid var(--brd);color:var(--t3);font-family:inherit;font-size:9px;letter-spacing:.1em;border-radius:3px;cursor:pointer;text-align:left;transition:all .12s}
.btnlo:hover{border-color:var(--red);color:var(--red)}

/* COMMON */
.ph{border-bottom:1px solid var(--brd);padding-bottom:14px;margin-bottom:20px;display:flex;align-items:flex-start;justify-content:space-between}
.ptit{font-size:13px;font-weight:700;letter-spacing:.12em;color:var(--acc);text-transform:uppercase;display:flex;align-items:center;gap:8px}
.psub{font-size:9px;color:var(--t3);letter-spacing:.08em;margin-top:3px}
.sg{display:grid;gap:10px;margin-bottom:18px}
.sg4{grid-template-columns:repeat(4,1fr)} .sg2{grid-template-columns:1fr 1fr} .sg3{grid-template-columns:repeat(3,1fr)}
.sc{background:var(--s1);border:1px solid var(--brd);border-radius:5px;padding:12px 14px}
.sl{font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}
.sv{font-size:22px;font-weight:700} .sv.ac{color:var(--acc)} .sv.gn{color:var(--grn)} .sv.rd{color:var(--red)} .sv.or{color:var(--ora)}
.card{background:var(--s1);border:1px solid var(--brd);border-radius:6px;margin-bottom:13px}
.ch{padding:10px 14px;border-bottom:1px solid var(--brd);display:flex;align-items:center;justify-content:space-between}
.ct{font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--t2)}
table{width:100%;border-collapse:collapse}
th{padding:8px 11px;text-align:left;color:var(--t3);font-size:9px;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--brd);font-weight:600;white-space:nowrap}
td{padding:8px 11px;border-bottom:1px solid rgba(30,45,64,.5);font-size:10px;vertical-align:middle}
tr:last-child td{border-bottom:none} tr:hover td{background:rgba(22,32,51,.5)}
.bdg{display:inline-block;padding:2px 7px;border-radius:3px;font-size:9px;font-weight:600;letter-spacing:.06em;border:1px solid;white-space:nowrap}
.ba{color:var(--acc);background:var(--a2);border-color:var(--a3)}
.bb{color:#60a5fa;background:var(--b2);border-color:var(--b3)}
.bg{color:#4ade80;background:var(--g2);border-color:var(--g3)}
.br{color:#f87171;background:var(--r2);border-color:rgba(239,68,68,.2)}
.bgy{color:var(--t2);background:var(--s2);border-color:var(--brd)}
.bp{color:#c084fc;background:var(--p2);border-color:rgba(168,85,247,.2)}
.bor{color:#fb923c;background:var(--o2);border-color:rgba(249,115,22,.2)}
.blm{color:#a3e635;background:rgba(163,230,53,.1);border-color:rgba(163,230,53,.2)}
.ri{background:var(--s1);border:1px solid var(--brd);border-radius:5px;padding:11px 14px;margin-bottom:7px;display:flex;align-items:center;justify-content:space-between;transition:border-color .12s}
.ri:hover{border-color:var(--brd2)}
.rt{font-size:11px;font-weight:600;color:var(--txt)} .rs{font-size:9px;color:var(--t3);margin-top:2px}
.btn{padding:5px 12px;border-radius:3px;font-family:inherit;font-size:9px;font-weight:700;letter-spacing:.1em;cursor:pointer;transition:all .12s;text-transform:uppercase;border:1px solid}
.btn-ac{background:var(--acc);color:#0f1724;border-color:var(--acc)} .btn-ac:hover{background:#fcd34d}
.btn-oc{background:none;color:var(--t3);border-color:var(--brd)} .btn-oc:hover{color:var(--txt);border-color:var(--brd2)}
.btn-sm{padding:3px 8px;font-size:9px} .btn-dr{background:none;color:#f87171;border-color:rgba(239,68,68,.25)} .btn-dr:hover{background:var(--r2)}
.fr{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.fr3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px}
.ff{margin-bottom:10px}
.alert-a{background:rgba(251,191,36,.06);border:1px solid var(--a3);border-radius:5px;padding:10px 14px;font-size:10px;color:var(--acc);display:flex;align-items:flex-start;gap:8px;margin-bottom:14px}
#toast{position:fixed;bottom:18px;right:18px;background:var(--s2);border:1px solid var(--brd);border-radius:5px;padding:9px 14px;font-size:10px;color:var(--txt);opacity:0;transition:opacity .25s;z-index:200;pointer-events:none}
#toast.show{opacity:1} #toast.ok{border-color:rgba(34,197,94,.3);color:var(--grn)}
.tabs{display:flex;gap:5px;margin-bottom:16px;flex-wrap:wrap}
.tab{padding:5px 12px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.1em;cursor:pointer;border:1px solid var(--brd);color:var(--t3);transition:all .15s;text-transform:uppercase}
.tab.on{background:var(--a2);color:var(--acc);border-color:var(--a3)} .tab:hover:not(.on){border-color:var(--brd2);color:var(--txt)}
.bbar{height:5px;background:var(--s2);border-radius:3px;overflow:hidden;width:100%;margin-top:3px}
.bfill{height:100%;border-radius:3px;transition:width .4s}
canvas{display:block}
#acvs{cursor:crosshair}
</style>
</head>
<body>
<div id="root">

<!-- LOGIN -->
<div id="login">
  <div class="lbox">
    <div class="lico">🛡</div>
    <div class="lt">VARTA AI HUB</div>
    <div class="ls">SECURE ACCESS REQUIRED</div>
    <div class="lcard">
      <div id="lerr" class="emsg" style="display:none"></div>
      <label class="lbl">Email</label>
      <input class="inp" id="lem" value="admin@varta.ai">
      <label class="lbl">Password</label>
      <input class="inp" id="lpw" type="password" value="ChangeMe123!">
      <button class="btn-pri" id="lbtn" onclick="doLogin()">ACCESS SYSTEM</button>
    </div>
    <div class="lhint">admin@varta.ai / ChangeMe123! (admin)<br>ml@varta.ai / MLEngineer123! (ml_engineer)<br>annotator@varta.ai / Annotator123! (annotator)</div>
  </div>
</div>

<!-- APP -->
<div id="app">
  <div id="sb">
    <div class="logo"><div class="logo-t">🛡 VARTA HUB</div><div class="logo-s">AI MODEL ECOSYSTEM</div></div>
    <nav id="snav"></nav>
    <div class="usr">
      <div class="un" id="uname"></div><div class="ur" id="urole"></div>
      <button class="btnlo" onclick="doLogout()">⎋ LOGOUT</button>
    </div>
  </div>
  <div id="main"></div>
</div>

<div id="toast"></div>
</div>

<script>
const API = '';
let TOK=null, ROLE=null, UNAME=null;

// ── Utils ─────────────────────────────────────────────────────────────────
const e=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
const fmtB=b=>b>1e12?(b/1e12).toFixed(1)+' TB':b>1e9?(b/1e9).toFixed(1)+' GB':(b/1e6).toFixed(0)+' MB';
const fmt=(v,d=3)=>v!=null?Number(v).toFixed(d):'—';
const fmtP=v=>v!=null?(v*100).toFixed(1)+'%':'—';
const mcol=v=>v==null?'color:var(--t3)':v>=.95?'color:#22c55e':v>=.88?'color:var(--acc)':'color:#ef4444';
const fcol=v=>v==null?'color:var(--t3)':v<=.02?'color:#22c55e':v<=.07?'color:var(--acc)':'color:#ef4444';
function toast(m,ok=true){const t=document.getElementById('toast');t.textContent=m;t.className='show'+(ok?' ok':'');setTimeout(()=>t.className='',2400)}
function setMain(h){document.getElementById('main').innerHTML=h}

async function api(method,path,body){
  const o={method,headers:{'Content-Type':'application/json'}};
  if(TOK)o.headers['Authorization']='Bearer '+TOK;
  if(body)o.body=JSON.stringify(body);
  const r=await fetch(API+path,o);
  return r.json();
}

// ── Auth ──────────────────────────────────────────────────────────────────
async function doLogin(){
  const btn=document.getElementById('lbtn');
  const err=document.getElementById('lerr');
  btn.disabled=true;btn.textContent='AUTHENTICATING...';err.style.display='none';
  const d=await api('POST','/api/v1/auth/login',{
    email:document.getElementById('lem').value,
    password:document.getElementById('lpw').value});
  if(!d.access_token){
    err.style.display='block';err.textContent='Невірний email або пароль';
    btn.disabled=false;btn.textContent='ACCESS SYSTEM';return;
  }
  TOK=d.access_token;ROLE=d.role;UNAME=d.name;
  document.getElementById('login').style.display='none';
  const app=document.getElementById('app');app.style.display='flex';
  document.getElementById('uname').textContent=d.name;
  document.getElementById('urole').textContent=d.role.replace('_',' ').toUpperCase();
  buildNav();go('dashboard');
}
document.getElementById('lpw').addEventListener('keydown',ev=>{if(ev.key==='Enter')doLogin()});
function doLogout(){TOK=null;document.getElementById('login').style.display='flex';document.getElementById('app').style.display='none';}

// ── Nav ───────────────────────────────────────────────────────────────────
const NAVS=[
  {id:'dashboard',ico:'◈',lbl:'DASHBOARD',roles:['admin','ml_engineer','annotator','analyst']},
  {id:'datasets', ico:'◫',lbl:'DATA LAKE', roles:['admin','ml_engineer','analyst']},
  {id:'models',   ico:'⬡',lbl:'MODEL ZOO', roles:['admin','ml_engineer']},
  {id:'training', ico:'⚙',lbl:'TRAINING',  roles:['admin','ml_engineer']},
  {id:'evaluation',ico:'◉',lbl:'EVALUATION',roles:['admin','ml_engineer','analyst']},
  {id:'leaderboard',ico:'◈',lbl:'LEADERBOARD',roles:['admin','ml_engineer','analyst']},
  {id:'analytics',ico:'▦',lbl:'ANALYTICS', roles:['admin','ml_engineer','analyst']},
  {id:'annotate', ico:'▣',lbl:'ANNOTATE',  roles:['admin','ml_engineer','annotator']},
  {id:'edge',     ico:'◌',lbl:'EDGE',       roles:['admin','ml_engineer']},
];
function buildNav(){
  document.getElementById('snav').innerHTML=NAVS
    .filter(n=>n.roles.includes(ROLE))
    .map(n=>`<div class="ni" id="ni-${n.id}" onclick="go('${n.id}')"><span class="ni-ico">${n.ico}</span>${n.lbl}</div>`)
    .join('');
}
function go(pg){
  document.querySelectorAll('.ni').forEach(el=>el.classList.remove('on'));
  const el=document.getElementById('ni-'+pg);if(el)el.classList.add('on');
  ({dashboard:pgDash,datasets:pgDS,models:pgMdl,training:pgTrain,
    evaluation:pgEval,leaderboard:pgLB,analytics:pgAn,annotate:pgAnn,edge:pgEdge}[pg]||pgDash)();
}

// ══════════════════════════════════════════════════════════════════════════
// PAGES
// ══════════════════════════════════════════════════════════════════════════

// ── DASHBOARD ─────────────────────────────────────────────────────────────
async function pgDash(){
  const[ov,fb]=await Promise.all([api('GET','/api/v1/analytics/overview'),api('GET','/api/v1/analytics/feedback-loop')]);
  const hard=ov.hard_frames_total||0;
  setMain(`
    <div class="ph"><div><div class="ptit">◈ Dashboard</div><div class="psub">System overview · Real-time</div></div></div>
    <div class="sg sg4">
      <div class="sc"><div class="sl">Datasets</div><div class="sv ac">${ov.total_datasets}</div></div>
      <div class="sc"><div class="sl">AI Models</div><div class="sv">${ov.total_models}</div></div>
      <div class="sc"><div class="sl">Annotations</div><div class="sv">${ov.total_annotations}</div></div>
      <div class="sc"><div class="sl">Missions</div><div class="sv">${ov.total_missions}</div></div>
    </div>
    ${hard>0?`<div class="alert-a"><span style="font-size:14px">⚠</span><div><strong>FEEDBACK LOOP ACTIVE</strong><br>${hard} складних кадрів з ${fb.missions_with_hard_frames} місій у черзі на re-annotation.</div></div>`:''}
    <div class="card">
      <div class="ch"><span class="ct">Recent Missions — Hard Frames</span></div>
      <table><thead><tr><th>Mission</th><th>Device</th><th>Hard Frames</th><th>Date</th></tr></thead><tbody>
      ${(fb.recent||[]).map(m=>`<tr><td><strong>${e(m.mission_id)}</strong></td><td style="color:var(--t3)">${e(m.device_id)}</td><td><span class="bdg ba">${m.hard_frames} frames</span></td><td style="color:var(--t3)">${m.date.slice(0,10)}</td></tr>`).join('')}
      ${!(fb.recent||[]).length?'<tr><td colspan="4" style="text-align:center;color:var(--t3);padding:20px">No hard frames yet</td></tr>':''}
      </tbody></table>
    </div>
    <div class="sg sg3" style="margin-top:14px">
      ${[['◫ Data Lake','Browse datasets','datasets'],['◉ Evaluation','Per-class metrics','evaluation'],['◈ Leaderboard','Precision·Recall·FPS','leaderboard']].map(([t,d,p])=>`
      <div class="ri" style="cursor:pointer;display:block;padding:14px" onclick="go('${p}')"><div class="rt">${t}</div><div class="rs">${d}</div></div>`).join('')}
    </div>`);
}

// ── DATA LAKE ─────────────────────────────────────────────────────────────
async function pgDS(){
  const ds=await api('GET','/api/v1/datasets');
  setMain(`
    <div class="ph"><div><div class="ptit">◫ Data Lake</div><div class="psub">Azure Blob · S3 · Storage management</div></div>
      <button class="btn btn-ac" onclick="toggleAddDS()">+ NEW DATASET</button></div>
    <div id="dsl">${ds.map(d=>`
      <div class="ri">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:32px;height:32px;background:var(--a2);border:1px solid var(--a3);border-radius:5px;display:flex;align-items:center;justify-content:center">◫</div>
          <div><div class="rt">${e(d.name)}</div><div class="rs">${d.source_type} · ${e(d.storage_path)}</div></div>
        </div>
        <div style="display:flex;align-items:center;gap:18px">
          <div style="text-align:right"><div style="font-size:9px;color:var(--t3)">Files</div><div style="font-size:12px;font-weight:600">${Number(d.total_files).toLocaleString()}</div></div>
          <div style="text-align:right"><div style="font-size:9px;color:var(--t3)">Size</div><div style="font-size:12px;font-weight:600">${fmtB(d.total_size_bytes)}</div></div>
          <div style="display:flex;gap:3px;flex-wrap:wrap">
            ${(JSON.parse(d.object_types||'[]')).map(t=>`<span class="bdg bb">${t}</span>`).join('')}
            ${d.is_synthetic?'<span class="bdg bgy">synthetic</span>':''}
          </div>
        </div>
      </div>`).join('')}
    </div>
    <div id="ds-form" style="display:none" class="card" style="margin-top:14px">
      <div class="ch"><span class="ct">+ New Dataset</span><button class="btn btn-oc btn-sm" onclick="toggleAddDS()">✕</button></div>
      <div style="padding:13px">
        <div class="fr"><div><label class="lbl">Name</label><input class="inp" id="dsn" placeholder="Shahed Training v3"></div>
        <div><label class="lbl">Source</label><select class="sel" id="dss"><option value="azure_blob">Azure Blob</option><option value="s3">AWS S3</option></select></div></div>
        <div class="ff"><label class="lbl">Storage Path</label><input class="inp" id="dsp" placeholder="raw-data/container/prefix"></div>
        <div class="fr"><div><label class="lbl">Object Types</label><input class="inp" id="dst" placeholder="shahed, fpv, air_air"></div>
        <div><label class="lbl">Synthetic?</label><select class="sel" id="dsy"><option value="0">No — Real footage</option><option value="1">Yes — Synthetic</option></select></div></div>
        <button class="btn btn-ac" onclick="addDS()">CREATE DATASET</button>
      </div>
    </div>`);
}
function toggleAddDS(){const f=document.getElementById('ds-form');f.style.display=f.style.display==='none'?'block':'none';}
async function addDS(){
  const n=document.getElementById('dsn').value;if(!n){toast('Enter a name',false);return;}
  await api('POST','/api/v1/datasets',{name:n,source_type:document.getElementById('dss').value,
    storage_path:document.getElementById('dsp').value,
    object_types:document.getElementById('dst').value.split(',').map(s=>s.trim()).filter(Boolean),
    is_synthetic:document.getElementById('dsy').value==='1'});
  toast('Dataset created ✓');pgDS();
}

// ── MODEL ZOO ─────────────────────────────────────────────────────────────
async function pgMdl(){
  const ms=await api('GET','/api/v1/models');
  setMain(`
    <div class="ph"><div><div class="ptit">⬡ Model Zoo</div><div class="psub">YOLO · RT-DETR · External models</div></div>
      <button class="btn btn-ac">⬆ UPLOAD MODEL</button></div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
      ${ms.map(m=>`
      <div class="card" style="margin:0"><div style="padding:13px">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
          <div><div style="font-size:12px;font-weight:700">${e(m.name)}</div><div style="font-size:9px;color:var(--t3);margin-top:2px">v${m.version} · ${e(m.author)}</div></div>
          <span class="bdg bb">${m.architecture}</span>
        </div>
        <div style="font-size:10px;color:var(--t3);margin-bottom:10px;line-height:1.5">${e(m.description)}</div>
        <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--t3);margin-bottom:10px">
          <span>${m.format.toUpperCase()} · ${m.input_size}</span><span>${Number(m.size_mb).toFixed(0)} MB</span>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:10px">
          ${(m.class_names||[]).map(c=>`<span class="bdg bgy" style="font-size:8px">${c}</span>`).join('')}
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-oc btn-sm" style="flex:1" onclick="go('leaderboard')">Benchmarks</button>
          <button class="btn btn-ac btn-sm" style="flex:1" onclick="go('edge')">Deploy</button>
        </div>
      </div></div>`).join('')}
    </div>`);
}

// ── TRAINING ──────────────────────────────────────────────────────────────
async function pgTrain(){
  const d=await api('GET','/api/v1/training/jobs');
  const SC={Completed:'bg',Running:'ba',Queued:'bb',Failed:'br',Canceled:'bgy'};
  setMain(`
    <div class="ph"><div><div class="ptit">⚙ Training Jobs</div><div class="psub">Azure ML · YOLO · Custom architectures</div></div></div>
    <div class="card" style="margin-bottom:16px">
      <div class="ch"><span class="ct">+ New Training Job</span></div>
      <div style="padding:13px">
        <div class="ff"><label class="lbl">Dataset Path</label><input class="inp" id="tjp" placeholder="azureml://datastores/varta/paths/datasets/shahed-v2"></div>
        <div class="fr3">
          <div><label class="lbl">Architecture</label><select class="sel" id="tja">${['yolov8n','yolov8s','yolov8m','yolov9c','yolov10n','rt_detr'].map(a=>`<option>${a}</option>`).join('')}</select></div>
          <div><label class="lbl">Epochs</label><input class="inp" id="tje" type="number" value="100" min="1" max="1000"></div>
          <div><label class="lbl">Experiment</label><input class="inp" id="tjx" value="varta-training"></div>
        </div>
        <button class="btn btn-ac" onclick="submitJob()">▷ SUBMIT JOB</button>
      </div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">Training Jobs</span><button class="btn btn-oc btn-sm" onclick="pgTrain()">↺ Refresh</button></div>
      <table><thead><tr><th>Job</th><th>Experiment</th><th>Arch</th><th>Epochs</th><th>Status</th><th>Date</th><th></th></tr></thead>
      <tbody>${d.jobs.map(j=>`<tr>
        <td><strong>${e(j.display_name)}</strong><div style="font-size:9px;color:var(--t3)">${j.job_id}</div></td>
        <td style="color:var(--t3)">${e(j.experiment)}</td>
        <td><span class="bdg bb">${e(j.model_arch)}</span></td>
        <td>${j.epochs}</td>
        <td><span class="bdg ${SC[j.status]||'bgy'}">${j.status}</span></td>
        <td style="color:var(--t3)">${(j.created_at||'').slice(0,10)}</td>
        <td>${j.status==='Running'?`<button class="btn btn-dr btn-sm" onclick="cancelJob('${j.job_id}')">■</button>`:''}</td>
      </tr>`).join('')}
      ${!d.jobs.length?'<tr><td colspan="7" style="text-align:center;color:var(--t3);padding:20px">No jobs yet</td></tr>':''}
      </tbody></table>
    </div>`);
}
async function submitJob(){
  const p=document.getElementById('tjp').value;if(!p){toast('Enter dataset path',false);return;}
  await api('POST','/api/v1/training/jobs',{dataset_path:p,model_arch:document.getElementById('tja').value,epochs:+document.getElementById('tje').value,experiment_name:document.getElementById('tjx').value});
  toast('Job queued ✓');setTimeout(pgTrain,2500);
}
async function cancelJob(jid){
  await api('DELETE',`/api/v1/training/jobs/${jid}`);toast('Job canceled');pgTrain();
}

// ── EVALUATION ────────────────────────────────────────────────────────────
const CLRS_MAP={BUILDING:'bor',FIELD:'bg',FOREST:'blm',SKY:'bb',UNKNOWN:'bgy',CLOUDS:'bp'};
let evTab='overview', evRun=null;

function fmtBar(v,inv=false){
  if(v==null)return '<span style="color:var(--t3)">—</span>';
  const pct=inv?Math.min(v/.15*100,100):v*100;
  const c=inv?(v<=.02?'#22c55e':v<=.07?'#fbbf24':'#ef4444'):(v>=.95?'#22c55e':v>=.88?'#fbbf24':'#ef4444');
  return `<div style="${inv?fcol(v):mcol(v)};font-weight:600">${fmt(v)}</div><div class="bbar"><div class="bfill" style="width:${pct}%;background:${c}"></div></div>`;
}
function clsBdg(c){return `<span class="bdg ${CLRS_MAP[c]||'bgy'}">${c}</span>`}

function renderMetricsTable(metrics,title){
  return `<div class="card" style="margin-bottom:12px">
    <div class="ch"><span class="ct">${title}</span></div>
    <div style="overflow-x:auto"><table>
    <thead><tr><th>Клас</th><th>N</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>F1</th><th>FPPI</th></tr></thead>
    <tbody>${metrics.map(m=>`<tr>
      <td>${clsBdg(m.class_name)}</td>
      <td style="color:var(--t3)">${Number(m.n).toLocaleString()}</td>
      <td style="color:#4ade80;font-weight:600">${Number(m.tp).toLocaleString()}</td>
      <td style="${m.fp>50?'color:#ef4444':m.fp>10?'color:var(--acc)':'color:var(--t3)'};font-weight:600">${m.fp}</td>
      <td style="${m.fn>50?'color:#ef4444':m.fn>10?'color:var(--acc)':'color:var(--t3)'};font-weight:600">${m.fn}</td>
      <td>${fmtBar(m.precision)}</td>
      <td>${fmtBar(m.recall)}</td>
      <td>${fmtBar(m.f1)}</td>
      <td>${fmtBar(m.fppi,true)}</td>
    </tr>`).join('')}</tbody></table></div>
  </div>`;
}

async function pgEval(){
  const runs=await api('GET','/api/v1/eval/runs');
  if(!evRun&&runs.length)evRun=runs[0].id;
  let runDetail=null,summary=null;
  if(evRun){
    [runDetail,summary]=await Promise.all([api('GET',`/api/v1/eval/runs/${evRun}`),api('GET',`/api/v1/eval/runs/${evRun}/summary`)]);
  }

  // Summary cards
  const fr=summary?.frame_background||{}, ob=summary?.object_background||{};
  const scards=runDetail?`
    <div class="sg sg4" style="margin-bottom:16px">
      <div class="sc"><div class="sl">Weighted F1 (кадр)</div><div class="sv gn">${fr.weighted_f1!=null?(fr.weighted_f1*100).toFixed(1)+'%':'—'}</div></div>
      <div class="sc"><div class="sl">Weighted F1 (об'єкт)</div><div class="sv ac">${ob.weighted_f1!=null?(ob.weighted_f1*100).toFixed(1)+'%':'—'}</div></div>
      <div class="sc"><div class="sl">Avg FPPI (кадр)</div><div class="sv ${fr.avg_fppi<=0.05?'gn':fr.avg_fppi<=0.08?'':'rd'}">${fr.avg_fppi!=null?Number(fr.avg_fppi).toFixed(4):'—'}</div></div>
      <div class="sc"><div class="sl">Avg FPPI (об'єкт)</div><div class="sv or">${ob.avg_fppi!=null?Number(ob.avg_fppi).toFixed(4):'—'}</div></div>
    </div>`:'';

  // Problem alerts
  let alerts='';
  if(runDetail){
    const all=[...runDetail.frame_background,...runDetail.object_background];
    const probs=all.filter(m=>(m.fppi!=null&&m.fppi>.07)||(m.recall!=null&&m.recall<.85)||(m.f1!=null&&m.f1<.85));
    if(probs.length) alerts=`<div class="alert-a"><span style="font-size:14px">⚠</span><div><strong>ПРОБЛЕМНІ КЛАСИ</strong><br>
      ${probs.map(m=>`${clsBdg(m.class_name)} <span style="font-size:9px;color:var(--t3)">(${m.background_type==='frame_background'?'кадр':"об'єкт"})</span>${m.fppi>.07?` <span style="color:#ef4444">FPPI=${fmt(m.fppi)}</span>`:''}${m.recall<.85&&m.recall!=null?` <span style="color:var(--acc)">Recall=${fmtP(m.recall)}</span>`:''}`).join(' &nbsp; ')}
      </div></div>`;
  }

  // Tab content
  let tabContent='';
  if(runDetail){
    if(evTab==='overview') tabContent=renderMetricsTable(runDetail.frame_background,'Фон кадра')+renderMetricsTable(runDetail.object_background,"Фон об'єкта");
    else if(evTab==='frame') tabContent=renderMetricsTable(runDetail.frame_background,'Фон кадра');
    else if(evTab==='object') tabContent=renderMetricsTable(runDetail.object_background,"Фон об'єкта");
  }

  setMain(`
    <div class="ph">
      <div><div class="ptit">◉ Evaluation Dashboard</div><div class="psub">Per-class metrics · Precision / Recall / F1 / FPPI</div></div>
      <button class="btn btn-ac" onclick="showEvalForm()">+ NEW RUN</button>
    </div>
    <div id="eval-form" style="display:none" class="card" style="margin-bottom:16px">
      <div class="ch"><span class="ct">+ New Evaluation Run</span><button class="btn btn-oc btn-sm" onclick="hideEvalForm()">✕</button></div>
      <div style="padding:13px">
        <div class="fr"><div><label class="lbl">Name</label><input class="inp" id="evn" placeholder="Field Eval v3"></div>
        <div><label class="lbl">Hardware</label><input class="inp" id="evh" placeholder="RTX 3080"></div></div>
        <p style="font-size:9px;color:var(--t3);margin-bottom:10px">Буде завантажено reference метрики з прикладу (скріншот). Для реальних даних — відправте POST /api/v1/eval/runs з власними метриками.</p>
        <button class="btn btn-ac" onclick="createEvalRun()">CREATE RUN</button>
      </div>
    </div>
    <div style="display:flex;gap:14px;align-items:flex-start">
      <div style="width:190px;flex-shrink:0">
        <div class="card" style="margin:0">
          <div class="ch"><span class="ct">Eval Runs</span></div>
          <div style="max-height:350px;overflow-y:auto;divide-y:divide-gray-800">
            ${runs.map(r=>`<div onclick="selectRun('${r.id}')" style="padding:10px 12px;cursor:pointer;border-bottom:1px solid var(--brd);transition:background .12s;background:${evRun===r.id?'var(--a2)':'transparent'}">
              <div style="font-size:10px;font-weight:600;color:${evRun===r.id?'var(--acc)':'var(--txt)'};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e(r.name)}</div>
              <div style="font-size:9px;color:var(--t3);margin-top:2px">${r.hardware||'—'}</div>
              <div style="font-size:9px;color:var(--t3)">${(r.created_at||'').slice(0,10)}</div>
            </div>`).join('')}
            ${!runs.length?'<div style="padding:16px;text-align:center;font-size:10px;color:var(--t3)">No runs yet</div>':''}
          </div>
        </div>
      </div>
      <div style="flex:1;min-width:0">
        ${!runDetail?'<div class="card"><div style="padding:48px;text-align:center;color:var(--t3);font-size:11px">Select a run to view metrics</div></div>':''}
        ${runDetail?`
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
            <div><div style="font-size:11px;font-weight:700">${e(runDetail.name)}</div>
            <div style="font-size:9px;color:var(--t3);margin-top:2px">${runDetail.hardware?'⌨ '+runDetail.hardware+' · ':''} ${(runDetail.created_at||'').slice(0,10)}</div></div>
            <button class="btn btn-dr btn-sm" onclick="deleteRun('${runDetail.id}')">🗑 Delete</button>
          </div>
          ${scards}${alerts}
          <div class="tabs">
            <div class="tab ${evTab==='overview'?'on':''}" onclick="evTab='overview';pgEval()">Overview</div>
            <div class="tab ${evTab==='frame'?'on':''}" onclick="evTab='frame';pgEval()">Фон кадра</div>
            <div class="tab ${evTab==='object'?'on':''}" onclick="evTab='object';pgEval()">Фон об'єкта</div>
          </div>
          ${tabContent}
        `:''}
      </div>
    </div>`);
}
function showEvalForm(){document.getElementById('eval-form').style.display='block';}
function hideEvalForm(){document.getElementById('eval-form').style.display='none';}
async function selectRun(id){evRun=id;evTab='overview';pgEval();}
async function createEvalRun(){
  const METS=[
    {background_type:'frame_background', class_name:'BUILDING',n:4,   tp:3,   fp:0, fn:1,  precision:1.000,recall:0.750,f1:0.857,fppi:0.000},
    {background_type:'frame_background', class_name:'FIELD',   n:1990,tp:2046,fp:116,fn:119,precision:0.946,recall:0.945,f1:0.946,fppi:0.058},
    {background_type:'frame_background', class_name:'FOREST',  n:406, tp:398, fp:7, fn:8,  precision:0.983,recall:0.980,f1:0.982,fppi:0.017},
    {background_type:'frame_background', class_name:'SKY',     n:456, tp:479, fp:48,fn:100,precision:0.909,recall:0.827,f1:0.866,fppi:0.105},
    {background_type:'frame_background', class_name:'UNKNOWN', n:1,   tp:0,   fp:0, fn:1,  precision:null, recall:0.000,f1:null, fppi:0.000},
    {background_type:'object_background',class_name:'BUILDING',n:7,   tp:5,   fp:1, fn:2,  precision:0.833,recall:0.714,f1:0.769,fppi:0.143},
    {background_type:'object_background',class_name:'CLOUDS',  n:427, tp:455, fp:40,fn:95, precision:0.919,recall:0.827,f1:0.871,fppi:0.094},
    {background_type:'object_background',class_name:'FIELD',   n:221, tp:209, fp:14,fn:12, precision:0.937,recall:0.946,f1:0.941,fppi:0.063},
    {background_type:'object_background',class_name:'FOREST',  n:408, tp:398, fp:8, fn:10, precision:0.980,recall:0.975,f1:0.978,fppi:0.020},
    {background_type:'object_background',class_name:'SKY',     n:1794,tp:1859,fp:108,fn:110,precision:0.945,recall:0.944,f1:0.945,fppi:0.060},
  ];
  const r=await api('POST','/api/v1/eval/runs',{
    name:document.getElementById('evn').value||'Field Eval Reference',
    hardware:document.getElementById('evh').value||'RTX 3080',
    model_id:'m1',dataset_id:'ds1',metrics:METS});
  evRun=r.id;evTab='overview';toast('Eval run created ✓');pgEval();
}
async function deleteRun(id){
  await api('DELETE',`/api/v1/eval/runs/${id}`);
  evRun=null;toast('Run deleted');pgEval();
}

// ── LEADERBOARD ───────────────────────────────────────────────────────────
let lbM='map50';
async function pgLB(m){
  if(m)lbM=m;
  const d=await api('GET',`/api/v1/models/leaderboard?metric=${lbM}`);
  const METS=[['map50','mAP@50'],['map50_95','mAP@50-95'],['precision','Precision'],['recall','Recall'],['fps','FPS']];
  const medals=['🥇','🥈','🥉'];
  setMain(`
    <div class="ph"><div><div class="ptit">◈ Leaderboard</div><div class="psub">Model performance ranking</div></div></div>
    <div class="tabs">${METS.map(([k,l])=>`<div class="tab ${lbM===k?'on':''}" onclick="pgLB('${k}')">${l}</div>`).join('')}</div>
    <div class="card">
      <table><thead><tr><th>#</th><th>Model</th><th>Author</th><th>Arch</th><th>mAP@50</th><th>Precision</th><th>Recall</th><th>FPS</th><th>Hardware</th></tr></thead>
      <tbody>${(d.rankings||[]).map(r=>`<tr>
        <td style="font-size:${r.rank<=3?14:10}px">${r.rank<=3?medals[r.rank-1]:`<span style="color:var(--t3)">#${r.rank}</span>`}</td>
        <td><strong>${e(r.model_name)}</strong></td>
        <td style="color:var(--t3)">${e(r.author)}</td>
        <td><span class="bdg bb">${e(r.arch)}</span></td>
        <td style="${lbM==='map50'?'color:var(--acc);font-weight:700':''}">${r.map50!=null?(r.map50*100).toFixed(1)+'%':'—'}</td>
        <td style="${lbM==='precision'?'color:var(--acc);font-weight:700':''}">${r.precision!=null?(r.precision*100).toFixed(1)+'%':'—'}</td>
        <td style="${lbM==='recall'?'color:var(--acc);font-weight:700':''}">${r.recall!=null?(r.recall*100).toFixed(1)+'%':'—'}</td>
        <td style="${lbM==='fps'?'color:var(--acc);font-weight:700':''}">${r.fps?Number(r.fps).toFixed(0):'—'}</td>
        <td style="color:var(--t3)">${e(r.hardware)||'—'}</td>
      </tr>`).join('')}
      ${!(d.rankings||[]).length?'<tr><td colspan="9" style="text-align:center;color:var(--t3);padding:20px">No benchmark data</td></tr>':''}
      </tbody></table>
    </div>`);
}

// ── ANALYTICS ─────────────────────────────────────────────────────────────
async function pgAn(){
  const[ov,fb]=await Promise.all([api('GET','/api/v1/analytics/overview'),api('GET','/api/v1/analytics/feedback-loop')]);
  setMain(`
    <div class="ph"><div><div class="ptit">▦ Analytics</div><div class="psub">Mission performance · Feedback loop</div></div></div>
    <div class="sg sg4">
      <div class="sc"><div class="sl">Datasets</div><div class="sv">${ov.total_datasets}</div></div>
      <div class="sc"><div class="sl">Active Models</div><div class="sv ac">${ov.total_models}</div></div>
      <div class="sc"><div class="sl">Annotations</div><div class="sv">${ov.total_annotations}</div></div>
      <div class="sc"><div class="sl">Missions</div><div class="sv">${ov.total_missions}</div></div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">↺ Feedback Loop</span>
        <div style="display:flex;gap:14px">
          <span style="font-size:10px;color:var(--t3)">Місій: <strong style="color:var(--txt)">${fb.missions_with_hard_frames}</strong></span>
          <span style="font-size:10px;color:var(--acc)">Hard frames у черзі: <strong>${fb.total_hard_frames}</strong></span>
        </div>
      </div>
      <table><thead><tr><th>Mission</th><th>Device</th><th>Hard Frames</th><th>Date</th></tr></thead><tbody>
      ${(fb.recent||[]).map(m=>`<tr><td><strong>${e(m.mission_id)}</strong></td><td style="color:var(--t3)">${e(m.device_id)}</td><td><span class="bdg ba">⚠ ${m.hard_frames}</span></td><td style="color:var(--t3)">${m.date.slice(0,10)}</td></tr>`).join('')}
      </tbody></table>
    </div>`);
}

// ── ANNOTATION CANVAS ─────────────────────────────────────────────────────
const LBLS=['shahed','fpv','air_air','ground_air','unknown'];
const CLRS={shahed:'#fbbf24',fpv:'#22c55e',air_air:'#3b82f6',ground_air:'#a855f7',unknown:'#94a3b8'};
let aBoxes=[],aDrw=false,aSt={x:0,y:0},aCur=null,aLbl='shahed',aCtx=null,aCvs=null;

function pgAnn(){
  setMain(`
    <div class="ph">
      <div><div class="ptit">▣ Annotation Lab</div><div class="psub">Click & drag to draw bounding boxes</div></div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-oc btn-sm" onclick="aClear()">↺ CLEAR</button>
        <button class="btn btn-ac" onclick="aSub()">✓ SUBMIT</button>
      </div>
    </div>
    <div style="display:flex;gap:12px;align-items:flex-start">
      <div style="flex:1;background:#000;border:1px solid var(--brd);border-radius:6px;overflow:hidden;cursor:crosshair">
        <canvas id="acvs" width="720" height="400"></canvas>
      </div>
      <div style="width:155px;flex-shrink:0">
        <div class="card" style="margin-bottom:8px">
          <div class="ch"><span class="ct">Label</span></div>
          <div style="padding:6px">${LBLS.map(l=>`<div id="lb-${l}" onclick="setLbl('${l}')" style="display:flex;align-items:center;gap:7px;padding:6px 8px;border-radius:3px;cursor:pointer;font-size:10px;color:var(--t3);border:1px solid transparent;margin-bottom:1px;transition:all .12s"><span style="width:8px;height:8px;border-radius:50%;background:${CLRS[l]};flex-shrink:0"></span>${l}</div>`).join('')}</div>
        </div>
        <div class="card">
          <div class="ch"><span class="ct">Boxes (<span id="bcnt">0</span>)</span></div>
          <div id="blist" style="padding:6px;max-height:180px;overflow-y:auto"></div>
        </div>
      </div>
    </div>`);
  aCvs=document.getElementById('acvs');aCtx=aCvs.getContext('2d');
  aBoxes=[];setLbl('shahed');drawA();
  aCvs.addEventListener('mousedown',ev=>{
    const r=aCvs.getBoundingClientRect();const sx=aCvs.width/r.width,sy=aCvs.height/r.height;
    aSt={x:(ev.clientX-r.left)*sx,y:(ev.clientY-r.top)*sy};aDrw=true;
    aCur={id:Date.now(),x:aSt.x,y:aSt.y,w:0,h:0,lbl:aLbl};
  });
  aCvs.addEventListener('mousemove',ev=>{
    if(!aDrw||!aCur)return;
    const r=aCvs.getBoundingClientRect();const sx=aCvs.width/r.width,sy=aCvs.height/r.height;
    const cx=(ev.clientX-r.left)*sx,cy=(ev.clientY-r.top)*sy;
    aCur={...aCur,x:Math.min(cx,aSt.x),y:Math.min(cy,aSt.y),w:Math.abs(cx-aSt.x),h:Math.abs(cy-aSt.y)};
    drawA();
  });
  aCvs.addEventListener('mouseup',()=>{
    if(aCur&&aCur.w>5&&aCur.h>5){aBoxes.push(aCur);updBL();}
    aDrw=false;aCur=null;drawA();
  });
}
function drawA(){
  if(!aCtx)return;const W=720,H=400;
  aCtx.fillStyle='#0a0f1a';aCtx.fillRect(0,0,W,H);
  aCtx.strokeStyle='#1a2535';aCtx.lineWidth=1;
  for(let x=0;x<W;x+=80){aCtx.beginPath();aCtx.moveTo(x,0);aCtx.lineTo(x,H);aCtx.stroke();}
  for(let y=0;y<H;y+=80){aCtx.beginPath();aCtx.moveTo(0,y);aCtx.lineTo(W,y);aCtx.stroke();}
  aCtx.fillStyle='#253348';aCtx.font='12px JetBrains Mono';aCtx.textAlign='center';
  aCtx.fillText('VIDEO FRAME — Select label → click & drag',W/2,H/2);
  aBoxes.forEach(b=>{
    const c=CLRS[b.lbl]||'#fff';
    aCtx.strokeStyle=c;aCtx.lineWidth=2;aCtx.fillStyle=c+'25';
    aCtx.fillRect(b.x,b.y,b.w,b.h);aCtx.strokeRect(b.x,b.y,b.w,b.h);
    aCtx.fillStyle=c+'cc';aCtx.font='bold 10px JetBrains Mono';aCtx.textAlign='left';
    aCtx.fillText(b.lbl,b.x+3,b.y-4);
  });
  if(aCur){
    const c=CLRS[aLbl]||'#fff';
    aCtx.strokeStyle=c;aCtx.lineWidth=2;aCtx.setLineDash([5,3]);
    aCtx.strokeRect(aCur.x,aCur.y,aCur.w,aCur.h);aCtx.setLineDash([]);
    aCtx.fillStyle=c+'18';aCtx.fillRect(aCur.x,aCur.y,aCur.w,aCur.h);
  }
}
function setLbl(l){
  aLbl=l;
  LBLS.forEach(x=>{const el=document.getElementById('lb-'+x);if(!el)return;
    if(x===l){el.style.background='var(--s2)';el.style.color='var(--txt)';el.style.borderColor='var(--brd)';}
    else{el.style.background='none';el.style.color='var(--t3)';el.style.borderColor='transparent';}
  });
}
function updBL(){
  const el=document.getElementById('bcnt');if(el)el.textContent=aBoxes.length;
  const bl=document.getElementById('blist');if(!bl)return;
  bl.innerHTML=aBoxes.map((b,i)=>`
    <div style="display:flex;align-items:center;justify-content:space-between;padding:3px 4px;font-size:9px">
      <span style="display:flex;align-items:center;gap:5px;color:var(--t3)"><span style="width:7px;height:7px;border-radius:50%;background:${CLRS[b.lbl]||'#fff'};display:inline-block"></span>${i+1}. ${b.lbl}</span>
      <button onclick="aRm(${b.id})" style="background:none;border:none;color:var(--t3);cursor:pointer;font-size:10px;padding:0">✕</button>
    </div>`).join('');
}
function aRm(id){aBoxes=aBoxes.filter(b=>b.id!==id);updBL();drawA();}
function aClear(){aBoxes=[];updBL();drawA();}
async function aSub(){
  if(!aBoxes.length){toast('Add at least one box',false);return;}
  await api('POST','/api/v1/annotations',{media_file_id:'demo-001',dataset_id:'ds1',frame_number:42,boxes:aBoxes.map(b=>({label:b.lbl,x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.w),h:Math.round(b.h)}))});
  toast(`${aBoxes.length} boxes submitted ✓`);aClear();
}

// ── EDGE ──────────────────────────────────────────────────────────────────
async function pgEdge(){
  const[devs,models]=await Promise.all([api('GET','/api/v1/edge/devices'),api('GET','/api/v1/models')]);
  setMain(`
    <div class="ph"><div><div class="ptit">◌ Edge Devices</div><div class="psub">Active deployments · Model selection on-the-fly</div></div></div>
    <div id="edgelist">${(devs.devices||[]).map(d=>`
      <div class="ri">
        <div style="display:flex;align-items:center;gap:12px">
          <div style="width:32px;height:32px;background:var(--g2);border:1px solid var(--g3);border-radius:5px;display:flex;align-items:center;justify-content:center">◌</div>
          <div><div class="rt">${e(d.device_name)}</div><div class="rs">ID: ${e(d.device_id)} · ${(d.deployed_at||'').slice(0,10)}</div></div>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
          <div style="text-align:right"><div style="font-size:9px;color:var(--t3)">Active Model</div><div style="font-size:11px;font-weight:600;color:var(--acc)">${e(d.model)}</div></div>
          <span class="bdg bg">ACTIVE</span>
        </div>
      </div>`).join('')}</div>
    <hr style="border:none;border-top:1px solid var(--brd);margin:16px 0">
    <div class="card">
      <div class="ch"><span class="ct">⬆ Deploy Model to Device</span></div>
      <div style="padding:13px">
        <div class="fr"><div><label class="lbl">Device ID</label><input class="inp" id="ddid" placeholder="drone-013"></div>
        <div><label class="lbl">Device Name</label><input class="inp" id="ddn" placeholder="Дрон Орел-13"></div></div>
        <div class="fr"><div><label class="lbl">Model</label><select class="sel" id="ddm">${models.map(m=>`<option value="${m.id}">${m.name} v${m.version} (${m.architecture})</option>`).join('')}</select></div>
        <div><label class="lbl">Confidence</label><input class="inp" id="ddc" type="number" value="0.45" min="0.1" max="0.99" step="0.05"></div></div>
        <button class="btn btn-ac" onclick="deployM()">▷ DEPLOY</button>
      </div>
    </div>
    <div class="card" style="margin-top:13px">
      <div class="ch"><span class="ct">Edge API Endpoints</span></div>
      <div style="padding:12px;font-size:10px;line-height:2.2">
        ${[['GET','GET /api/v1/edge/active-model/{device_id}','Отримати поточну модель + SAS URL'],
           ['POST','POST /api/v1/edge/mission-results','Відправити детекції + телеметрію + hard frames'],
           ['POST','POST /api/v1/edge/deploy-model','Задеплоїти модель на пристрій']].map(([m,p,d])=>`
        <div style="display:flex;align-items:center;gap:8px">
          <span class="bdg ${m==='GET'?'bb':'bg'}" style="width:38px;text-align:center">${m}</span>
          <code style="color:var(--txt);font-size:9px">${p}</code>
          <span style="color:var(--t3)">— ${d}</span>
        </div>`).join('')}
      </div>
    </div>`);
}
async function deployM(){
  const did=document.getElementById('ddid').value;if(!did){toast('Enter device ID',false);return;}
  await api('POST','/api/v1/edge/deploy-model',{device_id:did,device_name:document.getElementById('ddn').value||did,model_id:document.getElementById('ddm').value,config:{conf_threshold:+document.getElementById('ddc').value}});
  toast('Model deployed ✓');pgEdge();
}
</script>
</body></html>"""

# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    srv = HTTPServer(("0.0.0.0", PORT), H)
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  VARTA AI HUB — Full Platform                          ║
╠══════════════════════════════════════════════════════════╣
║  URL   : http://localhost:{PORT}                          ║
╠══════════════════════════════════════════════════════════╣
║  admin@varta.ai     / ChangeMe123!   → admin            ║
║  ml@varta.ai        / MLEngineer123! → ml_engineer      ║
║  annotator@varta.ai / Annotator123!  → annotator        ║
╚══════════════════════════════════════════════════════════╝
""")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Platform stopped.")
