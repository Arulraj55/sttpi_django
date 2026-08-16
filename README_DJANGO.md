# Syllabus2Project — Django version

This version keeps the existing frontend, SQLite database, Gemini integration, and API paths. The Flask backend has been replaced with Django.

## Run

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py runserver
```

Open: http://127.0.0.1:8000/

Keep your existing `.env` file with `GEMINI_API_KEY=...`.

## Notes

- `app.py` contains the Django request handlers so the backend logic stays close to the original project.
- `django_project/settings.py`, `urls.py`, `asgi.py`, and `wsgi.py` provide the Django project configuration.
- The existing `project_history.db` is retained and continues to use the same SQLite tables.
- The existing frontend files and API URLs are preserved.
- Django signed-cookie sessions are used, so no Django session migration is required.
