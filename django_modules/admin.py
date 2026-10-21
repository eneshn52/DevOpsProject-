from django.contrib import admin
from django import forms
from .models import (
    CapabilityMetric,
    AIModel,
    Agent,
    AgentCapabilityScore,
    Project,
    ChatMessage,
    Task,
    TaskDependency,
    TaskPlan,
    Metric,
    CodeArtifact,
    ArtifactReview,
    APIConnection,
    Conversation,
    ChatRequest,
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
    fields = ('sender', 'body', 'created_at')
    readonly_fields = ('created_at',)


class TaskDependencyForm(forms.ModelForm):
    class Meta:
        model = TaskDependency
        fields = '__all__'

    def clean(self):
        """Prevent manual edits to dependencies belonging to immutable generated plans."""
        data = super().clean()
        task = data.get('task')
        if task and task.plan_id:
            raise forms.ValidationError('Saved plan dependencies are read-only. Generate a new plan to revise them.')
        return data


class TaskDependencyInline(admin.TabularInline):
    model = TaskDependency
    form = TaskDependencyForm
    fk_name = 'task'
    extra = 1


class ArtifactReviewInline(admin.TabularInline):
    model = ArtifactReview
    extra = 0
    fields = ('reviewer_agent', 'verdict', 'comments', 'created_at')
    readonly_fields = ('created_at',)


# ==========================================
# 1. AI Infrastructure & Capabilities
# ==========================================

@admin.register(CapabilityMetric)
class CapabilityMetricAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'ollama_name', 'family', 'size_b', 'quantization', 'max_context')
    list_filter = ('family', 'quantization')
    search_fields = ('ollama_name', 'family')


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'role', 'machine', 'ai_model', 'api_connection', 'status', 'current_task', 'context_window', 'last_heartbeat')
    list_filter = ('status', 'ai_model')
    search_fields = ('name', 'machine')
    inlines = [AgentCapabilityScoreInline]


@admin.register(AgentCapabilityScore)
class AgentCapabilityScoreAdmin(admin.ModelAdmin):
    list_display = ('id', 'agent', 'metric', 'score', 'updated_at')
    list_filter = ('metric', 'agent')
    search_fields = ('agent__name', 'metric__name')


# ==========================================
# 2. Project & Chat System
# ==========================================

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'status', 'git_branch', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('name', 'requirements_text', 'spec_summary')
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'project', 'sender', 'body_summary', 'created_at')
    list_filter = ('sender', 'project', 'created_at')
    search_fields = ('body',)

    def body_summary(self, obj):
        """Return a short message preview for the admin list."""
        return obj.body[:50] + '...' if len(obj.body) > 50 else obj.body
    body_summary.short_description = 'Body'


@admin.register(APIConnection)
class APIConnectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'created_at')
    exclude = ('api_key',)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'updated_at', 'deleted_at')
    search_fields = ('title',)


@admin.register(ChatRequest)
class ChatRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'status', 'model_name', 'created_at')
    readonly_fields = tuple(field.name for field in ChatRequest._meta.fields)

    def has_add_permission(self, request):
        """Prevent manually creating chat request audit records."""
        return False


# ==========================================
# 3. Tasks & Dependencies
# ==========================================

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'type', 'assigned_agent', 'status', 'weights', 'attempts', 'created_at')
    list_filter = ('status', 'type', 'project')
    search_fields = ('title', 'instructions')
    inlines = [TaskDependencyInline]
    readonly_fields = ('plan', 'plan_key', 'required_capabilities', 'expected_output', 'acceptance_criteria')

    def has_change_permission(self, request, obj=None):
        """Keep generated plan tasks immutable while allowing legacy task edits."""
        return not (obj and obj.plan_id) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """Preserve tasks that belong to saved plan history."""
        return not (obj and obj.plan_id) and super().has_delete_permission(request, obj)

    def get_inlines(self, request, obj=None):
        """Hide dependency editing controls when viewing a saved plan task."""
        return [] if obj and obj.plan_id else super().get_inlines(request, obj)


@admin.register(TaskPlan)
class TaskPlanAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'status', 'model_name', 'created_at')
    list_filter = ('status', 'project')
    readonly_fields = tuple(field.name for field in TaskPlan._meta.fields)

    def has_add_permission(self, request):
        """Require task plans to pass through the validated planning API."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Retain saved plan history for inspection."""
        return False


@admin.register(TaskDependency)
class TaskDependencyAdmin(admin.ModelAdmin):
    form = TaskDependencyForm
    list_display = ('id', 'task', 'depends_on')
    search_fields = ('task__title', 'depends_on__title')

    def has_delete_permission(self, request, obj=None):
        """Prevent removing edges from a saved generated plan."""
        return not (obj and obj.task.plan_id) and super().has_delete_permission(request, obj)


# ==========================================
# 4. Metrics, Artifacts & Reviews
# ==========================================

@admin.register(Metric)
class MetricAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'agent', 'model', 'duration_s', 'prompt_tokens', 'output_tokens', 'success', 'retries')
    list_filter = ('success', 'model')
    search_fields = ('model', 'task__title')


@admin.register(CodeArtifact)
class CodeArtifactAdmin(admin.ModelAdmin):
    list_display = ('id', 'task', 'agent', 'attempt', 'test_verdict', 'created_at')
    list_filter = ('attempt', 'test_verdict')
    search_fields = ('notes', 'task__title')
    inlines = [ArtifactReviewInline]


@admin.register(ArtifactReview)
class ArtifactReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'artifact', 'reviewer_agent', 'verdict', 'created_at')
    list_filter = ('verdict', 'reviewer_agent')
    search_fields = ('comments',)
