from django.db import models


# ==========================================
# 1. AI Infrastructure & Agents
# ==========================================

class ApiProvider(models.Model):
    name = models.CharField(max_length=100)
    base_url = models.URLField(max_length=255)
    api_key = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class AiModel(models.Model):
    provider = models.ForeignKey(ApiProvider, on_delete=models.CASCADE, related_name='models')
    display_name = models.CharField(max_length=100)
    model_identifier = models.CharField(max_length=100)  # e.g., 'qwen2.5-coder:7b'
    context_window = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.display_name} ({self.model_identifier})"


class AgentRole(models.Model):
    code = models.CharField(max_length=50, unique=True)  # 'MASTER', 'FRONTEND', etc.
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Agent(models.Model):
    name = models.CharField(max_length=100)
    role = models.ForeignKey(AgentRole, on_delete=models.PROTECT, related_name='agents')
    model = models.ForeignKey(AiModel, on_delete=models.PROTECT, related_name='agents')
    system_prompt = models.TextField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} [{self.role.code}]"


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
# 2. Projects & Task Decomposition
# ==========================================

class Project(models.Model):
    STATUS_CHOICES = [
        ('PLANNING', 'Planning'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    title = models.CharField(max_length=200)
    raw_requirements = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PLANNING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Task(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ASSIGNED', 'Assigned'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    required_role = models.ForeignKey(AgentRole, on_delete=models.PROTECT)
    assigned_agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tasks')
    title = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    sequence_order = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence_order']

    def __str__(self):
        return f"[{self.project.title}] {self.title}"


class TaskDependency(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='dependencies')
    depends_on = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='dependent_tasks')

    class Meta:
        unique_together = ('task', 'depends_on')

    def __str__(self):
        return f"{self.task.title} -> {self.depends_on.title}"


# ==========================================
# 3. Chat & Inter-Agent Communication
# ==========================================

class ChatSession(models.Model):
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='chats')
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


class InterAgentMessage(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='agent_logs')
    sender_agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='sent_messages')
    receiver_agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='received_messages')
    message_type = models.CharField(max_length=50)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender_agent.name} -> {self.receiver_agent.name}: {self.message_type}"


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