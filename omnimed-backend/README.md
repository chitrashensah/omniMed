# OmniMed Backend

Flask API for the OmniMed multi-LLM biomedical research framework. It fans a single
question out to seven LLMs in parallel, synthesizes a consensus, parses uploaded
documents, enforces per-model access limits, and logs token usage.

See the [root README](../README.md) for the full overview, architecture, and API reference.

## Quick Start

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py                     # http://localhost:5000
```

Requires a `.env` with the AI provider keys and Supabase credentials (see root README),
and the schema from `supabase_setup.sql` applied to your Supabase project.

## Layout

```
app.py                  # app factory, blueprints, error handlers
routes/
  chat.py               # /ask — parallel model fan-out (core logic)
  compare.py            # /meta-analysis — Gemini consensus synthesis
  upload.py             # /upload — document text extraction
  admin.py              # /admin/granted-users — access control
  auth.py               # @require_auth JWT decorator
services/
  supabase_service.py   # data-access layer (documents, usage, quota, grants)
  gemini_meta.py        # Gemini synthesis call
  pdf_service.py        # PDF/Word/CSV/image → text
prompts/
  biomedical_prompt.txt # baseline biomedical system prompt
supabase_setup.sql      # full schema — run once in Supabase
```
