"""Projects API endpoint - Extension to OpenAI API."""

import uuid
import os
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import JSONResponse
import structlog

from claude_code_api.models.openai import (
    ProjectInfo, 
    CreateProjectRequest,
    PaginatedResponse,
    PaginationInfo
)
from claude_code_api.core.database import db_manager, Project
from claude_code_api.core.claude_manager import create_project_directory, cleanup_project_directory

logger = structlog.get_logger()
router = APIRouter()


@router.get("/projects", response_model=PaginatedResponse)
async def list_projects(
    page: int = 1,
    per_page: int = 20,
    req: Request = None
) -> PaginatedResponse:
    """List all projects."""
    
    # Get projects from database
    try:
        db_projects = await db_manager.get_projects(page=page, per_page=per_page)
        total_items = await db_manager.count_projects()

        # Convert database projects to ProjectInfo objects
        projects = []
        for db_project in db_projects:
            projects.append(ProjectInfo(
                id=db_project.id,
                name=db_project.name,
                description=db_project.description or "",
                path=db_project.path,
                created_at=db_project.created_at,
                updated_at=db_project.updated_at,
                is_active=db_project.is_active
            ))

        # Calculate pagination info
        total_pages = (total_items + per_page - 1) // per_page
        has_next = page < total_pages
        has_prev = page > 1

        pagination = PaginationInfo(
            page=page,
            per_page=per_page,
            total_items=total_items,
            total_pages=total_pages,
            has_next=has_next,
            has_prev=has_prev
        )

    except Exception as e:
        logger.error("Failed to get projects from database", error=str(e))
        # Fallback to empty list
        projects = []
        pagination = PaginationInfo(
            page=page,
            per_page=per_page,
            total_items=0,
            total_pages=0,
            has_next=False,
            has_prev=False
        )
    
    return PaginatedResponse(
        data=projects,
        pagination=pagination
    )


@router.post("/projects", response_model=ProjectInfo)
async def create_project(
    project_request: CreateProjectRequest,
    req: Request
) -> ProjectInfo:
    """Create a new project."""

    # Determine project path
    if project_request.path:
        project_path = project_request.path
        os.makedirs(project_path, exist_ok=True)

        # Check if a project with this path already exists
        existing_project = await db_manager.get_project_by_path(project_path)
        if existing_project:
            logger.info(
                "Project with path already exists, returning existing project",
                project_id=existing_project.id,
                path=project_path
            )
            return ProjectInfo(
                id=existing_project.id,
                name=existing_project.name,
                description=existing_project.description or "",
                path=existing_project.path,
                created_at=existing_project.created_at,
                updated_at=existing_project.updated_at,
                is_active=existing_project.is_active
            )
    else:
        project_id = str(uuid.uuid4())
        project_path = create_project_directory(project_id)

    # Create new project
    project_id = str(uuid.uuid4())
    project_data = {
        "id": project_id,
        "name": project_request.name,
        "description": project_request.description,
        "path": project_path,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "is_active": True
    }

    try:
        await db_manager.create_project(project_data)

        project_info = ProjectInfo(**project_data)

        logger.info(
            "Project created",
            project_id=project_id,
            name=project_request.name,
            path=project_path
        )

        return project_info

    except Exception as e:
        # If it's a unique constraint error on path, try to get existing project
        if "UNIQUE constraint failed: projects.path" in str(e):
            existing_project = await db_manager.get_project_by_path(project_path)
            if existing_project:
                logger.info(
                    "Path conflict resolved by returning existing project",
                    project_id=existing_project.id,
                    path=project_path
                )
                return ProjectInfo(
                    id=existing_project.id,
                    name=existing_project.name,
                    description=existing_project.description or "",
                    path=existing_project.path,
                    created_at=existing_project.created_at,
                    updated_at=existing_project.updated_at,
                    is_active=existing_project.is_active
                )

        logger.error("Failed to create project", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": {
                    "message": f"Failed to create project: {str(e)}",
                    "type": "internal_error",
                    "code": "project_creation_failed"
                }
            }
        )


@router.get("/projects/{project_id}", response_model=ProjectInfo)
async def get_project(project_id: str, req: Request) -> ProjectInfo:
    """Get project by ID."""
    
    project = await db_manager.get_project(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "message": f"Project {project_id} not found",
                    "type": "not_found",
                    "code": "project_not_found"
                }
            }
        )
    
    return ProjectInfo(
        id=project.id,
        name=project.name,
        description=project.description,
        path=project.path,
        created_at=project.created_at,
        updated_at=project.updated_at,
        is_active=project.is_active
    )


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, req: Request) -> JSONResponse:
    """Delete project by ID."""
    
    project = await db_manager.get_project(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "message": f"Project {project_id} not found",
                    "type": "not_found",
                    "code": "project_not_found"
                }
            }
        )
    
    # TODO: Implement project deletion in database
    # cleanup_project_directory(project.path)
    
    logger.info("Project deleted", project_id=project_id)
    
    return JSONResponse(
        content={
            "project_id": project_id,
            "status": "deleted"
        }
    )
