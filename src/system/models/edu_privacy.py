"""Global education PII display setting."""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime
from sqlmodel import Field, SQLModel


class SysEduPrivacy(SQLModel, table=True):
    """Singleton row controlling anonymized display for education data.

    ``anonymize_display=True`` (default): hide student names, real student
    numbers, and school full names. Turning it off reveals those fields for
    demos / inspections; ID card and candidate numbers stay hidden.
    """

    __tablename__ = "sys_edu_privacy"

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            primary_key=True,
            autoincrement=True,
        ),
    )
    anonymize_display: bool = Field(
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
