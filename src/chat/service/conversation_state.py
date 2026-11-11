"""Keep checkpoint history inside the current publication and authorization boundary."""

import hashlib
import json

from langchain_core.messages import AIMessage, HumanMessage

from datasource.models.permission import DsPermission, DsRule
from src.agent.education.privacy_mode import is_anonymize_display_enabled
from src.common.core.database import get_db_session
from src.datasource.service.edu_permission import load_edu_permission_config
from src.system.crud.crud_user import get_user_by_id
from system.authz import bypasses_column_visibility, bypasses_data_row_column_scope
from system.workspace_scope import assert_datasource_accessible


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
    ).encode()).hexdigest()


def authorization_fingerprint(user_id: int, workspace_oid: int, datasource_id: int) -> str:
    """Invalidate stored results when row/column rules, roles or privacy change.

    Include all datasource/workspace rules: changes to group membership and white
    lists must invalidate history too. Never persist credentials or user variables.
    """
    with get_db_session() as session:
        user = get_user_by_id(session, user_id)
        if user is None:
            raise ValueError("用户权限已失效。")
        assert_datasource_accessible(session, user, datasource_id, workspace_oid)
        permissions = session.query(DsPermission).filter(DsPermission.ds_id == datasource_id).all()
        rules = session.query(DsRule).filter(DsRule.oid == workspace_oid).all()
        value = {
            "user": user_id, "workspace": workspace_oid, "datasource": datasource_id,
            "role": user.sys_role, "variables": user.system_variables,
            "bypass_rows": bypasses_data_row_column_scope(user),
            "bypass_columns": bypasses_column_visibility(session, user),
            "permissions": sorted((item.model_dump() for item in permissions), key=lambda x: x["id"]),
            "rules": sorted((item.model_dump() for item in rules), key=lambda x: x["id"]),
            "education": load_edu_permission_config(),
            "anonymize": is_anonymize_display_enabled(),
        }
        from src.datasource.service.education_authorization import (
            assert_workspace_member,
            context_for,
        )
        from src.datasource.service.education_source import permission_manifest_for_user
        policy = permission_manifest_for_user(user, workspace_oid, datasource_id)
        if policy is not None:
            assert_workspace_member(session, user, workspace_oid)
            value["explicit_permission"] = context_for(user, workspace_oid, datasource_id, policy, session=session).fingerprint
    return fingerprint(value)


def load_conversation_history(conversation_id, user_id, workspace_oid, datasource_id, context_key):
    """Bootstrap an empty checkpoint once, using currently authorized host history.

    Match my-cowork's 16-message / 8,000-character host-history bounds. Records
    predating boundary tracking cannot safely restore old query results.
    """
    from src.chat.models.conversation import Conversation, ConversationRecord

    with get_db_session() as session:
        records = session.query(ConversationRecord).join(
            Conversation, Conversation.id == ConversationRecord.conversation_id,
        ).filter(
            Conversation.id == conversation_id, Conversation.user_id == user_id,
            Conversation.oid == workspace_oid, Conversation.datasource_id == datasource_id,
            Conversation.is_deleted.is_(False), ConversationRecord.user_id == user_id,
        ).order_by(ConversationRecord.id.desc()).limit(8).all()
        turns = []
        for record in records:
            try:
                result = json.loads(record.exec_result or "{}")
            except (TypeError, ValueError):
                break
            if not isinstance(result, dict) or result.get("context_boundary") != context_key:
                break
            turns.append(record)
        messages = []
        for record in reversed(turns):
            if record.question:
                messages.append(HumanMessage(content=record.question[:8000]))
            if record.is_success and record.summary:
                messages.append(AIMessage(content=record.summary[:8000]))
        return messages
