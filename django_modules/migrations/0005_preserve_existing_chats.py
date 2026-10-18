from django.db import migrations


def preserve_existing_chats(apps, schema_editor):
    """Group existing project and orphan messages into conversations after schema commit."""
    alias = schema_editor.connection.alias
    Workspace = apps.get_model('django_modules', 'Workspace')
    Project = apps.get_model('django_modules', 'Project')
    Conversation = apps.get_model('django_modules', 'Conversation')
    ChatMessage = apps.get_model('django_modules', 'ChatMessage')
    Workspace.objects.using(alias).get_or_create(pk=1)
    messages = ChatMessage.objects.using(alias).filter(conversation_id=None)
    if messages.filter(project_id=None).exists():
        recovered = Project.objects.using(alias).create(name='Recovered chats')
        messages.filter(project_id=None).update(project_id=recovered.pk)
    project_ids = messages.values_list('project_id', flat=True).distinct()
    for project in Project.objects.using(alias).filter(pk__in=project_ids):
        conversation = Conversation.objects.using(alias).create(project_id=project.pk, title=project.name[:200])
        messages.filter(project_id=project.pk).update(conversation_id=conversation.pk)


class Migration(migrations.Migration):
    dependencies = [('django_modules', '0004_workspace_persistence')]
    operations = [migrations.RunPython(preserve_existing_chats, migrations.RunPython.noop)]
