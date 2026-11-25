"""FR-20: the integration step that merges every task output of a plan into one runnable project.

When every task of a saved plan is COMPLETED, its FR-18 artifacts are merged in dependency order:

  1. merge      later tasks may replace a file only when they (transitively) depend on the task
                that wrote it; two independent tasks writing different content to one path is a
                conflict, because neither output can be chosen safely
  2. check      Python files must compile, JSON files must parse, local links in HTML files must
                point at merged files, and the project needs at least one start command
                (nothing generated is executed here; FR-21 runs tests)
  3. scaffold   root files only the integrator writes: README.md with run instructions,
                requirements.txt that includes every folder's requirements, integration.json
  4. commit     one "[integration]" commit on the project's FR-18 branch, outside any DB lock

The pure functions (plan_order, merge_outputs, check_files, run_commands, scaffold) take plain
data so every rule is easy to test. integrate_plan() is the database/Git wiring; the step runs
automatically after the plan's last task result and on demand from the plan API.
"""
import hashlib
import heapq
import json
import logging
import posixpath
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .git_repo import GitError, commit_files, git
from .models import CodeArtifact, ProjectIntegration, TaskPlan
from .workspace_api import APIError

logger = logging.getLogger(__name__)

GENERATOR = 'fr20-v1'                      # part of the fingerprint: a new generator re-integrates
README_PATH = 'README.md'
REQUIREMENTS_PATH = 'requirements.txt'
MANIFEST_PATH = 'integration.json'
STALE_RUNNING = timedelta(minutes=10)      # a RUNNING record older than this was interrupted
PYTHON_ENTRY_POINTS = ('backend/app.py', 'backend/main.py', 'backend/server.py', 'backend/run.py')
LINK_PATTERN = re.compile(r'''(?:src|href)\s*=\s*["']([^"']+)["']''', re.IGNORECASE)


# --- Pure functions ----------------------------------------------------------

def plan_order(task_ids, edges):
    """Return task ids with every prerequisite before its dependents; ties keep id order.

    edges: {task_id: set of task ids it depends on}. Plans are validated acyclic (FR-12);
    a cycle here is a programming error and raises ValueError.
    """
    remaining = {task_id: set(edges.get(task_id, ())) & set(task_ids) for task_id in task_ids}
    ready = [task_id for task_id, needs in remaining.items() if not needs]
    heapq.heapify(ready)
    order = []
    while ready:
        current = heapq.heappop(ready)
        order.append(current)
        for task_id, needs in remaining.items():
            if current in needs:
                needs.discard(current)
                if not needs:
                    heapq.heappush(ready, task_id)
    if len(order) != len(remaining):
        raise ValueError('Task dependencies form a cycle.')
    return order


def ancestors(task_id, edges):
    """Every task that task_id depends on, directly or through other tasks."""
    found, pending = set(), list(edges.get(task_id, ()))
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(edges.get(current, ()))
    return found


def merge_outputs(outputs, edges):
    """Merge task outputs given in dependency order.

    outputs: [(task_id, task_key, [{'path', 'content'}, ...]), ...]
    Returns (files, conflicts, overrides):
      files      {path: {'content': str, 'task': task_key, 'taskId': task_id}}
      conflicts  [{'path', 'tasks': [key, key]}]  independent tasks wrote different content
      overrides  [{'path', 'from': key, 'to': key}] a dependent task replaced a prerequisite's file
    """
    files, conflicts, overrides = {}, [], []
    for task_id, task_key, items in outputs:
        before = ancestors(task_id, edges)
        for item in items:
            path, content = item['path'], item['content']
            owner = files.get(path)
            if owner is None or owner['content'] == content:
                files.setdefault(path, {'content': content, 'task': task_key, 'taskId': task_id})
            elif owner['taskId'] in before:
                overrides.append({'path': path, 'from': owner['task'], 'to': task_key})
                files[path] = {'content': content, 'task': task_key, 'taskId': task_id}
            else:
                conflicts.append({'path': path, 'tasks': [owner['task'], task_key]})
    return files, conflicts, overrides


