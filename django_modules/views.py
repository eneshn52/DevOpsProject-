from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET
from django.db import DatabaseError, connection
from django.http import JsonResponse

from .chat_service import chat_api  # Retain the existing view import and route.


@ensure_csrf_cookie
def index(request):
    """Render the coding workspace and issue its browser CSRF cookie."""
    return render(request, 'index.html')


@require_GET
def health(request):
    """Report application/database readiness without exposing workspace records."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
