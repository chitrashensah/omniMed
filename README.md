# OmniMed — Multi-LLM Biomedical Research Framework

[![DOI](https://zenodo.org/badge/1289877865.svg)](https://doi.org/10.5281/zenodo.21210783)
&nbsp;[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

OmniMed is an open-source, AI-powered research assistant that queries **up to five of 23
large language models in parallel** and helps researchers identify therapeutic microRNA (miRNA)
and non-coding RNA (ncRNA) candidates for cardiovascular disease. Researchers compare the
models side by side, synthesize a consensus answer, and score each model against real
wet-lab outcomes — building a reliability profile that improves over time.

Developed at the **University of South Dakota** under the **UDiscover Undergraduate
Research Grant (2026)**.

> **Faculty Mentor:** Dr. William Chen  ·  **Developer:** Chitrashen Sah

---

## Highlights

- **Multi-LLM querying** — one question, up to five models, answers side by side
- **Free + Bring-Your-Own-Key** — Gemini, DeepSeek, Qwen, GPT-OSS and Cohere are free; Claude and
  GPT get 5 messages a day, unlimited with your own Anthropic / OpenAI key
- **Literature grounding** — per box, give a model papers from PubMed or Europe PMC in a date range
- **Citation checks** — every PMID, DOI and author–year citation verified (fabricated, off-topic,
  retracted), using the open full text when the abstract is not enough
- **Consensus verdict and AI scores** — a separate model ranks candidates by independent evidence,
  and scores each answer blind to which model wrote it
- **Wet-lab validation** — score each model 1–10 against experimental results
- **Custom prompts** — built-in research prompts plus your own, saved per mode
- **Document uploads** — PDFs, Word, CSVs, and images reasoned over in-chat
- **Admin dashboard** — model reliability, token usage & cost, users, and access control
- **Cross-device history** — conversations persist in Supabase, scoped per user

---

## Tech Stack

| Layer     | Technology                                   |
|-----------|----------------------------------------------|
| Frontend  | React 18, Vite, React Router                 |
| Backend   | Python, Flask, Flask-CORS                    |
| Database  | Supabase (PostgreSQL + Row Level Security)   |
| Auth      | Supabase Auth (Email + Google OAuth)         |
| Charts    | Recharts · **Export:** SheetJS (xlsx)        |

### Models

23 models from seven providers; any five at a time, like the site's five boxes. The list
lives in `omnimed-frontend/src/data/models.js` (display) and `omnimed-backend/routes/chat.py`
(`MODELS`, how each is called). Every id was checked against the provider's live model list.

| Provider | Models | Access |
|---|---|---|
| Google | Gemini 3.8, 3.7, 3.6, 2.5 Flash | Free |
| DeepSeek | DeepSeek V4 Pro | Free |
| Qwen | Qwen 3.8 Flash, 3.8 Max, 3.7 Plus, 3.6 Flash (OpenRouter) · Qwen 3.8 27B (Groq) | Free (OpenRouter needs project credit) |
| Groq | GPT-OSS 120B | Free |
| Cohere | Command A+ | Free |
| Anthropic | Claude Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.1, Opus 5, Sonnet 5 | 5/day free · own key unlocks |
| OpenAI | GPT-6.1 Sol, GPT-6 Astra, GPT-6 Luna, GPT-5.5, GPT-5.4 mini | 5/day free · own key unlocks |

Claude and GPT models each get **5 free messages per user per day** on the project's keys.
Beyond that, a user adds their own API key (stored only in their browser) or an admin
grants them unlimited access. Retired models (e.g. Claude Sonnet 4.6, GPT-4o) are no longer
offered but still named correctly in old chats and statistics.

---

## Architecture

The full design (components, data flow, the three validation layers, data model,
security and limits) is in [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md).

```
┌────────────────┐      REST + SSE        ┌────────────────┐
│  React (Vite)  │ ─────────────────────► │  Flask backend │
│                │   /ask  /agent/*       │                │
│  • Chat UI     │   /upload  /admin/*    │  • model fan-out (7 LLMs, parallel)
│  • Admin panel │                        │  • web search, prompt caching
│  • Prompt/Key  │                        │  • per-model quota + access gating
│    management  │                        │  • token usage logging
└───────┬────────┘                        └───────┬────────┘
        │                                         │
        │  direct (RLS-scoped, user JWT)          │  service-role writes
        └──────────────► ┌──────────────┐ ◄───────┘
                         │   Supabase    │
                         │  Postgres+RLS │  sessions · messages · documents
                         │   + Auth      │  usage_logs · user_quota · granted_users
                         └───────────────┘
```

The frontend talks to **Supabase directly** (under Row Level Security) for CRUD it owns —
conversations, messages, custom prompts, and wet-lab scores. It calls the **Flask backend**
only for what needs server-side secrets and orchestration: fanning a question out to all
models, synthesizing a conclusion, file parsing, and admin operations.

---

## Project Structure

```
omnimed/
├── omnimed-frontend/                # React app
│   └── src/
│       ├── pages/                   # Chat, Landing, Login, Admin
│       │   └── admin/               # Overview, Reliability, Usage, Users, GrantedUsers, ResearchReport
│       ├── components/              # Sidebar, PromptSelector, ApiKeyModal, Logo
│       ├── context/AuthContext.jsx  # auth + granted-access state
│       ├── lib/                     # supabase client, api (SSE), chatStore, apiKeys, verifyStore
│       └── data/
│           ├── models.js            # every model offered (and retired ones, for labels)
│           └── builtinPrompts.js    # selectable system prompts
│
└── omnimed-backend/                 # Flask API
    ├── app.py                       # app setup + blueprint registration
    ├── routes/
    │   ├── chat.py                  # /ask — model table, parallel fan-out, literature grounding
    │   ├── agent.py                 # /agent/council, /agent/score-all — verdict and AI scores
    │   ├── literature.py            # /literature/* — search, citation and uncited-claim checks
    │   ├── upload.py                # /upload — PDF/Word/CSV/image parsing
    │   ├── admin.py                 # /admin/* — access control, research report
    │   └── auth.py                  # JWT verification decorator
    ├── services/
    │   ├── literature_service.py    # PubMed / Europe PMC / Crossref / ClinicalTrials.gov
    │   ├── agent_service.py         # judge and council prompts, reply parsing
    │   ├── supabase_service.py      # data-access layer
    │   ├── rag_service.py           # document chunking
    │   ├── embedding_service.py     # embeddings for document search
    │   └── stats_service.py         # reliability statistics
    └── supabase_setup.sql           # full schema — run once in Supabase
```

---

## Setup

### Prerequisites
- Node.js 18+
- Python 3.11+
- A Supabase project
- API keys for the models you want to serve (at minimum a Gemini key)

### 1. Database

Open **Supabase → SQL Editor**, paste the entire `omnimed-backend/supabase_setup.sql`,
and run it. It is idempotent (safe to re-run) and creates every table, index, RLS
policy, and function the app needs.

> **Set your admin email first.** The schema grants full data access to one admin
> account. Replace `chitrashenshah@gmail.com` throughout `supabase_setup.sql` with your
> own email, and use the same value for `ADMIN_EMAIL` (backend) and `VITE_ADMIN_EMAIL`
> (frontend) below.

### 2. Backend

```bash
cd omnimed-backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copy `omnimed-backend/.env.example` to `omnimed-backend/.env` and fill it in:

```env
# AI providers (add only the ones you use)
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
GROQ_API_KEY=...
DEEPSEEK_API_KEY=...
OPENROUTER_API_KEY=...
COHERE_API_KEY=...

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_anon_key
SUPABASE_SERVICE_KEY=your_service_role_key   # required for server-side writes

# Admin account (unlimited access + dashboard)
ADMIN_EMAIL=you@example.com
```

```bash
python app.py            # http://localhost:5000
```

> `SUPABASE_SERVICE_KEY` (Project Settings → API → service_role) lets the backend write
> usage logs, documents, and session turns past RLS. Keep it **backend-only** — never
> expose it to the frontend.

### 3. Frontend

```bash
cd omnimed-frontend
npm install
```

Copy `omnimed-frontend/.env.example` to `omnimed-frontend/.env` and fill it in:

```env
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your_anon_key
VITE_ADMIN_EMAIL=you@example.com   # same as backend ADMIN_EMAIL
```

```bash
npm run dev              # http://localhost:5173
```

### Run both at once

From the repo root:

```bash
npm install              # installs `concurrently`
npm start                # backend + frontend together
```

---

## API Reference

All endpoints require a Supabase access token as `Authorization: Bearer <token>`
(the frontend attaches it automatically).

### `POST /ask`
Fan one question out to several models. Streams each result over Server-Sent Events
(`stream: true`) so panels render as each model finishes.

```jsonc
{
  "message": "Which miRNAs target ferroptosis in I/R injury?",
  "models": ["gemini", "claude", "deepseek"],
  "mode": "biomedical",                 // "normal" | "biomedical"
  "session_id": "<uuid>",
  "histories": { "gemini": [], "claude": [] },
  "system_prompt": "…",                 // resolved from the prompt selector
  "user_keys": { "openrouter": "sk-or-…" },  // from the user's browser, optional
  "attachments": [],
  "stream": true
}
```

### `POST /agent/council`
One structured verdict across every model's answer to a question: headline, ranked candidates (checked against PubMed and ClinicalTrials.gov), disagreements.

### `POST /agent/score-all`
Scores every answer to the latest question with a judge model from a different provider, blind to which model wrote each answer.

### `POST /upload`
Multipart file upload (PDF, Word, CSV, image). Returns extracted text used as context.

### `GET | POST | DELETE /admin/granted-users`
Admin-only. List, grant, or revoke unlimited-access users. Guarded by admin email.

---

## Access Model

| User                    | Claude / GPT models                  |
|-------------------------|--------------------------------------|
| Admin                   | Unlimited (backend keys)             |
| Admin-granted user      | Unlimited (backend keys)             |
| Regular user            | 5 messages/day **per model**         |
| User with own API key   | Unlimited (their key, browser-only)  |

Quota is tracked **per user per model per day** in Postgres, with an in-memory cache so
granted and over-limit users incur no repeat database calls.

---

## Security Notes

- User API keys live **only in the browser's localStorage** and are sent per-request —
  never persisted on the server.
- Every Supabase table uses **Row Level Security** scoping data to its owner; the admin
  email additionally sees aggregate data through `SECURITY DEFINER` functions.
- The Supabase **service-role key** is backend-only.

---

## Research Context

This project is funded by the University of South Dakota UDiscover Undergraduate Research
Scholarship (2026), supervised by Dr. William Chen. It aims to compress days of manual
literature review into minutes by combining multiple LLMs with wet-lab validation —
measuring how accurately each model interprets experimental data and building a reliability
score from real laboratory outcomes.

---

## Citing OmniMed

If you use OmniMed in your research, please cite it. Citation metadata lives in
[`CITATION.cff`](CITATION.cff) — GitHub renders a **"Cite this repository"** button from it.

The archived release on [Zenodo](https://zenodo.org) has a permanent **DOI**, which makes
citations trackable by Google Scholar and Crossref:

```
Sah, C. (2026). OmniMed: A Multi-LLM Framework for Identifying Therapeutic
microRNAs from Scientific Literature. Zenodo. https://doi.org/10.5281/zenodo.21210783
```

> **DOI:** [10.5281/zenodo.21210783](https://doi.org/10.5281/zenodo.21210783) (concept DOI —
> always resolves to the latest release).

---

## License

Released under the [MIT License](LICENSE).