def local_link(html_path, value):
    """The merged-tree path an HTML src/href points at, or None for external/templated links."""
    value = value.strip().split('#')[0].split('?')[0]
    if not value or value.startswith(('/', '//')) or ':' in value or '{' in value or '$' in value:
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(html_path), value))
    return target if target != '.' else None


def check_files(contents):
    """Return a list of problems that stop the merged tree from running. Nothing is executed.

    contents: {path: text}
    """
    problems = []
    for path in sorted(contents):
        text = contents[path]
        if path.endswith('.py'):
            try:
                compile(text, path, 'exec', dont_inherit=True)
            except (SyntaxError, ValueError) as error:
                line = f' (line {error.lineno})' if getattr(error, 'lineno', None) else ''
                problems.append(f'{path}: Python syntax error{line}: {getattr(error, "msg", error)}')
        elif path.endswith('.json'):
            try:
                json.loads(text)
            except ValueError as error:
                problems.append(f'{path}: invalid JSON: {error}')
        elif path.endswith(('.html', '.htm')):
            for value in LINK_PATTERN.findall(text):
                target = local_link(path, value)
                if target is not None and target not in contents and f'{target}/index.html' not in contents:
                    problems.append(f'{path}: links to {value!r}, which no task produced')
    return problems


def npm_script(contents, folder):
    """The npm command that starts folder/package.json, or None."""
    try:
        scripts = json.loads(contents.get(f'{folder}/package.json', '')).get('scripts') or {}
    except (ValueError, AttributeError):
        return None
    if not isinstance(scripts, dict):
        return None
    if 'start' in scripts:
        return f'npm --prefix {folder} start'
    if 'dev' in scripts:
        return f'npm --prefix {folder} run dev'
    return None


def run_commands(contents):
    """Work out how to install, start and test the merged project from the files it contains."""
    install, start, test = [], [], []
    if any(posixpath.basename(path) == 'requirements.txt' for path in contents):
        install.append(f'python -m pip install -r {REQUIREMENTS_PATH}')
    for folder in ('backend', 'frontend'):
        if f'{folder}/package.json' in contents:
            install.append(f'npm --prefix {folder} install')
            command = npm_script(contents, folder)
            if command:
                start.append(command)
    if 'backend/manage.py' in contents:
        start.append('python backend/manage.py runserver')
    else:
        entry = next((path for path in PYTHON_ENTRY_POINTS if path in contents), None)
        if entry:
            start.append(f'python {entry}')
    if 'frontend/index.html' in contents and not npm_script(contents, 'frontend'):
        start.append('python -m http.server 8080 --directory frontend')
    names = [posixpath.basename(path) for path in contents if path.startswith('tests/') and path.endswith('.py')]
    if any(name.startswith('test_') or name.endswith('_test.py') for name in names):
        test.append('python -m pytest tests')
    return {'install': install, 'start': start, 'test': test}


def scaffold(project_name, plan, tasks, files, run, overrides):
    """Return the root files the integrator writes: {path: text}.

    tasks: [{'key', 'title', 'type', 'files': [paths]}] in dependency order.
    The text holds no timestamps, so integrating the same outputs twice gives the same tree.
    """
    contents = {path: entry['content'] for path, entry in files.items()}
    generated = {}
    requirements = sorted(path for path in contents if posixpath.basename(path) == 'requirements.txt')
    if requirements:
        generated[REQUIREMENTS_PATH] = (
            '# Generated by the FR-20 integration step: installs every folder\'s requirements.\n'
            + ''.join(f'-r {path}\n' for path in requirements))

    def block(title, commands):
        """A README section with one shell block, or nothing when there are no commands."""
        return f'\n### {title}\n\n```\n' + '\n'.join(commands) + '\n```\n' if commands else ''

    folders = {}
    for path, entry in sorted(files.items()):
        folders.setdefault(path.split('/', 1)[0], set()).add(entry['task'])
    lines = [f'# {project_name}', '']
    if plan['summary']:
        lines += [plan['summary'], '']
    lines += [f'Assembled by the master from task plan "{plan["title"] or plan["id"]}": '
              f'{len(tasks)} tasks, {len(files)} files. See `{MANIFEST_PATH}` for which task wrote each file.',
              '', '## Run']
    readme = '\n'.join(lines) + '\n'
    readme += block('Install', run['install']) + block('Start', run['start']) + block('Test', run['test'])
    readme += '\n## Layout\n\n' + ''.join(
        f'- `{folder}/` — {sum(1 for p in files if p.startswith(folder + "/"))} files from {", ".join(sorted(keys))}\n'
        for folder, keys in sorted(folders.items()))
    readme += '\n## Tasks\n\n' + ''.join(
        f'{number}. `{task["key"]}` {task["title"]} ({task["type"]}) — '
        f'{len(task["files"])} file{"s" if len(task["files"]) != 1 else ""}\n'
        for number, task in enumerate(tasks, 1))
    generated[README_PATH] = readme

    manifest = {'generator': GENERATOR, 'project': project_name,
                'plan': {'id': plan['id'], 'title': plan['title']},
                'run': run, 'tasks': tasks,
                'files': [{'path': path, 'task': entry['task']} for path, entry in sorted(files.items())],
                'overrides': overrides}
    generated[MANIFEST_PATH] = json.dumps(manifest, indent=2, ensure_ascii=False) + '\n'
    return generated


