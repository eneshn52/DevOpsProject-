"""FR-18: turn a worker's finished answer into stored files and a commit on the project's branch.

Flow (called from task_execution):
  start()  -> needs_files(task)? add result_instructions() to the prompt and RESULT_SCHEMA to the job
  result() -> needs_files(task)? check_answer() -> valid: save_artifact() + commit after the DB commit
                                                -> invalid: the attempt is FAILED with the reason
"""
import logging

from django.conf import settings
from django.db import transaction

from .git_repo import GitError, branch_name, commit_files
from .models import CodeArtifact
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


def save_artifact(task, agent, attempt, parsed):
    """Store the checked files (caller is inside the result transaction) and schedule the commit."""
    artifact = CodeArtifact.objects.create(
        task=task, agent=agent, attempt=attempt, notes=parsed['notes'],
        files={'files': parsed['files'], 'summary': parsed['summary'],
               'branch': '', 'commit': None, 'commit_error': ''})
    # Git runs only after the database change is final, and never while rows are locked.
    transaction.on_commit(lambda: commit_artifact(artifact.pk))
    return artifact


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
        manifest['commit'] = commit_files(repo_dir, manifest['branch'], manifest['files'], message)
        manifest['commit_error'] = ''
    except (GitError, OSError, ValueError) as error:
        logger.warning('FR-18 commit failed for artifact %s: %s', artifact_id, error)
        manifest['commit_error'] = str(error)[:1000]
    artifact.files = manifest
    artifact.save(update_fields=['files'])
    return manifest['commit']
