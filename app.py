import sys
sys.path.insert(0, '/var/task/lib/python3.9/site-packages')

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import base64
from datetime import date
from groq import Groq
import cloudinary
import cloudinary.uploader
from pymongo import MongoClient
import certifi

load_dotenv()

app = Flask(__name__)
SUBJECTS = ["Humanities", "Physics", "Maths", "ECE"]

# ── Cloudinary ──────────────────────────────────────────────
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

# ── MongoDB ─────────────────────────────────────────────────
mongo_client = MongoClient(
    os.getenv("MONGODB_URI"),
    serverSelectionTimeoutMS=5000,
    tls=True,
    tlsAllowInvalidCertificates=True
)
db = mongo_client["notesphere"]
notes_col = db["notes"]

# ── Groq ────────────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


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
        return list(notes_col.find({}, {"_id": 0}))
    except Exception as e:
        print(f"load_notes error: {e}")
        return []


def save_note(student_name, subject, chapter, filename, image_url, formatted_notes):
    notes_col.insert_one({
        "student_name": student_name,
        "subject": subject,
        "chapter": chapter,
        "filename": filename,
        "image_url": image_url,
        "date": str(date.today()),
        "formatted_notes": formatted_notes,
        "pinned": False
    })


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

    # Save to MongoDB
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

    subject_notes = list(notes_col.find({"subject": subject}, {"_id": 1}))
    if index < 0 or index >= len(subject_notes):
        return jsonify({"error": "Note not found"}), 404

    notes_col.update_one(
        {"_id": subject_notes[index]["_id"]},
        {"$set": {"student_name": new_student, "formatted_notes": new_content}}
    )
    return jsonify({"success": True})


@app.route("/delete_note", methods=["POST"])
def delete_note():
    data = request.get_json()
    subject = data.get("subject")
    index = data.get("index")

    if not subject or index is None:
        return jsonify({"error": "Missing fields"}), 400

    subject_notes = list(notes_col.find({"subject": subject}, {"_id": 1}))
    if index < 0 or index >= len(subject_notes):
        return jsonify({"error": "Note not found"}), 404

    notes_col.delete_one({"_id": subject_notes[index]["_id"]})
    return jsonify({"success": True})


@app.route("/pin_note", methods=["POST"])
def pin_note():
    data = request.get_json()
    subject = data.get("subject")
    index = data.get("index")
    pinned = data.get("pinned", True)

    if not subject or index is None:
        return jsonify({"error": "Missing fields"}), 400

    subject_notes = list(notes_col.find({"subject": subject}, {"_id": 1}))
    if index < 0 or index >= len(subject_notes):
        return jsonify({"error": "Note not found"}), 404

    notes_col.update_one(
        {"_id": subject_notes[index]["_id"]},
        {"$set": {"pinned": pinned}}
    )
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True)