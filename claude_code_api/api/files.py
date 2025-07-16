"""
WebSocket-based file management API.
"""

import json
import uuid
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog

from ..core.websocket_file_manager import WebSocketFileManager

logger = structlog.get_logger()

router = APIRouter()

# Global WebSocket file manager instance
file_manager = WebSocketFileManager()

@router.websocket("/files/ws")
async def websocket_file_endpoint(websocket: WebSocket):
    """WebSocket endpoint for all file operations."""

    # Generate unique connection ID
    connection_id = str(uuid.uuid4())

    try:
        # Connect to file manager
        await file_manager.connect(websocket, connection_id)

        # Handle messages
        while True:
            # Receive message from client
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                await file_manager.handle_message(connection_id, message)
            except json.JSONDecodeError:
                await file_manager._send_error(connection_id, "Invalid JSON message")
            except Exception as e:
                logger.error("Error processing WebSocket message",
                           connection_id=connection_id, error=str(e))
                await file_manager._send_error(connection_id, f"Error: {str(e)}")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected", connection_id=connection_id)
    except Exception as e:
        logger.error("WebSocket error", connection_id=connection_id, error=str(e))
    finally:
        # Cleanup connection
        await file_manager.disconnect(connection_id)


