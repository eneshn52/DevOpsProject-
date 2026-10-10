from django.contrib import admin
from .models import (
    AiModel,
    Agent,
    CapabilityMetric,
    AgentCapabilityScore,
    Task,
    TaskDependency,
    ChatSession,
    ChatMessage,
    CodeArtifact,
    ArtifactReview,
)


# ==========================================
# Inlines
# ==========================================

class AgentCapabilityScoreInline(admin.TabularInline):
    model = AgentCapabilityScore
    extra = 1


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    fields = ('sender_type', 'sender_agent', 'category', 'content', 'created_at')
    readonly_fields = ('created_at',)


# ==========================================
# 1. AI Infrastructure & Agents
# ==========================================

@admin.register(AiModel)
class AiModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'display_name', 'model_identifier', 'api_url', 'context_window')
    search_fields = ('display_name', 'model_identifier', 'api_url')


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'role', 'model', 'is_active')
    list_filter = ('role', 'is_active')
    search_fields = ('name', 'role')
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
# 2. Task Management
# ==========================================

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'required_role', 'assigned_agent', 'status', 'sequence_order', 'created_at')
    list_filter = ('status', 'required_role')
    search_fields = ('title', 'description')


@admin.register(TaskDependency)
class TaskDependencyAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'depends_on')


# ==========================================
# 3. Chat System
# ==========================================

@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'created_at', 'updated_at')
    list_filter = ('created_at',)
    search_fields = ('title',)
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'session', 'sender_type', 'sender_agent', 'category', 'created_at')
    list_filter = ('sender_type', 'category', 'session')
    search_fields = ('content',)


# ==========================================
# 4. Artifacts & Code Reviews
# ==========================================

@admin.register(CodeArtifact)
class CodeArtifactAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'file_path', 'version', 'created_at')
    list_filter = ('version',)
    search_fields = ('file_path',)


@admin.register(ArtifactReview)
class ArtifactReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'artifact', 'reviewer_agent', 'status', 'created_at')
    list_filter = ('status', 'reviewer_agent')
    search_fields = ('feedback',)