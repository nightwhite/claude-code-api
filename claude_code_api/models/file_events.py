"""
File event models for watchdog integration.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class FileEventType(str, Enum):
    """File event types."""
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


class FileType(str, Enum):
    """File types."""
    FILE = "file"
    DIRECTORY = "directory"


class FileEvent(BaseModel):
    """File system event model."""
    event_type: FileEventType
    src_path: str
    dest_path: Optional[str] = None
    file_type: FileType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    project_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class WatcherConfig(BaseModel):
    """File watcher configuration."""
    path: str
    project_id: Optional[str] = None
    recursive: bool = True
    ignore_patterns: list[str] = Field(default_factory=lambda: [
        "*.pyc", "__pycache__", ".git", ".venv", "node_modules",
        "*.log", "*.tmp", ".DS_Store", "Thumbs.db"
    ])
    include_patterns: list[str] = Field(default_factory=lambda: ["*"])
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    debounce_seconds: float = 0.5  # 防抖延迟


class WatcherStatus(BaseModel):
    """File watcher status."""
    path: str
    project_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    last_event_at: Optional[datetime] = None
    event_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None


class FileTreeNode(BaseModel):
    """File tree node model."""
    name: str
    path: str
    type: FileType
    size: Optional[int] = None
    modified: Optional[datetime] = None
    children: list['FileTreeNode'] = Field(default_factory=list)
    is_expanded: bool = False
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class FileContent(BaseModel):
    """File content model."""
    path: str
    content: Optional[str] = None
    encoding: str = "utf-8"
    size: int
    modified: datetime
    is_binary: bool = False
    mime_type: Optional[str] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class WebSocketMessage(BaseModel):
    """WebSocket message model."""
    type: str
    data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# Update forward references
FileTreeNode.model_rebuild()
