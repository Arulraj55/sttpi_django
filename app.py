import json
import mimetypes
import os
import sqlite3
import urllib.error
import urllib.request
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "project_history.db"


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_env()


def db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with db() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          topic TEXT NOT NULL,
          interest TEXT NOT NULL,
          level TEXT NOT NULL,
          ideas_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)


initialize_database()


def gemini(prompt, model="gemini-2.5-flash"):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=70) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini request failed: {message}") from error
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as error:
        raise RuntimeError("Gemini returned an unexpected response.") from error


def identity(request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with db() as connection:
        return connection.execute(
            "SELECT id, name, email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def frontend_file(request, file_path):
    """Serve the existing frontend files without changing their paths."""
    requested = (ROOT / file_path).resolve()
    if ROOT not in requested.parents and requested != ROOT:
        return HttpResponse("Not found.", status=404)
    if not requested.is_file():
        return HttpResponse("Not found.", status=404)
    content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
    return FileResponse(open(requested, "rb"), content_type=content_type)


def home(request):
    return frontend_file(request, "index.html")


@csrf_exempt
@require_http_methods(["POST"])
def signup(request):
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    name = payload.get("name", "").strip()
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if len(name) < 2 or "@" not in email or len(password) < 6:
        return JsonResponse({"error": "Enter a name, a valid email, and a password of at least 6 characters."}, status=400)
    try:
        with db() as connection:
            cursor = connection.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (name, email, generate_password_hash(password), datetime.now(timezone.utc).isoformat()),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return JsonResponse({"error": "An account with this email already exists. Please log in."}, status=409)
    request.session["user_id"] = user_id
    return JsonResponse({"user": {"id": user_id, "name": name, "email": email}})


@csrf_exempt
@require_http_methods(["POST"])
def login(request):
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    with db() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE email = ?",
            (payload.get("email", "").strip().lower(),),
        ).fetchone()
    if not user or not check_password_hash(user["password_hash"], payload.get("password", "")):
        return JsonResponse({"error": "Incorrect email or password."}, status=401)
    request.session["user_id"] = user["id"]
    return JsonResponse({"user": {"id": user["id"], "name": user["name"], "email": user["email"]}})


@csrf_exempt
@require_http_methods(["POST"])
def logout(request):
    request.session.flush()
    return JsonResponse({"logged_out": True})


@csrf_exempt
@require_http_methods(["POST"])
def analyze_syllabus(request):
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    text = payload.get("text", "").strip()
    if len(text) < 12:
        return JsonResponse({"error": "We could not find enough readable text in this file."}, status=400)
    prompt = f'''You extract a college syllabus into chapters and topics. Return ONLY valid JSON with this exact structure:
{{"chapters":[{{"name":"Chapter name","topics":[{{"name":"Topic name","difficulty":"Beginner|Intermediate|Advanced"}}]}}]}}
Rules: Keep 2 to 8 chapters. Each chapter should have 3 to 8 concise topics. Use only information supported by the syllabus. Assign difficulty thoughtfully. Syllabus text:\n{text[:50000]}'''
    try:
        data = gemini(prompt, "gemini-2.5-flash")
        if not isinstance(data.get("chapters"), list):
            raise RuntimeError("Gemini did not return chapters.")
        return JsonResponse(data)
    except RuntimeError as error:
        return JsonResponse({"error": str(error)}, status=502)


@csrf_exempt
@require_http_methods(["POST"])
def generate_projects(request):
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    required = ["chapter", "topic", "topicDifficulty", "interest", "level"]
    if not all(payload.get(key) for key in required):
        return JsonResponse({"error": "Missing project preferences."}, status=400)
    prompt = f'''Create exactly 3 personalized, practical college project ideas. The student studied chapter "{payload['chapter']}" and selected topic "{payload['topic']}" (topic difficulty: {payload['topicDifficulty']}). Their human interest/hobby is "{payload['interest']}". Their desired project level is "{payload['level']}". Return ONLY valid JSON in this exact shape:
{{"ideas":[{{"title":"...","explanation":"2 concise sentences","realWorldApplication":"1 concise sentence","techStack":["...","...","...","..."]}}]}}
Make the hobby meaningfully influence every idea. Do not give generic ideas. Use realistic technology stacks for a college student.'''
    try:
        data = gemini(prompt, "gemini-2.5-flash")
        if not isinstance(data.get("ideas"), list):
            raise RuntimeError("Gemini did not return project ideas.")
        return JsonResponse(data)
    except RuntimeError as error:
        return JsonResponse({"error": str(error)}, status=502)


@csrf_exempt
def history(request):
    user = identity(request)

    if not user:
        return JsonResponse(
            {"error": "Please log in."},
            status=401
        )

    # GET = retrieve history
    if request.method == "GET":
        with db() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM project_history
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user["id"],),
            ).fetchall()

        return JsonResponse({
            "items": [
                {
                    **dict(row),
                    "ideas": json.loads(row["ideas_json"])
                }
                for row in rows
            ]
        })

    # POST = save history
    if request.method == "POST":
        try:
            payload = json.loads(
                request.body.decode("utf-8")
            ) if request.body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}

        if not isinstance(payload.get("ideas"), list):
            return JsonResponse(
                {"error": "No ideas to save."},
                status=400
            )

        with db() as connection:
            connection.execute(
                """
                INSERT INTO project_history
                (
                    user_id,
                    topic,
                    interest,
                    level,
                    ideas_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    payload.get("topic", "Topic"),
                    payload.get("interest", "Interest"),
                    payload.get("level", "Level"),
                    json.dumps(payload["ideas"]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        return JsonResponse({"saved": True})

    return JsonResponse(
        {"error": "Method not allowed."},
        status=405
    )


@csrf_exempt
@require_http_methods(["DELETE"])
def delete_history(request, history_id):
    user = identity(request)
    if not user:
        return JsonResponse({"error": "Please log in."}, status=401)
    with db() as connection:
        cursor = connection.execute(
            "DELETE FROM project_history WHERE id = ? AND user_id = ?",
            (history_id, user["id"]),
        )
    if not cursor.rowcount:
        return JsonResponse({"error": "Project not found."}, status=404)
    return JsonResponse({"deleted": True})


@csrf_exempt
@require_http_methods(["POST"])
def export_pdf(request):
    user = identity(request)
    if not user:
        return JsonResponse({"error": "Please log in."}, status=401)
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    ideas = payload.get("ideas")
    if not isinstance(ideas, list) or not ideas:
        return JsonResponse({"error": "No project ideas available for export."}, status=400)
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=46, bottomMargin=46)
    styles = getSampleStyleSheet()
    story = [Paragraph("Syllabus Topics To Project Ideas — Gemini Project Ideas", styles["Title"]), Spacer(1, 12)]
    story.append(Paragraph(f"Prepared for: {user['name']}", styles["Normal"]))
    story.append(Paragraph(f"Topic: {payload.get('topic', 'Selected topic')} · Interest: {payload.get('interest', 'Selected interest')} · Level: {payload.get('level', 'Selected level')}", styles["Normal"]))
    story.append(Spacer(1, 16))
    for index, idea in enumerate(ideas, start=1):
        story.append(Paragraph(f"{index}. {idea.get('title', 'Project idea')}", styles["Heading2"]))
        story.append(Paragraph(f"<b>What is this project?</b><br/>{idea.get('explanation', '')}", styles["BodyText"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>Real-time applications</b><br/>{idea.get('realWorldApplication', '')}", styles["BodyText"]))
        story.append(Spacer(1, 6))
        stack = ", ".join(idea.get("techStack", []))
        story.append(Paragraph(f"<b>Recommended tech stack</b><br/>{stack}", styles["BodyText"]))
        story.append(Spacer(1, 16))
    document.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="gemini-project-ideas.pdf"'
    return response
