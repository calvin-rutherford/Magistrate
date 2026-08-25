import json
import time
import subprocess
from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
import redis
import google.generativeai as genai
from .models import Ship, Mission, EventLog

genai.configure(api_key=settings.GEMINI_API_KEY)

redis_client = redis.Redis(host='redis', port=6379, db=0)

def execute_bash_in_sandbox(command: str, ship_id: str) -> str:
    """Executes a bash command inside the secure Ubuntu sandbox but requires Presidential Approval."""
    # Drop the approval request into Redis
    approval_id = f"approval:{ship_id}_{int(time.time())}"
    redis_client.set(approval_id, 'pending')
    
    # Send WebSocket alert to the President (UI)
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        'chat_default',
        {
            'type': 'approval_request',
            'approval_id': approval_id,
            'command': command,
            'ship_id': ship_id
        }
    )
    
    # Wait for the President's ruling
    while True:
        status = redis_client.get(approval_id)
        if status:
            status = status.decode('utf-8')
            if status == 'approved':
                break
            elif status == 'denied':
                return "Command execution DENIED by the President."
        time.sleep(1)
        
    # Execute safely in the sandbox container
    try:
        result = subprocess.run(
            ["docker", "exec", "magistrate-sandbox", "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=60
        )
        return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    except Exception as e:
        return f"Sandbox execution failed: {str(e)}"

@shared_task
def run_mission_loop(ship_id: str):
    """The core operational loop for a Ship undertaking a Mission."""
    try:
        ship = Ship.objects.get(id=ship_id)
        mission = ship.missions.last()
        if not mission:
            return
            
        mission.status = 'Underway'
        mission.save()

        # Alert the President
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            'chat_default',
            {
                'type': 'chat_message',
                'message': f"Fleet Command > Ship '{ship.name}' is now underway on mission: {mission.objective}"
            }
        )

        model = genai.GenerativeModel(
            model_name=settings.DEFAULT_LLM_MODEL,
            tools=[execute_bash_in_sandbox]
        )
        
        sys_prompt = f"You are the Crew of the Ship '{ship.name}'. Your mission is: {mission.objective}. Use the execute_bash_in_sandbox tool to run commands in your secure Ubuntu worktree."
        
        chat = model.start_chat()
        response = chat.send_message(sys_prompt)
        
        while True:
            response_text = response.text
            
            if response_text:
                async_to_sync(channel_layer.group_send)(
                    'chat_default',
                    {
                        'type': 'chat_message',
                        'message': f"Ship {ship.name} > {response_text}"
                    }
                )
            
            if "MISSION ACCOMPLISHED" in (response_text or ""):
                mission.status = 'Completed'
                mission.save()
                break

            if response.parts:
                for part in response.parts:
                    if part.function_call:
                        fc = part.function_call
                        if fc.name == 'execute_bash_in_sandbox':
                            args = {k: v for k, v in fc.args.items()}
                            args['ship_id'] = ship_id
                            result = execute_bash_in_sandbox(**args)
                            
                            response = chat.send_message(
                                genai.types.Part.from_function_response(
                                    name="execute_bash_in_sandbox",
                                    response={"result": result}
                                )
                            )
                            continue
            break
            
    except Exception as e:
        if 'mission' in locals():
            mission.status = 'Failed'
            mission.save()
        print(f"Mission error: {e}")

@shared_task
def process_directive_message(message: str, stateful: bool):
    from .broker_agent import ExecutiveOffice
    executive = ExecutiveOffice()
    response = async_to_sync(executive.process_directive)(message, stateful)
    
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        'chat_default',
        {
            'type': 'chat_message',
            'message': f"Executive Office > {response}"
        }
    )
