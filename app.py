from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import os
import base64
import json
from datetime import date
from groq import Groq

load_dotenv()

app = Flask(__name__)

UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
NOTES_FILE = "notes.json"
SUBJECTS = ["Humanities", "Physics", "Maths", "ECE"]

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def extract_and_format_notes(image_path, subject):
    base64_image = encode_image(image_path)
    ext = image_path.rsplit(".", 1)[-1].lower()
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
                        "image_url": {
                            "url": f"data:{mime};base64,{base64_image}"
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }],
            max_tokens=1024
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI formatting unavailable: {str(e)}"

def load_notes():
    if not os.path.exists(NOTES_FILE):
        return []
    with open(NOTES_FILE, "r") as f:
        data = json.load(f)
        return data.get("notes", [])

def save_note(student_name, subject, chapter, filename, formatted_notes):
    notes = load_notes()
    new_note = {
        "student_name": student_name,
        "subject": subject,
        "chapter": chapter,
        "filename": filename,
        "date": str(date.today()),
        "formatted_notes": formatted_notes,
        "pinned": False
    }
    notes.append(new_note)
    with open(NOTES_FILE, "w") as f:
        json.dump({"notes": notes}, f, indent=2)

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

    image_path = os.path.join(app.config["UPLOAD_FOLDER"], image.filename)
    image.save(image_path)

    formatted_notes = extract_and_format_notes(image_path, subject)
    save_note(student_name, subject, chapter, image.filename, formatted_notes)

    return render_template("result.html",
        student_name=student_name,
        subject=subject,
        chapter=chapter,
        date_today=str(date.today().strftime("%b %d, %Y")),
        filename=image.filename,
        formatted_notes=formatted_notes,
        image_url=f"/static/uploads/{image.filename}"
    )

@app.route("/edit_note", methods=["POST"])
def edit_note():
    data = request.get_json()
    subject     = data.get("subject")
    index       = data.get("index")
    new_student = data.get("student_name", "").strip()
    new_content = data.get("formatted_notes", "").strip()
    if not subject or index is None or not new_student or not new_content:
        return jsonify({"error": "Missing fields"}), 400
    notes = load_notes()
    subject_notes = [n for n in notes if n["subject"] == subject]
    if index < 0 or index >= len(subject_notes):
        return jsonify({"error": "Note not found"}), 404
    target = subject_notes[index]
    for note in notes:
        if note is target:
            note["student_name"]    = new_student
            note["formatted_notes"] = new_content
            break
    with open(NOTES_FILE, "w") as f:
        json.dump({"notes": notes}, f, indent=2)
    return jsonify({"success": True})

@app.route("/delete_note", methods=["POST"])
def delete_note():
    data    = request.get_json()
    subject = data.get("subject")
    index   = data.get("index")
    if not subject or index is None:
        return jsonify({"error": "Missing fields"}), 400
    notes = load_notes()
    subject_notes = [n for n in notes if n["subject"] == subject]
    if index < 0 or index >= len(subject_notes):
        return jsonify({"error": "Note not found"}), 404
    target = subject_notes[index]
    notes  = [n for n in notes if n is not target]
    with open(NOTES_FILE, "w") as f:
        json.dump({"notes": notes}, f, indent=2)
    return jsonify({"success": True})

@app.route("/pin_note", methods=["POST"])
def pin_note():
    data    = request.get_json()
    subject = data.get("subject")
    index   = data.get("index")
    pinned  = data.get("pinned", True)
    if not subject or index is None:
        return jsonify({"error": "Missing fields"}), 400
    notes = load_notes()
    subject_notes = [n for n in notes if n["subject"] == subject]
    if index < 0 or index >= len(subject_notes):
        return jsonify({"error": "Note not found"}), 404
    target = subject_notes[index]
    for note in notes:
        if note is target:
            note["pinned"] = pinned
            break
    with open(NOTES_FILE, "w") as f:
        json.dump({"notes": notes}, f, indent=2)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(debug=True)