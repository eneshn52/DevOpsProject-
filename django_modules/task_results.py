"""FR-18: turn a worker's finished answer into stored files and a commit on the project's branch.

Flow (called from task_execution):
  start()  -> needs_files(task)? add result_instructions() to the prompt and RESULT_SCHEMA to the job
  result() -> needs_files(task)? check_answer() -> valid: save_artifact() + commit after the DB commit
                                                -> invalid: the attempt is FAILED with the reason
"""
import hashlib
import json
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .git_repo import GitError, branch_name, commit_files
from .models import CodeArtifact, Project, Task, TaskDependency, TaskOutputConflict, TaskPlan
from .result_format import RESULT_SCHEMA, ResultError, allowed_folders, parse_result

logger = logging.getLogger(__name__)


def needs_files(task):
    """Project tasks answer with files (decision A1). FR-9 benchmark tasks and tasks
    without a project keep their plain-text answers."""
    return task.project_id is not None and not hasattr(task, 'benchmark_case')


def job_format(task):
    """The Ollama "format" value for this task's job, or None for plain text."""
    return RESULT_SCHEMA if needs_files(task) else None


def result_instructions(task):
    """Text added to the prompt so the model knows the answer shape and its folders."""
    folders = ', '.join(allowed_folders(task.type))
    return (
        '\n\nAnswer with JSON only, in exactly this shape:\n'
        '{"files": [{"path": "<relative/path>", "content": "<the complete file>"}], '
        '"summary": "<one or two sentences>", "notes": "<assumptions, open points>"}\n'
        f'Put every file under: {folders}\n'
        'Use relative paths with forward slashes. Give complete file contents, not fragments.'
    )


def check_answer(task, output):
    """Return (parsed, None) for a usable answer or (None, reason) for an unusable one."""
    try:
        return parse_result(output, task.type), None
    except ResultError as error:
        return None, f'Invalid result: {error}'


def check_resolver_answer(task, parsed):
    """Require a resolver to return only the complete file it was asked to merge."""
    conflict = TaskOutputConflict.objects.filter(resolver_task=task).order_by('-created_at', '-pk').first()
    if not conflict:
        return 'Invalid resolver task: no output conflict is attached.'
    if len(parsed['files']) != 1 or parsed['files'][0]['path'] != conflict.path:
        return f'Invalid resolver result: return exactly the complete file {conflict.path!r}.'
    return None


def save_artifact(task, agent, attempt, parsed):
    """Store the checked files (caller is inside the result transaction) and schedule the commit."""
    artifact = CodeArtifact.objects.create(
        task=task, agent=agent, attempt=attempt, notes=parsed['notes'],
        files={'files': parsed['files'], 'summary': parsed['summary'],
               'branch': '', 'commit': None, 'commit_error': ''})
    if task.project_id:
        if task.plan_id:
            TaskPlan.objects.select_for_update().get(pk=task.plan_id)
        else:
            Project.objects.select_for_update().get(pk=task.project_id)
        if TaskOutputConflict.objects.filter(resolver_task=task).exists():
            resolve_conflicts(task, artifact)
        else:
            detect_output_conflicts(artifact)
    # Git runs only after the database change is final, and never while rows are locked.
    transaction.on_commit(lambda: commit_artifact(artifact.pk))
    return artifact


def conflict_scope(task):
    return f'plan:{task.plan_id}' if task.plan_id else f'project:{task.project_id}'


def artifact_file(artifact, path_key):
    for item in artifact.files.get('files', []):
        if item['path'].casefold() == path_key:
            return item
    return None


def dependency_map(task):
    edges = TaskDependency.objects.filter(task__project_id=task.project_id, task__plan_id=task.plan_id)
    if task.plan_id is None:
        edges = edges.filter(task__plan__isnull=True)
    result = {}
    for task_id, depends_on_id in edges.values_list('task_id', 'depends_on_id'):
        result.setdefault(task_id, set()).add(depends_on_id)
    return result


def tasks_are_related(first_id, second_id, prerequisites):
    def is_ancestor(task_id, ancestor_id):
        pending, visited = [task_id], set()
        while pending:
            current = pending.pop()
            for dependency_id in prerequisites.get(current, ()):
                if dependency_id == ancestor_id:
                    return True
                if dependency_id not in visited:
                    visited.add(dependency_id)
                    pending.append(dependency_id)
        return False

    return is_ancestor(first_id, second_id) or is_ancestor(second_id, first_id)


def conflict_context(conflict):
    versions = []
    for artifact in conflict.artifacts.select_related('task').order_by('task_id', 'attempt', 'pk'):
        item = artifact_file(artifact, conflict.path_key)
        if item:
            versions.append({'task': artifact.task.title, 'task_id': artifact.task_id,
                             'path': item['path'], 'content': item['content']})
    return json.dumps({'conflicting_path': conflict.path, 'versions': versions},
                      ensure_ascii=False, separators=(',', ':'))


