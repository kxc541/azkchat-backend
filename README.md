# AzkChat — Multi-Tenant RAG Chatbot Platform

A production SaaS platform that lets businesses upload their own documents and instantly deploy an AI-powered chat assistant trained on that content. Customers interact via an embeddable widget or QR code — no setup required on their end.

Built and deployed to production as an MVP to capture real market feedback.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                            CLIENT LAYER                              │
│         React SPA  ·  Embeddable JS Widget  ·  QR Chat Page         │
└──────────────────────────────┬──────────────────────────────────────┘
                                │ HTTPS
                         ┌──────▼──────┐
                         │    Nginx    │  TLS termination · reverse proxy
                         └──────┬──────┘
                                │
                         ┌──────▼──────┐
                         │   FastAPI   │  Auth · rate limiting · Stripe webhooks
                         │   Backend   │  Multi-tenant routing · CORS
                         └───┬─────┬───┘
                             │     │
           ┌─────────────────┘     └──────────────────┐
           │  /upload                        /query   │
           ▼                                          ▼
  ┌─────────────────┐                  ┌──────────────────────┐
  │  Redis + RQ     │                  │     query_utils      │
  │  Job Queue      │                  │  embed → search → LLM│
  └────────┬────────┘                  └──────────┬───────────┘
           │                                      │
           ▼                                      │
  ┌─────────────────┐                             │
  │  RQ Worker      │                             │
  │                 │                             │
  │  load_file()    │                             │
  │  chunk_docs()   │                             │
  │  embed chunks   │──────────────┐              │
  │  store vectors  │              │              │
  └─────────────────┘              ▼              ▼
                           ┌───────────────────────────┐
                           │         Weaviate           │
                           │     Vector Database        │
                           │   DocumentChunk schema     │
                           │   Filtered by ownerId      │
                           └───────────────────────────┘
                                        │
                           ┌────────────▼──────────────┐
                           │        OpenAI APIs         │
                           │   text-embedding-ada-002   │
                           │         GPT-4o             │
                           └───────────────────────────┘

  ┌──────────────────┐     ┌──────────────────────────┐
  │    Firebase      │     │    Watchdog Process       │
  │    Firestore     │◄────│  Reaps stuck ingestion   │
  │  Auth · Files    │     │  jobs after 15 min TTL   │
  └──────────────────┘     └──────────────────────────┘

  ┌──────────────────┐     ┌──────────────────────────┐
  │    AWS           │     │    Stripe                │
  │    S3 (assets)   │     │    Subscriptions         │
  │    CloudWatch    │     │    Webhooks              │
  └──────────────────┘     └──────────────────────────┘
