"""
Unit tests for crewos/core/tenant.py
Tests ContextVar isolation, set/get, and context manager behavior.
"""
import pytest
from crewos.core.tenant import (
    get_current_tenant,
    set_tenant,
    tenant_context,
    _current_tenant,
)


class TestGetSetTenant:

    def test_default_tenant_is_none(self):
        token = _current_tenant.set(None)
        assert get_current_tenant() is None
        _current_tenant.reset(token)

    def test_set_and_get_tenant(self):
        token = _current_tenant.set(None)
        set_tenant("tenant-abc")
        assert get_current_tenant() == "tenant-abc"
        _current_tenant.reset(token)

    def test_set_tenant_overwrites_previous(self):
        token = _current_tenant.set(None)
        set_tenant("tenant-1")
        set_tenant("tenant-2")
        assert get_current_tenant() == "tenant-2"
        _current_tenant.reset(token)


class TestTenantContextManager:

    def test_context_manager_sets_tenant(self):
        with tenant_context("tenant-xyz"):
            assert get_current_tenant() == "tenant-xyz"

    def test_context_manager_resets_after_exit(self):
        token = _current_tenant.set(None)
        with tenant_context("tenant-xyz"):
            pass
        assert get_current_tenant() is None
        _current_tenant.reset(token)

    def test_context_manager_resets_on_exception(self):
        token = _current_tenant.set(None)
        try:
            with tenant_context("tenant-xyz"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert get_current_tenant() is None
        _current_tenant.reset(token)

    def test_nested_context_managers_restore_correctly(self):
        with tenant_context("outer"):
            assert get_current_tenant() == "outer"
            with tenant_context("inner"):
                assert get_current_tenant() == "inner"
            assert get_current_tenant() == "outer"

    def test_different_tenant_ids_are_isolated(self):
        results = []
        with tenant_context("tenant-a"):
            results.append(get_current_tenant())
        with tenant_context("tenant-b"):
            results.append(get_current_tenant())
        assert results == ["tenant-a", "tenant-b"]