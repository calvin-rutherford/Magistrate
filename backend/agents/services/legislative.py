from ..models import Law, Regulation, Policy, Agency, EventLog, CivilServantAgent

class LegislativeService:
    @staticmethod
    def propose_law(title: str, text: str, proposer: CivilServantAgent) -> Law:
        """A formal proposal for a new engineering Law."""
        law = Law.objects.create(title=title, text=text)
        EventLog.objects.create(
            actor_agent=proposer,
            event_type='LawProposed',
            payload={'law_id': str(law.id), 'title': title}
        )
        return law

    @staticmethod
    def issue_regulation(law: Law, agency: Agency, text: str, issuer: CivilServantAgent) -> Regulation:
        """An Agency issues a detailed implementation Regulation based on a Law."""
        reg = Regulation.objects.create(agency=agency, law=law, text=text)
        EventLog.objects.create(
            actor_agent=issuer,
            event_type='RegulationIssued',
            payload={'regulation_id': str(reg.id), 'law_id': str(law.id), 'agency': agency.name}
        )
        return reg

    @staticmethod
    def pass_policy(title: str, text: str, executor: CivilServantAgent) -> Policy:
        """The Executive Branch passes an operational Policy."""
        policy = Policy.objects.create(title=title, text=text)
        EventLog.objects.create(
            actor_agent=executor,
            event_type='PolicyPassed',
            payload={'policy_id': str(policy.id), 'title': title}
        )
        return policy
