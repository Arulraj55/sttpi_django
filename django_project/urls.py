from django.urls import path
import app

urlpatterns = [
    path("", app.home),
    path("api/auth/signup", app.signup),
    path("api/auth/login", app.login),
    path("api/auth/logout", app.logout),
    path("api/analyze-syllabus", app.analyze_syllabus),
    path("api/generate-projects", app.generate_projects),
    path("api/history", app.history),
    path("api/history/<int:history_id>", app.delete_history),
    path("api/export-pdf", app.export_pdf),
    path("<path:file_path>", app.frontend_file),
]
