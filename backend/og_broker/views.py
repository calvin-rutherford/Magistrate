import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from agents.services.executive import ExecutiveService
from agents.models import CivilServantAgent

logger = logging.getLogger(__name__)

@csrf_exempt
def siri_webhook(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
        
    try:
        data = json.loads(request.body)
        prompt = data.get("prompt", "")
        
        if not prompt:
            return JsonResponse({"error": "Prompt is required"}, status=400)
            
        # 1. Dispatch the fleet in the background so the work actually gets done!
        captain, _ = CivilServantAgent.objects.get_or_create(
            name="AutoCaptain",
            defaults={"clearance_level": 5, "rank": "Senior"}
        )
        
        fleet_name = f"Siri-{prompt[:10].strip()}"
        ExecutiveService.launch_fleet(
            fleet_name=fleet_name,
            objective=prompt,
            captain=captain
        )
        
        # 2. Return a quick conversational reply to read back to the user via Siri!
        siri_reply = "Understood. The Magistrate fleet has been deployed and is working on it."
            
        return JsonResponse({"response": siri_reply})
        
    except Exception as e:
        logger.error(f"Siri Webhook Error: {e}")
        return JsonResponse({"error": str(e)}, status=500)
