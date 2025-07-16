"""
WebSocket-based file management system.
"""

import os
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import WebSocket, WebSocketDisconnect
import structlog

from ..models.file_events import (
    FileEvent, WatcherConfig, FileTreeNode, FileContent,
    FileType
)
from .file_watcher import FileWatcher
from .event_manager import EventManager
from ..utils.gitignore_matcher import GitignoreMatcher
from .config import settings

logger = structlog.get_logger()


class WebSocketFileManager:
    """Manages file operations through WebSocket connections."""
    
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}  # connection_id -> websocket
        self.connection_projects: Dict[str, str] = {}  # connection_id -> project_id
        self.event_manager = EventManager()
        self.file_watcher = FileWatcher(self._handle_file_event)
        self.event_manager.start()
        
    async def connect(self, websocket: WebSocket, connection_id: str, project_id: Optional[str] = None):
        """Add new WebSocket connection."""
        await websocket.accept()
        self.connections[connection_id] = websocket
        if project_id:
            self.connection_projects[connection_id] = project_id
        
        await self.event_manager.add_websocket_connection(websocket, project_id)
        
        # Send welcome message
        await self._send_message(connection_id, {
            "type": "connected",
            "connection_id": connection_id,
            "project_id": project_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        logger.info("WebSocket file manager connection added", 
                   connection_id=connection_id, project_id=project_id)
    
    async def disconnect(self, connection_id: str):
        """Remove WebSocket connection."""
        if connection_id in self.connections:
            websocket = self.connections[connection_id]
            project_id = self.connection_projects.get(connection_id)
            
            await self.event_manager.remove_websocket_connection(websocket, project_id)
            
            del self.connections[connection_id]
            if connection_id in self.connection_projects:
                del self.connection_projects[connection_id]
            
            logger.info("WebSocket file manager connection removed", 
                       connection_id=connection_id, project_id=project_id)
    
    async def handle_message(self, connection_id: str, message: Dict[str, Any]):
        """Handle incoming WebSocket message."""
        try:
            message_type = message.get("type")
            data = message.get("data", {})
            
            if message_type == "get_file_tree":
                await self._handle_get_file_tree(connection_id, data)
            elif message_type == "get_file_content":
                await self._handle_get_file_content(connection_id, data)
            elif message_type == "start_watching":
                await self._handle_start_watching(connection_id, data)
            elif message_type == "stop_watching":
                await self._handle_stop_watching(connection_id, data)
            elif message_type == "get_watchers":
                await self._handle_get_watchers(connection_id, data)
            elif message_type == "ping":
                await self._handle_ping(connection_id, data)
            else:
                await self._send_error(connection_id, f"Unknown message type: {message_type}")
                
        except Exception as e:
            logger.error("Error handling WebSocket message", 
                        connection_id=connection_id, error=str(e))
            await self._send_error(connection_id, f"Error processing message: {str(e)}")
    
    async def _handle_get_file_tree(self, connection_id: str, data: Dict[str, Any]):
        """Handle get file tree request."""
        path = data.get("path")
        max_depth = data.get("max_depth", 3)
        
        if not path:
            await self._send_error(connection_id, "Path is required")
            return
        
        if not os.path.exists(path):
            await self._send_error(connection_id, "Path not found")
            return
        
        if not os.path.isdir(path):
            await self._send_error(connection_id, "Path is not a directory")
            return
        
        try:
            tree = self._build_file_tree(path, max_depth)
            await self._send_message(connection_id, {
                "type": "file_tree",
                "data": {
                    "tree": tree.dict(),
                    "path": path,
                    "max_depth": max_depth,
                    "scan_time": datetime.utcnow().isoformat()
                }
            })
        except Exception as e:
            await self._send_error(connection_id, f"Error building file tree: {str(e)}")
    
    async def _handle_get_file_content(self, connection_id: str, data: Dict[str, Any]):
        """Handle get file content request."""
        file_path = data.get("file_path")
        
        if not file_path:
            await self._send_error(connection_id, "File path is required")
            return
        
        if not os.path.exists(file_path):
            await self._send_error(connection_id, "File not found")
            return
        
        if not os.path.isfile(file_path):
            await self._send_error(connection_id, "Path is not a file")
            return
        
        try:
            file_content = self._read_file_content(file_path)
            await self._send_message(connection_id, {
                "type": "file_content",
                "data": file_content.dict()
            })
        except Exception as e:
            await self._send_error(connection_id, f"Error reading file: {str(e)}")
    
    async def _handle_start_watching(self, connection_id: str, data: Dict[str, Any]):
        """Handle start watching request."""
        path = data.get("path")
        project_id = data.get("project_id") or self.connection_projects.get(connection_id)
        recursive = data.get("recursive", True)
        
        if not path:
            await self._send_error(connection_id, "Path is required")
            return
        
        if not os.path.exists(path):
            await self._send_error(connection_id, "Path not found")
            return
        
        if not os.path.isdir(path):
            await self._send_error(connection_id, "Path is not a directory")
            return
        
        config = WatcherConfig(
            path=path,
            project_id=project_id,
            recursive=recursive
        )

        logger.info("Starting file watcher", path=path, project_id=project_id, recursive=recursive)
        success = self.file_watcher.start_watching(config)
        logger.info("File watcher start result", success=success)
        
        if success:
            await self._send_message(connection_id, {
                "type": "watching_started",
                "data": {
                    "path": path,
                    "project_id": project_id,
                    "recursive": recursive,
                    "status": "active"
                }
            })
        else:
            await self._send_error(connection_id, "Failed to start watching directory")
    
    async def _handle_stop_watching(self, connection_id: str, data: Dict[str, Any]):
        """Handle stop watching request."""
        path = data.get("path")
        
        if not path:
            await self._send_error(connection_id, "Path is required")
            return
        
        success = self.file_watcher.stop_watching(path)
        
        if success:
            await self._send_message(connection_id, {
                "type": "watching_stopped",
                "data": {
                    "path": path,
                    "status": "stopped"
                }
            })
        else:
            await self._send_error(connection_id, "Failed to stop watching directory")
    
    async def _handle_get_watchers(self, connection_id: str, data: Dict[str, Any]):
        """Handle get watchers request."""
        statuses = self.file_watcher.get_all_statuses()
        connection_stats = self.event_manager.get_connection_stats()
        
        await self._send_message(connection_id, {
            "type": "watchers_status",
            "data": {
                "active_watchers": [status.dict() for status in statuses],
                "connection_stats": connection_stats,
                "status": "active"
            }
        })
    
    async def _handle_ping(self, connection_id: str, data: Dict[str, Any]):
        """Handle ping request."""
        await self._send_message(connection_id, {
            "type": "pong",
            "data": {
                "timestamp": datetime.utcnow().isoformat()
            }
        })
    
    def _build_file_tree(self, dir_path: str, max_depth: int, current_depth: int = 0) -> FileTreeNode:
        """Build file tree recursively with gitignore-style filtering."""
        if current_depth >= max_depth:
            return FileTreeNode(
                name=os.path.basename(dir_path) or dir_path,
                path=dir_path,
                type=FileType.DIRECTORY
            )

        tree = FileTreeNode(
            name=os.path.basename(dir_path) or dir_path,
            path=dir_path,
            type=FileType.DIRECTORY
        )

        # Create gitignore matcher for this directory
        ignore_matcher = GitignoreMatcher(settings.file_watch_ignore_patterns, dir_path)

        try:
            items = sorted(os.listdir(dir_path))
            for item in items:
                item_path = os.path.join(dir_path, item)
                is_directory = os.path.isdir(item_path)

                # Check if item should be ignored
                if ignore_matcher.should_ignore(item_path, is_directory):
                    logger.debug("Skipping ignored item", path=item_path)
                    continue

                # Skip hidden files (unless explicitly included)
                if item.startswith('.') and not any(pattern.startswith('.') for pattern in settings.file_watch_ignore_patterns):
                    continue

                if is_directory:
                    child_tree = self._build_file_tree(item_path, max_depth, current_depth + 1)
                    tree.children.append(child_tree)
                else:
                    try:
                        stat_info = os.stat(item_path)
                        tree.children.append(FileTreeNode(
                            name=item,
                            path=item_path,
                            type=FileType.FILE,
                            size=stat_info.st_size,
                            modified=datetime.fromtimestamp(stat_info.st_mtime)
                        ))
                    except (OSError, IOError) as e:
                        logger.warning("Cannot access file", path=item_path, error=str(e))

        except PermissionError:
            logger.warning("Permission denied", path=dir_path)
        except Exception as e:
            logger.error("Error building file tree", path=dir_path, error=str(e))
        
        return tree
    
    def _read_file_content(self, file_path: str) -> FileContent:
        """Read file content."""
        stat_info = os.stat(file_path)
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            return FileContent(
                path=file_path,
                content=content,
                encoding="utf-8",
                size=stat_info.st_size,
                modified=datetime.fromtimestamp(stat_info.st_mtime),
                is_binary=False
            )
        
        except UnicodeDecodeError:
            return FileContent(
                path=file_path,
                content=None,
                encoding="binary",
                size=stat_info.st_size,
                modified=datetime.fromtimestamp(stat_info.st_mtime),
                is_binary=True,
                mime_type="application/octet-stream"
            )
    
    async def _send_message(self, connection_id: str, data: Dict[str, Any]):
        """Send message to specific connection."""
        if connection_id not in self.connections:
            return

        websocket = self.connections[connection_id]
        try:
            # Use custom JSON encoder to handle datetime objects
            await websocket.send_text(json.dumps(data, default=str))
        except Exception as e:
            logger.error("Error sending WebSocket message",
                        connection_id=connection_id, error=str(e))
            await self.disconnect(connection_id)
    
    async def _send_error(self, connection_id: str, error_message: str):
        """Send error message to specific connection."""
        await self._send_message(connection_id, {
            "type": "error",
            "data": {
                "message": error_message,
                "timestamp": datetime.utcnow().isoformat()
            }
        })
    
    def _handle_file_event(self, event: FileEvent):
        """Handle file system event from watcher."""
        # This will be called by the file watcher
        logger.info("File event received",
                   event_type=event.event_type,
                   path=event.src_path,
                   project_id=event.project_id)

        # Schedule the async event handling
        asyncio.create_task(self._async_handle_file_event(event))

    async def _async_handle_file_event(self, event: FileEvent):
        """Async handler for file events."""
        try:
            # Check if the file should be ignored
            ignore_matcher = GitignoreMatcher(settings.file_watch_ignore_patterns, "")
            is_ignored = ignore_matcher.should_ignore(
                event.src_path,
                event.file_type == FileType.DIRECTORY
            )

            logger.info("File event processing",
                       path=event.src_path,
                       event_type=event.event_type,
                       is_ignored=is_ignored,
                       patterns_count=len(settings.file_watch_ignore_patterns))

            # Broadcast to event manager
            await self.event_manager.handle_file_event(event)

            # Also send directly to all connected clients
            message = {
                "type": "file_event",
                "data": {
                    "event_type": event.event_type,
                    "src_path": event.src_path,
                    "dest_path": event.dest_path,
                    "project_id": event.project_id,
                    "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                    "file_type": event.file_type,
                    "is_directory": event.file_type == "directory",
                    "should_ignore": is_ignored  # 添加忽略标志
                }
            }

            # Send to all connections (or filter by project_id if needed)
            for connection_id, websocket in self.connections.items():
                try:
                    await websocket.send_text(json.dumps(message, default=str))
                except Exception as e:
                    logger.error("Error sending file event to connection",
                               connection_id=connection_id, error=str(e))

        except Exception as e:
            logger.error("Error in async file event handler", error=str(e))
    
    async def cleanup(self):
        """Cleanup resources."""
        self.file_watcher.stop_all()
        await self.event_manager.stop()
        
        # Close all connections
        for connection_id in list(self.connections.keys()):
            await self.disconnect(connection_id)
