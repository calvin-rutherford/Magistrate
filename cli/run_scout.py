#!/usr/bin/env python3
import os
import sys

# Add backend to path so we can import django settings
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'og_broker.settings')

import django
django.setup()

from agents.services.arbiter import ArbiterService
from agents.services.executive import ExecutiveService
from agents.models import CivilServantAgent

def run_scout():
    print("Sending Presidential Directive to the Arbiter...")
    directive = "Ahoy. Run Firstmate bootstrap diagnostics only. Do not modify or clone any project yet. Report every missing dependency and ask before installing."
    
    current_state = {
        "active_crews": 0,
        "policy": "Testing mode. Do not modify code."
    }
    
    decision = ArbiterService.process_presidential_directive(directive, current_state)
    
    print("================================")
    print("Arbiter Decision Packet:")
    print("================================")
    print(f"Intent: {decision.get('intent')}")
    print(f"Risk Tier: {decision.get('risk_tier')}")
    print(f"Execution Route: {decision.get('execution_route')}")
    print(f"Reasoning: {decision.get('reasoning')}")
    print("================================\n")
    
    # We proceed with the execution route if it's safe
    print("Broker: Dispatching Fleet...")
    captain, _ = CivilServantAgent.objects.get_or_create(
        name="ScoutCaptain",
        defaults={'clearance_level': 1, 'rank': 'Junior'}
    )
    
    # In this MVP, we launch the fleet regardless of the route string for the test
    fleet = ExecutiveService.launch_fleet(
        fleet_name="Scout-Fleet-Alpha",
        objective=directive,
        captain=captain
    )
    
    print(f"\nFleet dispatched successfully: {fleet.name}")
    print("To monitor the session, connect to tmux on the server: tmux a")

if __name__ == "__main__":
    run_scout()
