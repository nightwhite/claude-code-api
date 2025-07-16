"""
Event manager for file system events and WebSocket broadcasting.
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List, Set, Optional, Callable
from collections import defaultdict, deque
import structlog

from ..models.file_events import FileEvent, WebSocketMessage

logger = structlog.get_logger()


class EventAggregator:
    """Aggregates and deduplicates file events."""
    
    def __init__(self, window_seconds: float = 1.0, max_events: int = 100):
        self.window_seconds = window_seconds
        self.max_events = max_events
        self.events: Dict[str, FileEvent] = {}  # path -> latest event
        self.event_times: deque = deque()  # (timestamp, path) pairs
        self._lock = asyncio.Lock()
    
    async def add_event(self, event: FileEvent) -> bool:
        """Add event to aggregator. Returns True if event should be processed."""
        async with self._lock:
            path = event.src_path
            now = datetime.utcnow()
            
            # Clean old events
            await self._cleanup_old_events(now)
            
            # Check if we should aggregate this event
            existing_event = self.events.get(path)
            if existing_event:
                # Update existing event with latest data
                self.events[path] = event
                return False  # Don't process immediately, wait for aggregation
            else:
                # New event
                self.events[path] = event
                self.event_times.append((now, path))
                
                # Check if we should flush immediately for certain event types
                if event.event_type in ["deleted", "moved"]:
                    return True  # Process immediately
                
                return len(self.events) >= self.max_events
    
    async def get_pending_events(self) -> List[FileEvent]:
        """Get all pending events and clear the aggregator."""
        async with self._lock:
            events = list(self.events.values())
            self.events.clear()
            self.event_times.clear()
            return events
    
    async def _cleanup_old_events(self, now: datetime):
        """Remove events older than window."""
        cutoff = now.timestamp() - self.window_seconds
        
        while self.event_times and self.event_times[0][0].timestamp() < cutoff:
            _, path = self.event_times.popleft()
            self.events.pop(path, None)


class WebSocketManager:
    """Manages WebSocket connections and message broadcasting."""
    
    def __init__(self):
        self.connections: Set[object] = set()  # WebSocket connections
        self.project_connections: Dict[str, Set[object]] = defaultdict(set)
        self._lock = asyncio.Lock()
    
    async def add_connection(self, websocket, project_id: Optional[str] = None):
        """Add WebSocket connection."""
        async with self._lock:
            self.connections.add(websocket)
            if project_id:
                self.project_connections[project_id].add(websocket)
            
        logger.info("WebSocket connection added", 
                   total_connections=len(self.connections),
                   project_id=project_id)
    
    async def remove_connection(self, websocket, project_id: Optional[str] = None):
        """Remove WebSocket connection."""
        async with self._lock:
            self.connections.discard(websocket)
            if project_id and project_id in self.project_connections:
                self.project_connections[project_id].discard(websocket)
                if not self.project_connections[project_id]:
                    del self.project_connections[project_id]
        
        logger.info("WebSocket connection removed", 
                   total_connections=len(self.connections),
                   project_id=project_id)
    
    async def broadcast_to_all(self, message: WebSocketMessage):
        """Broadcast message to all connections."""
        if not self.connections:
            return
        
        message_json = json.dumps(message.dict())
        disconnected = set()
        
        for websocket in self.connections.copy():
            try:
                await websocket.send_text(message_json)
            except Exception as e:
                logger.warning("Failed to send message to WebSocket", error=str(e))
                disconnected.add(websocket)
        
        # Clean up disconnected connections
        if disconnected:
            async with self._lock:
                self.connections -= disconnected
                for project_connections in self.project_connections.values():
                    project_connections -= disconnected
    
    async def broadcast_to_project(self, project_id: str, message: WebSocketMessage):
        """Broadcast message to connections for specific project."""
        if project_id not in self.project_connections:
            return
        
        connections = self.project_connections[project_id].copy()
        if not connections:
            return
        
        message_json = json.dumps(message.dict())
        disconnected = set()
        
        for websocket in connections:
            try:
                await websocket.send_text(message_json)
            except Exception as e:
                logger.warning("Failed to send message to WebSocket", 
                             project_id=project_id, error=str(e))
                disconnected.add(websocket)
        
        # Clean up disconnected connections
        if disconnected:
            async with self._lock:
                self.project_connections[project_id] -= disconnected
                self.connections -= disconnected
    
    def get_connection_count(self) -> int:
        """Get total connection count."""
        return len(self.connections)
    
    def get_project_connection_count(self, project_id: str) -> int:
        """Get connection count for specific project."""
        return len(self.project_connections.get(project_id, set()))


class EventManager:
    """Central event manager for file system events."""
    
    def __init__(self):
        self.websocket_manager = WebSocketManager()
        self.event_aggregator = EventAggregator()
        self.event_handlers: List[Callable[[FileEvent], None]] = []
        self._processing_task: Optional[asyncio.Task] = None
        self._shutdown = False
    
    def start(self):
        """Start the event manager."""
        if self._processing_task is None or self._processing_task.done():
            self._shutdown = False
            self._processing_task = asyncio.create_task(self._process_events())
            logger.info("Event manager started")
    
    async def stop(self):
        """Stop the event manager."""
        self._shutdown = True
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        logger.info("Event manager stopped")
    
    def add_event_handler(self, handler: Callable[[FileEvent], None]):
        """Add custom event handler."""
        self.event_handlers.append(handler)
    
    def remove_event_handler(self, handler: Callable[[FileEvent], None]):
        """Remove custom event handler."""
        if handler in self.event_handlers:
            self.event_handlers.remove(handler)
    
    async def handle_file_event(self, event: FileEvent):
        """Handle incoming file event."""
        try:
            # Add to aggregator
            should_process = await self.event_aggregator.add_event(event)
            
            if should_process:
                # Process immediately for critical events
                await self._process_single_event(event)
            
        except Exception as e:
            logger.error("Error handling file event", event=event.dict(), error=str(e))
    
    async def _process_events(self):
        """Background task to process aggregated events."""
        while not self._shutdown:
            try:
                # Wait for aggregation window
                await asyncio.sleep(1.0)
                
                # Get pending events
                events = await self.event_aggregator.get_pending_events()
                
                # Process events
                for event in events:
                    await self._process_single_event(event)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in event processing loop", error=str(e))
                await asyncio.sleep(1.0)  # Prevent tight loop on errors
    
    async def _process_single_event(self, event: FileEvent):
        """Process a single file event."""
        try:
            # Create WebSocket message
            message = WebSocketMessage(
                type="file_event",
                data=event.dict()
            )
            
            # Broadcast to appropriate connections
            if event.project_id:
                await self.websocket_manager.broadcast_to_project(event.project_id, message)
            else:
                await self.websocket_manager.broadcast_to_all(message)
            
            # Call custom handlers
            for handler in self.event_handlers:
                try:
                    handler(event)
                except Exception as e:
                    logger.error("Error in custom event handler", error=str(e))
            
            logger.debug("Processed file event", 
                        event_type=event.event_type,
                        path=event.src_path,
                        project_id=event.project_id)
            
        except Exception as e:
            logger.error("Error processing file event", event=event.dict(), error=str(e))
    
    # WebSocket management methods
    async def add_websocket_connection(self, websocket, project_id: Optional[str] = None):
        """Add WebSocket connection."""
        await self.websocket_manager.add_connection(websocket, project_id)
    
    async def remove_websocket_connection(self, websocket, project_id: Optional[str] = None):
        """Remove WebSocket connection."""
        await self.websocket_manager.remove_connection(websocket, project_id)
    
    def get_connection_stats(self) -> Dict[str, int]:
        """Get connection statistics."""
        return {
            "total_connections": self.websocket_manager.get_connection_count(),
            "project_connections": {
                project_id: self.websocket_manager.get_project_connection_count(project_id)
                for project_id in self.websocket_manager.project_connections.keys()
            }
        }
