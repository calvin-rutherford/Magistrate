from pydantic import BaseModel
from typing import Optional, List

class UniversalInputContract(BaseModel):
    source: str = 'iphone'
    modality: str = 'text'
    type: str = 'prompt'
    text: Optional[str] = None
    target: str = 'captain'

class GestureInputContract(BaseModel):
    action: str
    target_id: Optional[str] = None
