import uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


# ==========================================
# 1. AI Infrastructure & Capabilities
# ==========================================

class CapabilityMetric(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        """Return a readable label for this database record."""
        return self.name


class AIModel(models.Model):
    ollama_name = models.CharField(max_length=100, unique=True, default='qwen2.5:3b', help_text="Ollama model identifier, e.g. 'qwen2.5:3b'")
    family = models.CharField(max_length=100, blank=True, default='', help_text="Model family, e.g. 'qwen', 'llama'")
    size_b = models.FloatField(null=True, blank=True, help_text="Parameter size in billions (e.g. 3.0, 7.0)")
    quantization = models.CharField(max_length=50, blank=True, help_text="Quantization level, e.g. 'Q4_K_M'")
    max_context = models.PositiveIntegerField(null=True, blank=True, help_text="Maximum context window tokens")

    def __str__(self):
        """Return a readable label for this database record."""
        return f"{self.ollama_name} ({self.family or 'general'})"


# Alias for backward compatibility with previous code imports
AiModel = AIModel


class Agent(models.Model):
    ROLE_CHOICES = [(value, value.title()) for value in (
        'MASTER', 'GENERAL', 'FRONTEND', 'BACKEND', 'DATABASE', 'TESTING', 'REVIEW', 'INTEGRATION'
    )]
    STATUS_CHOICES = [
        ('IDLE', 'Idle'),
        ('BUSY', 'Busy'),
        ('OFFLINE', 'Offline'),
    ]

    name = models.CharField(max_length=100)
    machine = models.CharField(max_length=100, blank=True, default='')
    ai_model = models.ForeignKey(AIModel, on_delete=models.SET_NULL, null=True, blank=True, related_name='agents', db_column='ai_model_id')
    context_window = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='IDLE')
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(default=timezone.now)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='GENERAL')
    api_connection = models.ForeignKey('APIConnection', on_delete=models.SET_NULL, null=True, blank=True, related_name='agents')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['role'], condition=models.Q(role='MASTER'), name='workspace_single_master')]

    def __str__(self):
        """Return a readable label for this database record."""
        return f"{self.name} [{self.status}]"


class AgentCapabilityScore(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='capability_scores', db_column='agent_id')
    metric = models.ForeignKey(CapabilityMetric, on_delete=models.CASCADE, related_name='agent_scores', db_column='metric_id')
    score = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text="Score between 0 and 100"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('agent', 'metric')

    def __str__(self):
        """Return a readable label for this database record."""
        return f"{self.agent.name} - {self.metric.name}: {self.score}"


# ==========================================
# 2. Project & Chat System
# ==========================================

