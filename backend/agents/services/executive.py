from ..models import ExecutiveOrder, Fleet, Ship, CivilServantAgent, EventLog, UserPresident, Mission

class ExecutiveService:
    @staticmethod
    def issue_executive_order(president: UserPresident, directive: str) -> ExecutiveOrder:
        """The President issues a high-level directive."""
        order = ExecutiveOrder.objects.create(president=president, directive=directive)
        EventLog.objects.create(
            event_type='ExecutiveOrderIssued',
            payload={'order_id': str(order.id), 'directive': directive}
        )
        return order

    @staticmethod
    def launch_fleet(fleet_name: str, objective: str, captain: CivilServantAgent) -> Fleet:
        """The Executive Branch translates a directive into a fleet launch, spawning Firstmate."""
        from .firstmate_adapter import FirstmateRuntimeAdapter
        
        fleet = Fleet.objects.create(name=fleet_name, objective=objective)
        ship = Ship.objects.create(fleet=fleet, name=f"{fleet_name} Flagship", captain=captain, status='Underway')
        mission = Mission.objects.create(ship=ship, objective=objective, status='Planning')
        
        # 1. Ask Arbiter to decompose the objective (in the background to prevent blocking ASGI)
        from .arbiter import ArbiterService
        import threading
        import time
        
        EventLog.objects.create(
            actor_agent=captain,
            event_type='FleetLaunched',
            payload={'fleet_name': fleet_name, 'objective': objective}
        )
        
        def fleet_orchestrator(fleet_obj):
            try:
                from .firstmate_adapter import FirstmateRuntimeAdapter
                from concurrent.futures import ThreadPoolExecutor, as_completed
                
                FirstmateRuntimeAdapter.broadcast_event("⚖️ Arbiter is analyzing the objective and decomposing tasks...")
                
                decision = ArbiterService.process_presidential_directive(objective, current_state={})
                sub_tasks_data = decision.get("sub_tasks", [{"title": "Execute Directive", "description": objective, "model_provider": "ollama/hermes3:8b"}])
                
                def execute_sub_task(i, task_data):
                    time.sleep(i * 2) # Stagger Tmux pane creation slightly to avoid race conditions
                    task_desc = f"{task_data['title']}: {task_data['description']}"
                    provider = task_data.get("model_provider", "ollama/hermes3:8b")
                    
                    from ..models import Task
                    task = Task.objects.create(mission=mission, description=task_desc, status='Open')
                    
                    FirstmateRuntimeAdapter.broadcast_event(f"🚀 Launching Agent {i+1}/{len(sub_tasks_data)} [{provider}]: {task_data['title']}")
                    
                    target_repo_path = "/home/spectre/firstmate"
                    exec_run = FirstmateRuntimeAdapter.start_firstmate(fleet_obj, task, target_repo_path, model_provider=provider)
                    session_name = f"mag-run-{exec_run.id[:8]}"
                    
                    task_completed = False
                    while not task_completed:
                        time.sleep(2)
                        try:
                            out = FirstmateRuntimeAdapter.capture_output(session_name)
                            if "[✓] Autonomous Execution Complete." in out or "[X] Execution failed" in out:
                                task_completed = True
                                task.status = 'Completed' if "[✓]" in out else 'Failed'
                                task.save()
                        except Exception:
                            pass
                            
                # Execute in parallel
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(execute_sub_task, i, task_data) for i, task_data in enumerate(sub_tasks_data)]
                    for future in as_completed(futures):
                        future.result() # Propagate any exceptions
                        
                FirstmateRuntimeAdapter.broadcast_event(f"🏁 Fleet {fleet_name} orchestration complete. All agents finished.")
            except Exception as e:
                FirstmateRuntimeAdapter.broadcast_event(f"❌ Fleet Orchestrator Crashed: {str(e)}")
            
        # Launch the orchestrator in the background to avoid blocking ASGI
        threading.Thread(target=fleet_orchestrator, args=(fleet,), daemon=True).start()
        
        return fleet
