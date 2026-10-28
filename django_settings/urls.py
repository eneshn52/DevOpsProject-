"""
URL configuration for Project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django_modules import views
from django_modules import workspace_api
from django_modules import task_planning
from django_modules import agent_heartbeat
from django_modules import task_distribution
from django_modules import task_execution

urlpatterns = [
    path('api/agents/<int:agent_id>/execution-profile/', task_execution.profile, name='worker_execution_profile'),
    path('api/agents/<int:agent_id>/tasks/<int:task_id>/start/', task_execution.start, name='worker_task_start'),
    path('api/agents/<int:agent_id>/tasks/<int:task_id>/result/', task_execution.result, name='worker_task_result'),
    path('admin/', admin.site.urls),
    path('', views.index, name='index'),
    path('health/', views.health, name='health'),
    path('api/chat/', views.chat_api, name='chat_api'),
    path('api/chat', views.chat_api, name='chat_api_no_slash'),
    path('api/workspace/', workspace_api.workspace_detail, name='workspace'),
    path('api/workspace/import/', workspace_api.import_workspace, name='workspace_import'),
    path('api/projects/', workspace_api.projects, name='projects'),
    path('api/projects/<int:project_id>/', workspace_api.project_detail, name='project_detail'),
    path('api/projects/<int:project_id>/plans/', task_planning.project_plans, name='project_plans'),
    path('api/projects/<int:project_id>/plans/<uuid:plan_id>/', task_planning.plan_detail, name='plan_detail'),
    path('api/conversations/', workspace_api.conversations, name='conversations'),
    path('api/conversations/<uuid:conversation_id>/', workspace_api.conversation_detail, name='conversation_detail'),
    path('api/connections/', workspace_api.connections, name='connections'),
    path('api/connections/<uuid:connection_id>/', workspace_api.connection_detail, name='connection_detail'),
    path('api/agents/', workspace_api.agents, name='agents'),
    path('api/agents/<int:agent_id>/', workspace_api.agent_detail, name='agent_detail'),
    path('api/agents/<int:agent_id>/heartbeat/', task_execution.for_worker()(agent_heartbeat.heartbeat), name='agent_heartbeat'),
    path('api/agents/<int:agent_id>/heartbeat', task_execution.for_worker()(agent_heartbeat.heartbeat), name='agent_heartbeat_no_slash'),
    path('api/agents/<int:agent_id>/next-task/', task_execution.for_worker()(task_distribution.next_task), name='agent_next_task'),
    path('api/agents/<int:agent_id>/next-task', task_execution.for_worker()(task_distribution.next_task), name='agent_next_task_no_slash'),
    path('api/master/', workspace_api.master, name='master'),
]

if settings.BROWSER_RELOAD:
    urlpatterns.append(path('__reload__/', include('django_browser_reload.urls')))
