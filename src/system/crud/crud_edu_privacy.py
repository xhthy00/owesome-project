"""CRUD for the education anonymize-display switch."""

from datetime import datetime

from sqlalchemy.orm import Session

from system.models.edu_privacy import SysEduPrivacy

DEFAULT_ANONYMIZE_DISPLAY = True


def get_anonymize_display(session: Session) -> bool:
    """Return current flag; missing row means anonymize (privacy-first)."""
    row = session.query(SysEduPrivacy).order_by(SysEduPrivacy.id.asc()).first()
    if row is None:
        return DEFAULT_ANONYMIZE_DISPLAY
    return bool(row.anonymize_display)


def set_anonymize_display(session: Session, enabled: bool) -> SysEduPrivacy:
    """Upsert the singleton flag."""
    row = session.query(SysEduPrivacy).order_by(SysEduPrivacy.id.asc()).first()
    now = datetime.utcnow()
    if row is None:
        row = SysEduPrivacy(
            anonymize_display=bool(enabled),
            create_time=now,
            update_time=now,
        )
        session.add(row)
    else:
        row.anonymize_display = bool(enabled)
        row.update_time = now
    session.commit()
    session.refresh(row)
    return row
