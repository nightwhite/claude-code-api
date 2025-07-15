"""
File management API for directory synchronization and web display.
"""

import os
import json
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import JSONResponse
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import structlog

logger = structlog.get_logger()

router = APIRouter()

# WebSocket 连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.watchers: Dict[str, Observer] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except:
                await self.disconnect(connection)

manager = ConnectionManager()

# 文件变化监控
class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, connection_manager: ConnectionManager):
        self.manager = connection_manager

    def on_any_event(self, event):
        if event.is_directory:
            return
        
        asyncio.create_task(self.manager.broadcast({
            "type": "file_change",
            "event_type": event.event_type,
            "src_path": event.src_path,
            "timestamp": datetime.now().isoformat()
        }))

@router.get("/files/tree")
async def get_file_tree(
    path: str = Query(..., description="Directory path to scan"),
    max_depth: int = Query(3, description="Maximum depth to scan")
) -> Dict[str, Any]:
    """Get directory tree structure."""
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path not found")
    
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Path is not a directory")
    
    def build_tree(dir_path: str, current_depth: int = 0) -> Dict[str, Any]:
        if current_depth >= max_depth:
            return {}
        
        tree = {
            "name": os.path.basename(dir_path) or dir_path,
            "path": dir_path,
            "type": "directory",
            "children": []
        }
        
        try:
            items = sorted(os.listdir(dir_path))
            for item in items:
                if item.startswith('.'):  # Skip hidden files
                    continue
                    
                item_path = os.path.join(dir_path, item)
                
                if os.path.isdir(item_path):
                    child_tree = build_tree(item_path, current_depth + 1)
                    if child_tree:
                        tree["children"].append(child_tree)
                else:
                    tree["children"].append({
                        "name": item,
                        "path": item_path,
                        "type": "file",
                        "size": os.path.getsize(item_path),
                        "modified": datetime.fromtimestamp(
                            os.path.getmtime(item_path)
                        ).isoformat()
                    })
        except PermissionError:
            logger.warning("Permission denied", path=dir_path)
        
        return tree
    
    tree = build_tree(path)
    
    return {
        "tree": tree,
        "total_files": sum(1 for _, _, files in os.walk(path) for _ in files),
        "scan_time": datetime.now().isoformat()
    }

@router.get("/files/content")
async def get_file_content(
    file_path: str = Query(..., description="File path to read")
) -> Dict[str, Any]:
    """Get file content."""
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=400, detail="Path is not a file")
    
    try:
        # Try to read as text first
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        file_info = os.stat(file_path)
        
        return {
            "path": file_path,
            "content": content,
            "size": file_info.st_size,
            "modified": datetime.fromtimestamp(file_info.st_mtime).isoformat(),
            "encoding": "utf-8",
            "type": "text"
        }
    
    except UnicodeDecodeError:
        # If not text, return file info only
        file_info = os.stat(file_path)
        return {
            "path": file_path,
            "content": None,
            "size": file_info.st_size,
            "modified": datetime.fromtimestamp(file_info.st_mtime).isoformat(),
            "encoding": "binary",
            "type": "binary",
            "message": "Binary file - content not displayed"
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

@router.post("/files/watch")
async def start_watching(
    path: str = Query(..., description="Directory path to watch")
) -> Dict[str, str]:
    """Start watching a directory for changes."""
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path not found")
    
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Path is not a directory")
    
    # Stop existing watcher if any
    if path in manager.watchers:
        manager.watchers[path].stop()
        del manager.watchers[path]
    
    # Start new watcher
    event_handler = FileChangeHandler(manager)
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()
    
    manager.watchers[path] = observer
    
    logger.info("Started watching directory", path=path)
    
    return {
        "message": f"Started watching {path}",
        "path": path,
        "status": "active"
    }

@router.delete("/files/watch")
async def stop_watching(
    path: str = Query(..., description="Directory path to stop watching")
) -> Dict[str, str]:
    """Stop watching a directory."""
    
    if path not in manager.watchers:
        raise HTTPException(status_code=404, detail="No watcher found for this path")
    
    manager.watchers[path].stop()
    del manager.watchers[path]
    
    logger.info("Stopped watching directory", path=path)
    
    return {
        "message": f"Stopped watching {path}",
        "path": path,
        "status": "stopped"
    }

@router.websocket("/files/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time file change notifications."""
    
    await manager.connect(websocket)
    
    try:
        while True:
            # Keep connection alive and handle client messages
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")

@router.get("/files/watchers")
async def get_active_watchers() -> Dict[str, Any]:
    """Get list of active file watchers."""
    
    return {
        "active_watchers": list(manager.watchers.keys()),
        "total_connections": len(manager.active_connections),
        "status": "active"
    }