```

---

## How the RAG Pipeline Works

### Upload (write path)
1. Tenant uploads a file (PDF, DOCX, TXT, CSV) via the API
2. File is validated (type, size, one-at-a-time admission guard)
3. A job is queued in Redis via RQ — API returns immediately
4. The worker picks up the job and runs the pipeline:
   - **Load** — extract raw text using LangChain document loaders
   - **Chunk** — split into ~1000-char overlapping chunks with `RecursiveCharacterTextSplitter`
   - **Embed** — each chunk is sent to OpenAI `text-embedding-ada-002` to generate a vector
   - **Store** — vector + metadata stored in Weaviate under the tenant's `ownerId`
   - **Images** — PDF pages are rendered as PNGs via `pdftoppm` for visual answers
5. Firestore tracks job progress at each stage (loading → chunking → embedding → storing → complete)

### Query (read path)
1. User submits a question via widget or API
2. Question is embedded using the same `text-embedding-ada-002` model
3. Weaviate performs a vector similarity search filtered to that tenant's documents
4. Top matching chunks are assembled into a context window
5. GPT-4o generates an answer grounded strictly in that context
6. Relevant page images are returned alongside the answer (filtered by certainty + page proximity)

### Multi-tenancy
Every chunk stored in Weaviate carries an `ownerId` field. All queries are filtered by `ownerId` — tenants can only ever retrieve their own documents. No shared state between tenants at the vector level.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python) |
| Vector DB | Weaviate |
| Embeddings | OpenAI text-embedding-ada-002 |
| LLM | OpenAI GPT-4o |
| Auth | Firebase Authentication |
| Database | Google Firestore |
| Job Queue | Redis + RQ |
| Billing | Stripe |
| File Storage | AWS S3 |
| Logging | structlog → AWS CloudWatch |
| Proxy | Nginx + Let's Encrypt (HTTPS) |
| Frontend | React + Vite |
| CI/CD | CI/CD pipeline (build → test → deploy → regression) |
| Hosting | AWS EC2 + Docker Compose |

---

## Key Features

- **Multi-tenant isolation** — each business's documents and vectors are fully isolated
- **Embeddable widget** — tenants get a JavaScript widget + API key to embed on any site
- **QR code integration** — physical QR codes redirect customers to a branded chat page
- **Async ingestion** — file processing is non-blocking; UI shows live progress per stage
- **Watchdog process** — independent process that reaps stuck ingestion jobs after 15 minutes, preventing infinite UI spinners
- **Rate limiting** — per-user request throttling via slowapi
- **Trial tier** — free users get one file upload; paid users get full access via Stripe checkout
- **Structured logging** — all services emit JSON logs to stdout, collected by CloudWatch agent on EC2
- **Domain allowlist** — widget API keys can be restricted to specific domains

---

## Project Structure

```
backend/
├── app.py                  # FastAPI app — all public routes
├── auth_decorators.py      # require_auth, require_subscription, widget_key_required
├── query_utils.py          # RAG query pipeline (embed → search → GPT-4o)
├── worker.py               # RQ worker — full ingestion pipeline
├── loader_utils.py         # File loading (PDF/DOCX/TXT/CSV) + PDF image extraction
├── embedding_utils.py      # OpenAI embedding wrapper
├── weaviate_utils.py       # Weaviate schema, store, delete
├── watchdog.py             # Ingestion job reaper
├── firebase_admin_init.py  # Firebase Admin SDK initialization
├── cache.py                # In-process TTL cache (admin read buffer)
├── config_uploads.py       # Upload limits and allowed MIME types
├── aws_utils.py            # S3 upload + presigned URL helpers
├── logger.py               # structlog configuration (JSON / dev console)
├── requirements.txt
├── requirements-test.txt   # Lean deps for CI test stage
└── tests/
    ├── conftest.py
    ├── test_auth_decorators.py
    ├── test_aws_utils.py
    ├── test_cache.py
    ├── test_embedding_utils.py
    ├── test_loader_utils.py
    ├── test_query_utils.py
    ├── test_weaviate_utils.py
    ├── test_worker.py
    └── test_watchdog.py
```

---

## CI/CD Pipeline

Every push to `main` runs four stages in sequence:

```
build → test → deploy → regression
```

- **build** — compiles the React frontend (Node 20)
- **test** — runs 79 pytest unit tests in a throwaway Python container; blocks deploy on failure
- **deploy** — SSHs to EC2, pulls latest code, rebuilds Docker containers
- **regression** — runs a smoke test suite against the live production environment

All external dependencies (Firebase, Weaviate, OpenAI, Redis) are mocked in the test stage — no infrastructure needed to run tests.

---

## Running Tests Locally

```bash
pip install -r backend/requirements-test.txt
cd backend && pytest tests/ -v
```

---

## Environment Variables

See `.env.example` for required configuration. Key variables:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `FIREBASE_ADMIN_BASE64` | Base64-encoded Firebase service account JSON |
| `WEAVIATE_URL` | Weaviate instance URL |
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `AWS_S3_BUCKET_NAME` | S3 bucket for asset storage |

---

## Design Decisions

**Why hand-rolled RAG instead of LangChain's full pipeline?**
LangChain's `RetrievalQA` chain was evaluated but the custom image filtering logic (certainty threshold, ±2 page window, dominant filename selection) was easier to maintain as explicit code. LangChain is still used for document loading and text splitting where it genuinely saves time.

**Why Weaviate over pgvector or Pinecone?**
Self-hosted on the same EC2 instance, no per-query cost, and the schema supports the multi-tenant `ownerId` filtering pattern cleanly.

**Why RQ over Celery?**
Simpler operational footprint. RQ with Redis is sufficient for the ingestion workload and avoids Celery's configuration overhead.

**Why a watchdog process instead of RQ's built-in failure handling?**
RQ can mark jobs failed, but Firestore status could still be stuck in `processing` if a worker dies mid-job. The watchdog is an independent process that treats Firestore as the source of truth — it queries all `processing` documents across all tenants in a single collection group query and reaps anything stale after 15 minutes.
