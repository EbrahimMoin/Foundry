"""Unit tests for Section 9.7 Test Dispute Protocol and Gate G4 risk-based review."""

from __future__ import annotations

import json
from typing import Any

import pytest

from foundry.config.loader import default_models_config
from foundry.config.schemas import ProviderConfig
from foundry.gateway.cassette import CassetteStore, MissPolicy
from foundry.gateway.protocol import GatewayRequest, GatewayResponse
from foundry.gateway.router import GatewayPolicyError, ModelGateway
from foundry.security.vault import CredentialVault
from foundry.verification import (
    G4Reviewer,
    LeadRulingResponse,
    OutboundRequestTrace,
    TestDisputeProtocol,
    evaluate_contract_dispute,
    should_run_g4,
)

OPENAPI_FIXTURE: dict[str, Any] = {
    "openapi": "3.1.0",
    "paths": {
        "/api/bookmarks": {
            "post": {
                "responses": {
                    "201": {"description": "Created"},
                    "409": {"description": "Conflict"},
                },
                "x-foundry-rules": {
                    "409": "Return 409 if URL already exists for user",
                },
            }
        }
    },
}


@pytest.mark.asyncio
async def test_dispute_protocol_steps_1_2_and_cap() -> None:
    proto = TestDisputeProtocol()

    # Step 1: Test asserts undeclared 418 -> test_wrong
    trace_201 = OutboundRequestTrace(
        method="POST",
        path="/api/bookmarks",
        status_code=201,
        request_valid=True,
    )
    res_test_wrong = await proto.resolve_dispute(
        feature="bookmarks",
        openapi_doc=OPENAPI_FIXTURE,
        trace=trace_201,
        failure_output="assert response.status_code == 418",
        current_dispute_rounds=0,
    )
    assert res_test_wrong.step == 1
    assert res_test_wrong.verdict == "test_wrong"

    # Step 1: Server returned undeclared 500 -> code_wrong
    trace_500 = OutboundRequestTrace(
        method="POST",
        path="/api/bookmarks",
        status_code=500,
        request_valid=True,
    )
    res_code_wrong = await proto.resolve_dispute(
        feature="bookmarks",
        openapi_doc=OPENAPI_FIXTURE,
        trace=trace_500,
        failure_output="assert 500 == 201",
        current_dispute_rounds=0,
    )
    assert res_code_wrong.step == 1
    assert res_code_wrong.verdict == "code_wrong"

    # Step 1 ambiguous (409 vs 201, both declared) -> Step 2 Lead ruling clarifying rules
    trace_409 = OutboundRequestTrace(
        method="POST",
        path="/api/bookmarks",
        status_code=409,
        request_valid=True,
    )
    step1, _ = evaluate_contract_dispute(
        OPENAPI_FIXTURE,
        trace_409,
        "ambiguous duplicate policy: assert 409 == 201",
    )
    assert step1 == "ambiguous"

    async def _lead_rules(
        feature: str,
        openapi_doc: dict[str, Any],
        trace: OutboundRequestTrace | None,
        failure_output: str,
    ) -> LeadRulingResponse:
        _ = (feature, openapi_doc, trace, failure_output)
        return LeadRulingResponse(
            verdict="contract_clarified",
            reason="Duplicates are allowed only across different folders; normalize URL scheme.",
            clarified_rules={"409": "Conflict only within same folder"},
        )

    res_step2 = await proto.resolve_dispute(
        feature="bookmarks",
        openapi_doc=OPENAPI_FIXTURE,
        trace=trace_409,
        failure_output="ambiguous duplicate policy: assert 409 == 201",
        current_dispute_rounds=0,
        lead_ruling_fn=_lead_rules,
    )
    assert res_step2.step == 2
    assert res_step2.verdict == "contract_clarified"
    assert res_step2.clarified_rules == {"409": "Conflict only within same folder"}

    # Step 3: Exceeding max 2 dispute rounds per feature -> escalated_orchestrator
    res_cap = await proto.resolve_dispute(
        feature="bookmarks",
        openapi_doc=OPENAPI_FIXTURE,
        trace=trace_409,
        failure_output="assert 409 == 201",
        current_dispute_rounds=2,
        lead_ruling_fn=_lead_rules,
    )
    assert res_cap.step == 3
    assert res_cap.verdict == "escalated_orchestrator"
    assert res_cap.dispute_round == 3


def test_should_run_g4_triggers() -> None:
    assert should_run_g4("t1", ["backend/app/features/auth/router.py"], 20).should_run is True
    assert should_run_g4("t2", ["backend/app/core/security.py"], 15).should_run is True
    assert should_run_g4("t3", ["backend/migrations/versions/0001.py"], 10).should_run is True
    assert should_run_g4("t4", ["backend/app/features/notes/router.py"], 350).should_run is True

    low = should_run_g4("t5", ["backend/app/features/notes/router.py"], 40, sample_rate=0.0)
    assert low.should_run is False


class _MockReviewTransport:
    name = "openai_compat"

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse:
        _ = request
        return GatewayResponse(
            provider_id=provider_id,
            model=model,
            transport=provider_cfg.transport,
            billing_mode=provider_cfg.billing_mode,
            content=json.dumps(self._payload),
            tokens_in=100,
            tokens_out=40,
        )

    async def health_check(self, provider_id: str, provider_cfg: ProviderConfig) -> Any:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_g4_reviewer_cross_family_and_findings(tmp_path: Any) -> None:
    cfg = default_models_config()
    transport = _MockReviewTransport({"approved": True, "summary": "Clean route.", "findings": []})
    gw = ModelGateway(
        cfg,
        vault=CredentialVault(tmp_path / "vault.enc", passphrase="test"),
        cassettes=CassetteStore(tmp_path / "cas", miss_policy=MissPolicy.PASSTHROUGH),
        transports={"openai_compat": transport, "google_genai": transport},
    )
    reviewer = G4Reviewer(gw)

    # @verifier default is xai (family="xai"), so gemini peer succeeds
    ok_res = await reviewer.review_task(
        task_id="tsk_auth",
        task_title="Auth middleware",
        task_description="JWT checks",
        changed_files=["backend/app/core/security.py"],
        diff_text="+def verify(): pass",
        contracts={"api": OPENAPI_FIXTURE},
        peer_implementer_family="gemini",
    )
    assert ok_res.approved is True
    assert ok_res.skipped is False

    # Same-family implementer ("xai") must raise GatewayPolicyError when diversity required
    with pytest.raises(GatewayPolicyError):
        await reviewer.review_task(
            task_id="tsk_auth",
            task_title="Auth middleware",
            task_description="JWT checks",
            changed_files=["backend/app/core/security.py"],
            diff_text="+def verify(): pass",
            contracts={"api": OPENAPI_FIXTURE},
            peer_implementer_family="xai",
        )