class Project(models.Model):
    STATUS_CHOICES = [
        ('PLANNING', 'Planning'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    name = models.CharField(max_length=200)
    requirements_text = models.TextField(blank=True)
    spec_summary = models.TextField(blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='PLANNING')
    git_branch = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        """Return a readable label for this database record."""
        return self.name


class ChatMessage(models.Model):
    SENDER_CHOICES = [
        ('user', 'User'),
        ('master', 'Master'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name='chat_messages', db_column='project_id')
    sender = models.CharField(max_length=20, choices=SENDER_CHOICES, default='user')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    conversation = models.ForeignKey('Conversation', on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    model_name = models.CharField(max_length=100, blank=True)
    agent_name = models.CharField(max_length=100, blank=True)
    is_error = models.BooleanField(default=False)

    def __str__(self):
        """Return a readable label for this database record."""
        project_name = self.project.name if self.project else "General"
        return f"[{project_name}] {self.sender}: {self.body[:40]}"


class APIConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    url = models.URLField(max_length=500)
    api_key = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return a readable label for this database record."""
        return self.name


class Workspace(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField(max_length=100, default='DevOpsProject-')


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='conversations')
    title = models.CharField(max_length=200, default='New chat')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        indexes = [models.Index(fields=['project', 'deleted_at', '-updated_at'], name='workspace_chat_list')]


class ChatRequest(models.Model):
    STATUS_CHOICES = [('PROCESSING', 'Processing'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='requests')
    user_message = models.OneToOneField(ChatMessage, on_delete=models.CASCADE, related_name='request_as_user')
    reply = models.OneToOneField(ChatMessage, on_delete=models.SET_NULL, null=True, blank=True, related_name='request_as_reply')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PROCESSING')
    model_name = models.CharField(max_length=100)
    agent_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['conversation', 'status'], name='workspace_chat_pending')]


class WorkspaceImport(models.Model):
    id = models.UUIDField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    summary = models.JSONField(default=dict)


# ==========================================
# 3. Tasks & Dependencies
# ==========================================

class TaskPlan(models.Model):
    STATUS_CHOICES = [('GENERATING', 'Generating'), ('READY', 'Saved'), ('FAILED', 'Failed')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='task_plans')
    request_text = models.TextField()
    project_context = models.JSONField(default=dict)
    title = models.CharField(max_length=200, blank=True)
    summary = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='GENERATING')
    model_name = models.CharField(max_length=100)
    agent_name = models.CharField(max_length=100)
    error = models.TextField(blank=True)
    error_code = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['project', '-created_at'], name='task_plan_project_history')]

    def __str__(self):
        """Identify a saved or generating task plan in the admin."""
        return self.title or self.request_text[:80]


class Task(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ASSIGNED', 'Assigned'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name='tasks', db_column='project_id')
    plan = models.ForeignKey(TaskPlan, on_delete=models.CASCADE, null=True, blank=True, related_name='tasks')
    plan_key = models.CharField(max_length=64, blank=True)
    required_capabilities = models.JSONField(default=list, blank=True)
    expected_output = models.TextField(blank=True)
    acceptance_criteria = models.JSONField(default=list, blank=True)
    assigned_agent = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tasks',
        db_column='assigned_agent_id'
    )
    type = models.CharField(max_length=50, default='GENERAL', help_text="e.g. FRONTEND, BACKEND, DATABASE, TESTING, INTEGRATION")
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    weights = models.FloatField(default=1.0)
    est_tokens = models.PositiveIntegerField(null=True, blank=True)
    max_tokens = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='PENDING')
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['plan', 'plan_key'], name='task_unique_plan_key')]

    def clean(self):
        """Keep a planned task in the same project as its plan."""
        super().clean()
        if self.plan_id and self.project_id != self.plan.project_id:
            raise ValidationError({'project': 'A task must belong to the same project as its plan.'})

    def __str__(self):
        """Return a readable label for this database record."""
        return f"[{self.type}] {self.title} ({self.status})"


class TaskDependency(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='dependencies', db_column='task_id')
    depends_on = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='dependent_tasks', db_column='depends_on_id')

    class Meta:
        unique_together = ('task', 'depends_on')

    def clean(self):
        """Reject self, cross-project, cross-plan, and circular dependency edits."""
        super().clean()
        if not self.task_id or not self.depends_on_id:
            return
        if self.task_id == self.depends_on_id:
            raise ValidationError('A task cannot depend on itself.')
        if self.task.project_id != self.depends_on.project_id or self.task.plan_id != self.depends_on.plan_id:
            raise ValidationError('Dependencies must belong to the same project and plan.')
        pending, visited = {self.depends_on_id}, set()
        while pending:
            if self.task_id in pending:
                raise ValidationError('Dependencies cannot form a cycle.')
            visited.update(pending)
            pending = set(TaskDependency.objects.filter(task_id__in=pending).exclude(pk=self.pk)
                          .values_list('depends_on_id', flat=True)) - visited

    def __str__(self):
        """Return a readable label for this database record."""
        return f"{self.task.title} -> depends on -> {self.depends_on.title}"


# ==========================================
# 4. Metrics, Artifacts & Reviews
# ==========================================

class Metric(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='metrics', null=True, blank=True, db_column='task_id')
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, related_name='metrics', null=True, blank=True, db_column='agent_id')
    model = models.CharField(max_length=100, blank=True)
    duration_s = models.FloatField(default=0.0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=True)
    retries = models.PositiveIntegerField(default=0)

    def __str__(self):
        """Return a readable label for this database record."""
        return f"Metric #{self.id}: {self.model} ({self.duration_s}s, success={self.success})"


class CodeArtifact(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='artifacts', db_column='task_id')
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name='code_artifacts', db_column='agent_id')
    attempt = models.PositiveIntegerField(default=1)
    files = models.JSONField(default=dict, blank=True, help_text="Structured file dictionary or manifest")
    notes = models.TextField(blank=True)
    test_verdict = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return a readable label for this database record."""
        return f"Artifact for Task #{self.task_id} (Attempt {self.attempt})"


class ArtifactReview(models.Model):
    artifact = models.ForeignKey(CodeArtifact, on_delete=models.CASCADE, related_name='reviews', db_column='artifact_id')
    reviewer_agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name='artifact_reviews', db_column='reviewer_agent_id')
    verdict = models.CharField(max_length=50, default='PASSED')
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Return a readable label for this database record."""
        return f"Review for Artifact #{self.artifact_id}: {self.verdict}"
