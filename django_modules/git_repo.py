"""FR-18 (decision C1): commit a task's files to its project's branch in the generated-projects Git repo.

One local Git repo (outside the platform repo) holds every generated project.
Branch "main" stays empty; each project gets its own branch "project/<id>-<name>",
and each completed task becomes one commit on that branch.

Files arrive here already checked by result_format.parse_result(). target_path()
is a second line of defence in case something unchecked ever reaches this module.
"""
import re
import subprocess
import threading
from pathlib import Path

BASE_BRANCH = 'main'
AUTHOR_NAME = 'DevOps Master'
AUTHOR_EMAIL = 'master@devops.local'

_lock = threading.Lock()  # one working folder: only one commit at a time


class GitError(RuntimeError):
    """A git command failed; the message holds git's own error text."""


def git(repo_dir, *args):
    """Run one git command inside repo_dir and return its output (done for you)."""
    done = subprocess.run(['git', *args], cwd=repo_dir, capture_output=True, text=True)
    if done.returncode != 0:
        raise GitError(f'git {" ".join(args)} failed: {done.stderr.strip()}')
    return done.stdout.strip()


def branch_name(project_id, project_name):
    """'Todo App', 12 -> 'project/12-todo-app'.

    1. lower-case the name
    2. replace every run of characters that are not a-z or 0-9 with one '-'  (re.sub)
    3. strip '-' from both ends, cut to 40 characters, strip '-' again
    4. if nothing is left, return 'project/<id>'
    """
    slug = re.sub(r'[^a-z0-9]+', '-', project_name.lower()).strip('-')[:40].rstrip('-')
    return f'project/{project_id}-{slug}' if slug else f'project/{project_id}'


def ensure_repo(repo_dir):
    """Create the repo the first time; do nothing if repo_dir/.git already exists.

    1. create the folder (parents=True, exist_ok=True)
    2. git init -b main
    3. git config user.name / user.email  (the master is the author, not a teammate)
    4. git config core.autocrlf false     (Windows must not change line endings)
    5. git commit --allow-empty -m "Start generated projects"
       (an empty first commit on main, so every project branch has a starting point)
    """
    repo_dir = Path(repo_dir)
    if (repo_dir / '.git').exists():
        return
    repo_dir.mkdir(parents=True, exist_ok=True)
    git(repo_dir, 'init', '-b', BASE_BRANCH)
    git(repo_dir, 'config', 'user.name', AUTHOR_NAME)
    git(repo_dir, 'config', 'user.email', AUTHOR_EMAIL)
    git(repo_dir, 'config', 'core.autocrlf', 'false')
    git(repo_dir, 'commit', '--allow-empty', '-m', 'Start generated projects')


def target_path(repo_dir, relative):
    """Return the absolute path for a file, or raise ValueError if it would land outside the repo.

    Hint: resolve() both the repo folder and (repo folder / relative), then check
    that the repo folder is one of target.parents.
    """
    root = Path(repo_dir).resolve()
    relative = Path(relative)
    target = (root / relative).resolve()
    if relative.anchor or root not in target.parents:
        raise ValueError(f'Path must be inside the repository: {relative}')
    if any(part.lower() == '.git' for part in target.relative_to(root).parts):
        raise ValueError(f'Path points into Git metadata: {relative}')
    return target


def commit_files(repo_dir, branch, files, message):
    """Write files to the branch and commit. Return the new commit id, or None if nothing changed.

    files: [{'path': 'backend/app.py', 'content': '...'}, ...]

    Everything inside `with _lock:`
      1. ensure_repo(repo_dir)
      2. work out ALL target paths first (target_path), before writing anything,
         so one bad path means nothing at all is written
      3. switch to the branch: if `git branch --list <branch>` prints something,
         `git checkout <branch>`; otherwise `git checkout -b <branch> main`
      4. write each file: create parent folders, then
         open(target, 'w', encoding='utf-8', newline='')   # newline='' keeps \\n as \\n on Windows
      5. git add -- <every path>
      6. if `git diff --cached --quiet` exits with 0 nothing changed -> return None
         (use subprocess.run directly: git() raises on exit code 1)
      7. git commit -m message, then return `git rev-parse HEAD`
    """
    with _lock:
        ensure_repo(repo_dir)
        git(repo_dir, 'reset', '--hard', '-q')  # drop anything a failed earlier call left behind
        git(repo_dir, 'clean', '-fdq')
        files = list(files)
        for item in files:
            target_path(repo_dir, item['path'])
        if git(repo_dir, 'branch', '--list', branch):
            git(repo_dir, 'checkout', branch)
        else:
            git(repo_dir, 'checkout', '-b', branch, BASE_BRANCH)

        # Resolve against the checked-out branch before writing any files.
        targets = [(target_path(repo_dir, item['path']), item['content']) for item in files]
        if not targets:
            return None
        for target, content in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('w', encoding='utf-8', newline='') as stream:
                stream.write(content)

        root = Path(repo_dir).resolve()
        paths = [str(target.relative_to(root)) for target, _ in targets]
        git(repo_dir, '--literal-pathspecs', 'add', '--', *paths)
        diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=repo_dir,
                              capture_output=True, text=True)
        if diff.returncode == 0:
            return None
        if diff.returncode != 1:
            raise GitError(f'git diff --cached --quiet failed: {diff.stderr.strip()}')
        git(repo_dir, 'commit', '-m', message)
        return git(repo_dir, 'rev-parse', 'HEAD')
