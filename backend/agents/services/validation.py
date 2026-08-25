from ..models import PullRequestRecord, Evidence, CourtCase, Ruling, CivilServantAgent

class ValidationPipelineService:
    @staticmethod
    def verify_pr_evidence(pr_record: PullRequestRecord) -> bool:
        """
        Checks if the given Pull Request has linked validation Evidence that passed,
        AND strictly ensures the Evidence corresponds to the EXACT PR head_sha.
        Article XVI: Validation is mandatory for code-changing work.
        """
        mission = pr_record.mission
        
        has_passed_evidence = False
        for task in mission.tasks.all():
            for artifact in task.artifacts.all():
                for evidence in artifact.evidence.all():
                    # STRICT SHA BINDING: The evidence is only valid if it matches the current PR commit.
                    if evidence.passed and evidence.commit_sha == pr_record.head_sha:
                        has_passed_evidence = True
        
        return has_passed_evidence

    @staticmethod
    def request_merge_ruling(pr_record: PullRequestRecord, reviewer: CivilServantAgent) -> Ruling:
        """
        If a PR is requested to be merged, this checks evidence. If none exists, 
        it files a CourtCase and issues an immediate Injunction.
        """
        from .judicial import JudicialService
        
        if not ValidationPipelineService.verify_pr_evidence(pr_record):
            # File a case for violating validation policy
            case = JudicialService.file_case(
                court_name="Security Court",
                defendant=reviewer,
                charge=f"Attempted to merge PR {pr_record.pr_url} without validation evidence."
            )
            
            # Issue an immediate injunction
            return JudicialService.issue_ruling(
                case=case,
                decision="Injunction",
                reasoning="Article XVI violation: No Evidence records found.",
                creates_precedent=False
            )
        
        # If passed, we can technically issue an Approval ruling, but normally 
        # higher-tier merges require the President anyway. We return a provisional approval.
        case = JudicialService.file_case(
            court_name="Quality Court",
            defendant=reviewer,
            charge=f"Merge review for PR {pr_record.pr_url}"
        )
        return JudicialService.issue_ruling(
            case=case,
            decision="Provisional Approval",
            reasoning="Evidence found. Ready for President merge.",
            creates_precedent=False
        )
