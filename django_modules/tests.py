from django.test import TestCase
from django.core.exceptions import ValidationError
from django_modules.models import (
    CapabilityMetric,
    AIModel,
    Agent,
    AgentCapabilityScore,
    Project,
    ChatMessage,
    Task,
    TaskDependency,
    Metric,
    CodeArtifact,
    ArtifactReview,
)


class MultiAgentModelTests(TestCase):
    def setUp(self):
        """Create the model fixtures used by each test."""
        self.metric = CapabilityMetric.objects.create(name="code_generation")
        self.ai_model = AIModel.objects.create(
            ollama_name="qwen2.5-coder:7b",
            family="qwen",
            size_b=7.0,
            quantization="Q4_K_M",
            max_context=32768
        )
        self.agent = Agent.objects.create(
            name="Coder Agent Alpha",
            machine="worker-node-1",
            ai_model=self.ai_model,
            context_window=32768,
            status="IDLE"
        )
        self.project = Project.objects.create(
            name="DevOps Automation Hub",
            requirements_text="Automate multi-agent workflows and deployments",
            spec_summary="Distributed architecture with Django backend",
            status="PLANNING",
            git_branch="feature/erd-migration"
        )

    def test_ai_model_and_agent(self):
        """Verify ai model and agent."""
        self.assertEqual(str(self.metric), "code_generation")
        self.assertIn("qwen2.5-coder:7b", str(self.ai_model))
        self.assertEqual(self.agent.ai_model, self.ai_model)
        self.assertEqual(self.ai_model.agents.count(), 1)

    def test_agent_capability_score(self):
        """Verify agent capability score."""
        score = AgentCapabilityScore.objects.create(
            agent=self.agent,
            metric=self.metric,
            score=88.5
        )
        self.assertEqual(score.score, 88.5)
        self.assertEqual(self.agent.capability_scores.count(), 1)

        # Test score validator
        invalid_score = AgentCapabilityScore(
            agent=self.agent,
            metric=self.metric,
            score=150.0  # Exceeds max 100
        )
        with self.assertRaises(ValidationError):
            invalid_score.full_clean()

    def test_project_and_chat_messages(self):
        """Verify project and chat messages."""
        msg = ChatMessage.objects.create(
            project=self.project,
            sender="user",
            body="Start task breakdown"
        )
        self.assertEqual(msg.project, self.project)
        self.assertEqual(self.project.chat_messages.count(), 1)
        self.assertEqual(self.project.chat_messages.first().sender, "user")

    def test_tasks_and_dependencies(self):
        """Verify tasks and dependencies."""
        task1 = Task.objects.create(
            project=self.project,
            assigned_agent=self.agent,
            type="DATABASE",
            title="Setup PostgreSQL schema",
            instructions="Migrate from SQLite to PostgreSQL",
            weights=1.2,
            est_tokens=1500,
            max_tokens=4000,
            status="IN_PROGRESS"
        )
        task2 = Task.objects.create(
            project=self.project,
            assigned_agent=self.agent,
            type="BACKEND",
            title="API endpoints",
            instructions="Create CRUD endpoints",
            weights=1.0,
            status="PENDING"
        )
        dep = TaskDependency.objects.create(
            task=task2,
            depends_on=task1
        )
        self.assertEqual(task2.dependencies.count(), 1)
        self.assertEqual(task1.dependent_tasks.count(), 1)
        self.assertEqual(self.project.tasks.count(), 2)

    def test_metrics_and_artifacts(self):
        """Verify metrics and artifacts."""
        task = Task.objects.create(
            project=self.project,
            assigned_agent=self.agent,
            type="BACKEND",
            title="Core logic",
            status="IN_PROGRESS"
        )
        metric = Metric.objects.create(
            task=task,
            agent=self.agent,
            model=self.ai_model.ollama_name,
            duration_s=5.2,
            prompt_tokens=450,
            output_tokens=320,
            success=True,
            retries=0
        )
        self.assertTrue(metric.success)
        self.assertEqual(task.metrics.count(), 1)

        artifact = CodeArtifact.objects.create(
            task=task,
            agent=self.agent,
            attempt=1,
            files={"views.py": "def home(): pass"},
            notes="Initial draft",
            test_verdict="PASSED"
        )
        self.assertEqual(artifact.files["views.py"], "def home(): pass")
        self.assertEqual(task.artifacts.count(), 1)

        review = ArtifactReview.objects.create(
            artifact=artifact,
            reviewer_agent=self.agent,
            verdict="PASSED",
            comments="Code is clean and tests pass."
        )
        self.assertEqual(artifact.reviews.count(), 1)
        self.assertEqual(review.verdict, "PASSED")
