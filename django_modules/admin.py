from django.contrib import admin
from .models import (
    ApiProvider,
    AiModel,
    AgentRole,
    Agent,
    CapabilityMetric,
    AgentCapabilityScore,
    Project,
    Task,
    TaskDependency,
    ChatSession,
    ChatMessage,
    InterAgentMessage,
    CodeArtifact,
    ArtifactReview,
)


# ==========================================
# Inlines (edit related items on the same page)
# ==========================================

class AiModelInline(admin.TabularInline):
    model = AiModel
    extra = 1


class AgentCapabilityScoreInline(admin.TabularInline):
    model = AgentCapabilityScore
    extra = 1


class TaskInline(admin.TabularInline):
    model = Task
    extra = 0
    fields = ('title', 'required_role', 'assigned_agent', 'status', 'sequence_order')


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ('sender_type', 'sender_agent', 'category', 'content', 'created_at')
    readonly_fields = ('created_at',)


# ==========================================
# 1. AI Infrastructure & Agents
# ==========================================

@admin.register(ApiProvider)
class ApiProviderAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'base_url', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'base_url')
    inlines = [AiModelInline]


@admin.register(AiModel)
class AiModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'display_name', 'model_identifier', 'provider', 'context_window')
    list_filter = ('provider',)
    search_fields = ('display_name', 'model_identifier')


@admin.register(AgentRole)
class AgentRoleAdmin(admin.ModelAdmin):
    list_display = ('id', 'code', 'name', 'description')
    search_fields = ('code', 'name')


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'role', 'model', 'is_active')
    list_filter = ('role', 'is_active')
    search_fields = ('name',)
    inlines = [AgentCapabilityScoreInline]


@admin.register(CapabilityMetric)
class CapabilityMetricAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(AgentCapabilityScore)
class AgentCapabilityScoreAdmin(admin.ModelAdmin):
    list_display = ('id', 'agent', 'metric', 'score')
    list_filter = ('metric', 'agent')


# ==========================================
# 2. Projects & Task Decomposition
# ==========================================

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'status', 'created_at', 'updated_at')
    list_filter = ('status', 'created_at')
    search_fields = ('title', 'raw_requirements')
    inlines = [TaskInline]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'required_role', 'assigned_agent', 'status', 'sequence_order', 'created_at')
    list_filter = ('status', 'required_role', 'project')
    search_fields = ('title', 'description')


@admin.register(TaskDependency)
class TaskDependencyAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'depends_on')
    list_filter = ('task__project',)


# ==========================================
# 3. Chat & Inter-Agent Communication
# ==========================================

@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'created_at', 'updated_at')
    list_filter = ('project', 'created_at')
    search_fields = ('title',)
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'session', 'sender_type', 'sender_agent', 'category', 'created_at')
    list_filter = ('sender_type', 'category', 'session')
    search_fields = ('content',)


@admin.register(InterAgentMessage)
class InterAgentMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'sender_agent', 'receiver_agent', 'message_type', 'created_at')
    list_filter = ('message_type', 'task')
    search_fields = ('content',)


# ==========================================
# 4. Artifacts & Code Reviews
# ==========================================

@admin.register(CodeArtifact)
class CodeArtifactAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'file_path', 'version', 'created_at')
    list_filter = ('version', 'task__project')
    search_fields = ('file_path',)


@admin.register(ArtifactReview)
class ArtifactReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'artifact', 'reviewer_agent', 'status', 'created_at')
    list_filter = ('status', 'reviewer_agent')
    search_fields = ('feedback',)