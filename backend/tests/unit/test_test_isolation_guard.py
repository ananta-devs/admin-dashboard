"""Unit and adversarial tests for test database isolation guard (DEF-ISO-001)."""

import pytest

from tests.isolation_guard import (
    APPROVED_TEST_DATABASES,
    PROHIBITED_DATABASES,
    extract_database_name,
    get_enforced_test_database_url,
    validate_database_url,
)


def test_extract_database_name():
    assert (
        extract_database_name("postgresql+asyncpg://usr:pwd@localhost:5432/vibe_enterprise_test")
        == "vibe_enterprise_test"
    )
    assert (
        extract_database_name("postgresql://usr:pwd@127.0.0.1:5432/vibe_enterprise")
        == "vibe_enterprise"
    )
    assert extract_database_name("") == ""


def test_prohibited_database_raises_fatal():
    for prohibited in PROHIBITED_DATABASES:
        url = f"postgresql+asyncpg://admin:secret@localhost:5432/{prohibited}"
        with pytest.raises(RuntimeError, match="Database isolation violation"):
            validate_database_url(url)


def test_unapproved_database_raises_fatal():
    unapproved = "postgresql+asyncpg://admin:secret@localhost:5432/some_random_db"
    with pytest.raises(RuntimeError, match="not in approved test database registry"):
        validate_database_url(unapproved)


def test_empty_or_none_database_url_raises_fatal():
    with pytest.raises(RuntimeError, match="No database URL provided"):
        validate_database_url("")


def test_approved_test_database_succeeds():
    for approved in APPROVED_TEST_DATABASES:
        url = f"postgresql+asyncpg://admin:secret@localhost:5432/{approved}"
        assert validate_database_url(url) == approved


def test_get_enforced_test_database_url_defaults_to_approved():
    url = get_enforced_test_database_url()
    assert "vibe_enterprise_test" in url
    assert "vibe_enterprise/" not in url
