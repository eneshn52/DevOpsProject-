"""FR-18: tests for result_format.parse_result (no database needed)."""
import json

import pytest

from .result_format import MAX_FILE_CHARS, MAX_FILES, ResultError, parse_result


def answer(files, **extra):
    """Build a worker answer the way the model would send it: a JSON string."""
    return json.dumps({'files': files, **extra})


def file(path, content='print("hi")\n'):
    return {'path': path, 'content': content}


def rejected(text, task_type='BACKEND'):
    """Return the error message, failing the test if the answer was accepted."""
    with pytest.raises(ResultError) as error:
        parse_result(text, task_type)
    return str(error.value)


# --- Valid answers ---------------------------------------------------------

def test_valid_answer_returns_files_summary_and_notes():
    result = parse_result(answer([file('backend/app.py')], summary='Made the app.', notes='None.'), 'BACKEND')
    assert result == {'files': [{'path': 'backend/app.py', 'content': 'print("hi")\n'}],
                      'summary': 'Made the app.', 'notes': 'None.'}


def test_several_files_keep_their_order():
    paths = ['backend/main.py', 'backend/models.py', 'backend/api/routes.py']
    result = parse_result(answer([file(p) for p in paths], summary='x'), 'BACKEND')
    assert [f['path'] for f in result['files']] == paths


def test_missing_summary_and_notes_become_empty_text():
    result = parse_result(answer([file('backend/app.py')]), 'BACKEND')
    assert result['summary'] == '' and result['notes'] == ''


def test_spaces_around_a_path_are_removed():
    result = parse_result(answer([file('  backend/app.py ')]), 'BACKEND')
    assert result['files'][0]['path'] == 'backend/app.py'


def test_empty_file_content_is_allowed():
    result = parse_result(answer([file('backend/__init__.py', '')]), 'BACKEND')
    assert result['files'][0]['content'] == ''


# --- Shape of the answer ---------------------------------------------------

def test_text_that_is_not_json_is_rejected():
    rejected('Sure! Here is your code: def add(a, b): ...')


def test_json_that_is_not_an_object_is_rejected():
    rejected(json.dumps([file('backend/app.py')]))


def test_missing_files_list_is_rejected():
    rejected(json.dumps({'summary': 'I did it.'}))


def test_files_that_is_not_a_list_is_rejected():
    rejected(json.dumps({'files': file('backend/app.py')}))


def test_empty_files_list_is_rejected():
    rejected(answer([]))


def test_exactly_the_file_limit_is_allowed():
    files = [file(f'backend/m{i}.py') for i in range(MAX_FILES)]
    assert len(parse_result(answer(files), 'BACKEND')['files']) == MAX_FILES


def test_one_file_over_the_limit_is_rejected():
    rejected(answer([file(f'backend/m{i}.py') for i in range(MAX_FILES + 1)]))


@pytest.mark.parametrize('entry', [
    'backend/app.py',                          # a string, not an object
    {'content': 'x'},                          # no path
    {'path': 'backend/app.py'},                # no content
    {'path': 42, 'content': 'x'},              # path not text
    {'path': 'backend/app.py', 'content': 7},  # content not text
])
def test_badly_shaped_file_entries_are_rejected(entry):
    rejected(answer([entry]))


def test_summary_that_is_not_text_is_rejected():
    rejected(answer([file('backend/app.py')], summary=['a', 'b']))


# --- Safe paths ------------------------------------------------------------

@pytest.mark.parametrize('path', [
    '',                          # empty
    '   ',                       # only spaces
    'backend\\app.py',           # Windows slashes
    '/backend/app.py',           # absolute
    'C:/backend/app.py',         # drive letter
    'backend/../../secret.txt',  # climbs out
    'backend/./app.py',          # "." part
    'backend//app.py',           # empty part
    'backend/',                  # a folder, not a file
    'backend/.git/config',       # writes inside a Git folder
    '.git/hooks/pre-commit',     # writes the repo's own Git folder
])
def test_unsafe_paths_are_rejected(path):
    rejected(answer([file(path)]))


# --- Folders per task type (decision A2) -----------------------------------

def test_backend_task_cannot_write_frontend_files():
    message = rejected(answer([file('frontend/app.js')]), 'BACKEND')
    assert 'frontend/app.js' in message  # the stored reason names the bad file


def test_folder_name_that_only_starts_the_same_is_rejected():
    rejected(answer([file('backendx/app.py')]), 'BACKEND')


def test_one_bad_file_rejects_the_whole_answer():
    rejected(answer([file('backend/app.py'), file('frontend/app.js')]), 'BACKEND')


@pytest.mark.parametrize('task_type, path', [
    ('REQUIREMENTS', 'docs/contracts/api.md'),
    ('BACKEND', 'backend/app.py'),
    ('DATABASE', 'backend/schema.sql'),
    ('FRONTEND', 'frontend/src/App.jsx'),
    ('TESTING', 'tests/test_api.py'),
    ('DOCUMENTATION', 'docs/README.md'),
    ('REVIEW', 'docs/reviews/task-12.md'),
    ('INTEGRATION', 'frontend/src/api.js'),
    ('GENERAL', 'tests/test_x.py'),
])
def test_each_type_may_write_its_own_folders(task_type, path):
    assert parse_result(answer([file(path)]), task_type)['files'][0]['path'] == path


@pytest.mark.parametrize('task_type, path', [
    ('REQUIREMENTS', 'docs/README.md'),
    ('DATABASE', 'frontend/schema.sql'),
    ('TESTING', 'backend/app.py'),
    ('REVIEW', 'docs/README.md'),
])
def test_types_cannot_write_outside_their_folders(task_type, path):
    rejected(answer([file(path)]), task_type)


def test_unknown_task_type_is_treated_like_general():
    assert parse_result(answer([file('docs/x.md')]), 'SOMETHING_NEW')['files'][0]['path'] == 'docs/x.md'
    rejected(answer([file('other/x.md')]), 'SOMETHING_NEW')


# --- Content ---------------------------------------------------------------

def test_file_exactly_at_the_size_limit_is_allowed():
    parse_result(answer([file('backend/big.py', 'x' * MAX_FILE_CHARS)]), 'BACKEND')


def test_file_over_the_size_limit_is_rejected():
    rejected(answer([file('backend/big.py', 'x' * (MAX_FILE_CHARS + 1))]))


def test_binary_content_is_rejected():
    rejected(answer([file('backend/logo.png', 'PNG\x00\x01\x02')]))


def test_same_path_twice_is_rejected():
    rejected(answer([file('backend/app.py', 'a'), file('backend/app.py', 'b')]))


def test_same_path_twice_after_trimming_spaces_is_rejected():
    rejected(answer([file('backend/app.py'), file(' backend/app.py')]))
