from flask import Flask, render_template, request
from dotenv import load_dotenv
import os
import pytesseract
from PIL import Image
from google import genai
import json
from datetime import date
from flask import jsonify

load_dotenv()

app = Flask(__name__)

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
NOTES_FILE = "notes.json"

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def format_notes_with_ai(raw_text, subject):
    return f"""## {subject} — Class Notes

**Key Concepts:**
- {raw_text[:100]}...

**Summary:**
- Notes extracted and formatted successfully
- AI formatting will be enabled after quota resets

**Topics Covered:**
- See raw OCR text below for full content"""

def load_notes():
    if not os.path.exists(NOTES_FILE):
        return []
    with open(NOTES_FILE, "r") as f:
        data = json.load(f)
        return data.get("notes", [])

def save_note(student_name, subject, filename, formatted_notes, raw_text):
    notes = load_notes()
    new_note = {
        "student_name": student_name,
        "subject": subject,
        "filename": filename,
        "date": str(date.today()),
        "formatted_notes": formatted_notes,
        "raw_text": raw_text,
        "pinned": False  # new field
    }
    notes.append(new_note)
    with open(NOTES_FILE, "w") as f:
        json.dump({"notes": notes}, f, indent=2)


@app.route("/")
def home():
    return render_template("index.html")

@app.route("/archive")
def archive():
    notes = load_notes()
    grouped = {}
    for note in notes:
        subject = note["subject"]
        if subject not in grouped:
            grouped[subject] = []
        grouped[subject].append(note)
    # Fix: count total notes properly
    total_notes = sum(len(v) for v in grouped.values())
    return render_template("archive.html", grouped=grouped, total_notes=total_notes)


@app.route("/upload", methods=["POST"])
def upload():
    student_name = request.form.get("student_name")
    subject = request.form.get("subject")
    image = request.files.get("notes_image")

    if not image or image.filename == "":
        return "No image uploaded. Please go back and try again."

    image_path = os.path.join(app.config["UPLOAD_FOLDER"], image.filename)
    image.save(image_path)

    img = Image.open(image_path)
    extracted_text = pytesseract.image_to_string(img)
    formatted_notes = format_notes_with_ai(extracted_text, subject)

    save_note(student_name, subject, image.filename, formatted_notes, extracted_text)

    return render_template("result.html",
        student_name=student_name,
        subject=subject,
        filename=image.filename,
        extracted_text=extracted_text,
        formatted_notes=formatted_notes
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