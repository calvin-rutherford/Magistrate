import pytest
from agents.models import (
    UserPresident, CivilServantAgent, Agency, Branch, Department, Law, Court
)
from agents.services.legislative import LegislativeService
from agents.services.judicial import JudicialService
from agents.services.executive import ExecutiveService

@pytest.fixture
def base_government():
    president = UserPresident.objects.create(name="Calvin")
    exec_branch = Branch.objects.create(name="Executive")
    eng_dept = Department.objects.create(name="Department of Engineering", branch=exec_branch)
    eng_agency = Agency.objects.create(name="Backend Engineering Agency", department=eng_dept)
    agent = CivilServantAgent.objects.create(name="Test Agent", agency=eng_agency, rank="SeniorAgent")
    return president, eng_agency, agent

@pytest.mark.django_db
def test_legislative_propose_law(base_government):
    president, agency, agent = base_government
    law = LegislativeService.propose_law("Test Law", "Thou shalt write tests.", proposer=agent)
    assert law.title == "Test Law"
    assert law.text == "Thou shalt write tests."

@pytest.mark.django_db
def test_judicial_issue_ruling(base_government):
    president, agency, agent = base_government
    case = JudicialService.file_case("Supreme Court", agent, "Refused to write tests.")
    assert case.status == 'Open'
    
    ruling = JudicialService.issue_ruling(case, "Injunction", "Block PR until tests are written.", True)
    
    # Case should now be closed
    case.refresh_from_db()
    assert case.status == 'Closed'
    assert ruling.creates_precedent is True

@pytest.mark.django_db
def test_executive_launch_fleet(base_government):
    president, agency, agent = base_government
    ExecutiveService.issue_executive_order(president, "Rebuild Omnigent to Magistrate")
    
    fleet = ExecutiveService.launch_fleet("Magistrate Builders", "Refactor everything", agent)
    assert fleet.name == "Magistrate Builders"
    assert fleet.ships.count() == 1
    assert fleet.ships.first().captain == agent
    assert fleet.ships.first().missions.first().status == 'Planning'
