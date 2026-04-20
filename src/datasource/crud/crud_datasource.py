"""Datasource CRUD operations."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from src.datasource.models.datasource import CoreDatasource
from src.common.utils.aes import encrypt_conf, decrypt_conf


def get_datasource_by_id(session: Session, datasource_id: int) -> Optional[CoreDatasource]:
    """Get datasource by ID."""
    return session.query(CoreDatasource).filter(CoreDatasource.id == datasource_id).first()


def get_datasources(
    session: Session,
    skip: int = 0,
    limit: int = 100,
    oid: Optional[int] = None,
) -> List[CoreDatasource]:
    """Get list of datasources."""
    query = session.query(CoreDatasource)
    if oid is not None:
        query = query.filter(CoreDatasource.oid == oid)
    return query.offset(skip).limit(limit).all()


def count_datasources(session: Session, oid: Optional[int] = None) -> int:
    """Count datasources."""
    query = session.query(CoreDatasource)
    if oid is not None:
        query = query.filter(CoreDatasource.oid == oid)
    return query.count()


def create_datasource(
    session: Session,
    name: str,
    type: str,
    config: dict,
    description: str = "",
    oid: int = 1,
    create_by: int = 1,
) -> CoreDatasource:
    """Create a new datasource with encrypted config."""
    encrypted_config = encrypt_conf(config)
    datasource = CoreDatasource(
        name=name,
        description=description,
        type=type,
        configuration=encrypted_config,
        create_time=datetime.now(),
        create_by=create_by,
        status="active",
        oid=oid,
    )
    session.add(datasource)
    session.commit()
    session.refresh(datasource)
    return datasource


def update_datasource(
    session: Session,
    datasource_id: int,
    **kwargs,
) -> Optional[CoreDatasource]:
    """Update datasource fields."""
    datasource = get_datasource_by_id(session, datasource_id)
    if not datasource:
        return None

    # Encrypt config if provided
    if "config" in kwargs:
        kwargs["configuration"] = encrypt_conf(kwargs.pop("config"))

    for key, value in kwargs.items():
        if value is not None and hasattr(datasource, key):
            setattr(datasource, key, value)

    session.commit()
    session.refresh(datasource)
    return datasource


def delete_datasource(session: Session, datasource_id: int) -> bool:
    """Delete datasource by ID."""
    datasource = get_datasource_by_id(session, datasource_id)
    if not datasource:
        return False
    session.delete(datasource)
    session.commit()
    return True


def get_decrypted_config(session: Session, datasource_id: int) -> Optional[dict]:
    """Get decrypted configuration for a datasource."""
    datasource = get_datasource_by_id(session, datasource_id)
    if not datasource:
        return None
    return decrypt_conf(datasource.configuration)