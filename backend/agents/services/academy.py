from ..models import CivilServantAgent, Certification, Agency

class AgentAcademyService:
    @staticmethod
    def verify_agent_readiness(agent: CivilServantAgent, required_certification: str = None) -> bool:
        """
        Verifies if an agent is allowed to execute a task based on rank and certifications.
        Article VI, Section 8: No child agent may be spawned without role assignment and training.
        """
        if agent.rank == 'Trainee':
            return False
            
        if required_certification:
            has_cert = Certification.objects.filter(agent=agent, title=required_certification).exists()
            if not has_cert:
                return False
                
        return True

    @staticmethod
    def onboard_new_agent(name: str, agency: Agency, rank: str = 'Trainee') -> CivilServantAgent:
        """
        Spawns a new agent into the Civil Service pool, initially as a Trainee.
        They must pass Academy before being assigned a Ship.
        """
        return CivilServantAgent.objects.create(
            name=name,
            agency=agency,
            rank=rank,
            clearance_level=1,
            status='Academy'
        )

    @staticmethod
    def graduate_agent(agent: CivilServantAgent, new_rank: str, certification_title: str):
        """Graduates an agent from the Academy, granting them a certification and rank."""
        agent.rank = new_rank
        agent.status = 'Idle'
        agent.save()
        
        Certification.objects.create(agent=agent, title=certification_title)
        return True
