"""FR-18 (decisions A1/A2): check a worker's JSON answer and turn it into a clean list of files.

The worker's model answers with JSON like:
    {"files": [{"path": "backend/app.py", "content": "..."}],
     "summary": "what was done", "notes": "assumptions"}

parse_result() either returns a clean copy of that, or raises ResultError with a
message that is stored as the reason the attempt failed. Nothing here touches the
database or the disk, so every rule is easy to test.
"""
import json

MAX_FILES = 20            # files in one answer
MAX_FILE_CHARS = 50_000   # characters in one file (the whole answer is capped at 100 000 by FR-16)

# Decision A2: which folders each task type may write into. Every entry ends with "/"
# so "backendx/app.py" does not count as being inside "backend/".
ALL_FOLDERS = ('backend/', 'frontend/', 'tests/', 'docs/')
ALLOWED_FOLDERS = {
    'REQUIREMENTS': ('docs/contracts/',),
    'BACKEND': ('backend/',),
    'DATABASE': ('backend/',),
    'FRONTEND': ('frontend/',),
    'TESTING': ('tests/',),
    'DOCUMENTATION': ('docs/',),
    'REVIEW': ('docs/reviews/',),
    'INTEGRATION': ALL_FOLDERS,
    'GENERAL': ALL_FOLDERS,
}

# Sent to Ollama as its "format" option, so the model is forced to answer in this shape.
# (Step 2 uses it; you don't need it for parse_result.)
RESULT_SCHEMA = {
    'type': 'object',
    'properties': {
        'files': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
            'required': ['path', 'content']}},
        'summary': {'type': 'string'},
        'notes': {'type': 'string'},
    },
    'required': ['files', 'summary'],
}


class ResultError(ValueError):
    """The answer cannot be used. The message is stored as the reason the attempt failed."""


def allowed_folders(task_type):
    """Return the tuple of folders this task type may write into.

    Unknown types (old tasks, typos) are treated like GENERAL, the same way
    task_weights.capability_weights_for() falls back for unknown types.
    """
    # Hint: one line with dict.get(key, default).
    return ALLOWED_FOLDERS.get(task_type, ALLOWED_FOLDERS['GENERAL'])


def clean_path(path, task_type):
    """Return the path with surrounding spaces removed, or raise ResultError.

    Reject, in this order (each with a message that includes the path):
      1. empty after stripping spaces
      2. contains a backslash "\\"           -> must use forward slashes
      3. starts with "/" or contains ":"      -> must be relative ("C:/x" has a colon)
      4. any part between slashes is "", ".", ".." or ".git"
         ("backend//x" has an empty part, "backend/" ends with one)
      5. does not start with one of allowed_folders(task_type)
         (str.startswith accepts a tuple)
    """
    path = path.strip()
    if not path:
        raise ResultError('A file has an empty path.')
    if '\\' in path:
        raise ResultError(f'Path {path!r} must use forward slashes.')
    if path.startswith('/') or ':' in path:
        raise ResultError(f'Path {path!r} must be relative.')
    for part in path.split('/'):
        if part in ('', '.', '..', '.git'):
            raise ResultError(f'Path {path!r} contains an invalid part {part!r}.')
    folders = allowed_folders(task_type)
    if not path.startswith(folders):
        raise ResultError(f'Path {path!r} is outside the folders allowed for {task_type}: {", ".join(folders)}')
    return path


def parse_result(text, task_type):
    """Turn the model's answer text into {'files': [...], 'summary': str, 'notes': str}.

    Steps:
      1. json.loads(text); if it fails (json.JSONDecodeError) raise ResultError.
      2. The result must be a dict.
      3. data.get('files') must be a non-empty list with at most MAX_FILES items.
      4. For each item:
           - it must be a dict whose 'path' and 'content' are both str
           - path = clean_path(item['path'], task_type)
           - content longer than MAX_FILE_CHARS -> ResultError (exactly the limit is fine)
           - content containing '\\x00' is binary -> ResultError
           - the same cleaned path twice -> ResultError (keep a set of seen paths)
           - append {'path': path, 'content': content} to the clean list
      5. summary and notes default to '' when missing, must be str, and are stripped.
      6. Return the clean dict. Files keep the order they arrived in.

    One bad file rejects the whole answer: we never commit half a result.
    """
        # 1. Read the JSON
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ResultError('The answer is not valid JSON.')

    # 2. Must be a dict
    if not isinstance(data, dict):
        raise ResultError('The answer must be a JSON object.')

    # 3. "files" must be a non-empty list, not too long
    files = data.get('files')
    if not isinstance(files, list) or not files:
        raise ResultError('The answer must contain a non-empty "files" list.')
    if len(files) > MAX_FILES:
        raise ResultError(f'Too many files: {len(files)} (limit {MAX_FILES}).')

    # 4. Check every file
    cleaned, seen = [], set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('content'), str):
            raise ResultError('Each file needs a text "path" and a text "content".')
        path = clean_path(item['path'], task_type)
        content = item['content']
        if len(content) > MAX_FILE_CHARS:
            raise ResultError(f'File {path!r} is too large ({len(content)} characters, limit {MAX_FILE_CHARS}).')
        if '\x00' in content:
            raise ResultError(f'File {path!r} is not a text file.')
        if path in seen:
            raise ResultError(f'File {path!r} appears twice.')
        seen.add(path)
        cleaned.append({'path': path, 'content': content})

    # 5. summary and notes: default to '', must be text
    summary, notes = data.get('summary', ''), data.get('notes', '')
    if not isinstance(summary, str) or not isinstance(notes, str):
        raise ResultError('"summary" and "notes" must be text.')

    # 6. Return the clean result
    return {'files': cleaned, 'summary': summary.strip(), 'notes': notes.strip()}