def add_conflict_dependency(resolver, source_task):
    if resolver.pk != source_task.pk:
        TaskDependency.objects.get_or_create(task=resolver, depends_on=source_task)


def task_depends_on(task_id, prerequisite_id, prerequisites):
    pending, visited = [task_id], set()
    while pending:
        current = pending.pop()
        for dependency_id in prerequisites.get(current, ()):
            if dependency_id == prerequisite_id:
                return True
            if dependency_id not in visited:
                visited.add(dependency_id)
                pending.append(dependency_id)
    return False


def gate_downstream_tasks(resolver, prerequisite_ids):
    """Make not-yet-running descendants wait for the merge resolver."""
    prerequisites = dependency_map(resolver)
    resolver_ids = set(TaskOutputConflict.objects.filter(scope_key=conflict_scope(resolver))
                       .exclude(resolver_task__isnull=True)
                       .values_list('resolver_task_id', flat=True))
    descendants = Task.objects.filter(
        project_id=resolver.project_id, plan_id=resolver.plan_id, status__in=['PENDING', 'ASSIGNED']
    )
    if resolver.plan_id is None:
        descendants = descendants.filter(plan__isnull=True)
    for downstream in descendants:
        if downstream.pk == resolver.pk or downstream.pk in resolver_ids:
            continue
        if any(task_depends_on(downstream.pk, prerequisite_id, prerequisites)
               for prerequisite_id in prerequisite_ids):
            add_conflict_dependency(downstream, resolver)


def configure_resolver(conflict, source_artifacts, previous_resolver=None):
    """Create a resolver task or extend its still-pending conflict with another output."""
    for artifact in source_artifacts:
        conflict.artifacts.add(artifact)

    resolver = Task.objects.create(
        project=conflict.project,
        plan=conflict.plan,
        plan_key=next_resolver_key(conflict) if conflict.plan_id else '',
        type='INTEGRATION',
        title=f'Resolve conflicting outputs: {conflict.path}'[:200],
        instructions=(
            f'Merge the conflicting outputs for {conflict.path}. Produce the complete, final file at that '
            'path. Preserve compatible changes from every version and resolve incompatible changes '
            'consistently with the task requirements.'
        ),
        worker_context=conflict_context(conflict),
        expected_output=f'A merged, conflict-free file at {conflict.path}',
        status='PENDING',
    )
    conflict.resolver_task = resolver
    conflict.save(update_fields=['resolver_task', 'updated_at'])
    for artifact in source_artifacts:
        add_conflict_dependency(resolver, artifact.task)
    if previous_resolver:
        add_conflict_dependency(resolver, previous_resolver)
    gate_downstream_tasks(
        resolver,
        {artifact.task_id for artifact in source_artifacts} |
        ({previous_resolver.pk} if previous_resolver else set()),
    )
    return resolver


def next_resolver_key(conflict):
    digest = hashlib.sha256(conflict.path_key.encode()).hexdigest()[:16]
    prefix = f'fr24-{digest}-'
    sequence = 1
    while Task.objects.filter(plan=conflict.plan, plan_key=f'{prefix}{sequence}').exists():
        sequence += 1
    return f'{prefix}{sequence}'


