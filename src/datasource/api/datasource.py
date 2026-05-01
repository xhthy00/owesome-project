"""Datasource API routes."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from common.core.database import get_session
from common.exceptions.base import NotFoundException
from common.schemas.response import success_response
from datasource.schemas import (
    ConnectionTestResult,
    DatasourceCreate,
    DatasourceUpdate,
)
from datasource.crud import crud_datasource
from common.utils.aes import decrypt_conf
from datasource.models.datasource import CoreTable, CoreField

router = APIRouter(prefix="/datasource", tags=["datasource"])


@router.get("")
def list_datasources(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    oid: Optional[int] = Query(None),
    session: Session = Depends(get_session),
):
    """List all datasources."""
    items = crud_datasource.get_datasources(session, skip, limit, oid)
    total = crud_datasource.count_datasources(session, oid)

    response_items = []
    for ds in items:
        response_items.append({
            "id": ds.id,
            "name": ds.name,
            "description": ds.description,
            "type": ds.type,
            "type_name": ds.type_name,
            "status": ds.status,
            "create_time": str(ds.create_time) if ds.create_time else None,
            "create_by": ds.create_by,
        })

    return success_response(
        data={
            "total": total,
            "items": response_items,
        }
    )


@router.get("/{datasource_id}")
def get_datasource(
    datasource_id: int,
    session: Session = Depends(get_session),
):
    """Get datasource by ID."""
    ds = crud_datasource.get_datasource_by_id(session, datasource_id)
    if not ds:
        raise NotFoundException("Datasource not found")

    config = decrypt_conf(ds.configuration) if ds.configuration else {}

    return success_response(
        data={
            "id": ds.id,
            "name": ds.name,
            "description": ds.description,
            "type": ds.type,
            "type_name": ds.type_name,
            "status": ds.status,
            "create_time": str(ds.create_time) if ds.create_time else None,
            "create_by": ds.create_by,
            "config": config,
        }
    )


@router.post("")
def create_datasource(
    datasource_in: DatasourceCreate,
    session: Session = Depends(get_session),
):
    """Create a new datasource."""
    config = datasource_in.config.model_dump()

    ds = crud_datasource.create_datasource(
        session,
        name=datasource_in.name,
        type=datasource_in.type,
        config=config,
        description=datasource_in.description,
    )

    return success_response(
        data={
            "id": ds.id,
            "name": ds.name,
            "description": ds.description,
            "type": ds.type,
            "type_name": ds.type_name,
            "status": ds.status,
            "create_time": str(ds.create_time) if ds.create_time else None,
            "create_by": ds.create_by,
        },
        message="Datasource created successfully"
    )


@router.put("/{datasource_id}")
def update_datasource(
    datasource_id: int,
    datasource_in: DatasourceUpdate,
    session: Session = Depends(get_session),
):
    """Update datasource."""
    update_data = datasource_in.model_dump(exclude_unset=True)

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
        raise NotFoundException("Datasource not found")

    return success_response(
        data={
            "id": ds.id,
            "name": ds.name,
            "description": ds.description,
            "type": ds.type,
            "type_name": ds.type_name,
            "status": ds.status,
            "create_time": str(ds.create_time) if ds.create_time else None,
            "create_by": ds.create_by,
        },
        message="Datasource updated successfully"
    )


@router.delete("/{datasource_id}")
def delete_datasource(
    datasource_id: int,
    session: Session = Depends(get_session),
):
    """Delete datasource."""
    success = crud_datasource.delete_datasource(session, datasource_id)
    if not success:
        raise NotFoundException("Datasource not found")

    return success_response(
        data={"id": datasource_id},
        message="Datasource deleted successfully"
    )


@router.post("/{datasource_id}/test-connection")
def test_connection(
    datasource_id: int,
    session: Session = Depends(get_session),
):
    """Test datasource connection."""
    ds = crud_datasource.get_datasource_by_id(session, datasource_id)
    if not ds:
        raise NotFoundException("Datasource not found")

    config = decrypt_conf(ds.configuration) if ds.configuration else {}

    try:
        from datasource.db.db import test_db_connection
        success, message, version = test_db_connection(ds.type, config)
        return success_response(
            data={
                "success": success,
                "message": message,
                "version": version,
            }
        )
    except Exception as e:
        return success_response(
            data={
                "success": False,
                "message": str(e),
                "version": None,
            }
        )


@router.get("/{datasource_id}/tables")
def list_tables(
    datasource_id: int,
    session: Session = Depends(get_session),
):
    rows = (
        session.query(CoreTable)
        .filter(CoreTable.ds_id == datasource_id)
        .order_by(CoreTable.id.asc())
        .all()
    )
    return success_response(
        data=[
            {
                "id": row.id,
                "table_name": row.table_name,
                "table_comment": row.table_comment,
            }
            for row in rows
        ]
    )


@router.get("/table/{table_id}/fields")
def list_table_fields(
    table_id: int,
    session: Session = Depends(get_session),
):
    rows = (
        session.query(CoreField)
        .filter(CoreField.table_id == table_id)
        .order_by(CoreField.field_index.asc())
        .all()
    )
    return success_response(
        data=[
            {
                "id": row.id,
                "field_name": row.field_name,
                "field_type": row.field_type,
                "field_comment": row.field_comment,
            }
            for row in rows
        ]
    )
