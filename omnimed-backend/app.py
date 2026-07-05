from flask import Flask, request
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

CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "OPTIONS"],
)

app.register_blueprint(compare_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(upload_bp)
app.register_blueprint(admin_bp)


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
