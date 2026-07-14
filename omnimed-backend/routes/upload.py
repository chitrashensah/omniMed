import io
import base64
from flask import Blueprint, request, jsonify
import fitz  # PyMuPDF
from routes.auth import require_auth

upload_bp = Blueprint("upload", __name__)

# Upload cap. RAG (chunk + retrieve) means large docs no longer bloat the prompt —
# only relevant passages are sent per query — so we can index whole papers/reviews.
MAX_CHARS = 250_000  # ~60k tokens of source text; RAG retrieves from it efficiently

MIME_MAP = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


@upload_bp.route("/upload", methods=["POST", "OPTIONS"])
@require_auth
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "NO_FILE"}), 400

    filename = file.filename
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    data = file.read()

    # ── PDF ──────────────────────────────────────────────────
    if ext == "pdf":
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            pages = []
            for i, page in enumerate(doc):
                page_text = page.get_text().strip()
                if page_text:
                    pages.append(f"[Page {i+1}]\n{page_text}")
            doc.close()
            text = "\n\n".join(pages)
            if not text.strip():
                return jsonify({
                    "filename": filename,
                    "file_type": "pdf",
                    "text": "",
                    "char_count": 0,
                    "truncated": False,
                    "warning": "SCANNED_PDF",
                    "warning_message": "No text could be extracted — this appears to be a scanned or image-based PDF.",
                }), 200
            truncated = len(text) > MAX_CHARS
            return jsonify({
                "filename": filename,
                "file_type": "pdf",
                "text": text[:MAX_CHARS] + ("\n\n[... document truncated at 60,000 characters to save tokens ...]" if truncated else ""),
                "char_count": len(text),
                "truncated": truncated,
            })
        except Exception as e:
            return jsonify({"error": f"PDF_PARSE_FAILED: {e}"}), 422

    # ── Word (.docx) ──────────────────────────────────────────
    elif ext in ("doc", "docx"):
        try:
            import docx as python_docx
            doc = python_docx.Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            truncated = len(text) > MAX_CHARS
            return jsonify({
                "filename": filename,
                "file_type": "docx",
                "text": text[:MAX_CHARS] + ("\n\n[... document truncated ...]" if truncated else ""),
                "char_count": len(text),
                "truncated": truncated,
            })
        except ImportError:
            return jsonify({"error": "DOCX_SUPPORT_UNAVAILABLE", "message": "Run: pip install python-docx"}), 501
        except Exception as e:
            return jsonify({"error": f"DOCX_PARSE_FAILED: {e}"}), 422

    # ── CSV / TXT / MD ────────────────────────────────────────
    elif ext in ("csv", "txt", "md"):
        text = data.decode("utf-8", errors="replace")
        truncated = len(text) > MAX_CHARS
        return jsonify({
            "filename": filename,
            "file_type": ext,
            "text": text[:MAX_CHARS] + ("\n\n[... truncated ...]" if truncated else ""),
            "char_count": len(text),
            "truncated": truncated,
        })

    # ── Images ───────────────────────────────────────────────
    elif ext in MIME_MAP:
        if len(data) > 20 * 1024 * 1024:  # 20MB image limit
            return jsonify({"error": "IMAGE_TOO_LARGE", "message": "Images must be under 20MB"}), 413
        b64 = base64.b64encode(data).decode("utf-8")
        return jsonify({
            "filename": filename,
            "file_type": "image",
            "mime_type": MIME_MAP[ext],
            "base64": b64,
            "size_bytes": len(data),
        })

    else:
        return jsonify({"error": "UNSUPPORTED_TYPE", "message": f".{ext} files are not supported"}), 415