# --- Database and Git wiring -------------------------------------------------

def integration_json(integration):
    """Serialize an integration for the plan API and the workspace UI."""
    if integration is None:
        return None
    return {'id': str(integration.pk), 'planId': str(integration.plan_id), 'status': integration.status,
            'branch': integration.branch or None, 'commit': integration.commit or None,
            'files': integration.files, 'run': integration.run, 'conflicts': integration.conflicts,
            'problems': integration.problems, 'warnings': integration.warnings,
            'error': integration.error or None, 'createdAt': integration.created_at.isoformat(),
            'completedAt': integration.completed_at.isoformat() if integration.completed_at else None}


def latest_integration(plan):
    """The plan's most recent integration, or None."""
    return plan.integrations.order_by('-created_at', '-pk').first()


def latest_artifacts(tasks):
    """{task_id: newest CodeArtifact}: the last accepted attempt is the one that counts."""
    newest = {}
    for artifact in CodeArtifact.objects.filter(task__in=tasks).order_by('attempt', 'pk'):
        newest[artifact.task_id] = artifact
    return newest


def fingerprint(artifacts):
    """Identify the exact inputs so the same outputs are not integrated twice."""
    raw = GENERATOR + ';' + ';'.join(f'{task_id}:{artifacts[task_id].pk}' for task_id in sorted(artifacts))
    return hashlib.sha256(raw.encode()).hexdigest()


def reusable(integration, key, now):
    """A previous run with the same inputs can be returned instead of integrating again."""
    if integration is None or integration.fingerprint != key:
        return False
    if integration.status == 'RUNNING':
        return now - integration.created_at < STALE_RUNNING
    if integration.status == 'COMPLETED' and settings.GENERATED_REPO_DIR and not integration.commit:
        return False  # committing was off last time and is on now
    return not integration.error  # a Git failure is worth retrying; a merge/check failure is not


def build(plan, tasks, edges, artifacts):
    """Merge, check and scaffold. Returns (record fields, {path: text} to commit)."""
    order = plan_order([task.pk for task in tasks], edges)
    by_id = {task.pk: task for task in tasks}
    outputs = [(task_id, by_id[task_id].plan_key or str(task_id),
                artifacts[task_id].files.get('files', []) if task_id in artifacts else [])
               for task_id in order]
    files, conflicts, overrides = merge_outputs(outputs, edges)
    contents = {path: entry['content'] for path, entry in files.items()}
    run = run_commands(contents)
    problems = check_files(contents)
    if not run['start']:
        problems.append('No start command found: add backend/manage.py, backend/app.py (or main.py, server.py, '
                        'run.py), a package.json with a start script, or frontend/index.html.')
    warnings = [f'Task {key} produced no files.' for task_id, key, items in outputs if not items]
    warnings += [f'{item["path"]}: {item["to"]} replaced the version from {item["from"]}.' for item in overrides]
    task_rows = [{'key': key, 'title': by_id[task_id].title, 'type': by_id[task_id].type,
                  'files': sorted(path for path, entry in files.items() if entry['taskId'] == task_id)}
                 for task_id, key, _ in outputs]
    plan_info = {'id': str(plan.pk), 'title': plan.title, 'summary': plan.summary}
    generated = scaffold(plan.project.name, plan_info, task_rows, files, run, overrides)
    write = {**contents, **generated}
    listing = [{'path': path, 'task': files[path]['task'] if path in files else None,
                'bytes': len(text.encode()), 'sha256': hashlib.sha256(text.encode()).hexdigest()}
               for path, text in sorted(write.items())]
    fields = {'files': listing, 'run': run, 'conflicts': conflicts, 'problems': problems, 'warnings': warnings,
              'status': 'FAILED' if conflicts or problems else 'RUNNING'}
    return fields, write


