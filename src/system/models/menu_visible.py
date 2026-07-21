"""Menu visibility configuration for non-platform-admin users."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime, String
from sqlmodel import Field, SQLModel


class SysMenuVisible(SQLModel, table=True):
    """Menu visibility table.

    Controls whether a top-level sidebar menu is visible to non-platform-admin
    users. Platform admins always see every menu regardless of this setting.
    Missing keys are treated as visible by default.
    """

    __tablename__ = "sys_menu_visible"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            primary_key=True,
            autoincrement=True,
        ),
    )
    menu_key: str = Field(
        sa_column=Column(
            String(64),
            nullable=False,
            unique=True,
            index=True,
        )
    )
    visible: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    create_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime, nullable=True, default=datetime.utcnow),
    )
    update_time: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=True,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
        ),
    )
