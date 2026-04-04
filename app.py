from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import base64
from datetime import date
from groq import Groq
import cloudinary
import cloudinary.uploader
import psycopg2
import psycopg2.extras

load_dotenv()

app = Flask(__name__)
SUBJECTS = ["Humanities", "Physics", "Maths", "ECE"]

# ── Cloudinary ──────────────────────────────────────────────
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

# ── Groq ────────────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def get_db():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def extract_and_format_notes(image_bytes, ext, subject):
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    mime = "image/png" if ext == "png" else "image/jpeg"

    prompt = f"""You are an academic notes formatter for a student app.
This is a photo of handwritten or printed class notes for subject: {subject}.

Please:
1. Read ALL the text carefully including handwriting
2. Fix unclear words using context
3. Format into clean structured notes with:
   - A bold heading with the subject name
   - Bullet points for key concepts
   - Sub-bullets for details
   - Keep it academic and concise

Return only the formatted notes in markdown, nothing else."""

    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{base64_image}"}
                    },
                    {"type": "text", "text": prompt}
                ]
            }],
            max_tokens=1024
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI formatting unavailable: {str(e)}"


def load_notes():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM notes ORDER BY id ASC")
        notes = [dict(row) for row in cur.fetchall()]
        cur.close()
        conn.close()
        return notes
    except Exception as e:
        print(f"load_notes error: {e}")
        return []


def save_note(student_name, subject, chapter, filename, image_url, formatted_notes):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO notes (student_name, subject, chapter, filename, image_url, date, formatted_notes, pinned)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (student_name, subject, chapter, filename, image_url, str(date.today()), formatted_notes, False))
    conn.commit()
    cur.close()
    conn.close()


@app.route("/")
def home():
    return render_template("index.html", subjects=SUBJECTS)


@app.route("/status")
def status():
    notes = load_notes()
    today = str(date.today())
    result = {}
    for subject in SUBJECTS:
        result[subject] = any(
            n["subject"] == subject and n["date"] == today
            for n in notes
        )
    return jsonify(result)


@app.route("/archive")
def archive():
    notes = load_notes()
    grouped = {}
    for note in notes:
        subject = note["subject"]
        if subject not in grouped:
            grouped[subject] = []
        grouped[subject].append(note)
    total_notes = sum(len(v) for v in grouped.values())
    return render_template("archive.html", grouped=grouped, total_notes=total_notes)


@app.route("/upload", methods=["POST"])
def upload():
    student_name = request.form.get("student_name")
    subject = request.form.get("subject")
    chapter = request.form.get("chapter", "")
    image = request.files.get("notes_image")

    if not image or image.filename == "":
        return "No image uploaded. Please go back and try again."

    image_bytes = image.read()
    ext = image.filename.rsplit(".", 1)[-1].lower()

    # Upload to Cloudinary
    try:
        upload_result = cloudinary.uploader.upload(
            image_bytes,
            folder="notesphere",
            resource_type="image"
        )
        image_url = upload_result["secure_url"]
    except Exception as e:
        return f"Image upload failed: {str(e)}", 500

    # Extract notes via Groq
    formatted_notes = extract_and_format_notes(image_bytes, ext, subject)

    # Save to Neon
    try:
        save_note(student_name, subject, chapter, image.filename, image_url, formatted_notes)
    except Exception as e:
        return f"Database save failed: {str(e)}", 500

    return render_template("result.html",
        student_name=student_name,
        subject=subject,
        chapter=chapter,
        date_today=str(date.today().strftime("%b %d, %Y")),
        filename=image.filename,
        formatted_notes=formatted_notes,
        image_url=image_url
    )


@app.route("/edit_note", methods=["POST"])
def edit_note():
    data = request.get_json()
    subject = data.get("subject")
    index = data.get("index")
    new_student = data.get("student_name", "").strip()
    new_content = data.get("formatted_notes", "").strip()

    if not subject or index is None or not new_student or not new_content:
        return jsonify({"error": "Missing fields"}), 400

    notes = [n for n in load_notes() if n["subject"] == subject]
    if index < 0 or index >= len(notes):
        return jsonify({"error": "Note not found"}), 404

    note_id = notes[index]["id"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE notes SET student_name=%s, formatted_notes=%s WHERE id=%s
    """, (new_student, new_content, note_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/delete_note", methods=["POST"])
def delete_note():
    data = request.get_json()
    subject = data.get("subject")
    index = data.get("index")

    if not subject or index is None:
        return jsonify({"error": "Missing fields"}), 400

    notes = [n for n in load_notes() if n["subject"] == subject]
    if index < 0 or index >= len(notes):
        return jsonify({"error": "Note not found"}), 404

    note_id = notes[index]["id"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE id=%s", (note_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/pin_note", methods=["POST"])
def pin_note():
    data = request.get_json()
    subject = data.get("subject")
    index = data.get("index")
    pinned = data.get("pinned", True)

    if not subject or index is None:
        return jsonify({"error": "Missing fields"}), 400

    notes = [n for n in load_notes() if n["subject"] == subject]
    if index < 0 or index >= len(notes):
        return jsonify({"error": "Note not found"}), 404

    note_id = notes[index]["id"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE notes SET pinned=%s WHERE id=%s", (pinned, note_id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True)