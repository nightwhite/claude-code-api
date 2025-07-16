"""
Claude Code API Gateway

A FastAPI-based service that provides OpenAI-compatible endpoints
while leveraging Claude Code's powerful workflow capabilities.
"""

import os
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from claude_code_api.core.config import settings
from claude_code_api.core.database import create_tables, close_database
from claude_code_api.core.session_manager import SessionManager
from claude_code_api.core.claude_manager import ClaudeManager
from claude_code_api.api.chat import router as chat_router
from claude_code_api.api.models import router as models_router
from claude_code_api.api.projects import router as projects_router
from claude_code_api.api.sessions import router as sessions_router
from claude_code_api.core.auth import auth_middleware


# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    logger.info("Starting Claude Code API Gateway", version="1.0.0")
    
    # Initialize database
    await create_tables()
    logger.info("Database initialized")
    
    # Initialize managers
    app.state.session_manager = SessionManager()
    app.state.claude_manager = ClaudeManager()
    logger.info("Managers initialized")

    # Initialize file watching components
    from .core.event_manager import EventManager
    from .core.file_watcher import FileWatcher

    # Create event manager
    event_manager = EventManager()
    event_manager.start()
    app.state.event_manager = event_manager

    # Create file watcher
    file_watcher = FileWatcher(event_manager.handle_file_event)
    app.state.file_watcher = file_watcher

    logger.info("File watching system initialized")
    
    # Verify Claude Code availability
    try:
        claude_version = await app.state.claude_manager.get_version()
        logger.info("Claude Code available", version=claude_version)
    except Exception as e:
        logger.error("Claude Code not available", error=str(e))
        raise HTTPException(
            status_code=503,
            detail="Claude Code CLI not available. Please ensure Claude Code is installed and accessible."
        )
    
    yield

    # Cleanup file watching components
    if hasattr(app.state, 'file_watcher'):
        app.state.file_watcher.stop_all()
        logger.info("File watchers stopped")

    if hasattr(app.state, 'event_manager'):
        await app.state.event_manager.stop()
        logger.info("Event manager stopped")

    # Cleanup other components
    logger.info("Shutting down Claude Code API Gateway")
    await app.state.session_manager.cleanup_all()
    await close_database()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Claude Code API Gateway",
    description="OpenAI-compatible API for Claude Code with enhanced project management",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication middleware
app.middleware("http")(auth_middleware)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler with structured logging."""
    logger.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal server error",
                "type": "internal_error",
                "code": "internal_error"
            }
        }
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        # Check Claude Code availability
        claude_version = await app.state.claude_manager.get_version()
        
        return {
            "status": "healthy",
            "version": "1.0.0",
            "claude_version": claude_version,
            "active_sessions": len(app.state.session_manager.active_sessions)
        }
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e)
            }
        )


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Claude Code API Gateway",
        "version": "1.0.0",
        "description": "OpenAI-compatible API for Claude Code",
        "endpoints": {
            "chat": "/v1/chat/completions",
            "models": "/v1/models",
            "projects": "/v1/projects",
            "sessions": "/v1/sessions"
        },
        "docs": "/docs",
        "health": "/health"
    }


# Include API routers
app.include_router(chat_router, prefix="/v1", tags=["chat"])
app.include_router(models_router, prefix="/v1", tags=["models"])
app.include_router(projects_router, prefix="/v1", tags=["projects"])
app.include_router(sessions_router, prefix="/v1", tags=["sessions"])

# Import and include files router
from .api import files
app.include_router(files.router, prefix="/v1", tags=["files"])


def main():
    """Main entry point for the application."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Claude Code API Gateway")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (development mode)")
    parser.add_argument("--log-level", default="info", help="Log level")

    args = parser.parse_args()

    uvicorn.run(
        "claude_code_api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level
    )


if __name__ == "__main__":
    main()