def detect_output_conflicts(artifact):
    """Find differing file outputs from independent tasks and queue an integration task."""
    task = artifact.task
    scope = conflict_scope(task)
    prerequisites = dependency_map(task)
    resolver_ids = TaskOutputConflict.objects.filter(scope_key=scope).exclude(
        resolver_task__isnull=True).values_list('resolver_task_id', flat=True)
    previous_artifacts = CodeArtifact.objects.filter(
        task__project_id=task.project_id, task__plan_id=task.plan_id, task__status='COMPLETED'
    ).exclude(task_id=task.pk).exclude(task_id__in=resolver_ids).select_related('task').order_by('task_id', '-attempt')
    if task.plan_id is None:
        previous_artifacts = previous_artifacts.filter(task__plan__isnull=True)

    latest_by_path = {}
    for item in artifact.files['files']:
        path_key = item['path'].casefold()
        if path_key not in latest_by_path:
            latest_by_path[path_key] = item

    for path_key, current_file in latest_by_path.items():
        latest_conflict = TaskOutputConflict.objects.filter(scope_key=scope, path_key=path_key).order_by(
            '-created_at', '-pk').first()
        if latest_conflict and latest_conflict.status == 'RESOLVED' and latest_conflict.resolved_artifact_id:
            baseline = artifact_file(latest_conflict.resolved_artifact, path_key)
            if baseline and (baseline['content'] != current_file['content'] or baseline['path'] != current_file['path']):
                if not tasks_are_related(task.pk, latest_conflict.resolved_artifact.task_id, prerequisites):
                    create_conflict(task, current_file, [latest_conflict.resolved_artifact, artifact])
            continue

        if latest_conflict and latest_conflict.status in {'OPEN', 'RESOLVING'}:
            resolver = latest_conflict.resolver_task
            if latest_conflict.status == 'OPEN' and resolver and resolver.status == 'PENDING':
                versions = [
                    row for row in latest_conflict.artifacts.select_related('task').all()
                    if not tasks_are_related(task.pk, row.task_id, prerequisites)
                ]
                has_different_version = False
                for row in versions:
                    version = artifact_file(row, path_key)
                    if version and (version['content'] != current_file['content'] or
                                    version['path'] != current_file['path']):
                        has_different_version = True
                        break
                if has_different_version:
                    latest_conflict.artifacts.add(artifact)
                    add_conflict_dependency(resolver, task)
                    gate_downstream_tasks(resolver, {task.pk})
                    resolver.worker_context = conflict_context(latest_conflict)
                    resolver.save(update_fields=['worker_context'])
            elif resolver:
                latest_conflict.status = 'RESOLVING'
                latest_conflict.save(update_fields=['status', 'updated_at'])
                if not tasks_are_related(task.pk, resolver.pk, prerequisites):
                    create_conflict(task, current_file, [artifact], previous_resolver=resolver)
            continue

        matches = []
        seen_tasks = set()
        for candidate in previous_artifacts:
            if candidate.task_id in seen_tasks:
                continue
            seen_tasks.add(candidate.task_id)
            previous_file = artifact_file(candidate, path_key)
            if (previous_file and not tasks_are_related(task.pk, candidate.task_id, prerequisites)
                    and (previous_file['content'] != current_file['content'] or previous_file['path'] != current_file['path'])):
                matches.append(candidate)
        if matches:
            create_conflict(task, current_file, [*matches, artifact])


def create_conflict(task, current_file, artifacts, previous_resolver=None):
    path_key = current_file['path'].casefold()
    conflict = TaskOutputConflict.objects.create(
        project=task.project, plan=task.plan, scope_key=conflict_scope(task),
        path=current_file['path'], path_key=path_key,
    )
    configure_resolver(conflict, artifacts, previous_resolver)
    return conflict


def resolve_conflicts(task, artifact):
    TaskOutputConflict.objects.filter(resolver_task=task, status__in=['OPEN', 'RESOLVING']).update(
        status='RESOLVED', resolved_artifact=artifact, updated_at=timezone.now())


def fail_conflicts_for_task(task):
    TaskOutputConflict.objects.filter(resolver_task=task, status__in=['OPEN', 'RESOLVING']).update(
        status='FAILED', updated_at=timezone.now())


def project_branch(project):
    """The project's branch, saved on the project the first time so the UI can show it."""
    if not project.git_branch.startswith('project/'):
        project.git_branch = branch_name(project.pk, project.name)
        project.save(update_fields=['git_branch'])
    return project.git_branch


def commit_artifact(artifact_id):
    """Write the artifact's files to the generated-projects repo and record the outcome.

    A failed commit never fails the task: the files are safe in the database and the
    error is stored on the artifact so it can be retried or shown.
    """
    repo_dir = settings.GENERATED_REPO_DIR
    if not repo_dir:
        return None
    artifact = CodeArtifact.objects.select_related('task__project', 'agent').get(pk=artifact_id)
    task = artifact.task
    manifest = dict(artifact.files)
    try:
        manifest['branch'] = project_branch(task.project)
        agent_name = artifact.agent.name if artifact.agent else 'unknown agent'
        message = f'[task {task.pk}] {task.title} ({agent_name}, attempt {artifact.attempt})'
        blocked_paths = {
            path.casefold()
            for path in TaskOutputConflict.objects.filter(
                scope_key=conflict_scope(task),
                status__in=['OPEN', 'RESOLVING', 'FAILED'],
            ).values_list('path', flat=True)
        }
        files = [item for item in manifest['files'] if item['path'].casefold() not in blocked_paths]
        manifest['commit'] = commit_files(repo_dir, manifest['branch'], files, message)
        manifest['commit_error'] = ''
    except (GitError, OSError, ValueError) as error:
        logger.warning('FR-18 commit failed for artifact %s: %s', artifact_id, error)
        manifest['commit_error'] = str(error)[:1000]
    artifact.files = manifest
    artifact.save(update_fields=['files'])
    return manifest['commit']
