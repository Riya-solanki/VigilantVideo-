# 🎥 VigilantVideo — AI Content Protection System

Protecting user-generated video content from unauthorized AI scraping, training, and misuse.

## 🚀 Overview

As AI models increasingly rely on large-scale scraped data, creators face a growing risk of their videos being harvested and used without consent. **VigilantVideo** is a full-stack web application that lets users upload videos and applies **encryption, adversarial perturbations, and digital watermarking** to make the content resistant to AI-based extraction — while preserving visual quality for human viewers.

Users get a dashboard to track processing status (pending → processing → completed/failed), monitor storage usage, and review activity across their uploads.

## ✨ Key Features

- **Adversarial perturbation pipeline** — GPU-accelerated processing that subtly alters pixel data to disrupt AI model training/extraction without visibly degrading video quality
- **Digital watermarking** — embeds ownership markers into processed videos
- **Encrypted storage** — content is encrypted at rest
- **Async background processing** — a dedicated worker handles video processing jobs outside the request/response cycle, so uploads don't block the UI
- **Status dashboard** — real-time tracking of upload → processing → completion, plus storage usage
- **Cloud object storage** — videos stored via Cloudflare R2 with configured CORS policies for secure client-side access

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| Database | SQLAlchemy (see `models.py`, `init_db.py`) |
| GPU Processing | Custom CUDA/GPU kernels (`protection_gpu_kernels.py`, `protection_gpu_v2.py`) |
| Background Jobs | `worker.py` (async processing queue) |
| Storage | Cloudflare R2 (object storage) with custom CORS policy |
| Frontend | HTML/CSS/JS (Flask templates + static assets) |
| Deployment | Procfile (Heroku-style process deployment) |

> *Confirm/adjust: exact GPU framework (CUDA/PyTorch/CuPy?), queue mechanism for `worker.py` (Celery/RQ/custom loop?), and frontend framework if any (vanilla JS vs. something else).*

## 🏗️ How It Works

1. **Upload** — user submits a video through the web dashboard
2. **Queue** — the job is handed off to `worker.py` for async processing (keeps the app responsive)
3. **Protect** — `protection_gpu_kernels.py` / `protection_gpu_v2.py` apply adversarial perturbations and watermarking on GPU
4. **Store** — the processed file is encrypted and pushed to Cloudflare R2 storage
5. **Track** — the dashboard polls/reflects job status (pending → processing → completed/failed) and shows storage usage

## 📦 Getting Started

### Prerequisites
- Python 3.x
- pip
- A Cloudflare R2 bucket (or compatible S3 storage) + credentials
- GPU with CUDA support (for the protection pipeline)

### Installation

```bash
git clone https://github.com/Riya-solanki/VigilantVideo.git
cd VigilantVideo
pip install -r requirements.txt
```

### Configuration

Set the following environment variables (or edit `config.py`):

```
R2_ACCESS_KEY_ID=your_key
R2_SECRET_ACCESS_KEY=your_secret
R2_BUCKET_NAME=your_bucket
DATABASE_URL=your_db_url
```

> *Confirm exact env var names used in `config.py`.*

### Initialize the database

```bash
python init_db.py
```

### Run the app

```bash
python app.py
```

### Run the background worker (separate process)

```bash
python worker.py
```

## 🧪 Tests

```bash
pytest tests/
```
