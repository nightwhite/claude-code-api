"""
File system watcher using watchdog.
"""

import os
import asyncio
import fnmatch
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
import structlog

from ..models.file_events import (
    FileEvent, FileEventType, FileType, WatcherConfig, WatcherStatus
)
from ..utils.gitignore_matcher import GitignoreMatcher
from ..core.config import settings

logger = structlog.get_logger()


class AsyncFileEventHandler(FileSystemEventHandler):
    """Async file system event handler."""
    
    def __init__(
        self,
        config: WatcherConfig,
        event_callback: Callable[[FileEvent], None],
        loop: asyncio.AbstractEventLoop
    ):
        super().__init__()
        self.config = config
        self.event_callback = event_callback
        self.loop = loop
        self.last_events: Dict[str, datetime] = {}

        # Initialize gitignore matcher
        self.ignore_matcher = GitignoreMatcher(
            settings.file_watch_ignore_patterns,
            config.path
        )
        
    def _should_ignore(self, path: str) -> bool:
        """Check if path should be ignored using gitignore-style patterns."""
        try:
            # Check if it's a directory
            is_directory = os.path.isdir(path)

            # Use gitignore matcher for pattern matching
            if self.ignore_matcher.should_ignore(path, is_directory):
                logger.debug("Path ignored by gitignore patterns", path=path)
                return True

            # Legacy: Check old-style ignore patterns from config
            if hasattr(self.config, 'ignore_patterns') and self.config.ignore_patterns:
                path_obj = Path(path)
                for pattern in self.config.ignore_patterns:
                    if fnmatch.fnmatch(path_obj.name, pattern) or fnmatch.fnmatch(str(path_obj), pattern):
                        logger.debug("Path ignored by legacy pattern", path=path, pattern=pattern)
                        return True

            # Check include patterns (legacy)
            if hasattr(self.config, 'include_patterns') and self.config.include_patterns:
                included = False
                path_obj = Path(path)
                for pattern in self.config.include_patterns:
                    if fnmatch.fnmatch(path_obj.name, pattern) or fnmatch.fnmatch(str(path_obj), pattern):
                        included = True
                        break
                if not included:
                    logger.debug("Path not in include patterns", path=path)
                    return True

            # Check file size
            if os.path.isfile(path):
                try:
                    size = os.path.getsize(path)
                    max_size = getattr(self.config, 'max_file_size', 100 * 1024 * 1024)  # 100MB default
                    if size > max_size:
                        logger.debug("File too large", path=path, size=size, max_size=max_size)
                        return True
                except (OSError, IOError):
                    logger.debug("Cannot access file", path=path)
                    return True

            return False

        except Exception as e:
            logger.error("Error checking if path should be ignored", path=path, error=str(e))
            return True  # Err on the side of caution
    
    def _should_debounce(self, path: str) -> bool:
        """Check if event should be debounced."""
        now = datetime.utcnow()
        last_time = self.last_events.get(path)
        
        if last_time:
            delta = (now - last_time).total_seconds()
            if delta < self.config.debounce_seconds:
                return True
        
        self.last_events[path] = now
        return False
    
    def _create_file_event(
        self,
        event_type: FileEventType,
        src_path: str,
        dest_path: Optional[str] = None
    ) -> FileEvent:
        """Create file event from filesystem event."""
        file_type = FileType.DIRECTORY if os.path.isdir(src_path) else FileType.FILE
        file_size = None
        
        if file_type == FileType.FILE and os.path.exists(src_path):
            try:
                file_size = os.path.getsize(src_path)
            except (OSError, IOError):
                pass
        
        return FileEvent(
            event_type=event_type,
            src_path=src_path,
            dest_path=dest_path,
            file_type=file_type,
            file_size=file_size,
            project_id=self.config.project_id,
            metadata={
                "watcher_path": self.config.path,
                "recursive": self.config.recursive
            }
        )
    
    def _handle_event(self, event_type: FileEventType, event: FileSystemEvent):
        """Handle file system event."""
        src_path = event.src_path
        dest_path = getattr(event, 'dest_path', None)
        
        # Apply filters
        if self._should_ignore(src_path):
            return
        
        if self._should_debounce(src_path):
            return
        
        # Create file event
        file_event = self._create_file_event(event_type, src_path, dest_path)
        
        # Schedule callback in event loop
        try:
            if self.loop and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.event_callback, file_event)
            else:
                # Fallback: call directly
                self.event_callback(file_event)
        except Exception as e:
            logger.error("Error scheduling event callback", error=str(e))
            # Fallback: call directly
            self.event_callback(file_event)
    
    def on_created(self, event: FileSystemEvent):
        """Handle file/directory creation."""
        self._handle_event(FileEventType.CREATED, event)
    
    def on_modified(self, event: FileSystemEvent):
        """Handle file/directory modification."""
        if not event.is_directory:  # Only handle file modifications
            self._handle_event(FileEventType.MODIFIED, event)
    
    def on_deleted(self, event: FileSystemEvent):
        """Handle file/directory deletion."""
        self._handle_event(FileEventType.DELETED, event)
    
    def on_moved(self, event: FileSystemEvent):
        """Handle file/directory move."""
        self._handle_event(FileEventType.MOVED, event)


