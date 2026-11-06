# Project 3 — Option C: MLFlowOps

## Concept

Build the infrastructure to **train, version, and serve a machine learning model** using DevOps practices. You don't need to be a data scientist — the model is simple. The challenge is the infrastructure: making ML repeatable, observable, and deployable.

## The application

**taskly**: https://github.com/professordiogodev/taskly — the same
minimal FastAPI task tracker used elsewhere in the bootcamp, extended with a `priority` field
and a `model/` directory that is the starter kit for this option:

- `model/train.py` — trains a task-priority classifier (title + description → low/medium/high)
  and logs params, metrics, and the model itself to MLflow. It's intentionally trivial
  (TF-IDF + logistic regression) — you are not being graded on model quality.
- `model/data/tasks_labeled.csv` — a synthetic labeled dataset checked in so `train.py` runs
  out of the box; `model/generate_data.py` regenerates it if you want more/different rows.
- `model/requirements.txt` — the extra deps (`mlflow`, `pandas`, `scikit-learn`) needed only for
  training, kept separate from the API's own `requirements.txt` so the serving container doesn't
  need to bundle them.

Fork the repo, confirm `python model/train.py` runs locally against a local MLflow server
(`pip install -r model/requirements.txt` first), then build everything around it: the tracking
server, the artifact store, the model registry and promotion workflow, the serving API, and the
CI/CD and observability that ties it together. That surrounding infrastructure is the actual
project — the training script itself should not need to change.

```
GitHub push → GitHub Actions
    │── run training job (on EC2 spot instance or Azure Spot VM)
    │── log metrics + model to MLflow tracking server
    │── build serving API container
    └── deploy to EKS / AKS
         ├── mlflow-server (Deployment)
         ├── model-serving-api (Deployment, HPA)
         └── postgres (for MLflow backend store)
```

## Requirements

### ML infrastructure
- [ ] MLflow tracking server deployed on Kubernetes with S3 or Azure Blob as artifact store
- [ ] Training pipeline runs on a spot/preemptible instance via GitHub Actions (not your laptop)
- [ ] Model registered in MLflow Model Registry with staging → production promotion workflow
- [ ] Model versioning: deploying a new model version doesn't require changing Kubernetes manifests

### Model serving
- [ ] FastAPI wrapper serves model at `POST /predict`
- [ ] Model loaded at startup from MLflow Model Registry (not baked into the container image)
- [ ] `/metrics` endpoint exposes: prediction request count, prediction latency histogram, model version label
- [ ] Container replaces running model without downtime (rolling update)

### Infrastructure
- [ ] EKS or AKS cluster provisioned with Terraform
- [ ] S3 or Azure Blob Storage for MLflow artifacts
- [ ] Spot/preemptible instances used for training jobs (cost optimisation)

### CI/CD
- [ ] GitHub Actions: training job → MLflow promotion → build serving API → deploy to staging → manual approval → production
- [ ] Pipeline can be re-run to retrain and redeploy with zero manual steps

### Observability
- [ ] Grafana dashboard: prediction request rate, error rate, model version in use
- [ ] Alert when error rate on predictions > 5%
- [ ] Structured logging from the serving API (include model version in every log line)

### Documentation
- [ ] `README.md`: how to trigger a retraining run, how to promote a model to production
- [ ] Architecture diagram
- [ ] Decision log: why spot instances for training? what happens if a training job is interrupted?

## What makes this option the right choice

Pick this if you're interested in ML engineering, data platforms, or working with data science teams. ML infrastructure is one of the fastest-growing areas in DevOps and very few bootcamp graduates have hands-on experience with it.
