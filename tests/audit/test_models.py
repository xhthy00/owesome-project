"""审计日志模型字段与建表。"""
from sqlmodel import Session

from audit.models import AuditAccessLog, AuditLoginLog, AuditOperationLog


def test_access_log_table_name():
    assert AuditAccessLog.__tablename__ == "audit_access_log"


def test_operation_log_table_name():
    assert AuditOperationLog.__tablename__ == "audit_operation_log"


def test_login_log_table_name():
    assert AuditLoginLog.__tablename__ == "audit_login_log"


def test_access_log_fields():
    log = AuditAccessLog(
        request_method="POST",
        request_path="/api/v1/chat/generate-sql",
        datasource_id=1,
        query_text="本月订单最多前三名",
        user_id=2,
        user_account="allen",
        success=True,
        elapsed_ms=120,
    )
    assert log.request_method == "POST"
    assert log.query_text == "本月订单最多前三名"
    assert log.success is True


def test_operation_log_fields():
    log = AuditOperationLog(
        operation_type="create",
        resource_type="user",
        resource_id="3",
        detail='{"account":"bob"}',
        user_id=1,
        user_account="admin",
        success=True,
    )
    assert log.operation_type == "create"
    assert log.resource_type == "user"


def test_login_log_fields():
    log = AuditLoginLog(
        account="allen",
        success=False,
        fail_reason="密码错误",
        user_account="allen",
    )
    assert log.account == "allen"
    assert log.fail_reason == "密码错误"
    assert log.success is False


def test_access_log_persistence_roundtrip(session: Session):
    """真实建表 + 插入 + refresh 回读：验证默认值与主键落库。"""
    log = AuditAccessLog(
        request_method="POST",
        request_path="/x",
        user_id=1,
    )
    session.add(log)
    session.commit()
    session.refresh(log)

    assert log.id is not None
    assert log.trace_id == "-"
    assert log.success is True
    assert log.created_at is not None

