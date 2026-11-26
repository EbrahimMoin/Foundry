"""Unit tests for post-test Test-Validator (`A7-7`) and G3 conftest instrumentation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from foundry.stack_packs import get_stack_pack
from foundry.verification import (
    OutboundRequestTrace,
    classify_g3_failure,
    find_unknown_testids,
    load_request_traces,
)


def test_post_test_classifier_passed_and_infra() -> None:
    passed = classify_g3_failure(0, "3 passed in 0.2s", "")
    assert passed.outcome == "passed"
    assert passed.route_to == "none"

    timed_out = classify_g3_failure(1, "", "", timed_out=True)
    assert timed_out.outcome == "infra_failure"
    assert timed_out.route_to == "infra"

    conn_err = classify_g3_failure(1, "", "asyncpg.exceptions.CannotConnectNowError: db down")
    assert conn_err.outcome == "infra_failure"
    assert conn_err.route_to == "infra"


def test_post_test_classifier_invalid_test_request_vs_negative_and_401() -> None:
    invalid_trace = OutboundRequestTrace(
        test_nodeid="tests/test_bookmarks.py::test_create",
        method="POST",
        path="/api/bookmarks",
        status_code=422,
        request_valid=False,
        expect_invalid=False,
        validation_error="Missing required body field 'url'",
    )
    res_invalid = classify_g3_failure(
        1,
        "FAILED test_create - assert 422 == 201",
        "",
        traces=[invalid_trace],
    )
    assert res_invalid.outcome == "invalid_test_request"
    assert res_invalid.route_to == "test_writer"
    assert res_invalid.failing_trace == invalid_trace

    # Explicit negative test with expect_invalid=True must NOT be marked invalid_test_request
    negative_trace = OutboundRequestTrace(
        test_nodeid="tests/test_bookmarks.py::test_reject_bad",
        method="POST",
        path="/api/bookmarks",
        status_code=422,
        request_valid=False,
        expect_invalid=True,
        validation_error="Missing required body field 'url'",
    )
    res_neg = classify_g3_failure(
        1,
        "FAILED test_reject_bad - assert 422 == 400",
        "",
        traces=[negative_trace],
    )
    assert res_neg.outcome == "assertion_failure"
    assert res_neg.route_to == "dispute"

    # Valid request returning 401/403 must route to dispute, never Test-Validator rejection
    auth_trace = OutboundRequestTrace(
        test_nodeid="tests/test_bookmarks.py::test_auth",
        method="GET",
        path="/api/bookmarks",
        status_code=401,
        request_valid=True,
        expect_invalid=False,
    )
    res_auth = classify_g3_failure(
        1,
        "FAILED test_auth - assert 401 == 200",
        "",
        traces=[auth_trace],
    )
    assert res_auth.outcome == "assertion_failure"
    assert res_auth.route_to == "dispute"


def test_post_test_classifier_broken_test_code_and_unknown_testids() -> None:
    collection_err = classify_g3_failure(
        2,
        "ERROR collecting backend/tests/test_x.py: ModuleNotFoundError: No module named 'nope'",
        "",
    )
    assert collection_err.outcome == "broken_test_code"
    assert collection_err.route_to == "test_writer"

    name_err = classify_g3_failure(
        1,
        'File "backend/tests/test_x.py", line 12, in test_it\nNameError: name "foo" is not defined',
        "",
    )
    assert name_err.outcome == "broken_test_code"
    assert name_err.route_to == "test_writer"

    sources = {
        "frontend/src/features/bookmarks/__tests__/Bookmark.test.tsx": (
            'screen.getByTestId("bookmark-list");\nscreen.getByTestId("undeclared-magic-btn");\n'
        )
    }
    assert find_unknown_testids(sources, ["bookmark-list", "bookmark-save"]) == [
        "undeclared-magic-btn"
    ]
    fe_res = classify_g3_failure(
        1,
        "FAIL Bookmark.test.tsx",
        "",
        test_sources=sources,
        declared_testids=["bookmark-list", "bookmark-save"],
    )
    assert fe_res.outcome == "broken_test_code"
    assert fe_res.route_to == "test_writer"
    assert fe_res.unknown_testids == ["undeclared-magic-btn"]


def test_load_request_traces_and_scaffolded_conftest(tmp_path: Path) -> None:
    pack = get_stack_pack("fastapi-react-postgres")
    pack.scaffold(tmp_path, app_name="TraceApp")
    conftest_text = (tmp_path / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "_InstrumentedAsyncClient" in conftest_text
    assert "expect_invalid" in conftest_text
    assert "g3_request_trace.jsonl" in conftest_text

    trace_path = tmp_path / ".foundry_tmp" / "g3_request_trace.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(
            {
                "test_nodeid": "test_1",
                "method": "POST",
                "path": "/api/items",
                "status_code": 422,
                "request_valid": False,
                "expect_invalid": False,
                "validation_error": "Missing required body field 'title'",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_request_traces(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].method == "POST"
    assert loaded[0].request_valid is False


@pytest.mark.asyncio
async def test_instrumented_conftest_client_records_traces_without_short_circuiting(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import importlib.util
    import sys

    pack = get_stack_pack("fastapi-react-postgres")
    pack.scaffold(tmp_path, app_name="InstrApp")

    # Point generated app.main import inside temp workspace to a minimal FastAPI app
    main_file = tmp_path / "backend" / "app" / "main.py"
    main_file.write_text(
        "from fastapi import FastAPI\n"
        "from fastapi.responses import JSONResponse\n"
        "app = FastAPI()\n"
        "@app.post('/api/items')\n"
        "async def create_item(payload: dict) -> JSONResponse:\n"
        "    if 'title' not in payload:\n"
        "        return JSONResponse({'detail': 'missing title'}, status_code=422)\n"
        "    return JSONResponse({'id': 1, 'title': payload['title']}, status_code=201)\n",
        encoding="utf-8",
    )

    locked_spec = {
        "openapi": "3.1.0",
        "paths": {
            "/api/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["title"],
                                    "properties": {"title": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            }
        },
    }
    locked_file = tmp_path / ".foundry_tmp" / "locked_openapi.json"
    locked_file.parent.mkdir(parents=True, exist_ok=True)
    locked_file.write_text(json.dumps(locked_spec), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path / "backend"))
    sys.modules.pop("app", None)
    sys.modules.pop("app.main", None)

    spec = importlib.util.spec_from_file_location(
        "temp_g3_conftest", tmp_path / "backend" / "tests" / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "temp_g3_conftest", mod)
    spec.loader.exec_module(mod)

    recorder = mod.RequestValidationRecorder()
    openapi_doc = mod._load_openapi_spec()
    import httpx

    transport = httpx.ASGITransport(app=mod.app)
    async with mod._InstrumentedAsyncClient(
        transport=transport,
        base_url="http://test",
        recorder=recorder,
        spec=openapi_doc,
    ) as client:
        r1 = await client.post("/api/items", json={"title": "ok"})
        assert r1.status_code == 201
        r2 = await client.post("/api/items", json={})
        assert r2.status_code == 422
        r3 = await client.post("/api/items", json={}, expect_invalid=True)
        assert r3.status_code == 422

    traces = load_request_traces(tmp_path)
    assert [t.request_valid for t in traces] == [True, False, False]
    assert [t.expect_invalid for t in traces] == [False, False, True]
    assert [t.status_code for t in traces] == [201, 422, 422]
