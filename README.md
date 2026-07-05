# OmniMed — Multi-LLM Biomedical Research Framework

[![DOI](https://zenodo.org/badge/1289877865.svg)](https://doi.org/10.5281/zenodo.21210783)
&nbsp;[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

OmniMed is an open-source, AI-powered research assistant that queries **seven large
language models in parallel** and helps researchers identify therapeutic microRNA (miRNA)
and non-coding RNA (ncRNA) candidates for cardiovascular disease. Researchers compare the
models side by side, synthesize a consensus answer, and score each model against real
wet-lab outcomes — building a reliability profile that improves over time.

Developed at the **University of South Dakota** under the **UDiscover Undergraduate
Research Grant (2026)**.

> **Faculty Mentor:** Dr. William Chen  ·  **Developer:** Chitrashen Sah

---

## Highlights

- **Multi-LLM querying** — one question, seven models, answers side by side
- **Free + Bring-Your-Own-Key** — five models free for everyone; add your own
  OpenRouter / Anthropic / OpenAI key to unlock unlimited Claude and GPT-4o
- **Consensus synthesis** — Gemini reads every response and produces one unified conclusion
- **Wet-lab validation** — score each model 1–10 against experimental results
- **Live web search** — Claude, GPT-4o, and Gemini search the web for recent literature
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

| Model               | Provider    | Web Search | Access                    |
|---------------------|-------------|:----------:|---------------------------|
| Gemini 2.5 Flash    | Google      | ✅         | Free                      |
| DeepSeek V3         | DeepSeek    | —          | Free                      |
| Llama 3.3 70B       | Groq        | —          | Free                      |
| Qwen 2.5 72B        | OpenRouter  | —          | Free                      |
| Command R+          | Cohere      | —          | Free                      |
| Claude Sonnet 4.6   | Anthropic   | ✅         | 5/day free · key unlocks  |
| GPT-4o              | OpenAI      | ✅         | 5/day free · key unlocks  |

Claude and GPT-4o each get **5 free messages per user per day** on the project's keys.
Beyond that, a user adds their own API key (stored only in their browser) or an admin
grants them unlimited access.

---

## Architecture

```
┌────────────────┐      REST + SSE        ┌────────────────┐
│  React (Vite)  │ ─────────────────────► │  Flask backend │
│                │   /ask  /meta-analysis │                │
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
│       │   └── admin/               # Overview, Reliability, Usage, Users, GrantedUsers
│       ├── components/              # Sidebar, PromptSelector, ApiKeyModal, Logo
│       ├── context/AuthContext.jsx  # auth + granted-access state
│       ├── lib/                     # supabase client, api (SSE), chatStore, apiKeys
│       └── data/builtinPrompts.js   # built-in system prompts
│
└── omnimed-backend/                 # Flask API
    ├── app.py                       # app factory + blueprint registration
    ├── routes/
    │   ├── chat.py                  # /ask — parallel model fan-out (the core)
    │   ├── compare.py               # /meta-analysis — consensus synthesis
    │   ├── upload.py                # /upload — PDF/Word/CSV/image parsing
    │   ├── admin.py                 # /admin/granted-users — access control
    │   └── auth.py                  # JWT verification decorator
    ├── services/
    │   ├── supabase_service.py      # data-access layer
    │   ├── gemini_meta.py           # Gemini synthesis call
    │   └── pdf_service.py           # document text extraction
    ├── prompts/biomedical_prompt.txt
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

### `POST /meta-analysis`
Synthesize every model's answer into one structured consensus.

```jsonc
{
  "model_responses": { "gemini": "…", "claude": "…" },
  "model_labels":    { "gemini": "Gemini 2.5 Flash", "claude": "Claude Sonnet 4.6" }
}
```

### `POST /upload`
Multipart file upload (PDF, Word, CSV, image). Returns extracted text used as context.

### `GET | POST | DELETE /admin/granted-users`
Admin-only. List, grant, or revoke unlimited-access users. Guarded by admin email.

---

## Access Model

| User                    | Claude / GPT-4o                      |
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
