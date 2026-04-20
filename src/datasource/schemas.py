"""Datasource schemas for request/response validation."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DatasourceConfig(BaseModel):
    """Datasource connection configuration."""
    host: str = Field(..., description="Database host")
    port: int = Field(..., description="Database port")
    username: str = Field(..., description="Database username")
    password: str = Field(..., description="Database password")
    database: str = Field(..., description="Database name")
    driver: str = Field(default="", description="JDBC driver")
    extraJdbc: str = Field(default="", description="Extra JDBC parameters")
    dbSchema: str = Field(default="", description="Database schema")
    timeout: int = Field(default=30, description="Connection timeout (seconds)")
    ssl: bool = Field(default=False, description="Use SSL")
    type: str = Field(default="pg", description="Database type: mysql/pg")


class DatasourceCreate(BaseModel):
    """Create datasource request."""
    name: str = Field(..., min_length=1, max_length=128, description="Datasource name")
    description: str = Field(default="", max_length=512, description="Description")
    type: str = Field(..., description="Database type: mysql/pg")
    config: DatasourceConfig = Field(..., description="Connection configuration")


class DatasourceUpdate(BaseModel):
    """Update datasource request."""
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=512)
    type: Optional[str] = None
    config: Optional[DatasourceConfig] = None


class DatasourceResponse(BaseModel):
    """Datasource response (with decrypted config for display)."""
    id: int
    name: str
    description: Optional[str] = None
    type: str
    type_name: Optional[str] = None
    status: Optional[str] = None
    create_time: Optional[datetime] = None
    create_by: Optional[int] = None

    class Config:
        from_attributes = True


class DatasourceListResponse(BaseModel):
    """List datasources response."""
    total: int
    items: List[DatasourceResponse]


class ConnectionTestResult(BaseModel):
    """Connection test result."""
    success: bool
    message: str
    version: Optional[str] = None