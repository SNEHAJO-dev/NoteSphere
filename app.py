from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import base64
import json
import mimetypes
import uuid
from datetime import date
from werkzeug.utils import secure_filename
from groq import Groq
 
load_dotenv()
 
# ─────────────────────────────────────────────
#  App & Config
# ─────────────────────────────────────────────
app = Flask(__name__)
 
UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB hard cap
 
# Absolute path so it works regardless of working directory
NOTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes.json")
 
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
 
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
 
 
# ─────────────────────────────────────────────
#  Utility Helpers
# ─────────────────────────────────────────────
def allowed_file(filename: str) -> bool:
    """Whitelist-check the file extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )
 
 
def encode_image(image_path: str) -> tuple[str, str]:
    """
    Base64-encode the image and detect its real MIME type.
    Returns (base64_string, mime_type).
    """
    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return b64, mime_type
 
 
def sanitize_field(value: str, max_len: int = 100) -> str:
    """Strip prompt-injection characters and cap length."""
    return value[:max_len].replace('"', "").replace("\n", " ").strip()
 
 
# ─────────────────────────────────────────────
#  AI Formatting
# ─────────────────────────────────────────────
def extract_and_format_notes(image_path: str, subject: str) -> str | None:
    """
    Send the image to Groq vision model.
    Returns formatted notes string, or None on failure.
    """
    b64_image, mime_type = encode_image(image_path)
    safe_subject = sanitize_field(subject)
 
    prompt = f"""You are an academic notes formatter for a student app.
This is a photo of handwritten class notes for the subject: {safe_subject}.
 
Please:
1. Read all the handwritten text carefully
2. Fix any unclear words using context
3. Format the notes into:
   - A clear heading with the subject
   - Bullet points for key concepts
   - Sub-bullets for details
   - Keep it academic and concise
 
Return only the formatted notes, nothing else."""
 
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64_image}"
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            max_tokens=1024,
        )
        return response.choices[0].message.content
    except Exception as e:
        app.logger.error(f"Groq API error: {e}")
        return None
 
 
# ─────────────────────────────────────────────
#  JSON Persistence
# ─────────────────────────────────────────────
def load_notes() -> list:
    """Load all notes from the JSON store. Returns empty list on any failure."""
    if not os.path.exists(NOTES_FILE):
        return []
    try:
        with open(NOTES_FILE, "r") as f:
            data = json.load(f)
            return data.get("notes", [])
    except (json.JSONDecodeError, IOError) as e:
        app.logger.error(f"Failed to load notes: {e}")
        return []
 
 
def save_note(
    student_name: str,
    subject: str,
    image_filename: str,
    formatted_notes: str,
) -> str:
    """
    Append a new note to notes.json and write a companion .txt file.
    Returns the generated note ID.
    """
    notes = load_notes()
    note_id = str(uuid.uuid4())
 
    new_note = {
        "id": note_id,
        "student_name": student_name,
        "subject": subject,
        "image_filename": image_filename,
        "txt_filename": image_filename.rsplit(".", 1)[0] + ".txt",
        "date": str(date.today()),
        "formatted_notes": formatted_notes,
    }
    notes.append(new_note)
 
    # ── Write JSON store ──────────────────────
    with open(NOTES_FILE, "w") as f:
        json.dump({"notes": notes}, f, indent=2)
 
    # ── Write companion .txt file ─────────────
    txt_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        new_note["txt_filename"],
    )
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"Student : {student_name}\n")
        f.write(f"Subject : {subject}\n")
        f.write(f"Date    : {date.today()}\n")
        f.write("─" * 40 + "\n\n")
        f.write(formatted_notes)
 
    return note_id
 
 
# ─────────────────────────────────────────────
#  Web Routes
# ─────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")
 
 
@app.route("/archive")
def archive():
    notes = load_notes()
    grouped: dict = {}
    for note in notes:
        subj = note["subject"]
        grouped.setdefault(subj, []).append(note)
    return render_template("archive.html", grouped=grouped)
 
 
@app.route("/upload", methods=["POST"])
def upload():
    student_name = request.form.get("student_name", "").strip()
    subject      = request.form.get("subject", "").strip()
    image        = request.files.get("notes_image")
 
    # ── Input validation ──────────────────────
    if not student_name or not subject:
        return "Missing name or subject. Please go back and fill in all fields.", 400
 
    if not image or image.filename == "":
        return "No image uploaded. Please go back and try again.", 400
 
    if not allowed_file(image.filename):
        return "Invalid file type. Please upload a JPG, PNG, or WEBP image.", 400
 
    # ── Secure, collision-proof filename ─────
    ext           = image.filename.rsplit(".", 1)[1].lower()
    safe_filename = f"{uuid.uuid4().hex}.{ext}"
    image_path    = os.path.join(app.config["UPLOAD_FOLDER"], safe_filename)
 
    try:
        image.save(image_path)
    except Exception as e:
        app.logger.error(f"Image save failed: {e}")
        return "Failed to save uploaded image. Please try again.", 500
 
    # ── AI processing ─────────────────────────
    formatted_notes = extract_and_format_notes(image_path, subject)
 
    if not formatted_notes:
        # Clean up orphaned image
        if os.path.exists(image_path):
            os.remove(image_path)
        return "AI processing failed. Please try again in a moment.", 500
 
    note_id = save_note(student_name, subject, safe_filename, formatted_notes)
 
    return render_template(
        "result.html",
        student_name=student_name,
        subject=subject,
        filename=safe_filename,
        txt_filename=safe_filename.rsplit(".", 1)[0] + ".txt",
        formatted_notes=formatted_notes,
        note_id=note_id,
    )
 
 
# ─────────────────────────────────────────────
#  JSON API  (for frontend / AJAX access)
# ─────────────────────────────────────────────
@app.route("/api/notes", methods=["GET"])
def api_all_notes():
    """Return every note as JSON."""
    notes = load_notes()
    return jsonify({"count": len(notes), "notes": notes})
 
 
@app.route("/api/notes/subject/<string:subject>", methods=["GET"])
def api_notes_by_subject(subject: str):
    """Return all notes for a given subject (case-insensitive)."""
    notes = load_notes()
    filtered = [n for n in notes if n["subject"].lower() == subject.lower()]
    return jsonify({"subject": subject, "count": len(filtered), "notes": filtered})
 
 
@app.route("/api/notes/<string:note_id>", methods=["GET"])
def api_note_by_id(note_id: str):
    """Return a single note by its UUID."""
    notes = load_notes()
    note  = next((n for n in notes if n.get("id") == note_id), None)
    if not note:
        return jsonify({"error": "Note not found"}), 404
    return jsonify(note)
 
 
@app.route("/api/notes/student/<string:student_name>", methods=["GET"])
def api_notes_by_student(student_name: str):
    """Return all notes for a given student (case-insensitive)."""
    notes    = load_notes()
    filtered = [
        n for n in notes
        if n["student_name"].lower() == student_name.lower()
    ]
    return jsonify({"student": student_name, "count": len(filtered), "notes": filtered})
 
 
# ─────────────────────────────────────────────
#  Error Handlers
# ─────────────────────────────────────────────
@app.errorhandler(413)
def too_large(_):
    return "File too large. Maximum upload size is 10 MB.", 413
 
 
@app.errorhandler(500)
def server_error(_):
    return "Internal server error. Please try again.", 500
 
 
# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────
if __name__ == "_main_":
    app.run(debug=True)