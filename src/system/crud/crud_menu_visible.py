"""Menu visibility CRUD operations."""

from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from system.models.menu_visible import SysMenuVisible


def get_visibility_map(session: Session) -> Dict[str, bool]:
    """Return {menu_key: visible} for all stored records.

    Missing keys should be treated as visible by callers.
    """
    rows = session.query(SysMenuVisible.menu_key, SysMenuVisible.visible).all()
    return {row.menu_key: bool(row.visible) for row in rows}


def get_by_menu_key(session: Session, menu_key: str) -> Optional[SysMenuVisible]:
    """Get visibility record by menu key."""
    return session.query(SysMenuVisible).filter(SysMenuVisible.menu_key == menu_key).first()


def set_visibility(session: Session, menu_key: str, visible: bool) -> SysMenuVisible:
    """Upsert visibility for a menu key."""
    record = get_by_menu_key(session, menu_key)
    now = datetime.utcnow()
    if record is None:
        record = SysMenuVisible(
            menu_key=menu_key,
            visible=visible,
            create_time=now,
            update_time=now,
        )
        session.add(record)
    else:
        record.visible = visible
        record.update_time = now
    session.commit()
    session.refresh(record)
    return record
