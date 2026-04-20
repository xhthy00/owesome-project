"""Datasource API routes."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.common.core.database import get_session
from src.datasource.schemas import (
    ConnectionTestResult,
    DatasourceCreate,
    DatasourceListResponse,
    DatasourceResponse,
    DatasourceUpdate,
)
from src.datasource.crud import crud_datasource
from src.common.utils.aes import decrypt_conf

router = APIRouter(prefix="/datasource", tags=["datasource"])


def get_datasource_with_config(
    datasource_id: int,
    session: Session,
) -> dict:
    """Helper to get datasource with decrypted config."""
    ds = crud_datasource.get_datasource_by_id(session, datasource_id)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource not found",
        )
    config = decrypt_conf(ds.configuration) if ds.configuration else {}
    return {
        "id": ds.id,
        "name": ds.name,
        "description": ds.description,
        "type": ds.type,
        "type_name": ds.type_name,
        "status": ds.status,
        "create_time": ds.create_time,
        "create_by": ds.create_by,
        "config": config,
    }


@router.get("", response_model=DatasourceListResponse)
def list_datasources(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    oid: Optional[int] = Query(None),
    session: Session = Depends(get_session),
):
    """List all datasources."""
    items = crud_datasource.get_datasources(session, skip, limit, oid)
    total = crud_datasource.count_datasources(session, oid)

    # Decrypt configs for response
    response_items = []
    for ds in items:
        config = decrypt_conf(ds.configuration) if ds.configuration else {}
        response_items.append({
            "id": ds.id,
            "name": ds.name,
            "description": ds.description,
            "type": ds.type,
            "type_name": ds.type_name,
            "status": ds.status,
            "create_time": ds.create_time,
            "create_by": ds.create_by,
        })

    return DatasourceListResponse(total=total, items=response_items)


@router.get("/{datasource_id}", response_model=DatasourceResponse)
def get_datasource(
    datasource_id: int,
    session: Session = Depends(get_session),
):
    """Get datasource by ID."""
    ds = crud_datasource.get_datasource_by_id(session, datasource_id)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource not found",
        )
    return ds


@router.post("", response_model=DatasourceResponse, status_code=status.HTTP_201_CREATED)
def create_datasource(
    datasource_in: DatasourceCreate,
    session: Session = Depends(get_session),
):
    """Create a new datasource."""
    # Build config dict
    config = datasource_in.config.model_dump()

    ds = crud_datasource.create_datasource(
        session,
        name=datasource_in.name,
        type=datasource_in.type,
        config=config,
        description=datasource_in.description,
    )
    return ds


@router.put("/{datasource_id}", response_model=DatasourceResponse)
def update_datasource(
    datasource_id: int,
    datasource_in: DatasourceUpdate,
    session: Session = Depends(get_session),
):
    """Update datasource."""
    # Build update dict, excluding None values
    update_data = datasource_in.model_dump(exclude_unset=True)

    # Handle config separately (rename to configuration)
    if "config" in update_data:
        config = update_data.pop("config")
        if config:
            update_data["config"] = config

    ds = crud_datasource.update_datasource(
        session,
        datasource_id,
        **update_data,
    )
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource not found",
        )
    return ds


@router.delete("/{datasource_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_datasource(
    datasource_id: int,
    session: Session = Depends(get_session),
):
    """Delete datasource."""
    success = crud_datasource.delete_datasource(session, datasource_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource not found",
        )


@router.post("/{datasource_id}/test-connection", response_model=ConnectionTestResult)
def test_connection(
    datasource_id: int,
    session: Session = Depends(get_session),
):
    """Test datasource connection."""
    ds = crud_datasource.get_datasource_by_id(session, datasource_id)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Datasource not found",
        )

    config = decrypt_conf(ds.configuration) if ds.configuration else {}

    try:
        # Import here to avoid circular dependency
        from src.datasource.db.db import test_db_connection
        success, message, version = test_db_connection(ds.type, config)
        return ConnectionTestResult(success=success, message=message, version=version)
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))