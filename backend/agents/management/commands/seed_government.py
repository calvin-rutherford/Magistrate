from django.core.management.base import BaseCommand
from agents.models import (
    UserPresident, Constitution, Branch, Department, Agency, 
    Court, State, City, CivilServantAgent, ApprovalGate, Law
)

class Command(BaseCommand):
    help = 'Seeds the initial Magistrate Government MVP hierarchy.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Initializing Magistrate Government State (Lean MVP)...")

        president, _ = UserPresident.objects.get_or_create(name="Mr. Kezic")
        
        Constitution.objects.get_or_create(
            version="0.1",
            text="The Supreme Law of the Magistrate Engineering State. All agents must follow Validation rules before merging."
        )

        # The Three Core Branches (Pruned for MVP)
        exec_branch, _ = Branch.objects.get_or_create(name="Executive")
        leg_branch, _ = Branch.objects.get_or_create(name="Legislative")
        jud_branch, _ = Branch.objects.get_or_create(name="Judicial")

        # Essential Departments
        dept_eng, _ = Department.objects.get_or_create(name="Department of Engineering", branch=exec_branch)
        dept_sec, _ = Department.objects.get_or_create(name="Department of Security", branch=exec_branch)

        # Essential Agencies
        backend_eng, _ = Agency.objects.get_or_create(name="Backend Engineering Agency", department=dept_eng)
        frontend_eng, _ = Agency.objects.get_or_create(name="Frontend Engineering Agency", department=dept_eng)
        threat_model, _ = Agency.objects.get_or_create(name="Threat Modeling Agency", department=dept_sec)

        # Courts and Approval Gates
        SecurityCourt, _ = Court.objects.get_or_create(name="Security Court")
        QualityCourt, _ = Court.objects.get_or_create(name="Quality Court")
        
        ApprovalGate.objects.get_or_create(
            court=SecurityCourt,
            risk_tier=4,
            description="Dangerous Action (e.g., executing unchecked bash scripts or modifying secrets)"
        )

        # Core System Law
        Law.objects.get_or_create(
            title="Article XVI: Validation First",
            text="Validation is mandatory for code-changing work. Evidence must be durable and linked to PRs."
        )

        # Federalism
        state_magistrate, _ = State.objects.get_or_create(name="Magistrate State")
        City.objects.get_or_create(name="magistrate-core", state=state_magistrate, repository_url="github.com/calvin-rutherford/Magistrate")

        # Initial Core Agent
        CivilServantAgent.objects.get_or_create(name="Broker Executive", agency=backend_eng, rank="ChiefOfStaff")

        self.stdout.write(self.style.SUCCESS('Successfully seeded the Magistrate Lean MVP State!'))
