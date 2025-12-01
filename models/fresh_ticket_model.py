from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class FreshRequester(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = Field(default=None)
    location: Optional[str] = None


class FreshConversation(BaseModel):
    id: int
    body: Optional[str] = None
    body_text: Optional[str] = None
    public: Optional[bool] = None
    user_id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FreshTicket(BaseModel):
    id: int
    description: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[int] = None
    source: Optional[int] = None
    type: Optional[str] = None
    urgency: Optional[int] = Field(default=None)
    impact: Optional[int] = Field(default=None)
    group_id: Optional[int] = None
    department_id: Optional[int] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    requester_id: int
    requester: Optional[FreshRequester] = Field(default=None)
    location: Optional[str] = Field(default=None)
    subject: str
    created_at: Optional[datetime] = Field(default=None)
    updated_at: Optional[datetime] = Field(default=None)
    conversations: List[FreshConversation] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
