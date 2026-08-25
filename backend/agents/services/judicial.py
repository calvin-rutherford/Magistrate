from ..models import Court, CourtCase, Ruling, CivilServantAgent, EventLog

class JudicialService:
    @staticmethod
    def file_case(court_name: str, defendant: CivilServantAgent, charge: str) -> CourtCase:
        """Files a case against an agent for violating an engineering law."""
        court, _ = Court.objects.get_or_create(name=court_name)
        case = CourtCase.objects.create(court=court, defendant_agent=defendant, charge=charge)
        EventLog.objects.create(
            actor_agent=defendant,
            event_type='CourtCaseOpened',
            payload={'case_id': str(case.id), 'court': court_name, 'charge': charge}
        )
        return case

    @staticmethod
    def issue_ruling(case: CourtCase, decision: str, reasoning: str, creates_precedent: bool = False) -> Ruling:
        """The Court issues a formal ruling, either approving, blocking, or setting precedent."""
        ruling = Ruling.objects.create(
            case=case, 
            decision=decision, 
            reasoning=reasoning, 
            creates_precedent=creates_precedent
        )
        case.status = 'Closed'
        case.save()
        
        EventLog.objects.create(
            actor_agent=case.defendant_agent,
            event_type='RulingIssued',
            payload={
                'case_id': str(case.id), 
                'decision': decision, 
                'precedent': creates_precedent
            }
        )
        return ruling
