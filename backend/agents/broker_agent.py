import json
from asgiref.sync import sync_to_async
import google.generativeai as genai
from django.conf import settings
from .models import CivilServantAgent, EventLog, Message, Fleet, Ship, Mission

genai.configure(api_key=settings.GEMINI_API_KEY)

def omni_spawn_fleet(fleet_name: str, objective: str):
    """Spawns a new Fleet to handle a complex objective."""
    fleet = Fleet.objects.create(name=fleet_name, objective=objective)
    ship = Ship.objects.create(fleet=fleet, name=f"{fleet_name} Flagship", status='Docked')
    agent = CivilServantAgent.objects.create(name=f"{fleet_name} Commander", status='Waiting')
    Mission.objects.create(ship=ship, objective=objective)
    EventLog.objects.create(actor_agent=agent, event_type='FleetLaunched', payload={'fleet_name': fleet_name, 'objective': objective})
    
    # Send the mission to the Celery executor queue (the operations command)
    from .tasks import run_mission_loop
    run_mission_loop.delay(str(ship.id))
    
    return f"Fleet '{fleet_name}' launched and ship deployed for objective."

class ExecutiveOffice:
    """The Chief Administrator / Broker that takes directives from the UserPresident."""
    
    def __init__(self):
        self.model = genai.GenerativeModel(
            model_name=settings.DEFAULT_LLM_MODEL,
            tools=[omni_spawn_fleet]
        )
        self.system_prompt = (
            "You are the Executive Office of the Magistrate Government. "
            "The user is the President. You take high-level directives from the President "
            "and translate them into action. If a directive requires engineering work, "
            "you must use the `omni_spawn_fleet` tool to launch a fleet to handle it. "
            "Do not do the coding yourself. You govern and delegate."
        )

    async def process_directive(self, prompt: str, stateful: bool = True) -> str:
        history = []
        if stateful:
            messages = await sync_to_async(list)(Message.objects.all().order_by('timestamp'))
            for msg in messages:
                history.append({'role': msg.role, 'parts': [msg.content]})

        chat = self.model.start_chat(history=history)
        
        # Save President's directive
        await sync_to_async(Message.objects.create)(role='user', content=prompt)
        
        response = chat.send_message(f"System: {self.system_prompt}\nPresident Directive: {prompt}")
        
        response_text = response.text
        if not response_text and response.parts:
            for part in response.parts:
                if part.function_call:
                    fc = part.function_call
                    if fc.name == 'omni_spawn_fleet':
                        args = {k: v for k, v in fc.args.items()}
                        result = await sync_to_async(omni_spawn_fleet)(**args)
                        
                        fc_response = chat.send_message(
                            genai.types.Part.from_function_response(
                                name="omni_spawn_fleet",
                                response={"result": result}
                            )
                        )
                        response_text = fc_response.text

        # Save Executive response
        if response_text:
            await sync_to_async(Message.objects.create)(role='model', content=response_text)
            return response_text
        return "Acknowledged, Mr. President."
