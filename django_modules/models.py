from django.db import models


# ==========================================
# 1. AI Infrastructure & Agents
# ==========================================

class AiModel(models.Model):
    display_name = models.CharField(max_length=100)
    model_identifier = models.CharField(max_length=100)  # e.g., 'qwen2.5:3b'
    api_url = models.URLField(max_length=255, default='http://localhost:11434')
    api_key = models.CharField(max_length=255, blank=True, null=True)
    context_window = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.display_name} ({self.model_identifier})"


class Agent(models.Model):
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=100, default='General Agent')  # e.g., 'FRONTEND', 'BACKEND', 'DATABASE'
    model = models.ForeignKey(AiModel, on_delete=models.PROTECT, related_name='agents')
    system_prompt = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} [{self.role}]"


class CapabilityMetric(models.Model):
    name = models.CharField(max_length=50, unique=True)  # 'coding', 'reasoning', etc.

    def __str__(self):
        return self.name


class AgentCapabilityScore(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='capabilities')
    metric = models.ForeignKey(CapabilityMetric, on_delete=models.CASCADE)
    score = models.FloatField()

    class Meta:
        unique_together = ('agent', 'metric')

    def __str__(self):
        return f"{self.agent.name} - {self.metric.name}: {self.score}"


# ==========================================
# 2. Task Management
# ==========================================

class Task(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ASSIGNED', 'Assigned'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    required_role = models.CharField(max_length=100, default='General')
    assigned_agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tasks')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    sequence_order = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence_order']

    def __str__(self):
        return self.title


class TaskDependency(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='dependencies')
    depends_on = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='dependent_tasks')

    class Meta:
        unique_together = ('task', 'depends_on')

    def __str__(self):
        return f"{self.task.title} -> {self.depends_on.title}"


# ==========================================
# 3. Chat System
# ==========================================

class ChatSession(models.Model):
    title = models.CharField(max_length=200, default='New chat')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class ChatMessage(models.Model):
    SENDER_TYPE_CHOICES = [
        ('USER', 'User'),
        ('AGENT', 'Agent'),
        ('SYSTEM', 'System'),
    ]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=10, choices=SENDER_TYPE_CHOICES)
    sender_agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True)
    category = models.CharField(max_length=50, blank=True, null=True)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender_type}: {self.content[:30]}"


# ==========================================
# 4. Artifacts & Code Reviews
# ==========================================

class CodeArtifact(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='artifacts')
    file_path = models.CharField(max_length=255)
    file_content = models.TextField()
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.file_path} (v{self.version})"


class ArtifactReview(models.Model):
    STATUS_CHOICES = [
        ('PASSED', 'Passed'),
        ('REJECTED', 'Rejected'),
        ('REVISION_REQUESTED', 'Revision Requested'),
    ]

    artifact = models.ForeignKey(CodeArtifact, on_delete=models.CASCADE, related_name='reviews')
    reviewer_agent = models.ForeignKey(Agent, on_delete=models.PROTECT)
    status = models.CharField(max_length=25, choices=STATUS_CHOICES)
    feedback = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.artifact.file_path} - {self.status}"