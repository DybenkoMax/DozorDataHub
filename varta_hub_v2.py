#!/usr/bin/env python3
"""
VARTA AI HUB v2.0
Updated per detailed TZ:
- Pipeline tracker (raw→frames→annotated→model runs→metrics→palantir)
- CVAT integration (tasks, XML annotation import)
- Multi-source storage (Backblaze B2, S3, Azure, GDrive, YouTube)
- Palantir containers (build, upload, track)
- Mission results per model (polygon + real field)
- Experiment comparison dashboard
- Extended model architectures
- Telemetry from flight controllers
- Version management
- Base path: /aihub/
Zero dependencies: pure Python 3.12 + SQLite.
"""
import sqlite3, uuid, json, hashlib, time, threading, math, re, xml.etree.ElementTree as ET
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT     = 8767
BASE     = "/aihub"   # deploy prefix → dozorai.com/aihub/

# ══════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════
DB = sqlite3.connect(":memory:", check_same_thread=False)
DB.row_factory = sqlite3.Row
LK = threading.Lock()

def sql(q, p=()):
    with LK:
        c = DB.execute(q, p); DB.commit(); return c

def rows(q, p=()): return [dict(r) for r in sql(q, p).fetchall()]
def row(q, p=()):  r = sql(q, p).fetchone(); return dict(r) if r else None
def one(q, p=()):  return sql(q, p).fetchone()[0]

