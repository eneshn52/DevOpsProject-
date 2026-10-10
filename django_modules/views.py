import json
import urllib.request
import urllib.error
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import ChatSession, ChatMessage


def index(request):
    return render(request, 'index.html')


@csrf_exempt
def chat_api(request):
    """
    Backend relay endpoint that receives chat prompts from the frontend,
    forwards them to local Ollama via Python (preventing CORS issues),
    persists messages to the SQLite database, and returns the AI response.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed. Please use POST.'}, status=405)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON in request.'}, status=400)

    prompt = data.get('prompt', '').strip()
    if not prompt:
        return JsonResponse({'error': 'Prompt cannot be empty.'}, status=400)

    # Retrieve model name and API URL (fallback to sensible Ollama defaults)
    model_name = data.get('model', '').strip() or 'qwen2.5:3b'
    api_url = data.get('api_url', '').strip() or 'http://localhost:11434'
    api_key = data.get('api_key', '').strip()

    # Normalize Ollama URL
    api_url = api_url.rstrip('/')
    ollama_url = f"{api_url}/api/chat"

    # Build Ollama chat payload
    ollama_payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "stream": False
    }

    req_body = json.dumps(ollama_payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
    }
    if api_key:
        headers['Authorization'] = f"Bearer {api_key}"

    req = urllib.request.Request(ollama_url, data=req_body, headers=headers, method='POST')

    try:
        # Send request from Python server to local Ollama server
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode('utf-8'))
            answer = result.get('message', {}).get('content', '')
            if not answer and 'response' in result:
                answer = result.get('response', '')
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode('utf-8'))
            err_msg = err_body.get('error', str(e))
        except Exception:
            err_msg = f"HTTP {e.code}: {e.reason}"
        return JsonResponse({'error': f"Ollama Error ({e.code}): {err_msg}"}, status=502)
    except urllib.error.URLError as e:
        return JsonResponse({
            'error': f"Could not connect to Ollama at {api_url}. Is Ollama running? (Try running 'ollama serve' in your terminal)."
        }, status=502)
    except Exception as e:
        return JsonResponse({'error': f"Unexpected server error: {str(e)}"}, status=500)

    # Persist in SQLite database (ChatMessage & ChatSession) if tables exist
    try:
        session, _ = ChatSession.objects.get_or_create(title="Web Chat")
        ChatMessage.objects.create(
            session=session,
            sender_type='USER',
            content=prompt
        )
        ChatMessage.objects.create(
            session=session,
            sender_type='AGENT',
            category=model_name,
            content=answer
        )
    except Exception:
        # If migrations haven't run yet, ignore DB error so user still gets AI answer
        pass

    return JsonResponse({
        'category': model_name,
        'answer': answer
    })