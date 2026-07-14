import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from routes.compare import compare_bp, run_comparison_logic
from routes.chat import chat_bp
from routes.upload import upload_bp
from routes.admin import admin_bp
from routes.auth import require_auth

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max upload

# CORS — restrict to known frontends. Override with ALLOWED_ORIGINS (comma-sep)
# on the server; defaults cover local dev + the production Vercel domain.
_DEFAULT_ORIGINS = "http://localhost:5173,https://omni--med.vercel.app"
_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()]
CORS(
    app,
    resources={r"/*": {"origins": _ALLOWED_ORIGINS}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "OPTIONS"],
)

app.register_blueprint(compare_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(upload_bp)
app.register_blueprint(admin_bp)


@app.route("/health", methods=["GET"])
def health():
    """Lightweight health check. Used by uptime pingers to keep the free-tier
    server warm, and to confirm which provider keys are configured (booleans
    only — never exposes values)."""
    def has(k):
        return bool(os.getenv(k))
    return jsonify({
        "status": "ok",
        "keys_configured": {
            "openai (embeddings + GPT-4o)": has("OPENAI_API_KEY"),
            "anthropic (Claude)":           has("ANTHROPIC_API_KEY"),
            "gemini":                       has("GEMINI_API_KEY"),
            "groq":                         has("GROQ_API_KEY"),
            "deepseek":                     has("DEEPSEEK_API_KEY"),
            "openrouter (qwen)":            has("OPENROUTER_API_KEY"),
            "cohere":                       has("COHERE_API_KEY"),
            "supabase_service":             has("SUPABASE_SERVICE_KEY"),
        },
        "rag_ready": has("OPENAI_API_KEY"),
        "admins_configured": bool(os.getenv("ADMIN_EMAILS") or os.getenv("ADMIN_EMAIL")),
    }), 200


@app.route("/meta-analysis", methods=["POST", "OPTIONS"])
@require_auth
def meta_analysis_fallback():
    return run_comparison_logic()


@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        return app.make_default_options_response()


@app.errorhandler(413)
def too_large(e):
    return {"error": "FILE_TOO_LARGE", "message": "File exceeds 50MB limit"}, 413


@app.errorhandler(404)
def not_found(e):
    return {"error": "NOT_FOUND", "message": str(e)}, 404


@app.errorhandler(500)
def server_error(e):
    return {"error": "SERVER_ERROR", "message": str(e)}, 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
