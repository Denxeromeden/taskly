# Project 3 — Option A: Taskly

## Concept

Deploy and operate a production-grade task management application on cloud infrastructure with full observability, automated deployments, and incident response capability. This is the closest option to what a DevOps engineer does on a real product team.

## The application

**Taskly** is a task management API. You'll use the `taskly` repository as your base:

https://github.com/professordiogodev/taskly

As shipped, it's deliberately minimal: an in-memory task store with no database, no cache, no
metrics, and no structured logging. That's intentional — this option is the broadest in scope of
the three, and "extend it with a proper production stack" means the application layer too, not
just the infrastructure around it. Concretely, before any of the Infrastructure/CI/CD/Observability
requirements below can be satisfied, you'll need to add to the app itself:

- Persistence: swap the in-memory `dict` for real Postgres reads/writes
- Caching: a Redis-backed cache for at least one read path
- A `/metrics` endpoint (Prometheus format)
- Structured JSON logging with a trace ID on every request line
- At least one custom business metric (e.g. `tasks_created_total`)

The container image already runs as a non-root user with a working healthcheck, so you can start
from `docker compose up --build` and go straight to iterating on the app — don't spend time
re-fixing the base container.

You extend and deploy it with a proper production stack:

```
CloudFront / Azure Front Door (CDN + WAF)
        │
   Load Balancer
        │
   EKS / AKS cluster
   ├── taskly-api (FastAPI, 3 replicas, HPA)
   ├── postgres   (RDS / Azure Database, managed)
   └── redis      (ElastiCache / Azure Cache, managed)
        │
   Monitoring stack (same cluster or separate)
   ├── Prometheus + Grafana
   ├── Loki (log aggregation)
   └── Tempo (distributed tracing)
```

## Requirements

### Infrastructure
- [ ] Kubernetes cluster (EKS or AKS) provisioned with Terraform
- [ ] Managed database (RDS PostgreSQL or Azure Database for PostgreSQL) — not a container
- [ ] Managed cache (ElastiCache Redis or Azure Cache for Redis) — not a container
- [ ] Terraform remote state; `terraform destroy` works cleanly
- [ ] At least two environments: staging and production

### CI/CD
- [ ] GitHub Actions pipeline: test → build → scan → push → deploy to staging → manual gate → deploy to production
- [ ] Image tagged with commit SHA
- [ ] Rollback mechanism (documented + tested)

### Observability
- [ ] Prometheus scraping app metrics (`/metrics` endpoint)
- [ ] Grafana dashboard showing: request rate, error rate, p95 latency, active tasks count
- [ ] Loki collecting application logs
- [ ] At least 3 alert rules: one for error rate, one for latency, one for availability
- [ ] Alerts routed to a Slack channel or email via Alertmanager

### Application instrumentation
- [ ] `/metrics` endpoint exposes at least: request count by status code, request duration histogram
- [ ] Structured logging in JSON (one log line per request, including trace ID)
- [ ] At least one custom business metric (e.g., `tasks_created_total`)

### Documentation
- [ ] `README.md`: how to provision, how to deploy, how to access dashboards, how to trigger an alert
- [ ] Incident runbook: what to do when the API error rate exceeds 5%
- [ ] Architecture diagram

## What makes this option the right choice

Pick this if you want to demonstrate end-to-end ownership of a service — from infrastructure to deployment to observability. It's the broadest scope and the closest to a real senior DevOps job description. Because the base app has no persistence, caching, or instrumentation built in, expect to spend real time in application code, not just YAML and Terraform — that's the trade-off for the most complete, portfolio-ready result of the three options.