def plan_tasks(plan):
    """The plan's tasks and their dependency edges {task_id: {prerequisite ids}}."""
    tasks = list(plan.tasks.order_by('pk'))
    edges = {task.pk: set() for task in tasks}
    for task in tasks:
        edges[task.pk].update(task.dependencies.values_list('depends_on_id', flat=True))
    return tasks, edges


def integrate_plan(plan_id):
    """Run FR-20 for one saved plan. Returns (ProjectIntegration, created).

    Raises APIError 409 when the plan is not saved or some tasks have not completed.
    """
    now = timezone.now()
    with transaction.atomic():
        # Locking the plan serialises concurrent integrations of the same plan.
        plan = TaskPlan.objects.select_for_update().select_related('project').get(pk=plan_id)
        if plan.status != 'READY':
            raise APIError('Only a saved plan can be integrated.', 409, 'PLAN_NOT_READY')
        tasks, edges = plan_tasks(plan)
        unfinished = [task.plan_key or str(task.pk) for task in tasks if task.status != 'COMPLETED']
        if not tasks or unfinished:
            raise APIError(f'Every task must be completed before integration. Not completed: '
                           f'{", ".join(unfinished) or "no tasks"}.', 409, 'PLAN_INCOMPLETE')
        artifacts = latest_artifacts(tasks)
        key = fingerprint(artifacts)
        previous = latest_integration(plan)
        if reusable(previous, key, now):
            return previous, False
        fields, write = build(plan, tasks, edges, artifacts)
        if fields['status'] == 'FAILED':
            fields['completed_at'] = now
        integration = ProjectIntegration.objects.create(plan=plan, project=plan.project, fingerprint=key, **fields)
    if integration.status == 'RUNNING':
        commit(integration, write)
    return integration, True


def commit(integration, write):
    """Commit the merged tree to the project's branch (no DB transaction or lock is held)."""
    from .task_results import project_branch  # task_results imports this module for the auto-trigger
    repo_dir = settings.GENERATED_REPO_DIR
    try:
        if repo_dir:
            integration.branch = project_branch(integration.project)
            plan = integration.plan
            message = f'[integration] {plan.title or "Task plan"} (plan {plan.pk}, {len(integration.files)} files)'
            files = [{'path': path, 'content': text} for path, text in sorted(write.items())]
            integration.commit = (commit_files(repo_dir, integration.branch, files, message)
                                  or git(repo_dir, 'rev-parse', integration.branch))
        integration.status = 'COMPLETED'
    except (GitError, OSError, ValueError) as error:
        logger.warning('FR-20 integration commit failed for plan %s: %s', integration.plan_id, error)
        integration.status, integration.error = 'FAILED', f'Commit failed: {error}'[:1000]
    integration.completed_at = timezone.now()
    integration.save(update_fields=['branch', 'commit', 'status', 'error', 'completed_at'])


def integrate_when_complete(plan_id):
    """After-commit hook for task results: integrate once the plan's last task has completed.

    Never raises, so a problem here cannot affect the worker's already-saved result.
    """
    try:
        plan = TaskPlan.objects.filter(pk=plan_id, status='READY', project__archived_at__isnull=True).first()
        if plan is None or not plan.tasks.exists() or plan.tasks.exclude(status='COMPLETED').exists():
            return None
        return integrate_plan(plan_id)[0]
    except Exception:  # noqa: BLE001 - logged; the integration can be retried from the plan API
        logger.exception('FR-20 automatic integration failed for plan %s', plan_id)
        return None
