from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SecResponseCacheRead(BaseModel):
    id: int
    cache_key: str
    url: str
    response_json: dict[str, Any]
    status_code: int
    fetched_at: datetime
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
