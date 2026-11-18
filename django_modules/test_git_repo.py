"""FR-18: tests for git_repo (real git in a temporary folder, no database needed)."""
import shutil
import subprocess

import pytest

from .git_repo import BASE_BRANCH, branch_name, commit_files

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='git is not installed')


def git(repo, *args):
    """Run git in the test repo and return its output (for checking results)."""
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def files_on(repo, branch):
    """List the file paths stored in a branch's latest commit."""
    return sorted(git(repo, 'ls-tree', '-r', '--name-only', branch).splitlines())


def f(path, content='x = 1\n'):
    return {'path': path, 'content': content}


# --- branch_name ----------------------------------------------------------

@pytest.mark.parametrize('project_id, name, expected', [
    (12, 'Todo App', 'project/12-todo-app'),
    (3, '  Library   Manager!! v2 ', 'project/3-library-manager-v2'),
    (7, 'Öğrenci Sistemi', 'project/7-renci-sistemi'),
    (5, '!!!', 'project/5'),
    (9, '', 'project/9'),
])
def test_branch_name(project_id, name, expected):
    assert branch_name(project_id, name) == expected


def test_branch_name_is_cut_to_a_short_length():
    name = branch_name(1, 'a' * 100)
    assert name == 'project/1-' + 'a' * 40


def test_branch_name_does_not_end_with_a_dash_after_cutting():
    assert not branch_name(1, 'a' * 39 + ' b').endswith('-')


# --- commit_files ---------------------------------------------------------

def test_first_commit_creates_the_repo_and_the_branch(tmp_path):
    repo = tmp_path / 'generated'
    sha = commit_files(repo, 'project/1-todo', [f('backend/app.py', 'print(1)\n')], '[task 1] Build API')
    assert sha == git(repo, 'rev-parse', 'project/1-todo')
    assert git(repo, 'show', 'project/1-todo:backend/app.py') == 'print(1)'
    assert git(repo, 'log', '-1', '--format=%s', 'project/1-todo') == '[task 1] Build API'


def test_commits_are_made_by_the_master(tmp_path):
    commit_files(tmp_path, 'project/1-todo', [f('backend/app.py')], 'msg')
    assert git(tmp_path, 'log', '-1', '--format=%an', 'project/1-todo') == 'DevOps Master'


def test_nested_folders_are_created(tmp_path):
    commit_files(tmp_path, 'project/1-x', [f('frontend/src/components/Button.jsx')], 'msg')
    assert files_on(tmp_path, 'project/1-x') == ['frontend/src/components/Button.jsx']


def test_second_task_adds_a_second_commit_on_the_same_branch(tmp_path):
    commit_files(tmp_path, 'project/1-x', [f('backend/app.py')], '[task 1] a')
    commit_files(tmp_path, 'project/1-x', [f('tests/test_app.py')], '[task 2] b')
    assert files_on(tmp_path, 'project/1-x') == ['backend/app.py', 'tests/test_app.py']
    assert git(tmp_path, 'log', '--format=%s', BASE_BRANCH + '..project/1-x').splitlines() == ['[task 2] b', '[task 1] a']


def test_projects_do_not_see_each_others_files(tmp_path):
    commit_files(tmp_path, 'project/1-a', [f('backend/a.py')], 'a')
    commit_files(tmp_path, 'project/2-b', [f('backend/b.py')], 'b')
    commit_files(tmp_path, 'project/1-a', [f('backend/a2.py')], 'a2')
    assert files_on(tmp_path, 'project/1-a') == ['backend/a.py', 'backend/a2.py']
    assert files_on(tmp_path, 'project/2-b') == ['backend/b.py']


def test_main_stays_empty(tmp_path):
    commit_files(tmp_path, 'project/1-a', [f('backend/a.py')], 'a')
    assert files_on(tmp_path, BASE_BRANCH) == []


def test_a_later_task_can_overwrite_a_file(tmp_path):
    commit_files(tmp_path, 'project/1-a', [f('backend/app.py', 'old\n')], 'a')
    commit_files(tmp_path, 'project/1-a', [f('backend/app.py', 'new\n')], 'b')
    assert git(tmp_path, 'show', 'project/1-a:backend/app.py') == 'new'


def test_nothing_changed_returns_none_and_makes_no_commit(tmp_path):
    commit_files(tmp_path, 'project/1-a', [f('backend/app.py')], 'a')
    before = git(tmp_path, 'rev-parse', 'project/1-a')
    assert commit_files(tmp_path, 'project/1-a', [f('backend/app.py')], 'again') is None
    assert git(tmp_path, 'rev-parse', 'project/1-a') == before


def test_content_is_stored_exactly(tmp_path):
    content = 'line one\nline two\r\nşğü émoji ✓\n'
    commit_files(tmp_path, 'project/1-a', [f('docs/notes.md', content)], 'a')
    stored = subprocess.run(['git', 'show', 'project/1-a:docs/notes.md'], cwd=tmp_path, capture_output=True, check=True).stdout
    assert stored == content.encode('utf-8')


def test_path_leaving_the_repo_is_refused_and_nothing_is_written(tmp_path):
    repo = tmp_path / 'generated'
    with pytest.raises(ValueError):
        commit_files(repo, 'project/1-a', [f('backend/ok.py'), f('../escaped.py')], 'a')
    assert not (tmp_path / 'escaped.py').exists()
    assert not (repo / 'backend' / 'ok.py').exists()


def test_leftovers_from_a_failed_commit_do_not_leak_into_another_project(tmp_path):
    commit_files(tmp_path, 'project/1-a', [f('backend/a.py')], 'a')
    (tmp_path / 'backend' / 'stray.py').write_text('left over')  # as if the commit crashed after "git add"
    git(tmp_path, 'add', 'backend/stray.py')
    commit_files(tmp_path, 'project/2-b', [f('backend/b.py')], 'b')
    assert files_on(tmp_path, 'project/2-b') == ['backend/b.py']
