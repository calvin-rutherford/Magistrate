import uuid
import hashlib
import json
from django.db import models

# ==========================================
# 1. Core State & Constitution
# ==========================================
class UserPresident(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

class Constitution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.CharField(max_length=50)
    text = models.TextField()
    enacted_at = models.DateTimeField(auto_now_add=True)

class ConstitutionVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version_hash = models.CharField(max_length=64, unique=True)
    text = models.TextField()
    enacted_at = models.DateTimeField(auto_now_add=True)

# ==========================================
# 2. Federalism & Agencies
# ==========================================
class State(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

class City(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name='cities')
    name = models.CharField(max_length=255)
    repository_url = models.CharField(max_length=512)

class District(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name='districts')
    name = models.CharField(max_length=255)

class Branch(models.Model):
    name = models.CharField(max_length=100, unique=True)

class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='departments')
    name = models.CharField(max_length=255)

class Agency(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='agencies')
    name = models.CharField(max_length=255)

class CivilServantAgent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    agency = models.ForeignKey(Agency, null=True, blank=True, on_delete=models.SET_NULL)
    rank = models.CharField(max_length=100, default='Trainee')
    clearance_level = models.IntegerField(default=1)
    status = models.CharField(max_length=50, default='Idle')
    created_at = models.DateTimeField(auto_now_add=True)

class Certification(models.Model):
    agent = models.ForeignKey(CivilServantAgent, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    issued_at = models.DateTimeField(auto_now_add=True)

class AuthorityGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(CivilServantAgent, on_delete=models.CASCADE)
    granted_tier = models.IntegerField() # 0-4
    description = models.TextField()
    granted_at = models.DateTimeField(auto_now_add=True)

# ==========================================
# 3. Legislative Layer
# ==========================================
class Law(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    text = models.TextField()
    passed_at = models.DateTimeField(auto_now_add=True)

class Regulation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agency = models.ForeignKey(Agency, on_delete=models.CASCADE)
    law = models.ForeignKey(Law, on_delete=models.CASCADE)
    text = models.TextField()

class Policy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    text = models.TextField()
    is_active = models.BooleanField(default=True)

# ==========================================
# 4. Judicial Layer
# ==========================================
class Court(models.Model):
    name = models.CharField(max_length=255)

class CourtCase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    court = models.ForeignKey(Court, on_delete=models.CASCADE)
    defendant_agent = models.ForeignKey(CivilServantAgent, on_delete=models.CASCADE)
    charge = models.TextField()
    status = models.CharField(max_length=50, default='Open')

class Ruling(models.Model):
    case = models.OneToOneField(CourtCase, on_delete=models.CASCADE)
    decision = models.CharField(max_length=50)
    reasoning = models.TextField()
    creates_precedent = models.BooleanField(default=False)

class ApprovalGate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    court = models.ForeignKey(Court, on_delete=models.CASCADE)
    risk_tier = models.IntegerField(default=4) 
    description = models.TextField()
    status = models.CharField(max_length=50, default='Pending')

# ==========================================
# 5. Executive & Operations
# ==========================================
class Command(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    president = models.ForeignKey(UserPresident, on_delete=models.CASCADE)
    directive = models.TextField()
    tier_requested = models.IntegerField(default=1)
    issued_at = models.DateTimeField(auto_now_add=True)

class ExecutiveOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    president = models.ForeignKey(UserPresident, on_delete=models.CASCADE)
    directive = models.TextField()
    issued_at = models.DateTimeField(auto_now_add=True)

class Objective(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(max_length=50, default='Pending')

class Fleet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    objective = models.TextField()

class Ship(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fleet = models.ForeignKey(Fleet, on_delete=models.CASCADE, related_name='ships')
    name = models.CharField(max_length=255)
    captain = models.ForeignKey(CivilServantAgent, on_delete=models.SET_NULL, null=True)
    status = models.CharField(max_length=50, default='Docked')

class Crew(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ship = models.ForeignKey(Ship, on_delete=models.CASCADE, related_name='crew_members')
    agent = models.ForeignKey(CivilServantAgent, on_delete=models.CASCADE)
    role = models.CharField(max_length=100)

class Mission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ship = models.ForeignKey(Ship, on_delete=models.CASCADE, related_name='missions')
    objective = models.TextField()
    status = models.CharField(max_length=50, default='Planning')

class Task(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name='tasks')
    description = models.TextField()
    assigned_to = models.ForeignKey(Crew, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=50, default='Open')

class DecisionRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    reasoning = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

# ==========================================
# 6. Execution Isolation & Artifacts
# ==========================================
class ExecutionRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='execution_runs')
    worktree_path = models.CharField(max_length=512)
    branch_name = models.CharField(max_length=255)
    status = models.CharField(max_length=50, default='Active')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

class ModelInvocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution_run = models.ForeignKey(ExecutionRun, on_delete=models.CASCADE, null=True)
    provider = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    prompt_tokens = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    timestamp = models.DateTimeField(auto_now_add=True)

class Artifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='artifacts')
    name = models.CharField(max_length=255)
    content_uri = models.CharField(max_length=512)

class Evidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    artifact = models.ForeignKey(Artifact, on_delete=models.CASCADE, related_name='evidence')
    validation_type = models.CharField(max_length=100) 
    passed = models.BooleanField(default=False)
    commit_sha = models.CharField(max_length=40, default='')  # STRICT SHA BINDING

class PullRequestRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE)
    pr_url = models.CharField(max_length=512)
    head_sha = models.CharField(max_length=40, default='')
    status = models.CharField(max_length=50, default='Open')

# ==========================================
# 7. Persistent Event History (Append-Only)
# ==========================================
class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_agent = models.ForeignKey(CivilServantAgent, null=True, blank=True, on_delete=models.SET_NULL)
    command = models.ForeignKey(Command, null=True, blank=True, on_delete=models.SET_NULL)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    event_hash = models.CharField(max_length=64, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.event_hash:
            # Simple cryptographic chain for the ledger
            last_event = AuditEvent.objects.order_by('-timestamp').first()
            previous_hash = last_event.event_hash if last_event else "GENESIS"
            canonical_payload = json.dumps(self.payload, sort_keys=True)
            self.event_hash = hashlib.sha256(f"{previous_hash}{canonical_payload}".encode()).hexdigest()
        super().save(*args, **kwargs)

class EventLog(models.Model):
    # DEPRECATED: Replaced by AuditEvent. Keeping for backward compatibility in current sprints.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_agent = models.ForeignKey(CivilServantAgent, null=True, blank=True, on_delete=models.SET_NULL)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)