class FileWatcher:
    """File system watcher."""
    
    def __init__(self, event_callback: Callable[[FileEvent], None]):
        self.event_callback = event_callback
        self.observers: Dict[str, Observer] = {}
        self.configs: Dict[str, WatcherConfig] = {}
        self.statuses: Dict[str, WatcherStatus] = {}
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        
    def start_watching(self, config: WatcherConfig) -> bool:
        """Start watching a directory."""
        path = config.path

        logger.info("FileWatcher.start_watching called", path=path, config=config.dict())

        if not os.path.exists(path):
            logger.error("Path does not exist", path=path)
            return False

        if not os.path.isdir(path):
            logger.error("Path is not a directory", path=path)
            return False
        
        # Stop existing watcher if any
        self.stop_watching(path)
        
        try:
            # Create event handler
            handler = AsyncFileEventHandler(config, self._on_file_event, self.loop)
            
            # Create and start observer
            observer = Observer()
            observer.schedule(handler, path, recursive=config.recursive)
            observer.start()
            
            # Store references
            self.observers[path] = observer
            self.configs[path] = config
            self.statuses[path] = WatcherStatus(
                path=path,
                project_id=config.project_id,
                is_active=True,
                created_at=datetime.utcnow()
            )
            
            logger.info("Started watching directory", path=path, project_id=config.project_id)
            return True
            
        except Exception as e:
            logger.error("Failed to start watching", path=path, error=str(e))
            return False
    
    def stop_watching(self, path: str) -> bool:
        """Stop watching a directory."""
        if path not in self.observers:
            return False
        
        try:
            observer = self.observers[path]
            observer.stop()
            observer.join(timeout=5.0)
            
            # Clean up
            del self.observers[path]
            del self.configs[path]
            if path in self.statuses:
                self.statuses[path].is_active = False
            
            logger.info("Stopped watching directory", path=path)
            return True
            
        except Exception as e:
            logger.error("Failed to stop watching", path=path, error=str(e))
            return False
    
    def stop_all(self):
        """Stop all watchers."""
        paths = list(self.observers.keys())
        for path in paths:
            self.stop_watching(path)
    
    def get_status(self, path: str) -> Optional[WatcherStatus]:
        """Get watcher status."""
        return self.statuses.get(path)
    
    def get_all_statuses(self) -> List[WatcherStatus]:
        """Get all watcher statuses."""
        return list(self.statuses.values())
    
    def is_watching(self, path: str) -> bool:
        """Check if path is being watched."""
        return path in self.observers and self.observers[path].is_alive()
    
    def _on_file_event(self, event: FileEvent):
        """Handle file event from watcher."""
        try:
            # Update status
            if event.src_path in self.statuses:
                status = self.statuses[event.src_path]
                status.last_event_at = event.timestamp
                status.event_count += 1
            
            # Call user callback
            self.event_callback(event)
            
        except Exception as e:
            logger.error("Error handling file event", event=event.dict(), error=str(e))
            
            # Update error status
            path = event.metadata.get("watcher_path")
            if path and path in self.statuses:
                status = self.statuses[path]
                status.error_count += 1
                status.last_error = str(e)
