# 🛡 VARTA AI HUB v2.0

**AI Model Management Ecosystem** — повний цикл від сирих відео до задеплоєних моделей на edge-пристроях.

> Deploy: `https://www.dozorai.com/aihub/`

## Запуск

```bash
python3 varta_hub_v2.py
# → http://localhost:8767/aihub/
```

## Модулі

| Модуль | Опис |
|--------|------|
| ⇢ Pipeline Tracker | raw → frames → annotated → model_run → metrics → palantir |
| ◫ Data Lake | Backblaze B2, Azure Blob, S3, Google Drive |
| ▣ CVAT | Tasks, XML 1.1 import, annotation coverage |
| ⬡ Model Zoo | YOLO / RT-DETR / MobileNet-SSD / NanoDet / RTMDet / PicoDet |
| ⚗ Experiments | Multi-arch, multi-dataset comparison |
| ◈ Leaderboard | mAP50 / Precision / Recall / FPS |
| ◉ Evaluation | Per-class F1 / FPPI (Фон кадра + Фон об'єкта) |
| ⬢ Palantir | Container build → upload → track results |
| ✦ Missions | Polygon + Real Field + Telemetry + Feedback Loop |
| ◌ Edge Devices | Deploy models to drones + flight controller API |

## Credentials

| Email | Password | Role |
|-------|----------|------|
| admin@varta.ai | ChangeMe123! | admin |
| ml@varta.ai | MLEngineer123! | ml_engineer |
| analyst@varta.ai | Analyst123! | analyst |

## Nginx (dozorai.com)

```nginx
location /aihub/ {
    proxy_pass http://127.0.0.1:8767/aihub/;
    proxy_set_header Host $host;
}
```

---
*Zero dependencies · Python 3.12 + SQLite · 1860 lines*