for s in [
# ── Core ──────────────────────────────────────────────────────────────────
"""CREATE TABLE users(id TEXT PK, email TEXT UNIQUE, name TEXT,
    pw_hash TEXT, role TEXT DEFAULT 'analyst',
    is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT(datetime('now')))""",
"""CREATE TABLE sessions(token TEXT PK, user_id TEXT, created_at TEXT DEFAULT(datetime('now')))""",

# ── Storage & Data Lake ───────────────────────────────────────────────────
"""CREATE TABLE storage_sources(
    id TEXT PRIMARY KEY, name TEXT, source_type TEXT,
    url TEXT, bucket TEXT, prefix TEXT DEFAULT '',
    credentials TEXT DEFAULT '{}', notes TEXT DEFAULT '',
    created_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",

"""CREATE TABLE raw_videos(
    id TEXT PRIMARY KEY, source_id TEXT, filename TEXT,
    url TEXT, size_bytes INTEGER DEFAULT 0,
    duration_sec REAL DEFAULT 0, fps REAL DEFAULT 0,
    resolution TEXT DEFAULT '', tags TEXT DEFAULT '{}',
    pipeline_status TEXT DEFAULT 'raw',
    -- raw → frames_extracted → annotated → model_run → metrics_collected → palantir_exported
    frames_extracted INTEGER DEFAULT 0,
    annotation_status TEXT DEFAULT 'pending',
    models_run TEXT DEFAULT '[]',
    palantir_exported INTEGER DEFAULT 0,
    created_at TEXT DEFAULT(datetime('now')))""",

# ── CVAT Integration ──────────────────────────────────────────────────────
"""CREATE TABLE cvat_tasks(
    id TEXT PRIMARY KEY, video_id TEXT, task_name TEXT,
    cvat_task_id TEXT DEFAULT '', fps_sample REAL DEFAULT 2.0,
    total_frames INTEGER DEFAULT 0, annotated_frames INTEGER DEFAULT 0,
    status TEXT DEFAULT 'created',
    -- created → in_progress → review → done → exported
    assignee TEXT DEFAULT '', labels TEXT DEFAULT '[]',
    exported_at TEXT, export_format TEXT DEFAULT 'CVAT XML 1.1',
    created_at TEXT DEFAULT(datetime('now')))""",

"""CREATE TABLE cvat_annotations(
    id TEXT PRIMARY KEY, task_id TEXT, video_id TEXT,
    frame_number INTEGER, label TEXT, track_id TEXT DEFAULT '',
    xtl REAL, ytl REAL, xbr REAL, ybr REAL,
    occluded INTEGER DEFAULT 0, keyframe INTEGER DEFAULT 1,
    outside INTEGER DEFAULT 0, z_order INTEGER DEFAULT 0,
    source TEXT DEFAULT 'manual',
    created_at TEXT DEFAULT(datetime('now')))""",

# ── Datasets ──────────────────────────────────────────────────────────────
"""CREATE TABLE datasets(
    id TEXT PRIMARY KEY, name TEXT, description TEXT DEFAULT '',
    source_type TEXT, storage_path TEXT,
    object_types TEXT DEFAULT '[]', tags TEXT DEFAULT '{}',
    is_synthetic INTEGER DEFAULT 0, scenario TEXT DEFAULT '',
    total_files INTEGER DEFAULT 0, total_size_bytes INTEGER DEFAULT 0,
    annotation_coverage REAL DEFAULT 0.0,
    created_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",

# ── Models ────────────────────────────────────────────────────────────────
"""CREATE TABLE ai_models(
    id TEXT PRIMARY KEY, name TEXT, version TEXT,
    architecture TEXT, author TEXT,
    storage_path TEXT DEFAULT '', format TEXT DEFAULT 'pt',
    size_mb REAL DEFAULT 0, input_size TEXT DEFAULT '640x640',
    class_names TEXT DEFAULT '[]', description TEXT DEFAULT '',
    tags TEXT DEFAULT '{}', is_active INTEGER DEFAULT 1,
    parent_model_id TEXT DEFAULT '',
    experiment_id TEXT DEFAULT '',
    created_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",

"""CREATE TABLE experiments(
    id TEXT PRIMARY KEY, name TEXT, description TEXT DEFAULT '',
    base_model_arch TEXT, dataset_id TEXT, model_id TEXT DEFAULT '',
    hyperparams TEXT DEFAULT '{}', status TEXT DEFAULT 'planned',
    -- planned → running → completed → archived
    map50 REAL, map50_95 REAL, precision REAL, recall REAL,
    inference_ms REAL, fps REAL, hardware TEXT DEFAULT '',
    epochs INTEGER DEFAULT 100, batch_size INTEGER DEFAULT 16,
    notes TEXT DEFAULT '',
    created_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",

"""CREATE TABLE model_benchmarks(
    id TEXT PRIMARY KEY, model_id TEXT, dataset_id TEXT,
    precision REAL, recall REAL, map50 REAL, map50_95 REAL,
    inference_ms REAL, fps REAL, hardware TEXT DEFAULT '',
    notes TEXT DEFAULT '', created_by TEXT,
    created_at TEXT DEFAULT(datetime('now')))""",

# ── Palantir ──────────────────────────────────────────────────────────────
"""CREATE TABLE palantir_containers(
    id TEXT PRIMARY KEY, name TEXT,
    dataset_ids TEXT DEFAULT '[]', model_ids TEXT DEFAULT '[]',
    annotation_task_ids TEXT DEFAULT '[]',
    container_path TEXT DEFAULT '', size_bytes INTEGER DEFAULT 0,
    status TEXT DEFAULT 'building',
    -- building → ready → uploading → uploaded → processing → done → failed
    palantir_dataset_rid TEXT DEFAULT '',
    upload_progress INTEGER DEFAULT 0,
    result_path TEXT DEFAULT '',
    result_metrics TEXT DEFAULT '{}',
    notes TEXT DEFAULT '',
    created_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",

# ── Edge & Missions ───────────────────────────────────────────────────────
"""CREATE TABLE edge_deployments(
    id TEXT PRIMARY KEY, device_id TEXT, device_name TEXT,
    model_id TEXT, status TEXT DEFAULT 'active',
    deployed_at TEXT DEFAULT(datetime('now')),
    deployed_by TEXT, config TEXT DEFAULT '{}')""",

"""CREATE TABLE mission_results(
    id TEXT PRIMARY KEY, mission_id TEXT,
    device_id TEXT, model_id TEXT,
    mission_type TEXT DEFAULT 'polygon',
    -- polygon | real_field | simulation
    location TEXT DEFAULT '', start_time TEXT DEFAULT '',
    detections TEXT DEFAULT '[]',
    hard_frames TEXT DEFAULT '[]',
    telemetry TEXT DEFAULT '{}',
    flight_log TEXT DEFAULT '{}',
    total_frames INTEGER DEFAULT 0,
    detected_targets INTEGER DEFAULT 0,
    false_positives INTEGER DEFAULT 0,
    false_negatives INTEGER DEFAULT 0,
    storage_path TEXT DEFAULT '',
    created_at TEXT DEFAULT(datetime('now')))""",

# ── Training ──────────────────────────────────────────────────────────────
"""CREATE TABLE training_jobs(
    id TEXT PRIMARY KEY, job_id TEXT, display_name TEXT,
    experiment_id TEXT DEFAULT '',
    experiment TEXT, status TEXT DEFAULT 'Queued',
    model_arch TEXT, epochs INTEGER DEFAULT 100,
    batch_size INTEGER DEFAULT 16,
    dataset_path TEXT DEFAULT '', hardware TEXT DEFAULT '',
    map50 REAL, inference_ms REAL,
    submitted_by TEXT, created_at TEXT DEFAULT(datetime('now')))""",

# ── Evaluation ────────────────────────────────────────────────────────────
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
]:
    sql(s)

# ══════════════════════════════════════════════════════════════════════════
# SEED DATA
# ══════════════════════════════════════════════════════════════════════════
def h(pw): return hashlib.sha256(pw.encode()).hexdigest()
def uid():  return uuid.uuid4().hex[:12]
def now():  return datetime.utcnow().isoformat()

# Users
for u in [
    (uid(),'admin@varta.ai',    'System Admin',   h('ChangeMe123!'),   'admin'),
    (uid(),'ml@varta.ai',       'ML Engineer',    h('MLEngineer123!'), 'ml_engineer'),
    (uid(),'analyst@varta.ai',  'Analyst 1',      h('Analyst123!'),    'analyst'),
]:
    sql("INSERT INTO users(id,email,name,pw_hash,role) VALUES(?,?,?,?,?)", u)

ADMIN_ID = row("SELECT id FROM users WHERE role='admin'")["id"]
ML_ID    = row("SELECT id FROM users WHERE role='ml_engineer'")["id"]

# Storage Sources
SRC_IDS = []
for src in [
    ('Backblaze B2 — Polygon Tests', 'backblaze_b2',
     'https://f003.backblazeb2.com/file/varta-cold-datasets/', 'varta-cold-datasets', 'polygon-tests/'),
    ('Azure Blob — Raw Missions', 'azure_blob',
     'https://vartastorage.blob.core.windows.net/', 'raw-missions', 'videos/'),
    ('AWS S3 — Archive', 's3',
     'https://s3.eu-central-1.amazonaws.com/', 'varta-archive', 'raw/'),
    ('Google Drive — Synthetic', 'gdrive',
     'https://drive.google.com/drive/folders/', '', 'synthetic/'),
]:
    sid = uid(); SRC_IDS.append(sid)
    sql("INSERT INTO storage_sources(id,name,source_type,url,bucket,prefix,created_by) VALUES(?,?,?,?,?,?,?)",
        (sid,)+src+(ADMIN_ID,))

# Raw Videos
VID_IDS = []
pipeline_states = ['raw','frames_extracted','annotated','model_run','metrics_collected','palantir_exported']
for v in [
    ('polygon_test_fpv_001.mp4', SRC_IDS[0],
     'https://f003.backblazeb2.com/file/varta-cold-datasets/polygon-tests/fpv_001.mp4',
     524_288_000, 142.3, 30.0, '1920x1080', 'palantir_exported', 4269, 'done', '["m1","m2","m4"]', 1),
    ('polygon_test_shahed_007.mp4', SRC_IDS[0],
     'https://f003.backblazeb2.com/file/varta-cold-datasets/polygon-tests/shahed_007.mp4',
     892_334_080, 218.6, 25.0, '1920x1080', 'metrics_collected', 5465, 'done', '["m1","m4"]', 0),
    ('field_mission_OPS_2025_003.mp4', SRC_IDS[1],
     'https://vartastorage.blob.core.windows.net/raw-missions/ops_2025_003.mp4',
     1_258_291_200, 312.0, 25.0, '1280x720', 'model_run', 7800, 'in_progress', '["m1"]', 0),
    ('shahed_night_thermal_012.mp4', SRC_IDS[0],
     'https://f003.backblazeb2.com/file/varta-cold-datasets/polygon-tests/thermal_012.mp4',
     311_427_072, 78.4, 25.0, '640x512', 'annotated', 1960, 'review', '[]', 0),
    ('synthetic_urban_scene_v3.mp4', SRC_IDS[3],
     'https://drive.google.com/file/d/1xSynthetic003/',
     209_715_200, 60.0, 30.0, '1920x1080', 'frames_extracted', 1800, 'pending', '[]', 0),
    ('air_air_engagement_014.mp4', SRC_IDS[1],
     'https://vartastorage.blob.core.windows.net/raw-missions/air_air_014.mp4',
     673_185_792, 178.0, 30.0, '1920x1080', 'raw', 0, 'pending', '[]', 0),
]:
    vid = uid(); VID_IDS.append(vid)
    sql("INSERT INTO raw_videos(id,filename,source_id,url,size_bytes,duration_sec,fps,resolution,"
        "pipeline_status,frames_extracted,annotation_status,models_run,palantir_exported,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
        (vid,)+v)

# CVAT Tasks
CVAT_IDS = []
for ct in [
    (VID_IDS[0], 'polygon_fpv_001_cvat', 'task-1842', 2.0, 285, 285, 'done', 'ml@varta.ai', '["fpv","bird","noise"]'),
    (VID_IDS[1], 'shahed_007_cvat',      'task-1901', 2.0, 437, 437, 'done', 'ml@varta.ai', '["shahed","helicopter"]'),
    (VID_IDS[2], 'ops_003_mission_cvat', 'task-2011', 2.0, 624, 412, 'in_progress', 'ml@varta.ai', '["shahed","fpv","air_air"]'),
    (VID_IDS[3], 'thermal_012_cvat',     'task-2078', 2.0, 157, 101, 'review', 'analyst@varta.ai', '["shahed"]'),
]:
    cid = uid(); CVAT_IDS.append(cid)
    sql("INSERT INTO cvat_tasks(id,video_id,task_name,cvat_task_id,fps_sample,total_frames,annotated_frames,status,assignee,labels,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'))", (cid,)+ct)

# Sample CVAT annotations (from the TZ XML format)
for ann in [
    (CVAT_IDS[0], VID_IDS[0], 60, 'fpv', '1', 443.02, 193.26, 521.86, 244.90),
    (CVAT_IDS[0], VID_IDS[0], 65, 'fpv', '1', 508.97, 194.70, 595.00, 252.10),
    (CVAT_IDS[0], VID_IDS[0], 70, 'fpv', '1', 530.48, 189.00, 625.10, 250.67),
    (CVAT_IDS[0], VID_IDS[0], 75, 'fpv', '1', 536.21, 183.30, 643.70, 244.93),
    (CVAT_IDS[0], VID_IDS[0], 80, 'fpv', '1', 549.12, 173.26, 656.60, 240.60),
    (CVAT_IDS[1], VID_IDS[1], 30, 'shahed', '2', 220.5, 140.2, 310.8, 198.4),
    (CVAT_IDS[1], VID_IDS[1], 35, 'shahed', '2', 235.1, 138.9, 328.3, 196.0),
]:
    sql("INSERT INTO cvat_annotations(id,task_id,video_id,frame_number,label,track_id,xtl,ytl,xbr,ybr) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (uid(),)+ann)

# Datasets
DS_IDS = []
for d in [
    ('Shahed-136 Training Set',    'azure_blob','raw-data/shahed-136/2024',
     '["shahed"]', 'Детекція Шахедів з різних ракурсів', 4821, 94e9, 0.94),
    ('FPV Combat Synthetic',       'azure_blob','raw-data/fpv-synthetic/v1',
     '["fpv"]',    'Синтетичні FPV дані',                12300,21e9, 0.88),
    ('Ground-Air Benchmark v2',    'azure_blob','datasets/ground-air-bm-v2',
     '["ground_air"]','Бенчмарк земля-повітря',           890,  3.2e9,0.97),
    ('Multi-Target Mixed',         's3',        's3-bucket/mixed-targets',
     '["shahed","air_air","fpv"]','Мультиціль',           7650,158e9, 0.71),
    ('Polygon Tests Q1-2025',      'backblaze_b2','polygon-tests/',
     '["fpv","shahed"]','Полігонні випробування',          1840, 18e9, 0.83),
]:
    did = uid(); DS_IDS.append(did)
    sql("INSERT INTO datasets(id,name,source_type,storage_path,object_types,description,"
        "total_files,total_size_bytes,annotation_coverage,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (did, d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7], ADMIN_ID))

# Models (extended architectures)
M_IDS = []
for m_row in [
    ('ShahedNet',   '3.1','yolov8m',      'Олег', 52.3,  '["shahed","fpv","bird"]',            'Основна модель детекції Шахедів'),
    ('FastEye',     '1.4','yolov8n',      'Петя', 6.2,   '["shahed","fpv"]',                   'Edge модель — Jetson Nano'),
    ('AirTracker',  '2.0','yolov9c',      'Вася', 103.7, '["air_air","ground_air"]',            'Повітря-повітря детекція'),
    ('MultiStrike', '4.0','yolov10n',     'Олег', 4.8,   '["shahed","fpv","air_air","ground_air"]','Multi-class YOLOv10'),
    ('RTDetector',  '1.0','rt_detr',      'Петя', 149.1, '["shahed","fpv","air_air"]',          'RT-DETR transformer'),
    ('ThermalEye',  '1.2','mobilenet_ssd','Вася', 3.1,   '["shahed"]',                          'Теплова камера — MobileNet-SSD TFLite'),
    ('NanoTrack',   '2.1','nanodet',      'Олег', 2.8,   '["fpv","shahed"]',                    'NanoDet-Tracking для ARM'),
    ('RTMDetX',     '1.0','rtmdet',       'Петя', 38.4,  '["shahed","fpv","air_air","ground_air"]','RTMDet — OpenMMLab швидкий'),
    ('PicoEye',     '1.0','picodet',      'Вася', 1.2,   '["fpv"]',                             'PicoDet — Raspberry Pi'),]:
    mid = uid(); M_IDS.append(mid)
    sql("INSERT INTO ai_models(id,name,version,architecture,author,size_mb,class_names,description,created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?)", (mid,)+m_row+(ADMIN_ID,))

# Experiments
EXP_IDS = []
for exp in [
    ('ShahedNet v3.1 — Full training',  'yolov8m', DS_IDS[0], M_IDS[0], 'completed', 0.941, 0.712, 0.934, 0.912, 18.4, 54.3, 'RTX 3080', 100, 16, 'Базова модель prod'),
    ('MultiStrike v4 — YOLOv10',        'yolov10n',DS_IDS[3], M_IDS[3], 'completed', 0.968, 0.753, 0.963, 0.951,  7.3,136.9, 'RTX 4090', 150, 32, 'Найкраща мультиклас'),
    ('RTDetector — RT-DETR baseline',   'rt_detr', DS_IDS[0], M_IDS[4], 'completed', 0.981, 0.789, 0.978, 0.967, 31.2, 32.1, 'RTX 4090', 200, 16, 'Найвища mAP'),
    ('NanoTrack — arm benchmark',       'nanodet', DS_IDS[0], M_IDS[6], 'completed', 0.871, 0.612, 0.883, 0.858,  4.8,208.3, 'Jetson Nano',80,64, 'ARM оптимізація'),
    ('ThermalEye — thermal cam',        'mobilenet_ssd',DS_IDS[0],M_IDS[5],'completed',0.824,0.581,0.849,0.801, 3.2,312.5, 'Jetson Nano',60,32, 'Теплова камера'),
    ('PicoEye — RaspPi test',           'picodet', DS_IDS[1], M_IDS[8], 'running',   0.791, 0.553, 0.812, 0.771, 1.9, 526.3,  'RPi 4',   120, 16, 'Поточне навчання'),
]:
    eid = uid(); EXP_IDS.append(eid)
    sql("INSERT INTO experiments(id,name,base_model_arch,dataset_id,model_id,status,"
        "map50,map50_95,precision,recall,inference_ms,fps,hardware,epochs,batch_size,notes,created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (eid,)+exp+(ADMIN_ID,))

# Benchmarks
for b in [
    (M_IDS[0],DS_IDS[0],0.934,0.912,0.941,0.712,18.4, 54.3,'RTX 3080'),
    (M_IDS[1],DS_IDS[0],0.878,0.861,0.882,0.634, 6.1,163.9,'Jetson Nano'),
    (M_IDS[3],DS_IDS[0],0.963,0.951,0.968,0.753, 7.3,136.9,'RTX 4090'),
    (M_IDS[4],DS_IDS[0],0.978,0.967,0.981,0.789,31.2, 32.1,'RTX 4090'),
    (M_IDS[5],DS_IDS[0],0.849,0.801,0.824,0.812, 3.2,312.5,'Jetson Nano'),
    (M_IDS[6],DS_IDS[0],0.883,0.858,0.871,0.862, 4.8,208.3,'Jetson Nano'),
    (M_IDS[7],DS_IDS[0],0.951,0.938,0.962,0.942, 9.1,109.8,'RTX 3080'),
]:
    sql("INSERT INTO model_benchmarks(id,model_id,dataset_id,precision,recall,map50,map50_95,inference_ms,fps,hardware,created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uid(),)+b+(ML_ID,))

# Edge deployments
for e in [
    ('drone-007','Дрон «Сокіл-7»',      M_IDS[1], '{"conf":0.45,"iou":0.5}'),
    ('drone-012','Дрон «Беркут-12»',    M_IDS[3], '{"conf":0.50,"iou":0.45}'),
    ('station-3','Пост спостереження №3',M_IDS[0],'{"conf":0.40,"iou":0.5}'),
    ('drone-019','Дрон «Орлан-19» (thermal)',M_IDS[5],'{"conf":0.35,"iou":0.5}'),
]:
    sql("INSERT INTO edge_deployments(id,device_id,device_name,model_id,config,deployed_by) VALUES(?,?,?,?,?,?)",
        (uid(),)+e+(ML_ID,))

# Missions
for m in [
    ('OPS-2025-001','drone-007', M_IDS[1],'polygon','Полігон Олексіївка',
     '[{"fr":10,"lbl":"fpv","c":0.91,"track":"T1"},{"fr":45,"lbl":"fpv","c":0.87,"track":"T1"}]',
     '[88,134]','{"lat":50.45,"lon":30.52,"alt":120,"spd":15.2}',285,2,0,1),
    ('OPS-2025-002','drone-012',M_IDS[3],'polygon','Полігон Новопетрівка',
     '[{"fr":23,"lbl":"shahed","c":0.96,"track":"T1"}]',
     '[]','{"lat":49.22,"lon":28.41,"alt":90,"spd":12.1}',540,1,0,0),
    ('OPS-2025-003','station-3',M_IDS[0],'real_field','Реальна місія — північ',
     '[{"fr":5,"lbl":"air_air","c":0.89},{"fr":67,"lbl":"air_air","c":0.92}]',
     '[102,211,289]','{"lat":51.10,"lon":31.90,"alt":0,"wind_ms":4.2}',780,2,3,2),
    ('OPS-2025-004','drone-007',M_IDS[1],'real_field','Реальна місія — схід',
     '[]','[12,33,67,91]','{"lat":50.60,"lon":30.70,"alt":80,"spd":18.5}',420,0,4,3),
    ('OPS-2025-005','drone-019',M_IDS[5],'polygon','Тест теплової камери',
     '[{"fr":12,"lbl":"shahed","c":0.81}]',
     '[45]','{"lat":50.45,"lon":30.52,"alt":60,"temp_c":22}',195,1,1,0),
]:
    sql("INSERT INTO mission_results(id,mission_id,device_id,model_id,mission_type,location,"
        "detections,hard_frames,telemetry,total_frames,detected_targets,false_positives,false_negatives,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))", (uid(),)+m)

# Palantir containers
for pc in [
    ('PALANTIR-2025-001','Polygon FPV Full Export',
     json.dumps([DS_IDS[0],DS_IDS[4]]), json.dumps([M_IDS[0],M_IDS[3]]),
     json.dumps([CVAT_IDS[0],CVAT_IDS[1]]),
     'palantir/exports/2025-001/', 2_147_483_648, 'done',
     'ri.dataset.main.dataset.abc123', 100, 'palantir/results/2025-001/'),
    ('PALANTIR-2025-002','Shahed Multi-Model',
     json.dumps([DS_IDS[0]]), json.dumps([M_IDS[0],M_IDS[4],M_IDS[7]]),
     json.dumps([CVAT_IDS[1]]),
     'palantir/exports/2025-002/', 1_073_741_824, 'uploaded',
     'ri.dataset.main.dataset.def456', 67, ''),
    ('PALANTIR-2025-003','Mission Real Field Q1',
     json.dumps([DS_IDS[3]]), json.dumps([M_IDS[3]]),
     json.dumps([]),
     '', 0, 'building', '', 35, ''),
]:
    sql("INSERT INTO palantir_containers(id,name,dataset_ids,model_ids,annotation_task_ids,"
        "container_path,size_bytes,status,palantir_dataset_rid,upload_progress,result_path,created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uid(), pc[1], pc[2], pc[3], pc[4], pc[5], pc[6], pc[7], pc[8], pc[9], pc[10], ADMIN_ID))

# Training jobs
for j in [
    ('azml-run-001','shahed-yolov8m-v3.1','shahed-detection','Completed','yolov8m', 100,16,'RTX 3080',0.941,18.4),
    ('azml-run-002','multi-yolov10n-v4',  'multi-target',    'Completed','yolov10n',150,32,'RTX 4090',0.968, 7.3),
    ('azml-run-003','rtdetr-baseline-v1', 'rt-detr-exp',     'Running',  'rt_detr', 200,16,'RTX 4090',None, None),
    ('azml-run-004','nanodet-arm-v2.1',   'edge-optimization','Completed','nanodet',  80,64,'Jetson Nano',0.871, 4.8),
    ('azml-run-005','picodet-rpi-v1',     'rpi-deployment',  'Running',  'picodet', 120,16,'RPi 4', None, None),
]:
    sql("INSERT INTO training_jobs(id,job_id,display_name,experiment,status,model_arch,epochs,batch_size,"
        "hardware,map50,inference_ms,submitted_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
        (uid(),)+j+(ML_ID,))

# Evaluation run
EVAL_RUN_ID = uid()
sql("INSERT INTO evaluation_runs(id,model_id,dataset_id,name,hardware,created_by) VALUES(?,?,?,?,?,?)",
    (EVAL_RUN_ID, M_IDS[0], DS_IDS[0], 'Field Evaluation — Reference', 'RTX 3080', ML_ID))
for m in [
    ('frame_background','BUILDING',4,   3,   0,  1,  1.000,0.750,0.857,0.000),
    ('frame_background','FIELD',   1990,2046,116,119,0.946,0.945,0.946,0.058),
    ('frame_background','FOREST',  406, 398, 7,  8,  0.983,0.980,0.982,0.017),
    ('frame_background','SKY',     456, 479, 48, 100,0.909,0.827,0.866,0.105),
    ('frame_background','UNKNOWN', 1,   0,   0,  1,  None, 0.000,None, 0.000),
    ('object_background','BUILDING',7,  5,   1,  2,  0.833,0.714,0.769,0.143),
    ('object_background','CLOUDS',  427,455,40, 95, 0.919,0.827,0.871,0.094),
    ('object_background','FIELD',   221,209,14, 12, 0.937,0.946,0.941,0.063),
    ('object_background','FOREST',  408,398, 8, 10, 0.980,0.975,0.978,0.020),
    ('object_background','SKY',     1794,1859,108,110,0.945,0.944,0.945,0.060),
]:
    sql("INSERT INTO class_metrics(id,run_id,background_type,class_name,n,tp,fp,fn,precision,recall,f1,fppi) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uid(),EVAL_RUN_ID)+m)

# ══════════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════
def login(email, pw):
    u = row("SELECT * FROM users WHERE email=? AND pw_hash=? AND is_active=1", (email, h(pw)))
    if not u: return None
    tok = hashlib.sha256(f"{u['id']}{time.time()}".encode()).hexdigest()
    sql("INSERT OR REPLACE INTO sessions VALUES(?,?,datetime('now'))", (tok, u['id']))
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
PIPELINE_STAGES = ['raw','frames_extracted','annotated','model_run','metrics_collected','palantir_exported']
PIPELINE_LABELS = ['Сирі дані','Кадри','Анотація','Моделі','Метрики','Palantir']

class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a):
        path = self.path.split('?')[0]
        if path not in [BASE+'/health', BASE+'/']:
            print(f"  {self.command:6s} {path} → {a[1]}")

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers(); self.wfile.write(body)

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
        # Strip base path
        path = p.path.rstrip("/")
        if path.startswith(BASE): path = path[len(BASE)]  if path==BASE else path[len(BASE):]
        path = path.rstrip("/") or "/"
        qs = parse_qs(p.query)
        g = lambda k,d=None: qs.get(k,[d])[0]
        user = get_user(self.token())

        if path in ("", "/"): return self.send_html(HTML)
        if path == "/health": return self.send_json({"status":"healthy","version":"2.0.0","base":BASE})

        # ── Storage Sources ────────────────────────────────────────────────
        if path == "/api/v1/storage/sources":
            return self.send_json(rows("SELECT * FROM storage_sources ORDER BY created_at"))

        # ── Pipeline / Videos ─────────────────────────────────────────────
        if path == "/api/v1/pipeline/videos":
            src_id = g("source_id")
            stage  = g("stage")
            q = "SELECT v.*,s.name as source_name FROM raw_videos v JOIN storage_sources s ON s.id=v.source_id WHERE 1=1"
            pr = []
            if src_id: q += " AND v.source_id=?"; pr.append(src_id)
            if stage:  q += " AND v.pipeline_status=?"; pr.append(stage)
            q += " ORDER BY v.created_at DESC"
            return self.send_json(rows(q, pr))

        if path == "/api/v1/pipeline/stats":
            stats = {}
            for stage in PIPELINE_STAGES:
                stats[stage] = one("SELECT COUNT(*) FROM raw_videos WHERE pipeline_status=?", (stage,))
            total = one("SELECT COUNT(*) FROM raw_videos")
            done  = one("SELECT COUNT(*) FROM raw_videos WHERE palantir_exported=1")
            stats["total"] = total
            stats["fully_processed"] = done
            stats["coverage_pct"] = round(done/total*100) if total else 0
            return self.send_json(stats)

        # ── CVAT Tasks ─────────────────────────────────────────────────────
        if path == "/api/v1/cvat/tasks":
            vid = g("video_id")
            q = "SELECT t.*,v.filename FROM cvat_tasks t JOIN raw_videos v ON v.id=t.video_id WHERE 1=1"
            pr = []
            if vid: q += " AND t.video_id=?"; pr.append(vid)
            q += " ORDER BY t.created_at DESC"
            return self.send_json(rows(q, pr))

        if path.startswith("/api/v1/cvat/tasks/") and path.endswith("/annotations"):
            tid = path.split("/")[5]
            limit = int(g("limit","50"))
            return self.send_json(rows("SELECT * FROM cvat_annotations WHERE task_id=? ORDER BY frame_number LIMIT ?",
                                       (tid, limit)))

        if path == "/api/v1/cvat/stats":
            total_tasks = one("SELECT COUNT(*) FROM cvat_tasks")
            done_tasks  = one("SELECT COUNT(*) FROM cvat_tasks WHERE status='done'")
            total_frames = one("SELECT SUM(total_frames) FROM cvat_tasks") or 0
            ann_frames  = one("SELECT SUM(annotated_frames) FROM cvat_tasks") or 0
            total_boxes = one("SELECT COUNT(*) FROM cvat_annotations")
            return self.send_json({
                "total_tasks": total_tasks, "completed_tasks": done_tasks,
                "total_frames": total_frames, "annotated_frames": ann_frames,
                "annotation_pct": round(ann_frames/total_frames*100,1) if total_frames else 0,
                "total_boxes": total_boxes,
            })

        # ── Experiments ────────────────────────────────────────────────────
        if path == "/api/v1/experiments":
            status = g("status")
            q = "SELECT e.*,m.name as model_name,m.version as model_ver,d.name as ds_name FROM experiments e " \
                "LEFT JOIN ai_models m ON m.id=e.model_id " \
                "LEFT JOIN datasets d ON d.id=e.dataset_id WHERE 1=1"
            pr = []
            if status: q += " AND e.status=?"; pr.append(status)
            q += " ORDER BY e.created_at DESC"
            return self.send_json(rows(q, pr))

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
            q = "SELECT b.*,m.name as mname,m.version as ver,m.author,m.architecture as arch " \
                "FROM model_benchmarks b JOIN ai_models m ON m.id=b.model_id WHERE 1=1"
            pr = []
            if did: q += " AND b.dataset_id=?"; pr.append(did)
            brs = rows(q, pr)
            brs = [b for b in brs if b.get(metric) is not None]
            brs.sort(key=lambda b: b[metric], reverse=(metric!='inference_ms'))
            for i,b in enumerate(brs):
                b["rank"] = i+1
                b["model_name"] = f"{b['mname']} v{b['ver']}"
            return self.send_json({"metric":metric,"dataset_id":did,"rankings":brs})

        # ── Palantir ──────────────────────────────────────────────────────
        if path == "/api/v1/palantir/containers":
            return self.send_json(rows("SELECT * FROM palantir_containers ORDER BY created_at DESC"))

        if path == "/api/v1/palantir/stats":
            pcs = rows("SELECT status, COUNT(*) as cnt FROM palantir_containers GROUP BY status")
            total_bytes = one("SELECT SUM(size_bytes) FROM palantir_containers WHERE status='done'") or 0
            return self.send_json({
                "by_status": {r["status"]: r["cnt"] for r in pcs},
                "total_exported_gb": round(total_bytes/1e9, 2),
            })

        # ── Analytics ─────────────────────────────────────────────────────
        if path == "/api/v1/analytics/overview":
            ms = rows("SELECT hard_frames,detected_targets,false_positives FROM mission_results")
            hard = sum(len(json.loads(m["hard_frames"])) for m in ms)
            total_dets = sum(m["detected_targets"] or 0 for m in ms)
            total_fp   = sum(m["false_positives"] or 0 for m in ms)
            return self.send_json({
                "total_videos":      one("SELECT COUNT(*) FROM raw_videos"),
                "total_datasets":    one("SELECT COUNT(*) FROM datasets"),
                "total_models":      one("SELECT COUNT(*) FROM ai_models WHERE is_active=1"),
                "total_annotations": one("SELECT COUNT(*) FROM cvat_annotations"),
                "total_missions":    one("SELECT COUNT(*) FROM mission_results"),
                "total_experiments": one("SELECT COUNT(*) FROM experiments"),
                "hard_frames_total": hard,
                "total_detections":  total_dets,
                "total_fp":          total_fp,
                "palantir_done":     one("SELECT COUNT(*) FROM palantir_containers WHERE status='done'"),
            })

        if path == "/api/v1/analytics/feedback-loop":
            ms = rows("SELECT * FROM mission_results ORDER BY created_at DESC")
            with_hard = [m for m in ms if json.loads(m["hard_frames"])]
            total_hard = sum(len(json.loads(m["hard_frames"])) for m in ms)
            return self.send_json({
                "missions_with_hard_frames": len(with_hard),
                "total_hard_frames": total_hard,
                "recent": [{"mission_id":m["mission_id"],"device_id":m["device_id"],
                             "mission_type":m["mission_type"],
                             "hard_frames":len(json.loads(m["hard_frames"])),"date":m["created_at"]}
                            for m in with_hard[:10]],
            })

        if path == "/api/v1/analytics/telemetry":
            ms = rows("SELECT mission_id,device_id,mission_type,telemetry,total_frames,"
                      "detected_targets,false_positives,false_negatives FROM mission_results ORDER BY created_at DESC")
            for m in ms:
                m["telemetry"] = json.loads(m["telemetry"] or "{}")
            return self.send_json(ms)

        # ── Edge ──────────────────────────────────────────────────────────
        if path == "/api/v1/edge/devices":
            deps = rows("SELECT e.*,m.name as mname,m.version as ver FROM edge_deployments e "
                        "JOIN ai_models m ON m.id=e.model_id WHERE e.status='active'")
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
            return self.send_json(rows("SELECT * FROM evaluation_runs ORDER BY created_at DESC"))

        if path.startswith("/api/v1/eval/runs/") and path.endswith("/summary"):
            rid = path.split("/")[5]
            run = row("SELECT * FROM evaluation_runs WHERE id=?", (rid,))
            if not run: return self.send_json({"detail":"Not found"},404)
            ms = rows("SELECT * FROM class_metrics WHERE run_id=?", (rid,))
            def summarize(mlist):
                if not mlist: return {}
                return {"weighted_f1": round(weighted_avg(mlist,"f1") or 0,4),
                        "weighted_precision": round(weighted_avg(mlist,"precision") or 0,4),
                        "weighted_recall": round(weighted_avg(mlist,"recall") or 0,4),
                        "avg_fppi": round(sum(m["fppi"] or 0 for m in mlist)/len(mlist),4),
                        "total_tp": sum(m["tp"] for m in mlist),
                        "total_fp": sum(m["fp"] for m in mlist)}
            return self.send_json({"run_id":rid,"name":run["name"],
                "frame_background":  summarize([m for m in ms if m["background_type"]=="frame_background"]),
                "object_background": summarize([m for m in ms if m["background_type"]=="object_background"])})

        if path.startswith("/api/v1/eval/runs/"):
            rid = path.split("/")[5]
            run = row("SELECT * FROM evaluation_runs WHERE id=?", (rid,))
            if not run: return self.send_json({"detail":"Not found"},404)
            ms = rows("SELECT * FROM class_metrics WHERE run_id=?", (rid,))
            return self.send_json({**dict(run),
                "frame_background":  [m for m in ms if m["background_type"]=="frame_background"],
                "object_background": [m for m in ms if m["background_type"]=="object_background"]})

        self.send_json({"detail":"Not found"},404)

    def do_POST(self):
        path_full = urlparse(self.path).path.rstrip("/")
        path = path_full[len(BASE):] if path_full.startswith(BASE) else path_full
        path = path.rstrip("/") or "/"
        body = self.rbody()
        user = get_user(self.token())
        uid_fn = uid

        if path == "/api/v1/auth/login":
            r = login(body.get("email",""), body.get("password",""))
            if not r: return self.send_json({"detail":"Invalid credentials"},401)
            tok, u = r
            return self.send_json({"access_token":tok,"token_type":"bearer","role":u["role"],"name":u["name"]})

        if path == "/api/v1/pipeline/videos":
            vid = uid_fn()
            sql("INSERT INTO raw_videos(id,filename,source_id,url,size_bytes,duration_sec,fps,resolution,"
                "pipeline_status,frames_extracted,annotation_status,models_run,tags,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (vid, body.get("filename","new.mp4"), body.get("source_id",SRC_IDS[0]),
                 body.get("url",""), body.get("size_bytes",0), body.get("duration_sec",0),
                 body.get("fps",25.0), body.get("resolution",""), "raw", 0, "pending","[]",
                 json.dumps(body.get("tags",{}))))
            return self.send_json(row("SELECT * FROM raw_videos WHERE id=?", (vid,)), 201)

        if path == "/api/v1/pipeline/advance":
            vid = body.get("video_id","")
            v = row("SELECT * FROM raw_videos WHERE id=?", (vid,))
            if not v: return self.send_json({"detail":"Not found"},404)
            cur_idx = PIPELINE_STAGES.index(v["pipeline_status"]) if v["pipeline_status"] in PIPELINE_STAGES else 0
            if cur_idx < len(PIPELINE_STAGES)-1:
                new_stage = PIPELINE_STAGES[cur_idx+1]
                sql("UPDATE raw_videos SET pipeline_status=? WHERE id=?", (new_stage, vid))
                return self.send_json({"video_id":vid,"new_stage":new_stage})
            return self.send_json({"video_id":vid,"new_stage":v["pipeline_status"],"note":"already complete"})

        if path == "/api/v1/cvat/tasks":
            cid = uid_fn()
            sql("INSERT INTO cvat_tasks(id,video_id,task_name,cvat_task_id,fps_sample,labels,status,assignee,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                (cid, body.get("video_id",""), body.get("task_name","New Task"),
                 body.get("cvat_task_id",""), body.get("fps_sample",2.0),
                 json.dumps(body.get("labels",[])), "created",
                 user["email"] if user else ""))
            return self.send_json({"id":cid},201)

        if path == "/api/v1/cvat/import-xml":
            # Parse CVAT XML annotation
            xml_str = body.get("xml","")
            task_id = body.get("task_id","")
            video_id = body.get("video_id","")
            count = 0
            try:
                root = ET.fromstring(xml_str)
                for track in root.iter("track"):
                    label = track.get("label","unknown")
                    track_id = track.get("id","0")
                    for bbox in track.iter("box"):
                        fn = int(bbox.get("frame",0))
                        sql("INSERT OR IGNORE INTO cvat_annotations(id,task_id,video_id,frame_number,label,track_id,xtl,ytl,xbr,ybr,occluded,keyframe,outside) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (uid_fn(), task_id, video_id, fn, label, track_id,
                             float(bbox.get("xtl",0)), float(bbox.get("ytl",0)),
                             float(bbox.get("xbr",0)), float(bbox.get("ybr",0)),
                             int(bbox.get("occluded",0)), int(bbox.get("keyframe",1)),
                             int(bbox.get("outside",0))))
                        count += 1
            except ET.ParseError as ex:
                return self.send_json({"detail":f"XML parse error: {ex}"},400)
            return self.send_json({"imported":count,"task_id":task_id},201)

        if path == "/api/v1/experiments":
            eid = uid_fn()
            sql("INSERT INTO experiments(id,name,base_model_arch,dataset_id,status,epochs,batch_size,hardware,notes,created_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (eid, body.get("name","New Experiment"), body.get("model_arch","yolov8n"),
                 body.get("dataset_id",DS_IDS[0]), "planned",
                 body.get("epochs",100), body.get("batch_size",16),
                 body.get("hardware",""), body.get("notes",""), user["id"] if user else ADMIN_ID))
            return self.send_json({"id":eid},201)

        if path == "/api/v1/datasets":
            did = uid_fn()
            sql("INSERT INTO datasets(id,name,source_type,storage_path,object_types,is_synthetic,description,created_by) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (did, body.get("name",""), body.get("source_type","azure_blob"),
                 body.get("storage_path",""), json.dumps(body.get("object_types",[])),
                 1 if body.get("is_synthetic") else 0, body.get("description",""),
                 user["id"] if user else ADMIN_ID))
            return self.send_json(row("SELECT * FROM datasets WHERE id=?", (did,)), 201)

        if path == "/api/v1/palantir/containers":
            pid = uid_fn()
            sql("INSERT INTO palantir_containers(id,name,dataset_ids,model_ids,annotation_task_ids,status,created_by) "
                "VALUES(?,?,?,?,?,?,?)",
                (pid, body.get("name","New Container"),
                 json.dumps(body.get("dataset_ids",[])),
                 json.dumps(body.get("model_ids",[])),
                 json.dumps(body.get("task_ids",[])),
                 "building", user["id"] if user else ADMIN_ID))
            def simulate_build(container_id):
                for prog in [10,25,50,75,100]:
                    time.sleep(1.5)
                    sql("UPDATE palantir_containers SET upload_progress=? WHERE id=?", (prog, container_id))
                sql("UPDATE palantir_containers SET status='ready' WHERE id=?", (container_id,))
            threading.Thread(target=simulate_build, args=(pid,), daemon=True).start()
            return self.send_json({"id":pid,"status":"building"},201)

        if path == "/api/v1/palantir/upload":
            pid = body.get("container_id","")
            pc = row("SELECT * FROM palantir_containers WHERE id=?", (pid,))
            if not pc: return self.send_json({"detail":"Not found"},404)
            sql("UPDATE palantir_containers SET status='uploading',upload_progress=0 WHERE id=?", (pid,))
            def simulate_upload(cid):
                for prog in [20,40,60,80,100]:
                    time.sleep(1.2)
                    sql("UPDATE palantir_containers SET upload_progress=? WHERE id=?", (prog, cid))
                sql("UPDATE palantir_containers SET status='uploaded' WHERE id=?", (cid,))
            threading.Thread(target=simulate_upload, args=(pid,), daemon=True).start()
            return self.send_json({"container_id":pid,"status":"uploading"})

        if path == "/api/v1/training/jobs":
            jid = uid_fn()
            n = one("SELECT COUNT(*) FROM training_jobs")+1
            job_ref = f"azml-run-{n:03d}"
            sql("INSERT INTO training_jobs(id,job_id,display_name,experiment,status,model_arch,epochs,batch_size,dataset_path,hardware,submitted_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (jid, job_ref, f"{body.get('model_arch','yolo')}-{body.get('experiment_name','exp')}",
                 body.get("experiment_name","varta-training"), "Queued",
                 body.get("model_arch","yolov8n"), body.get("epochs",100), body.get("batch_size",16),
                 body.get("dataset_path",""), body.get("hardware",""),
                 user["id"] if user else ML_ID))
            j = row("SELECT * FROM training_jobs WHERE id=?", (jid,))
            def start():
                time.sleep(2)
                sql("UPDATE training_jobs SET status='Running' WHERE id=?", (jid,))
            threading.Thread(target=start, daemon=True).start()
            return self.send_json(j, 201)

        if path == "/api/v1/mission-results":
            mid = uid_fn()
            sql("INSERT INTO mission_results(id,mission_id,device_id,model_id,mission_type,location,"
                "detections,hard_frames,telemetry,total_frames,detected_targets,false_positives,false_negatives,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (mid, body.get("mission_id",f"OPS-{int(time.time())}"),
                 body.get("device_id",""), body.get("model_id",""),
                 body.get("mission_type","polygon"), body.get("location",""),
                 json.dumps(body.get("detections",[])), json.dumps(body.get("hard_frames",[])),
                 json.dumps(body.get("telemetry",{})),
                 body.get("total_frames",0), body.get("detected_targets",0),
                 body.get("false_positives",0), body.get("false_negatives",0)))
            return self.send_json({"mission_result_id":mid},201)

        if path == "/api/v1/eval/runs":
            rid = uid_fn()
            sql("INSERT INTO evaluation_runs(id,model_id,dataset_id,name,hardware,created_by) VALUES(?,?,?,?,?,?)",
                (rid, body.get("model_id",""), body.get("dataset_id",""),
                 body.get("name","Unnamed"), body.get("hardware",""),
                 user["id"] if user else ML_ID))
            for m in body.get("metrics",[]):
                pre=m.get("precision") or m.get("pre")
                rec=m.get("recall") or m.get("rec")
                f1=m.get("f1")
                if pre is None and m.get("tp",0)+m.get("fp",0)>0: pre=m["tp"]/(m["tp"]+m["fp"])
                if rec is None and m.get("tp",0)+m.get("fn",0)>0: rec=m["tp"]/(m["tp"]+m["fn"])
                if f1 is None and pre and rec and pre+rec>0: f1=2*pre*rec/(pre+rec)
                sql("INSERT INTO class_metrics(id,run_id,background_type,class_name,n,tp,fp,fn,precision,recall,f1,fppi) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid_fn(),rid,m.get("background_type","frame_background"),
                     m.get("class_name","?").upper(),m.get("n",0),m.get("tp",0),m.get("fp",0),m.get("fn",0),
                     pre,rec,f1,m.get("fppi")))
            return self.send_json({"id":rid},201)

        if path == "/api/v1/edge/deploy-model":
            sql("UPDATE edge_deployments SET status='superseded' WHERE device_id=?", (body.get("device_id"),))
            eid = uid_fn()
            sql("INSERT INTO edge_deployments(id,device_id,device_name,model_id,config,deployed_by) VALUES(?,?,?,?,?,?)",
                (eid, body.get("device_id",""), body.get("device_name",""),
                 body.get("model_id",""), json.dumps(body.get("config",{})),
                 user["id"] if user else ML_ID))
            return self.send_json({"deployment_id":eid,"status":"active"},201)

        self.send_json({"detail":"Not found"},404)

    def do_DELETE(self):
        path = urlparse(self.path).path.rstrip("/")
        if path.startswith(BASE): path = path[len(BASE):]
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
# FRONTEND HTML — VARTA AI HUB v2
# ══════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VARTA AI HUB v2</title>
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
  --cya:#06b6d4;--c2:rgba(6,182,212,.1);
}
html,body,#root{height:100%;font-family:'JetBrains Mono',monospace;background:var(--bg);color:var(--txt);font-size:11px}
::-webkit-scrollbar{width:3px;height:3px}::-webkit-scrollbar-thumb{background:#253348;border-radius:2px}
#root{display:flex;flex-direction:column;height:100vh}

/* LOGIN */
#login{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:99;background-image:linear-gradient(rgba(251,191,36,.03)1px,transparent 1px),linear-gradient(90deg,rgba(251,191,36,.03)1px,transparent 1px);background-size:60px 60px}
.lbox{width:320px}.lico{width:54px;height:54px;background:var(--a2);border:1px solid var(--a3);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:22px;margin:0 auto 12px}
.lt{text-align:center;font-size:13px;font-weight:700;letter-spacing:.2em;color:var(--acc)}.ls{text-align:center;font-size:9px;color:var(--t3);letter-spacing:.15em;margin-bottom:20px}
.lcard{background:var(--s1);border:1px solid var(--brd);border-radius:8px;padding:20px}
.lbl{display:block;font-size:9px;color:var(--t3);letter-spacing:.1em;text-transform:uppercase;margin-bottom:5px}
.inp{width:100%;background:var(--bg);border:1px solid var(--brd);border-radius:4px;padding:8px 10px;font-size:11px;font-family:inherit;color:var(--txt);outline:none;margin-bottom:12px;transition:border .15s}
.inp:focus{border-color:var(--a3)}.sel{width:100%;background:var(--bg);border:1px solid var(--brd);border-radius:4px;padding:8px 10px;font-size:11px;font-family:inherit;color:var(--txt);outline:none;margin-bottom:12px}
.btn-pri{width:100%;padding:10px;background:var(--acc);color:#0f1724;font-family:inherit;font-size:10px;font-weight:700;letter-spacing:.15em;border:none;border-radius:4px;cursor:pointer;text-transform:uppercase;transition:background .15s}
.btn-pri:hover{background:#fcd34d}.lhint{font-size:9px;color:var(--t3);text-align:center;margin-top:10px;line-height:1.9}
.emsg{background:var(--r2);border:1px solid rgba(239,68,68,.2);border-radius:4px;padding:7px 10px;font-size:10px;color:var(--red);margin-bottom:10px}

/* LAYOUT */
#app{display:none;flex:1;overflow:hidden;flex-direction:row}
#sb{width:210px;flex-shrink:0;background:var(--s1);border-right:1px solid var(--brd);display:flex;flex-direction:column}
#main{flex:1;overflow-y:auto;padding:22px 26px}
.logo{padding:16px 16px 12px;border-bottom:1px solid var(--brd)}
.logo-t{font-size:12px;font-weight:700;letter-spacing:.2em;color:var(--acc)}.logo-s{font-size:8px;color:var(--t3);letter-spacing:.1em;margin-top:2px}
nav{flex:1;padding:10px 8px;overflow-y:auto}
.ng{font-size:8px;color:var(--t3);letter-spacing:.12em;text-transform:uppercase;padding:8px 9px 4px;margin-top:4px}
.ni{display:flex;align-items:center;gap:8px;padding:6px 9px;border-radius:4px;font-size:10px;letter-spacing:.1em;cursor:pointer;color:var(--t3);transition:all .12s;border:1px solid transparent;margin-bottom:1px;user-select:none}
.ni:hover{color:var(--txt);background:var(--s2)}.ni.on{background:var(--a2);color:var(--acc);border-color:var(--a3)}
.ni-ico{font-size:11px;width:14px;text-align:center;flex-shrink:0}
.usr{padding:10px;border-top:1px solid var(--brd)}.un{font-size:10px;color:var(--txt);padding:4px 8px}.ur{font-size:9px;color:var(--t3);letter-spacing:.1em;padding:0 8px 6px}
.btnlo{width:100%;padding:6px 8px;background:none;border:1px solid var(--brd);color:var(--t3);font-family:inherit;font-size:9px;border-radius:3px;cursor:pointer;text-align:left;transition:all .12s}
.btnlo:hover{border-color:var(--red);color:var(--red)}

/* COMMON */
.ph{border-bottom:1px solid var(--brd);padding-bottom:14px;margin-bottom:20px;display:flex;align-items:flex-start;justify-content:space-between}
.ptit{font-size:13px;font-weight:700;letter-spacing:.12em;color:var(--acc);text-transform:uppercase;display:flex;align-items:center;gap:8px}
.psub{font-size:9px;color:var(--t3);letter-spacing:.08em;margin-top:3px}
.sg{display:grid;gap:10px;margin-bottom:18px}
.sg4{grid-template-columns:repeat(4,1fr)}.sg2{grid-template-columns:1fr 1fr}.sg3{grid-template-columns:repeat(3,1fr)}.sg5{grid-template-columns:repeat(5,1fr)}
.sc{background:var(--s1);border:1px solid var(--brd);border-radius:5px;padding:12px 14px}
.sl{font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}
.sv{font-size:20px;font-weight:700}.sv.ac{color:var(--acc)}.sv.gn{color:var(--grn)}.sv.rd{color:var(--red)}.sv.or{color:var(--ora)}.sv.cy{color:var(--cya)}
.card{background:var(--s1);border:1px solid var(--brd);border-radius:6px;margin-bottom:13px}
.ch{padding:10px 14px;border-bottom:1px solid var(--brd);display:flex;align-items:center;justify-content:space-between}
.ct{font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--t2)}
table{width:100%;border-collapse:collapse}
th{padding:7px 10px;text-align:left;color:var(--t3);font-size:9px;letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--brd);font-weight:600;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid rgba(30,45,64,.5);font-size:10px;vertical-align:middle}
tr:last-child td{border-bottom:none}tr:hover td{background:rgba(22,32,51,.5)}
.bdg{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;font-weight:600;letter-spacing:.05em;border:1px solid;white-space:nowrap}
.ba{color:var(--acc);background:var(--a2);border-color:var(--a3)}.bb{color:#60a5fa;background:var(--b2);border-color:var(--b3)}
.bg{color:#4ade80;background:var(--g2);border-color:var(--g3)}.br{color:#f87171;background:var(--r2);border-color:rgba(239,68,68,.2)}
.bgy{color:var(--t2);background:var(--s2);border-color:var(--brd)}.bp{color:#c084fc;background:var(--p2);border-color:rgba(168,85,247,.2)}
.bor{color:#fb923c;background:var(--o2);border-color:rgba(249,115,22,.2)}.bcy{color:#22d3ee;background:var(--c2);border-color:rgba(6,182,212,.2)}
.btn{padding:5px 12px;border-radius:3px;font-family:inherit;font-size:9px;font-weight:700;letter-spacing:.1em;cursor:pointer;transition:all .12s;text-transform:uppercase;border:1px solid}
.btn-ac{background:var(--acc);color:#0f1724;border-color:var(--acc)}.btn-ac:hover{background:#fcd34d}
.btn-oc{background:none;color:var(--t3);border-color:var(--brd)}.btn-oc:hover{color:var(--txt);border-color:var(--brd2)}
.btn-sm{padding:3px 8px;font-size:9px}.btn-dr{background:none;color:#f87171;border-color:rgba(239,68,68,.25)}.btn-dr:hover{background:var(--r2)}
.btn-cy{background:var(--c2);color:var(--cya);border-color:rgba(6,182,212,.3)}.btn-cy:hover{background:rgba(6,182,212,.2)}
.fr{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.fr3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px}
.ff{margin-bottom:10px}
.alert-a{background:rgba(251,191,36,.06);border:1px solid var(--a3);border-radius:5px;padding:10px 14px;font-size:10px;color:var(--acc);display:flex;align-items:flex-start;gap:8px;margin-bottom:14px}
#toast{position:fixed;bottom:18px;right:18px;background:var(--s2);border:1px solid var(--brd);border-radius:5px;padding:9px 14px;font-size:10px;color:var(--txt);opacity:0;transition:opacity .25s;z-index:200;pointer-events:none}
#toast.show{opacity:1}#toast.ok{border-color:rgba(34,197,94,.3);color:var(--grn)}
.tabs{display:flex;gap:5px;margin-bottom:16px;flex-wrap:wrap}
.tab{padding:5px 12px;border-radius:3px;font-size:9px;font-weight:700;letter-spacing:.1em;cursor:pointer;border:1px solid var(--brd);color:var(--t3);transition:all .15s;text-transform:uppercase}
.tab.on{background:var(--a2);color:var(--acc);border-color:var(--a3)}.tab:hover:not(.on){border-color:var(--brd2);color:var(--txt)}
/* Pipeline stages */
.pipe-stage{display:flex;flex-direction:column;align-items:center;gap:4px;flex:1}
.pipe-dot{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;border:2px solid;transition:all .2s}
.pipe-dot.done{background:var(--g2);border-color:var(--grn);color:var(--grn)}
.pipe-dot.active{background:var(--a2);border-color:var(--acc);color:var(--acc)}
.pipe-dot.pending{background:var(--s2);border-color:var(--brd);color:var(--t3)}
.pipe-line{flex:1;height:2px;margin-top:13px;transition:background .3s}
.pipe-line.done{background:var(--grn)}.pipe-line.pending{background:var(--brd)}
.pipe-label{font-size:8px;color:var(--t3);letter-spacing:.08em;text-align:center;max-width:70px}
/* Progress bar */
.prog-wrap{height:6px;background:var(--s2);border-radius:3px;overflow:hidden;width:100%}
.prog-fill{height:100%;border-radius:3px;transition:width .5s}
.bbar{height:4px;background:var(--s2);border-radius:2px;overflow:hidden;width:60px;margin-top:3px;display:inline-block}
.bfill{height:100%;border-radius:2px}
</style>
</head>
<body>
<div id="root">

<div id="login">
  <div class="lbox">
    <div class="lico">🛡</div>
    <div class="lt">VARTA AI HUB</div>
    <div class="ls">v2.0 · AI MODEL ECOSYSTEM</div>
    <div class="lcard">
      <div id="lerr" class="emsg" style="display:none"></div>
      <label class="lbl">Email</label>
      <input class="inp" id="lem" value="admin@varta.ai">
      <label class="lbl">Password</label>
      <input class="inp" id="lpw" type="password" value="ChangeMe123!">
      <button class="btn-pri" id="lbtn" onclick="doLogin()">ACCESS SYSTEM</button>
    </div>
    <div class="lhint">admin@varta.ai / ChangeMe123! (admin)<br>ml@varta.ai / MLEngineer123! (ml_engineer)<br>analyst@varta.ai / Analyst123! (analyst)</div>
  </div>
</div>

<div id="app">
  <div id="sb">
    <div class="logo"><div class="logo-t">🛡 VARTA HUB</div><div class="logo-s">AI MODEL ECOSYSTEM v2.0</div></div>
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
const BASE_PATH = '/aihub';
const API = '';
let TOK=null,ROLE=null,UNAME=null;
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
  const r=await fetch(BASE_PATH+path,o);
  return r.json().catch(()=>({}));
}

async function doLogin(){
  const btn=document.getElementById('lbtn');
  const err=document.getElementById('lerr');
  btn.disabled=true;btn.textContent='AUTHENTICATING...';err.style.display='none';
  const d=await api('POST','/api/v1/auth/login',{email:document.getElementById('lem').value,password:document.getElementById('lpw').value});
  if(!d.access_token){err.style.display='block';err.textContent='Невірний email або пароль';btn.disabled=false;btn.textContent='ACCESS SYSTEM';return;}
  TOK=d.access_token;ROLE=d.role;UNAME=d.name;
  document.getElementById('login').style.display='none';
  document.getElementById('app').style.display='flex';
  document.getElementById('uname').textContent=d.name;
  document.getElementById('urole').textContent=d.role.replace('_',' ').toUpperCase();
  buildNav();go('dashboard');
}
document.getElementById('lpw').addEventListener('keydown',ev=>{if(ev.key==='Enter')doLogin()});
function doLogout(){TOK=null;document.getElementById('login').style.display='flex';document.getElementById('app').style.display='none';}

// ── NAV ───────────────────────────────────────────────────────────────────
const NAVS=[
  {grp:'ОПЕРАЦІЇ'},
  {id:'dashboard',ico:'◈',lbl:'DASHBOARD',   roles:['admin','ml_engineer','analyst']},
  {id:'pipeline', ico:'⇢',lbl:'PIPELINE',    roles:['admin','ml_engineer','analyst']},
  {grp:'ДАНІ'},
  {id:'datalake', ico:'◫',lbl:'DATA LAKE',   roles:['admin','ml_engineer','analyst']},
  {id:'cvat',     ico:'▣',lbl:'CVAT / ANNOTATIONS', roles:['admin','ml_engineer','analyst']},
  {grp:'МОДЕЛІ'},
  {id:'models',   ico:'⬡',lbl:'MODEL ZOO',   roles:['admin','ml_engineer']},
  {id:'experiments',ico:'⚗',lbl:'EXPERIMENTS',roles:['admin','ml_engineer']},
  {id:'training', ico:'⚙',lbl:'TRAINING',    roles:['admin','ml_engineer']},
  {id:'leaderboard',ico:'◈',lbl:'LEADERBOARD',roles:['admin','ml_engineer','analyst']},
  {id:'evaluation',ico:'◉',lbl:'EVALUATION', roles:['admin','ml_engineer','analyst']},
  {grp:'DEPLOY'},
  {id:'palantir', ico:'⬢',lbl:'PALANTIR',    roles:['admin','ml_engineer','analyst']},
  {id:'missions', ico:'✦',lbl:'MISSIONS',    roles:['admin','ml_engineer','analyst']},
  {id:'edge',     ico:'◌',lbl:'EDGE DEVICES',roles:['admin','ml_engineer']},
];
function buildNav(){
  document.getElementById('snav').innerHTML=NAVS.filter(n=>!n.roles||n.roles.includes(ROLE)).map(n=>
    n.grp
      ? `<div class="ng">${n.grp}</div>`
      : `<div class="ni" id="ni-${n.id}" onclick="go('${n.id}')"><span class="ni-ico">${n.ico}</span>${n.lbl}</div>`
  ).join('');
}
function go(pg){
  document.querySelectorAll('.ni').forEach(el=>el.classList.remove('on'));
  const el=document.getElementById('ni-'+pg);if(el)el.classList.add('on');
  const pages={dashboard:pgDash,pipeline:pgPipeline,datalake:pgDL,cvat:pgCVAT,
    models:pgMdl,experiments:pgExp,training:pgTrain,leaderboard:pgLB,evaluation:pgEval,
    palantir:pgPalantir,missions:pgMissions,edge:pgEdge};
  (pages[pg]||pgDash)();
}

// ── HELPERS ───────────────────────────────────────────────────────────────
const PIPE_STAGES=['raw','frames_extracted','annotated','model_run','metrics_collected','palantir_exported'];
const PIPE_LBLS=['Сирі дані','Кадри','Анотація','Моделі','Метрики','Palantir'];
const PIPE_ICO=['📹','🎞','🏷','🤖','📊','🚀'];
const STATUS_BDGS={
  'raw':'bgy','frames_extracted':'bb','annotated':'ba','model_run':'bp',
  'metrics_collected':'bg','palantir_exported':'bcy',
  'done':'bg','running':'ba','in_progress':'ba','completed':'bg',
  'building':'ba','ready':'bg','uploading':'bcy','uploaded':'bg',
  'queued':'bb','Queued':'bb','Running':'ba','Completed':'bg','Canceled':'bgy','Failed':'br',
  'planned':'bgy','archived':'bgy','pending':'bgy','review':'ba','created':'bgy',
  'polygon':'bb','real_field':'bor','simulation':'bp',
};
function sbdg(s){return`<span class="bdg ${STATUS_BDGS[s]||'bgy'}">${s}</span>`}
function pipeBar(stage){
  const idx=PIPE_STAGES.indexOf(stage);
  return PIPE_STAGES.map((s,i)=>`<div style="display:flex;align-items:center;${i<PIPE_STAGES.length-1?'flex:1':''}">
    <div style="text-align:center">
      <div class="pipe-dot ${i<idx?'done':i===idx?'active':'pending'}">${i<idx?'✓':PIPE_ICO[i]}</div>
    </div>
    ${i<PIPE_STAGES.length-1?`<div class="pipe-line ${i<idx?'done':'pending'}" style="flex:1;min-width:8px"></div>`:''}
  </div>`).join('');
}
function fmtBar(v,inv=false){
  if(v==null)return'<span style="color:var(--t3)">—</span>';
  const pct=inv?Math.min(v/.15*100,100):v*100;
  const c=inv?(v<=.02?'#22c55e':v<=.07?'#fbbf24':'#ef4444'):(v>=.95?'#22c55e':v>=.88?'#fbbf24':'#ef4444');
  return`<div style="${inv?fcol(v):mcol(v)};font-weight:600">${fmt(v)}</div><div class="bbar"><div class="bfill" style="width:${pct}%;background:${c}"></div></div>`;
}

// ══════════════════════════════════════════════════════════════════════════
// DASHBOARD
// ══════════════════════════════════════════════════════════════════════════
async function pgDash(){
  const[ov,ps,fb,cs]=await Promise.all([
    api('GET','/api/v1/analytics/overview'),
    api('GET','/api/v1/pipeline/stats'),
    api('GET','/api/v1/analytics/feedback-loop'),
    api('GET','/api/v1/cvat/stats'),
  ]);
  const hard=ov.hard_frames_total||0;
  setMain(`
    <div class="ph"><div><div class="ptit">◈ Dashboard</div><div class="psub">VARTA AI HUB v2.0 · System overview · Real-time</div></div></div>
    <div class="sg sg4">
      <div class="sc"><div class="sl">Відео файлів</div><div class="sv ac">${ps.total||0}</div><div style="font-size:9px;color:var(--grn);margin-top:3px">▲ ${ps.fully_processed||0} оброблено</div></div>
      <div class="sc"><div class="sl">CVAT Annotations</div><div class="sv">${(cs.total_boxes||0).toLocaleString()}</div><div style="font-size:9px;color:var(--t3);margin-top:3px">${cs.annotation_pct||0}% кадрів</div></div>
      <div class="sc"><div class="sl">AI Моделей</div><div class="sv gn">${ov.total_models||0}</div><div style="font-size:9px;color:var(--t3);margin-top:3px">${ov.total_experiments||0} експериментів</div></div>
      <div class="sc"><div class="sl">Місій (полігон+реальних)</div><div class="sv or">${ov.total_missions||0}</div><div style="font-size:9px;color:${hard>0?'var(--red)':'var(--t3)'};margin-top:3px">⚠ ${hard} hard frames</div></div>
    </div>

    <!-- Pipeline overview -->
    <div class="card">
      <div class="ch"><span class="ct">⇢ Pipeline Coverage</span>
        <span style="font-size:10px;color:var(--t3)">${ps.coverage_pct||0}% повністю оброблено</span>
      </div>
      <div style="padding:14px">
        <div style="display:flex;align-items:flex-start;gap:0;margin-bottom:12px">
          ${pipeBar('palantir_exported')}
        </div>
        <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px">
          ${PIPE_STAGES.map((s,i)=>`
          <div class="sc" style="padding:8px;text-align:center;cursor:pointer" onclick="go('pipeline')">
            <div style="font-size:9px;color:var(--t3);margin-bottom:4px">${PIPE_LBLS[i]}</div>
            <div style="font-size:18px;font-weight:700;color:${i===0?'var(--t2)':i===5?'var(--cya)':'var(--acc)'}">${ps[s]||0}</div>
          </div>`).join('')}
        </div>
      </div>
    </div>

    <!-- Stats row -->
    <div class="sg sg3">
      <div class="sc">
        <div class="sl">CVAT Tasks Coverage</div>
        <div style="font-size:18px;font-weight:700;color:var(--grn)">${cs.annotation_pct||0}%</div>
        <div class="prog-wrap" style="margin-top:6px"><div class="prog-fill" style="width:${cs.annotation_pct||0}%;background:var(--grn)"></div></div>
        <div style="font-size:9px;color:var(--t3);margin-top:4px">${(cs.annotated_frames||0).toLocaleString()} / ${(cs.total_frames||0).toLocaleString()} кадрів</div>
      </div>
      <div class="sc">
        <div class="sl">Pipeline Coverage</div>
        <div style="font-size:18px;font-weight:700;color:var(--cya)">${ps.coverage_pct||0}%</div>
        <div class="prog-wrap" style="margin-top:6px"><div class="prog-fill" style="width:${ps.coverage_pct||0}%;background:var(--cya)"></div></div>
        <div style="font-size:9px;color:var(--t3);margin-top:4px">${ps.fully_processed||0} / ${ps.total||0} відео в Palantir</div>
      </div>
      <div class="sc">
        <div class="sl">Palantir Exported</div>
        <div style="font-size:18px;font-weight:700;color:var(--pur)">${ov.palantir_done||0}</div>
        <div class="prog-wrap" style="margin-top:6px"><div class="prog-fill" style="width:${Math.min((ov.palantir_done||0)*33,100)}%;background:var(--pur)"></div></div>
        <div style="font-size:9px;color:var(--t3);margin-top:4px">контейнерів завершено</div>
      </div>
    </div>

    ${hard>0?`<div class="alert-a"><span style="font-size:14px">⚠</span><div><strong>FEEDBACK LOOP ACTIVE</strong><br>${hard} hard frames з ${fb.missions_with_hard_frames} місій у черзі на re-annotation.</div></div>`:''}

    <div class="sg sg3">
      ${[['⇢ Pipeline','Контроль обробки відео','pipeline'],
         ['▣ CVAT','Стан анотування','cvat'],
         ['⬢ Palantir','Контейнери та результати','palantir']].map(([t,d,p])=>`
      <div class="sc" style="cursor:pointer" onclick="go('${p}')">
        <div class="rt" style="font-size:11px;font-weight:700">${t}</div>
        <div style="font-size:9px;color:var(--t3);margin-top:3px">${d}</div>
      </div>`).join('')}
    </div>`);
}

// ══════════════════════════════════════════════════════════════════════════
// PIPELINE TRACKER
// ══════════════════════════════════════════════════════════════════════════
async function pgPipeline(){
  const[vids,stats,srcs]=await Promise.all([
    api('GET','/api/v1/pipeline/videos'),
    api('GET','/api/v1/pipeline/stats'),
    api('GET','/api/v1/storage/sources'),
  ]);
  const STAGE_COLOR={'raw':'var(--t3)','frames_extracted':'#60a5fa','annotated':'var(--acc)','model_run':'#c084fc','metrics_collected':'var(--grn)','palantir_exported':'#22d3ee'};
  setMain(`
    <div class="ph">
      <div><div class="ptit">⇢ Pipeline Tracker</div><div class="psub">raw → frames → annotated → model run → metrics → palantir</div></div>
      <button class="btn btn-ac" onclick="showAddVideo()">+ ADD VIDEO</button>
    </div>

    <!-- Stage summary -->
    <div class="sg" style="grid-template-columns:repeat(6,1fr);margin-bottom:18px">
      ${PIPE_STAGES.map((s,i)=>`
      <div class="sc" style="text-align:center;padding:10px 8px;cursor:pointer;border-bottom:2px solid ${STAGE_COLOR[s]}" onclick="filterPipeline('${s}')">
        <div style="font-size:18px;margin-bottom:2px">${PIPE_ICO[i]}</div>
        <div style="font-size:18px;font-weight:700;color:${STAGE_COLOR[s]}">${stats[s]||0}</div>
        <div style="font-size:8px;color:var(--t3);margin-top:2px">${PIPE_LBLS[i]}</div>
      </div>`).join('')}
    </div>

    <div id="add-video-form" style="display:none" class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">+ Add Video / URL</span><button class="btn btn-oc btn-sm" onclick="hideAddVideo()">✕</button></div>
      <div style="padding:13px">
        <div class="fr"><div><label class="lbl">URL (Backblaze B2 / S3 / Azure / GDrive / YouTube)</label>
          <input class="inp" id="vurl" placeholder="https://f003.backblazeb2.com/file/varta-cold-datasets/polygon-tests/..."></div>
        <div><label class="lbl">Source</label><select class="sel" id="vsrc">
          ${srcs.map(s=>`<option value="${s.id}">${s.name}</option>`).join('')}
        </select></div></div>
        <div class="fr"><div><label class="lbl">Filename</label><input class="inp" id="vfn" placeholder="mission_001.mp4"></div>
        <div><label class="lbl">Resolution</label><input class="inp" id="vres" value="1920x1080"></div></div>
        <div class="fr3">
          <div><label class="lbl">Duration (sec)</label><input class="inp" id="vdur" type="number" value="120"></div>
          <div><label class="lbl">FPS</label><input class="inp" id="vfps" type="number" value="25"></div>
          <div><label class="lbl">Size (MB)</label><input class="inp" id="vsz" type="number" value="512"></div>
        </div>
        <button class="btn btn-ac" onclick="addVideo()">ADD TO PIPELINE</button>
      </div>
    </div>

    <div class="card">
      <div class="ch"><span class="ct">Video Files (${vids.length})</span>
        <span style="font-size:9px;color:var(--t3)">${stats.fully_processed||0} / ${stats.total||0} fully processed</span>
      </div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>Filename</th><th>Source</th><th>Size</th><th>Duration</th><th>Stage</th><th>Pipeline</th><th>Actions</th></tr></thead>
        <tbody>${vids.map(v=>`<tr>
          <td><div style="font-size:10px;font-weight:600;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${e(v.url)}">${e(v.filename)}</div></td>
          <td style="color:var(--t3);font-size:9px">${e(v.source_name||'')}</td>
          <td style="color:var(--t3)">${fmtB(v.size_bytes)}</td>
          <td style="color:var(--t3)">${v.duration_sec?v.duration_sec.toFixed(0)+'s':'—'}</td>
          <td>${sbdg(v.pipeline_status)}</td>
          <td style="min-width:180px">
            <div style="display:flex;align-items:center;gap:2px">
              ${PIPE_STAGES.map((s,i)=>`<div title="${PIPE_LBLS[i]}" style="width:20px;height:20px;border-radius:50%;background:${PIPE_STAGES.indexOf(v.pipeline_status)>=i?STAGE_COLOR[s]:'var(--s2)'};border:1px solid ${PIPE_STAGES.indexOf(v.pipeline_status)>=i?STAGE_COLOR[s]:'var(--brd)'};font-size:9px;display:flex;align-items:center;justify-content:center">${PIPE_STAGES.indexOf(v.pipeline_status)>i?'✓':PIPE_STAGES.indexOf(v.pipeline_status)===i?PIPE_ICO[i]:''}</div>`).join('')}
            </div>
          </td>
          <td>
            ${v.pipeline_status!=='palantir_exported'?`<button class="btn btn-ac btn-sm" onclick="advanceVideo('${v.id}')">▷ NEXT</button>`:'<span class="bdg bcy">✓ DONE</span>'}
            ${v.url?`<a href="${e(v.url)}" target="_blank" style="margin-left:6px" class="btn btn-oc btn-sm">↗</a>`:''}
          </td>
        </tr>`).join('')}
        </tbody>
      </table></div>
    </div>`);
}
function showAddVideo(){document.getElementById('add-video-form').style.display='block';}
function hideAddVideo(){document.getElementById('add-video-form').style.display='none';}
async function addVideo(){
  const url=document.getElementById('vurl').value;
  if(!url){toast('Enter URL',false);return;}
  await api('POST','/api/v1/pipeline/videos',{
    url, filename:document.getElementById('vfn').value||url.split('/').pop(),
    source_id:document.getElementById('vsrc').value,
    size_bytes:(+document.getElementById('vsz').value)*1e6,
    duration_sec:+document.getElementById('vdur').value,
    fps:+document.getElementById('vfps').value,
    resolution:document.getElementById('vres').value,
  });
  toast('Video added ✓'); pgPipeline();
}
async function advanceVideo(vid){
  const r=await api('POST','/api/v1/pipeline/advance',{video_id:vid});
  toast(`Stage: ${r.new_stage} ✓`); pgPipeline();
}

// ══════════════════════════════════════════════════════════════════════════
// DATA LAKE
// ══════════════════════════════════════════════════════════════════════════
async function pgDL(){
  const[ds,srcs]=await Promise.all([api('GET','/api/v1/datasets'),api('GET','/api/v1/storage/sources')]);
  setMain(`
    <div class="ph"><div><div class="ptit">◫ Data Lake</div><div class="psub">Azure Blob · S3 · Backblaze B2 · GDrive · YouTube</div></div>
      <button class="btn btn-ac" onclick="toggleAddDS()">+ NEW DATASET</button></div>
    <div class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">Storage Sources</span></div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0">
        ${srcs.map(s=>`<div style="padding:12px 14px;border-right:1px solid var(--brd)">
          <div style="font-size:9px;color:var(--t3);margin-bottom:3px">${e(s.source_type).toUpperCase()}</div>
          <div style="font-size:10px;font-weight:600">${e(s.name)}</div>
          <div style="font-size:9px;color:var(--t3);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px" title="${e(s.url)}">${e(s.bucket||'')}${e(s.prefix||'')}</div>
          ${s.url?`<a href="${e(s.url)}" target="_blank" style="font-size:9px;color:var(--acc);text-decoration:none">↗ Open</a>`:''}
        </div>`).join('')}
      </div>
    </div>
    <div id="ds-form" style="display:none" class="card" style="margin-bottom:13px">
      <div class="ch"><span class="ct">+ New Dataset</span><button class="btn btn-oc btn-sm" onclick="document.getElementById('ds-form').style.display='none'">✕</button></div>
      <div style="padding:13px">
        <div class="fr"><div><label class="lbl">Name</label><input class="inp" id="dsn" placeholder="Shahed v4"></div>
        <div><label class="lbl">Scenario</label><input class="inp" id="dsc" placeholder="Полігонні тести, синтетика..."></div></div>
        <div class="fr"><div><label class="lbl">Source</label><select class="sel" id="dss">${srcs.map(s=>`<option value="${s.source_type}">${s.name}</option>`).join('')}</select></div>
        <div><label class="lbl">Storage Path</label><input class="inp" id="dsp" placeholder="bucket/prefix"></div></div>
        <div class="fr"><div><label class="lbl">Object Types</label><input class="inp" id="dst" placeholder="shahed, fpv, air_air"></div>
        <div><label class="lbl">Synthetic?</label><select class="sel" id="dsy"><option value="0">No — Real</option><option value="1">Yes — Synthetic</option></select></div></div>
        <button class="btn btn-ac" onclick="addDS()">CREATE DATASET</button>
      </div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">Datasets (${ds.length})</span></div>
      <table><thead><tr><th>Name</th><th>Source</th><th>Path</th><th>Types</th><th>Files</th><th>Size</th><th>Annotation</th></tr></thead>
      <tbody>${ds.map(d=>`<tr>
        <td><strong>${e(d.name)}</strong><div style="font-size:9px;color:var(--t3)">${e(d.description||d.scenario||'')}</div></td>
        <td><span class="bdg bgy">${d.source_type}</span></td>
        <td style="color:var(--t3);font-size:9px;max-width:140px;overflow:hidden;text-overflow:ellipsis">${e(d.storage_path)}</td>
        <td>${(JSON.parse(d.object_types||'[]')).map(t=>`<span class="bdg bb" style="margin-right:2px">${t}</span>`).join('')}${d.is_synthetic?'<span class="bdg bp">synth</span>':''}</td>
        <td style="color:var(--t3)">${Number(d.total_files).toLocaleString()}</td>
        <td style="color:var(--t3)">${fmtB(d.total_size_bytes)}</td>
        <td>
          <div style="${d.annotation_coverage>=.9?'color:var(--grn)':d.annotation_coverage>=.7?'color:var(--acc)':'color:var(--red)'};font-weight:600">
            ${d.annotation_coverage?Math.round(d.annotation_coverage*100)+'%':'—'}
          </div>
          ${d.annotation_coverage?`<div class="bbar"><div class="bfill" style="width:${d.annotation_coverage*100}%;background:${d.annotation_coverage>=.9?'#22c55e':d.annotation_coverage>=.7?'#fbbf24':'#ef4444'}"></div></div>`:''}
        </td>
      </tr>`).join('')}</tbody></table>
    </div>`);
}
function toggleAddDS(){const f=document.getElementById('ds-form');f.style.display=f.style.display==='none'?'block':'none';}
async function addDS(){
  await api('POST','/api/v1/datasets',{name:document.getElementById('dsn').value,source_type:document.getElementById('dss').value,storage_path:document.getElementById('dsp').value,object_types:document.getElementById('dst').value.split(',').map(s=>s.trim()).filter(Boolean),is_synthetic:document.getElementById('dsy').value==='1',description:document.getElementById('dsc').value});
  toast('Dataset created ✓');pgDL();
}

// ══════════════════════════════════════════════════════════════════════════
// CVAT
// ══════════════════════════════════════════════════════════════════════════
async function pgCVAT(){
  const[tasks,stats]=await Promise.all([api('GET','/api/v1/cvat/tasks'),api('GET','/api/v1/cvat/stats')]);
  setMain(`
    <div class="ph"><div><div class="ptit">▣ CVAT / Annotation Lab</div><div class="psub">Task management · XML import · Quality control</div></div>
      <button class="btn btn-ac" onclick="showCVATForm()">+ NEW TASK</button></div>
    <div class="sg sg4">
      <div class="sc"><div class="sl">Tasks (completed)</div><div class="sv ac">${stats.completed_tasks||0} / ${stats.total_tasks||0}</div></div>
      <div class="sc"><div class="sl">Frames annotated</div><div class="sv gn">${(stats.annotated_frames||0).toLocaleString()}</div></div>
      <div class="sc"><div class="sl">Coverage</div><div class="sv">${stats.annotation_pct||0}%</div>
        <div class="prog-wrap" style="margin-top:5px"><div class="prog-fill" style="width:${stats.annotation_pct||0}%;background:var(--grn)"></div></div>
      </div>
      <div class="sc"><div class="sl">Total Boxes</div><div class="sv cy">${(stats.total_boxes||0).toLocaleString()}</div></div>
    </div>
    <div id="cvat-form" style="display:none" class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">+ New CVAT Task / Import XML</span><button class="btn btn-oc btn-sm" onclick="document.getElementById('cvat-form').style.display='none'">✕</button></div>
      <div style="padding:13px">
        <div class="tabs"><div class="tab on" onclick="setCvatTab('new')">New Task</div><div class="tab" onclick="setCvatTab('xml')">Import XML</div></div>
        <div id="cvat-new">
          <div class="fr"><div><label class="lbl">Task Name</label><input class="inp" id="ctn" placeholder="mission_001_cvat"></div>
          <div><label class="lbl">CVAT Task ID</label><input class="inp" id="ctid" placeholder="task-2099"></div></div>
          <div class="fr"><div><label class="lbl">FPS Sample (кадрів/сек)</label><input class="inp" id="ctfps" type="number" value="2" step="0.5"></div>
          <div><label class="lbl">Labels (comma)</label><input class="inp" id="ctlbl" placeholder="shahed, fpv, air_air"></div></div>
          <button class="btn btn-ac" onclick="createCVATTask()">CREATE TASK</button>
        </div>
        <div id="cvat-xml" style="display:none">
          <div class="ff"><label class="lbl">CVAT XML 1.1 (вставте XML з CVAT export)</label>
          <textarea class="inp" id="cxml" rows="6" style="margin-bottom:0;resize:vertical;font-size:9px" placeholder='&lt;annotations&gt;\n  &lt;track id="1" label="fpv"&gt;\n    &lt;box frame="60" xtl="443.02" ytl="193.26" xbr="521.86" ybr="244.90"/&gt;\n  &lt;/track&gt;\n&lt;/annotations&gt;'></textarea></div>
          <div class="fr"><div><label class="lbl">Task ID</label><input class="inp" id="cxtid" placeholder="task id..."></div>
          <div><label class="lbl">Video ID</label><input class="inp" id="cxvid" placeholder="video id..."></div></div>
          <button class="btn btn-ac" onclick="importXML()">IMPORT ANNOTATIONS</button>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">CVAT Tasks</span></div>
      <table><thead><tr><th>Task</th><th>CVAT ID</th><th>Video</th><th>FPS sample</th><th>Frames</th><th>Progress</th><th>Status</th><th>Assignee</th></tr></thead>
      <tbody>${tasks.map(t=>`<tr>
        <td><strong>${e(t.task_name)}</strong></td>
        <td><code style="color:var(--cya);font-size:9px">${e(t.cvat_task_id)||'—'}</code></td>
        <td style="color:var(--t3);font-size:9px;max-width:140px;overflow:hidden;text-overflow:ellipsis">${e(t.filename||'')}</td>
        <td style="color:var(--t3)">${t.fps_sample} fps</td>
        <td><span style="color:var(--grn);font-weight:600">${t.annotated_frames||0}</span><span style="color:var(--t3)">/${t.total_frames||0}</span></td>
        <td style="min-width:90px">
          <div style="font-size:9px;color:var(--t3)">${t.total_frames?Math.round((t.annotated_frames||0)/t.total_frames*100):0}%</div>
          <div class="prog-wrap"><div class="prog-fill" style="width:${t.total_frames?Math.min((t.annotated_frames||0)/t.total_frames*100,100):0}%;background:var(--grn)"></div></div>
        </td>
        <td>${sbdg(t.status)}</td>
        <td style="color:var(--t3);font-size:9px">${e(t.assignee||'—')}</td>
      </tr>`).join('')}</tbody></table>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">CVAT Annotation Format (reference)</span></div>
      <div style="padding:12px;font-family:monospace;font-size:9px;color:var(--t3);line-height:1.8;background:var(--s2);border-radius:4px;margin:10px">
        <span style="color:var(--acc)">&lt;annotations&gt;</span><br>
        &nbsp;&nbsp;<span style="color:var(--blu)">&lt;track id="1" label="shahed"&gt;</span><br>
        &nbsp;&nbsp;&nbsp;&nbsp;<span style="color:var(--grn)">&lt;box frame="60" keyframe="1" xtl="443.02" ytl="193.26" xbr="521.86" ybr="244.90" z_order="0"/&gt;</span><br>
        &nbsp;&nbsp;&nbsp;&nbsp;<span style="color:var(--grn)">&lt;box frame="65" keyframe="1" xtl="508.97" ytl="194.70" xbr="595.00" ybr="252.10" z_order="0"/&gt;</span><br>
        &nbsp;&nbsp;<span style="color:var(--blu)">&lt;/track&gt;</span><br>
        <span style="color:var(--acc)">&lt;/annotations&gt;</span>
      </div>
    </div>`);
}
function showCVATForm(){document.getElementById('cvat-form').style.display='block';}
function setCvatTab(t){
  document.querySelectorAll('#cvat-form .tab').forEach((el,i)=>el.classList.toggle('on',i===(t==='new'?0:1)));
  document.getElementById('cvat-new').style.display=t==='new'?'block':'none';
  document.getElementById('cvat-xml').style.display=t==='xml'?'block':'none';
}
async function createCVATTask(){
  await api('POST','/api/v1/cvat/tasks',{task_name:document.getElementById('ctn').value,cvat_task_id:document.getElementById('ctid').value,fps_sample:+document.getElementById('ctfps').value,labels:document.getElementById('ctlbl').value.split(',').map(s=>s.trim()).filter(Boolean)});
  toast('CVAT task created ✓');pgCVAT();
}
async function importXML(){
  const xml=document.getElementById('cxml').value.trim();
  if(!xml){toast('Paste XML first',false);return;}
  const r=await api('POST','/api/v1/cvat/import-xml',{xml,task_id:document.getElementById('cxtid').value,video_id:document.getElementById('cxvid').value});
  toast(`Imported ${r.imported||0} annotations ✓`);pgCVAT();
}

// ══════════════════════════════════════════════════════════════════════════
// EXPERIMENTS
// ══════════════════════════════════════════════════════════════════════════
async function pgExp(){
  const exps=await api('GET','/api/v1/experiments');
  const ARCH_LIST=['yolov8n','yolov8s','yolov8m','yolov9c','yolov10n','rt_detr','mobilenet_ssd','nanodet','rtmdet','picodet'];
  setMain(`
    <div class="ph"><div><div class="ptit">⚗ Experiments</div><div class="psub">Порівняння версій · Multi-dataset · Гіперпараметри</div></div>
      <button class="btn btn-ac" onclick="showExpForm()">+ NEW EXPERIMENT</button></div>
    <div id="exp-form" style="display:none" class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">+ New Experiment</span><button class="btn btn-oc btn-sm" onclick="document.getElementById('exp-form').style.display='none'">✕</button></div>
      <div style="padding:13px">
        <div class="ff"><label class="lbl">Name</label><input class="inp" id="exn" placeholder="ShahedNet v3.2 — augmented"></div>
        <div class="fr3">
          <div><label class="lbl">Architecture</label><select class="sel" id="exa">${ARCH_LIST.map(a=>`<option>${a}</option>`).join('')}</select></div>
          <div><label class="lbl">Epochs</label><input class="inp" id="exe" type="number" value="100"></div>
          <div><label class="lbl">Batch Size</label><input class="inp" id="exb" type="number" value="16"></div>
        </div>
        <div class="fr"><div><label class="lbl">Hardware</label><input class="inp" id="exh" placeholder="RTX 4090"></div>
        <div><label class="lbl">Notes</label><input class="inp" id="exnt" placeholder="Нотатки..."></div></div>
        <button class="btn btn-ac" onclick="createExp()">CREATE</button>
      </div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">Experiments (${exps.length})</span></div>
      <table><thead><tr><th>#</th><th>Name</th><th>Arch</th><th>Dataset</th><th>mAP50</th><th>FPS</th><th>Epochs/Batch</th><th>Hardware</th><th>Status</th></tr></thead>
      <tbody>${exps.map((x,i)=>`<tr>
        <td style="color:var(--t3)">${i+1}</td>
        <td><strong>${e(x.name)}</strong><div style="font-size:9px;color:var(--t3)">${e(x.notes||'')}</div></td>
        <td><span class="bdg bb">${e(x.base_model_arch)}</span></td>
        <td style="color:var(--t3);font-size:9px">${e(x.ds_name||x.dataset_id||'—')}</td>
        <td>${x.map50!=null?`<span style="${mcol(x.map50)};font-weight:600">${(x.map50*100).toFixed(1)}%</span>`:'<span style="color:var(--t3)">—</span>'}</td>
        <td style="${x.fps!=null?'color:var(--acc)':'color:var(--t3)'}">
          ${x.fps!=null?x.fps.toFixed(0)+'':'—'}
        </td>
        <td style="color:var(--t3)">${x.epochs}ep / bs${x.batch_size}</td>
        <td style="color:var(--t3);font-size:9px">${e(x.hardware||'—')}</td>
        <td>${sbdg(x.status)}</td>
      </tr>`).join('')}</tbody></table>
    </div>`);
}
function showExpForm(){document.getElementById('exp-form').style.display='block';}
async function createExp(){
  await api('POST','/api/v1/experiments',{name:document.getElementById('exn').value,model_arch:document.getElementById('exa').value,epochs:+document.getElementById('exe').value,batch_size:+document.getElementById('exb').value,hardware:document.getElementById('exh').value,notes:document.getElementById('exnt').value});
  toast('Experiment created ✓');pgExp();
}

// ══════════════════════════════════════════════════════════════════════════
// MODELS
// ══════════════════════════════════════════════════════════════════════════
async function pgMdl(){
  const ms=await api('GET','/api/v1/models');
  const ARCH_COLORS={yolov8m:'bb',yolov8n:'bb',yolov9c:'bb',yolov10n:'ba',rt_detr:'bp',mobilenet_ssd:'bor',nanodet:'bcy',rtmdet:'bg',picodet:'bgy'};
  setMain(`
    <div class="ph"><div><div class="ptit">⬡ Model Zoo</div><div class="psub">YOLO · RT-DETR · MobileNet-SSD · NanoDet · RTMDet · PicoDet</div></div>
      <button class="btn btn-ac">⬆ UPLOAD MODEL</button></div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
      ${ms.map(m=>`
      <div class="card" style="margin:0"><div style="padding:12px">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">
          <div><div style="font-size:12px;font-weight:700">${e(m.name)}</div><div style="font-size:9px;color:var(--t3);margin-top:1px">v${m.version} · by ${e(m.author)}</div></div>
          <span class="bdg ${ARCH_COLORS[m.architecture]||'bgy'}">${m.architecture}</span>
        </div>
        <div style="font-size:9px;color:var(--t3);margin-bottom:8px;line-height:1.5">${e(m.description)}</div>
        <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--t3);margin-bottom:8px">
          <span>${m.format.toUpperCase()} · ${m.input_size}</span><span>${Number(m.size_mb).toFixed(1)} MB</span>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:2px;margin-bottom:8px">
          ${(m.class_names||[]).map(c=>`<span class="bdg bgy" style="font-size:8px">${c}</span>`).join('')}
        </div>
        <div style="display:flex;gap:5px">
          <button class="btn btn-oc btn-sm" style="flex:1" onclick="go('leaderboard')">Benchmark</button>
          <button class="btn btn-ac btn-sm" style="flex:1" onclick="go('edge')">Deploy</button>
        </div>
      </div></div>`).join('')}
    </div>`);
}

// ══════════════════════════════════════════════════════════════════════════
// TRAINING
// ══════════════════════════════════════════════════════════════════════════
async function pgTrain(){
  const d=await api('GET','/api/v1/training/jobs');
  const SC={Completed:'bg',Running:'ba',Queued:'bb',Failed:'br',Canceled:'bgy'};
  const ARCHS=['yolov8n','yolov8s','yolov8m','yolov9c','yolov10n','rt_detr','mobilenet_ssd','nanodet','rtmdet','picodet'];
  setMain(`
    <div class="ph"><div><div class="ptit">⚙ Training Jobs</div><div class="psub">Azure ML · YOLO · NanoDet · RTMDet · PicoDet</div></div></div>
    <div class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">+ Submit Job</span></div>
      <div style="padding:13px">
        <div class="ff"><label class="lbl">Dataset Path</label><input class="inp" id="tjp" placeholder="azureml://datastores/varta/paths/..."></div>
        <div class="fr3">
          <div><label class="lbl">Architecture</label><select class="sel" id="tja">${ARCHS.map(a=>`<option>${a}</option>`).join('')}</select></div>
          <div><label class="lbl">Epochs</label><input class="inp" id="tje" type="number" value="100"></div>
          <div><label class="lbl">Batch Size</label><input class="inp" id="tjb" type="number" value="16"></div>
        </div>
        <div class="fr"><div><label class="lbl">Hardware</label><input class="inp" id="tjhw" placeholder="RTX 4090 / Jetson / RPi"></div>
        <div><label class="lbl">Experiment Name</label><input class="inp" id="tjx" value="varta-training"></div></div>
        <button class="btn btn-ac" onclick="submitJob()">▷ SUBMIT</button>
      </div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">Jobs</span><button class="btn btn-oc btn-sm" onclick="pgTrain()">↺</button></div>
      <table><thead><tr><th>Job</th><th>Arch</th><th>Ep/BS</th><th>Hardware</th><th>mAP50</th><th>Inf ms</th><th>Status</th></tr></thead>
      <tbody>${d.jobs.map(j=>`<tr>
        <td><strong>${e(j.display_name)}</strong><div style="font-size:9px;color:var(--t3)">${j.job_id}</div></td>
        <td><span class="bdg bb">${e(j.model_arch)}</span></td>
        <td style="color:var(--t3)">${j.epochs}ep / bs${j.batch_size||16}</td>
        <td style="color:var(--t3);font-size:9px">${e(j.hardware||'—')}</td>
        <td>${j.map50!=null?`<span style="${mcol(j.map50)};font-weight:600">${(j.map50*100).toFixed(1)}%</span>`:'—'}</td>
        <td style="color:var(--t3)">${j.inference_ms?j.inference_ms.toFixed(1)+'ms':'—'}</td>
        <td>${sbdg(j.status)}</td>
      </tr>`).join('')}</tbody></table>
    </div>`);
}
async function submitJob(){
  const p=document.getElementById('tjp').value;if(!p){toast('Enter path',false);return;}
  await api('POST','/api/v1/training/jobs',{dataset_path:p,model_arch:document.getElementById('tja').value,epochs:+document.getElementById('tje').value,batch_size:+document.getElementById('tjb').value,hardware:document.getElementById('tjhw').value,experiment_name:document.getElementById('tjx').value});
  toast('Job queued ✓');setTimeout(pgTrain,2500);
}

// ══════════════════════════════════════════════════════════════════════════
// LEADERBOARD
// ══════════════════════════════════════════════════════════════════════════
let lbM='map50';
async function pgLB(m){
  if(m)lbM=m;
  const d=await api('GET',`/api/v1/models/leaderboard?metric=${lbM}`);
  const METS=[['map50','mAP@50'],['map50_95','mAP@50-95'],['precision','Precision'],['recall','Recall'],['fps','FPS'],['inference_ms','Inference ms']];
  const medals=['🥇','🥈','🥉'];
  setMain(`
    <div class="ph"><div><div class="ptit">◈ Leaderboard</div><div class="psub">Model performance ranking · All architectures</div></div></div>
    <div class="tabs">${METS.map(([k,l])=>`<div class="tab ${lbM===k?'on':''}" onclick="pgLB('${k}')">${l}</div>`).join('')}</div>
    <div class="card">
      <table><thead><tr><th>#</th><th>Model</th><th>Author</th><th>Arch</th><th>mAP50</th><th>Precision</th><th>Recall</th><th>FPS</th><th>Inf ms</th><th>Hardware</th></tr></thead>
      <tbody>${(d.rankings||[]).map(r=>`<tr>
        <td style="font-size:${r.rank<=3?14:10}px">${r.rank<=3?medals[r.rank-1]:`<span style="color:var(--t3)">#${r.rank}</span>`}</td>
        <td><strong>${e(r.model_name)}</strong></td>
        <td style="color:var(--t3)">${e(r.author)}</td>
        <td><span class="bdg bb">${e(r.arch)}</span></td>
        <td style="${lbM==='map50'?'color:var(--acc);font-weight:700':''}">${r.map50!=null?(r.map50*100).toFixed(1)+'%':'—'}</td>
        <td style="${lbM==='precision'?'color:var(--acc);font-weight:700':''}">${r.precision!=null?(r.precision*100).toFixed(1)+'%':'—'}</td>
        <td style="${lbM==='recall'?'color:var(--acc);font-weight:700':''}">${r.recall!=null?(r.recall*100).toFixed(1)+'%':'—'}</td>
        <td style="${lbM==='fps'?'color:var(--acc);font-weight:700':''}">${r.fps?r.fps.toFixed(0):'—'}</td>
        <td style="${lbM==='inference_ms'?'color:var(--acc);font-weight:700':''}">${r.inference_ms?r.inference_ms.toFixed(1)+'ms':'—'}</td>
        <td style="color:var(--t3);font-size:9px">${e(r.hardware||'—')}</td>
      </tr>`).join('')}
      ${!(d.rankings||[]).length?'<tr><td colspan="10" style="text-align:center;color:var(--t3);padding:20px">No benchmark data</td></tr>':''}
      </tbody></table>
    </div>`);
}

// ══════════════════════════════════════════════════════════════════════════
// EVALUATION
// ══════════════════════════════════════════════════════════════════════════
const CLRS_MAP={BUILDING:'bor',FIELD:'bg',FOREST:'blm',SKY:'bb',UNKNOWN:'bgy',CLOUDS:'bp'};
let evTab='overview',evRun=null;
async function pgEval(){
  const runs=await api('GET','/api/v1/eval/runs');
  if(!evRun&&runs.length)evRun=runs[0].id;
  let runDetail=null,summary=null;
  if(evRun){[runDetail,summary]=await Promise.all([api('GET',`/api/v1/eval/runs/${evRun}`),api('GET',`/api/v1/eval/runs/${evRun}/summary`)]);}
  const fr=summary?.frame_background||{},ob=summary?.object_background||{};
  function mTable(metrics,title){
    return`<div class="card" style="margin-bottom:10px"><div class="ch"><span class="ct">${title}</span></div>
    <div style="overflow-x:auto"><table><thead><tr><th>Клас</th><th>N</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>F1</th><th>FPPI</th></tr></thead>
    <tbody>${metrics.map(m=>`<tr>
      <td><span class="bdg ${CLRS_MAP[m.class_name]||'bgy'}">${m.class_name}</span></td>
      <td style="color:var(--t3)">${Number(m.n).toLocaleString()}</td>
      <td style="color:#4ade80;font-weight:600">${m.tp}</td>
      <td style="${m.fp>50?'color:#ef4444':m.fp>10?'color:var(--acc)':'color:var(--t3)'};font-weight:600">${m.fp}</td>
      <td style="${m.fn>50?'color:#ef4444':m.fn>10?'color:var(--acc)':'color:var(--t3)'};font-weight:600">${m.fn}</td>
      <td>${fmtBar(m.precision)}</td><td>${fmtBar(m.recall)}</td><td>${fmtBar(m.f1)}</td><td>${fmtBar(m.fppi,true)}</td>
    </tr>`).join('')}</tbody></table></div></div>`;
  }
  setMain(`
    <div class="ph"><div><div class="ptit">◉ Evaluation Dashboard</div><div class="psub">Per-class metrics · Precision / Recall / F1 / FPPI</div></div>
      <button class="btn btn-ac" onclick="showEvalForm()">+ NEW RUN</button></div>
    <div id="eval-form" style="display:none" class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">+ New Run</span><button class="btn btn-oc btn-sm" onclick="hideEvalForm()">✕</button></div>
      <div style="padding:13px">
        <div class="fr"><div><label class="lbl">Name</label><input class="inp" id="evn" placeholder="Field Eval v3"></div>
        <div><label class="lbl">Hardware</label><input class="inp" id="evh" placeholder="RTX 3080"></div></div>
        <button class="btn btn-ac" onclick="createEvalRun()">CREATE (reference data)</button>
      </div>
    </div>
    <div style="display:flex;gap:12px">
      <div style="width:180px;flex-shrink:0">
        <div class="card" style="margin:0"><div class="ch"><span class="ct">Runs</span></div>
          <div style="max-height:300px;overflow-y:auto">
            ${runs.map(r=>`<div onclick="selectRun('${r.id}')" style="padding:9px 12px;cursor:pointer;border-bottom:1px solid var(--brd);background:${evRun===r.id?'var(--a2)':'transparent'}">
              <div style="font-size:10px;font-weight:600;color:${evRun===r.id?'var(--acc)':'var(--txt)'};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${e(r.name)}</div>
              <div style="font-size:9px;color:var(--t3)">${r.hardware||'—'} · ${(r.created_at||'').slice(0,10)}</div>
            </div>`).join('')}
            ${!runs.length?'<div style="padding:14px;font-size:10px;color:var(--t3);text-align:center">No runs</div>':''}
          </div>
        </div>
      </div>
      <div style="flex:1;min-width:0">
        ${!runDetail?'<div class="card"><div style="padding:40px;text-align:center;color:var(--t3)">Select a run</div></div>':''}
        ${runDetail?`
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <div><div style="font-size:11px;font-weight:700">${e(runDetail.name)}</div><div style="font-size:9px;color:var(--t3)">${runDetail.hardware||''} · ${(runDetail.created_at||'').slice(0,10)}</div></div>
            <button class="btn btn-dr btn-sm" onclick="deleteRun('${runDetail.id}')">🗑</button>
          </div>
          <div class="sg sg4">
            <div class="sc"><div class="sl">wF1 (кадр)</div><div class="sv gn">${fr.weighted_f1!=null?(fr.weighted_f1*100).toFixed(1)+'%':'—'}</div></div>
            <div class="sc"><div class="sl">wF1 (об'єкт)</div><div class="sv ac">${ob.weighted_f1!=null?(ob.weighted_f1*100).toFixed(1)+'%':'—'}</div></div>
            <div class="sc"><div class="sl">FPPI (кадр)</div><div class="sv ${fr.avg_fppi<=.04?'gn':'or'}">${fr.avg_fppi!=null?fr.avg_fppi.toFixed(4):'—'}</div></div>
            <div class="sc"><div class="sl">FPPI (об'єкт)</div><div class="sv or">${ob.avg_fppi!=null?ob.avg_fppi.toFixed(4):'—'}</div></div>
          </div>
          <div class="tabs">
            <div class="tab ${evTab==='overview'?'on':''}" onclick="evTab='overview';pgEval()">Overview</div>
            <div class="tab ${evTab==='frame'?'on':''}" onclick="evTab='frame';pgEval()">Фон кадра</div>
            <div class="tab ${evTab==='object'?'on':''}" onclick="evTab='object';pgEval()">Фон об'єкта</div>
          </div>
          ${evTab==='overview'?mTable(runDetail.frame_background,'Фон кадра')+mTable(runDetail.object_background,"Фон об'єкта"):''}
          ${evTab==='frame'?mTable(runDetail.frame_background,'Фон кадра'):''}
          ${evTab==='object'?mTable(runDetail.object_background,"Фон об'єкта"):''}
        `:''}
      </div>
    </div>`);
}
function showEvalForm(){document.getElementById('eval-form').style.display='block';}
function hideEvalForm(){document.getElementById('eval-form').style.display='none';}
async function selectRun(id){evRun=id;evTab='overview';pgEval();}
async function createEvalRun(){
  const METS=[
    {background_type:'frame_background',class_name:'BUILDING',n:4,tp:3,fp:0,fn:1,precision:1.000,recall:0.750,f1:0.857,fppi:0.000},
    {background_type:'frame_background',class_name:'FIELD',n:1990,tp:2046,fp:116,fn:119,precision:0.946,recall:0.945,f1:0.946,fppi:0.058},
    {background_type:'frame_background',class_name:'FOREST',n:406,tp:398,fp:7,fn:8,precision:0.983,recall:0.980,f1:0.982,fppi:0.017},
    {background_type:'frame_background',class_name:'SKY',n:456,tp:479,fp:48,fn:100,precision:0.909,recall:0.827,f1:0.866,fppi:0.105},
    {background_type:'frame_background',class_name:'UNKNOWN',n:1,tp:0,fp:0,fn:1,precision:null,recall:0.000,f1:null,fppi:0.000},
    {background_type:'object_background',class_name:'BUILDING',n:7,tp:5,fp:1,fn:2,precision:0.833,recall:0.714,f1:0.769,fppi:0.143},
    {background_type:'object_background',class_name:'CLOUDS',n:427,tp:455,fp:40,fn:95,precision:0.919,recall:0.827,f1:0.871,fppi:0.094},
    {background_type:'object_background',class_name:'FIELD',n:221,tp:209,fp:14,fn:12,precision:0.937,recall:0.946,f1:0.941,fppi:0.063},
    {background_type:'object_background',class_name:'FOREST',n:408,tp:398,fp:8,fn:10,precision:0.980,recall:0.975,f1:0.978,fppi:0.020},
    {background_type:'object_background',class_name:'SKY',n:1794,tp:1859,fp:108,fn:110,precision:0.945,recall:0.944,f1:0.945,fppi:0.060},
  ];
  const r=await api('POST','/api/v1/eval/runs',{name:document.getElementById('evn').value||'Field Eval v2',hardware:document.getElementById('evh').value||'RTX 3080',model_id:'',dataset_id:'',metrics:METS});
  evRun=r.id;evTab='overview';toast('Eval run created ✓');pgEval();
}
async function deleteRun(id){await api('DELETE',`/api/v1/eval/runs/${id}`);evRun=null;toast('Deleted');pgEval();}

// ══════════════════════════════════════════════════════════════════════════
// PALANTIR
// ══════════════════════════════════════════════════════════════════════════
async function pgPalantir(){
  const[pcs,pst]=await Promise.all([api('GET','/api/v1/palantir/containers'),api('GET','/api/v1/palantir/stats')]);
  const STATUS_COLOR={building:'ba',ready:'bg',uploading:'bcy',uploaded:'bg',processing:'bp',done:'bg',failed:'br'};
  setMain(`
    <div class="ph"><div><div class="ptit">⬢ Palantir Containers</div><div class="psub">Build → Upload → Process → Results</div></div>
      <button class="btn btn-ac" onclick="showPalForm()">+ NEW CONTAINER</button></div>
    <div class="sg sg4">
      ${Object.entries(pst.by_status||{}).map(([s,c])=>`<div class="sc"><div class="sl">${s}</div><div class="sv ${STATUS_COLOR[s]||'ac'}">${c}</div></div>`).join('')}
      <div class="sc"><div class="sl">Exported (GB)</div><div class="sv cy">${pst.total_exported_gb||0}</div></div>
    </div>
    <div id="pal-form" style="display:none" class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">+ New Palantir Container</span><button class="btn btn-oc btn-sm" onclick="document.getElementById('pal-form').style.display='none'">✕</button></div>
      <div style="padding:13px">
        <div class="ff"><label class="lbl">Container Name</label><input class="inp" id="pln" placeholder="PALANTIR-2025-004"></div>
        <div class="ff"><label class="lbl">Notes</label><input class="inp" id="plnt" placeholder="Опис контейнера..."></div>
        <button class="btn btn-ac" onclick="createContainer()">BUILD CONTAINER</button>
      </div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">Containers</span></div>
      <table><thead><tr><th>Name</th><th>Status</th><th>Progress</th><th>Size</th><th>Dataset RID</th><th>Result</th><th>Actions</th></tr></thead>
      <tbody>${pcs.map(pc=>`<tr>
        <td><strong>${e(pc.name)}</strong><div style="font-size:9px;color:var(--t3)">${(pc.created_at||'').slice(0,10)}</div></td>
        <td><span class="bdg ${STATUS_COLOR[pc.status]||'bgy'}">${pc.status}</span></td>
        <td style="min-width:100px">
          <div style="font-size:9px;color:var(--t3)">${pc.upload_progress||0}%</div>
          <div class="prog-wrap"><div class="prog-fill" style="width:${pc.upload_progress||0}%;background:${pc.status==='done'?'var(--grn)':pc.status==='failed'?'var(--red)':'var(--cya)'}"></div></div>
        </td>
        <td style="color:var(--t3)">${pc.size_bytes?fmtB(pc.size_bytes):'—'}</td>
        <td><code style="font-size:8px;color:var(--cya)">${pc.palantir_dataset_rid?pc.palantir_dataset_rid.slice(0,28)+'...':'—'}</code></td>
        <td>${pc.result_path?`<span class="bdg bg">✓ Ready</span>`:'<span style="color:var(--t3);font-size:9px">—</span>'}</td>
        <td>
          ${pc.status==='ready'?`<button class="btn btn-cy btn-sm" onclick="uploadContainer('${pc.id}')">⬆ Upload</button>`:''}
          ${pc.status==='building'?`<span style="font-size:9px;color:var(--acc)">Building... ${pc.upload_progress||0}%</span>`:''}
        </td>
      </tr>`).join('')}</tbody></table>
    </div>`);
}
function showPalForm(){document.getElementById('pal-form').style.display='block';}
async function createContainer(){
  const r=await api('POST','/api/v1/palantir/containers',{name:document.getElementById('pln').value||'New Container',notes:document.getElementById('plnt').value});
  toast('Container building... ✓');
  setTimeout(pgPalantir,2000);
}
async function uploadContainer(id){
  await api('POST','/api/v1/palantir/upload',{container_id:id});
  toast('Uploading to Palantir...');setTimeout(pgPalantir,1500);
}

// ══════════════════════════════════════════════════════════════════════════
// MISSIONS
// ══════════════════════════════════════════════════════════════════════════
async function pgMissions(){
  const[mis,tele]=await Promise.all([api('GET','/api/v1/analytics/feedback-loop'),api('GET','/api/v1/analytics/telemetry')]);
  setMain(`
    <div class="ph"><div><div class="ptit">✦ Missions</div><div class="psub">Polygon · Real Field · Telemetry · Feedback Loop</div></div>
      <button class="btn btn-ac" onclick="showMisForm()">+ UPLOAD RESULTS</button></div>
    <div class="sg sg4">
      <div class="sc"><div class="sl">Total missions</div><div class="sv ac">${tele.length||0}</div></div>
      <div class="sc"><div class="sl">With hard frames</div><div class="sv rd">${mis.missions_with_hard_frames||0}</div></div>
      <div class="sc"><div class="sl">Hard frames queued</div><div class="sv or">${mis.total_hard_frames||0}</div></div>
      <div class="sc"><div class="sl">Polygon missions</div><div class="sv bb">${tele.filter(m=>m.mission_type==='polygon').length}</div></div>
    </div>
    ${mis.total_hard_frames>0?`<div class="alert-a"><span>⚠</span><div><strong>FEEDBACK LOOP: ${mis.total_hard_frames} hard frames</strong> з ${mis.missions_with_hard_frames} місій очікують re-annotation у CVAT.</div></div>`:''}
    <div id="mis-form" style="display:none" class="card" style="margin-bottom:14px">
      <div class="ch"><span class="ct">Upload Mission Results</span><button class="btn btn-oc btn-sm" onclick="hideMisForm()">✕</button></div>
      <div style="padding:13px">
        <div class="fr3"><div><label class="lbl">Mission ID</label><input class="inp" id="msi" placeholder="OPS-2025-006"></div>
        <div><label class="lbl">Device ID</label><input class="inp" id="msd" placeholder="drone-007"></div>
        <div><label class="lbl">Mission Type</label><select class="sel" id="mst"><option value="polygon">Polygon</option><option value="real_field">Real Field</option><option value="simulation">Simulation</option></select></div></div>
        <div class="fr"><div><label class="lbl">Location</label><input class="inp" id="msl" placeholder="Полігон Олексіївка"></div>
        <div><label class="lbl">Total Frames</label><input class="inp" id="msf" type="number" value="300"></div></div>
        <div class="fr3"><div><label class="lbl">Detections</label><input class="inp" id="msdt" type="number" value="2"></div>
        <div><label class="lbl">False Positives</label><input class="inp" id="msfp" type="number" value="0"></div>
        <div><label class="lbl">Hard Frames (comma)</label><input class="inp" id="mshf" placeholder="42,88,134"></div></div>
        <button class="btn btn-ac" onclick="uploadMission()">UPLOAD</button>
      </div>
    </div>
    <div class="card">
      <div class="ch"><span class="ct">Mission Results — Telemetry</span></div>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>Mission ID</th><th>Device</th><th>Type</th><th>Location</th><th>Frames</th><th>Detections</th><th>FP</th><th>Hard</th><th>Telemetry</th></tr></thead>
        <tbody>${tele.map(m=>`<tr>
          <td><strong>${e(m.mission_id)}</strong></td>
          <td style="color:var(--t3)">${e(m.device_id)}</td>
          <td>${sbdg(m.mission_type)}</td>
          <td style="color:var(--t3);font-size:9px">${e(m.location||'—')}</td>
          <td style="color:var(--t3)">${m.total_frames||0}</td>
          <td style="color:var(--grn);font-weight:600">${m.detected_targets||0}</td>
          <td style="${(m.false_positives||0)>2?'color:var(--red)':'color:var(--t3)'}">${m.false_positives||0}</td>
          <td>${(m.false_negatives||0)>0?`<span class="bdg ba">${m.false_negatives}</span>`:'<span style="color:var(--grn)">0</span>'}</td>
          <td style="font-size:9px;color:var(--t3)">
            ${m.telemetry?Object.entries(m.telemetry).slice(0,3).map(([k,v])=>`${k}:${v}`).join(' '):'—'}
          </td>
        </tr>`).join('')}
        </tbody>
      </table></div>
    </div>`);
}
function showMisForm(){document.getElementById('mis-form').style.display='block';}
function hideMisForm(){document.getElementById('mis-form').style.display='none';}
async function uploadMission(){
  const hf=document.getElementById('mshf').value.split(',').map(s=>parseInt(s.trim())).filter(n=>!isNaN(n));
  await api('POST','/api/v1/mission-results',{mission_id:document.getElementById('msi').value,device_id:document.getElementById('msd').value,mission_type:document.getElementById('mst').value,location:document.getElementById('msl').value,total_frames:+document.getElementById('msf').value,detected_targets:+document.getElementById('msdt').value,false_positives:+document.getElementById('msfp').value,hard_frames:hf});
  toast('Mission results uploaded ✓');hideMisForm();pgMissions();
}

// ══════════════════════════════════════════════════════════════════════════
// EDGE
// ══════════════════════════════════════════════════════════════════════════
async function pgEdge(){
  const[devs,models]=await Promise.all([api('GET','/api/v1/edge/devices'),api('GET','/api/v1/models')]);
  setMain(`
    <div class="ph"><div><div class="ptit">◌ Edge Devices</div><div class="psub">Active deployments · Model on-the-fly · Flight controller API</div></div></div>
    ${(devs.devices||[]).map(d=>`<div class="card" style="margin-bottom:8px"><div style="padding:12px;display:flex;align-items:center;justify-content:space-between">
      <div style="display:flex;align-items:center;gap:12px">
        <div style="width:32px;height:32px;background:var(--g2);border:1px solid var(--g3);border-radius:5px;display:flex;align-items:center;justify-content:center">◌</div>
        <div><div class="rt">${e(d.device_name)}</div><div class="rs">ID: ${e(d.device_id)} · conf=${d.config?.conf_threshold||d.config?.conf||'—'}</div></div>
      </div>
      <div style="text-align:right"><div style="font-size:9px;color:var(--t3)">Active Model</div><div style="font-size:11px;font-weight:600;color:var(--acc)">${e(d.model)}</div><div style="font-size:9px;color:var(--t3)">${(d.deployed_at||'').slice(0,10)}</div></div>
    </div></div>`).join('')}
    <div class="card" style="margin-top:14px">
      <div class="ch"><span class="ct">⬆ Deploy Model to Device</span></div>
      <div style="padding:13px">
        <div class="fr"><div><label class="lbl">Device ID</label><input class="inp" id="ddid" placeholder="drone-020"></div>
        <div><label class="lbl">Device Name</label><input class="inp" id="ddn" placeholder="Дрон Яструб-20"></div></div>
        <div class="fr"><div><label class="lbl">Model</label><select class="sel" id="ddm">${models.map(m=>`<option value="${m.id}">${m.name} v${m.version} (${m.architecture})</option>`).join('')}</select></div>
        <div><label class="lbl">Confidence</label><input class="inp" id="ddc" type="number" value="0.45" min="0.1" max="0.99" step="0.05"></div></div>
        <button class="btn btn-ac" onclick="deployM()">▷ DEPLOY</button>
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <div class="ch"><span class="ct">Edge API — Flight Controller Integration</span></div>
      <div style="padding:12px;font-size:10px;line-height:2.2">
        ${[['GET','GET /aihub/api/v1/edge/active-model/{device_id}','Завантажити активну модель (SAS URL)'],
           ['POST','POST /aihub/api/v1/edge/deploy-model','Задеплоїти нову модель на пристрій'],
           ['POST','POST /aihub/api/v1/mission-results','Відправити результати місії + телеметрію'],
           ['POST','POST /aihub/api/v1/pipeline/advance','Просунути відео по pipeline'],
        ].map(([m,p,d])=>`<div style="display:flex;align-items:center;gap:8px">
          <span class="bdg ${m==='GET'?'bb':'bg'}" style="min-width:38px;text-align:center">${m}</span>
          <code style="color:var(--cya);font-size:9px">${p}</code>
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
╔══════════════════════════════════════════════════════════════╗
║  VARTA AI HUB v2.0                                         ║
╠══════════════════════════════════════════════════════════════╣
║  Local : http://localhost:{PORT}/aihub/                       ║
║  Deploy: https://www.dozorai.com/aihub/                     ║
╠══════════════════════════════════════════════════════════════╣
║  New modules:                                               ║
║  ⇢ Pipeline Tracker (raw→frames→annotated→models→palantir)  ║
║  ▣ CVAT Integration (tasks, XML import)                     ║
║  ⬢ Palantir Containers (build, upload, results)             ║
║  ✦ Missions (polygon + real field + telemetry)              ║
║  ⚗ Experiments (multi-arch, multi-dataset comparison)       ║
║  ◌ Extended: Backblaze B2, NanoDet, RTMDet, PicoDet         ║
╠══════════════════════════════════════════════════════════════╣
║  admin@varta.ai / ChangeMe123! (admin)                      ║
║  ml@varta.ai    / MLEngineer123! (ml_engineer)              ║
╚══════════════════════════════════════════════════════════════╝
""")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Platform stopped.")
