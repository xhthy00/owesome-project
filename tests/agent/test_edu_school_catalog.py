from src.datasource.service.edu_permission import (
    EduScope,
    apply_school_catalog,
    edu_school_display_label,
    looks_like_school_token,
    match_edu_school,
)


def _catalog() -> list[dict[str, str]]:
    return [
        {
            "id": "GZ_E62620B2",
            "name": "gz_e62620b2",
            "s_name": "B04安宜中学",
            "label": "B04安宜中学",
        },
        {
            "id": "GZ_HJ001",
            "name": "gz_hj001",
            "s_name": "A07邗江中学",
            "label": "A07邗江中学",
        },
    ]


def test_looks_like_school_token() -> None:
    assert looks_like_school_token("GZ_E62620B2")
    assert looks_like_school_token("gz_2d2b5c7b")
    assert not looks_like_school_token("安宜中学")
    assert not looks_like_school_token("B04安宜中学")
    assert not looks_like_school_token("")


def test_display_label_prefers_s_name() -> None:
    assert edu_school_display_label("GZ_E62620B2", "gz_e62620b2", "B04安宜中学") == "B04安宜中学"
    assert edu_school_display_label("GZ_E62620B2", "gz_e62620b2", "gz_e62620b2") == "GZ_E62620B2"


def test_match_plain_name_strips_school_code() -> None:
    hit, err = match_edu_school(_catalog(), "安宜中学")
    assert err is None
    assert hit is not None
    assert hit["id"] == "GZ_E62620B2"

    hit, err = match_edu_school(_catalog(), "邗江中学")
    assert err is None
    assert hit is not None
    assert hit["id"] == "GZ_HJ001"


def test_apply_catalog_resolves_plaintext_to_token() -> None:
    scope = EduScope(edu_role="school_admin", school_id="安宜中学")
    err = apply_school_catalog(scope, _catalog())
    assert err is None
    assert scope.school_id == "GZ_E62620B2"
    assert scope.school_name == "B04安宜中学"


def test_apply_catalog_keeps_token_and_fills_name() -> None:
    scope = EduScope(edu_role="school_admin", school_id="GZ_E62620B2")
    err = apply_school_catalog(scope, _catalog())
    assert err is None
    assert scope.school_id == "GZ_E62620B2"
    assert scope.school_name == "B04安宜中学"


def test_apply_catalog_keeps_unknown_token() -> None:
    scope = EduScope(edu_role="school_admin", school_id="GZ_UNKNOWN")
    err = apply_school_catalog(scope, _catalog())
    assert err is None
    assert scope.school_id == "GZ_UNKNOWN"


def test_apply_catalog_rejects_unknown_plain_name() -> None:
    scope = EduScope(edu_role="school_admin", school_id="不存在中学")
    err = apply_school_catalog(scope, _catalog())
    assert err is not None
    assert "未找到学校" in err


def test_apply_catalog_rejects_plain_name_without_catalog() -> None:
    scope = EduScope(edu_role="school_admin", school_id="安宜中学")
    err = apply_school_catalog(scope, [])
    assert err is not None
    assert "密文学校 ID" in err
