"""Authentication diagnostics remain bounded, private and retry-compatible."""

import ast
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.auth.imported_session import _TransientRequest
from src.auth.session_bundle import SessionError
from src.auth.session_diagnostics import SessionDiagnostic


def test_known_diagnostic_round_trip_and_distinct_http_status_sources():
    diagnostic = SessionDiagnostic("CATALOG", "issued_validation", "integrity", "Campaigns",
                                   http_status=400, upstream_status=200)
    assert SessionDiagnostic.from_dict(diagnostic.to_dict()) == diagnostic
    assert diagnostic.to_dict() == {
        "code": "CATALOG", "stage": "issued_validation", "reason": "integrity",
        "operation": "Campaigns", "http_status": 400, "upstream_status": 200,
    }
    with pytest.raises(FrozenInstanceError):
        diagnostic.reason = "unknown"


@pytest.mark.parametrize("value", [None, [], "OAuth secret", 1, True])
def test_non_object_remote_diagnostic_has_only_fixed_unknown_labels(value):
    assert SessionDiagnostic.from_dict(value) == SessionDiagnostic()


@pytest.mark.parametrize("value", [None, [], {}, 1, True, "private-cookie\nOAuth secret"])
def test_untrusted_labels_are_never_retained_or_rendered(value):
    diagnostic = SessionDiagnostic.from_dict({
        "code": value, "stage": value, "reason": value, "operation": value,
        "token": "private-token", "message": "private response body",
    })
    assert diagnostic == SessionDiagnostic()
    text = json.dumps(diagnostic.to_dict()) + repr(diagnostic)
    assert "private" not in text and "OAuth" not in text


@pytest.mark.parametrize("value", [True, False, "400", 400.0, 99, 600, -1, None,
                                   float("inf"), float("nan"), [], {}])
def test_http_statuses_accept_only_bounded_nonboolean_integers(value):
    diagnostic = SessionDiagnostic.from_dict({"http_status": value, "upstream_status": value})
    assert diagnostic.http_status is None and diagnostic.upstream_status is None


@pytest.mark.parametrize("status", [100, 200, 400, 599])
def test_http_status_boundaries(status):
    diagnostic = SessionDiagnostic(http_status=status, upstream_status=status)
    assert diagnostic.http_status == status and diagnostic.upstream_status == status


def test_direct_construction_is_as_private_as_remote_projection():
    diagnostic = SessionDiagnostic("private-code", "private-stage", "private-reason", "private-op")
    assert diagnostic == SessionDiagnostic()
    assert "private" not in repr(diagnostic)


def test_unknown_error_code_keeps_compatibility_but_exception_text_is_fixed():
    error = SessionError("private-cookie\nOAuth secret")
    assert error.code == "private-cookie\nOAuth secret"
    assert error.diagnostic.code == "HELPER_FAILED"
    assert "SESSION_HELPER_FAILED" in str(error)
    assert "private" not in str(error) + repr(error)


def test_annotate_fills_unknown_fields_without_overwriting_inner_failure():
    error = SessionError("CATALOG", reason="integrity", operation="Campaigns", upstream_status=200)
    assert error.annotate(stage="issued_validation", http_status=400) is error
    first = error.diagnostic
    assert error.annotate(stage="helper_admission", reason="unexpected_exception",
                          operation="Inventory", http_status=503, upstream_status=403) is error
    assert error.diagnostic == first
    assert first.to_dict() == {
        "code": "CATALOG", "stage": "issued_validation", "reason": "integrity",
        "operation": "Campaigns", "http_status": 400, "upstream_status": 200,
    }


def test_annotation_preserves_transient_retry_subclass_and_redacts_unknown_context():
    error = _TransientRequest()
    assert error.annotate(stage="captured_validation", reason="connection", upstream_status=503) is error
    assert isinstance(error, _TransientRequest) and error.code == "REQUEST"
    error.annotate(code="private-code", stage="private-stage", reason="private-reason",
                   operation="private-op", http_status=True, private="secret")
    assert error.diagnostic == SessionDiagnostic("REQUEST", "captured_validation", "connection",
                                                upstream_status=503)
    assert "private" not in json.dumps(error.diagnostic.to_dict())


def test_allowlist_covers_statically_emitted_authentication_error_codes():
    root = Path(__file__).resolve().parents[1] / "src"
    emitted = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "SessionError" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                emitted.add(node.args[0].value)
    assert emitted <= SessionDiagnostic.CODES
