from pydantic import BaseModel, ConfigDict
from typing import Optional


class SettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    theme: str = "dark"
    default_broker: Optional[str] = None
    notifications_enabled: bool = True
    settings_json: dict = {}


class SettingsUpdate(BaseModel):
    theme: Optional[str] = None
    default_broker: Optional[str] = None
    notifications_enabled: Optional[bool] = None
    settings_json: Optional[dict] = None
