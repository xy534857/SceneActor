"""Provider boundary and strict response acceptance for template contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .contracts import (
    PROTOCOL_VERSION,
    ProtocolFault,
    TemplatePerformanceContract,
    TemplateResolveRequest,
    TemplateResolveResponse,
    resolution_hash,
    validate_selected_slots,
)


class TemplateProvider(Protocol):
    def resolve(self, request: TemplateResolveRequest) -> TemplateResolveResponse: ...


class TemplateProtocolError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ResolvedTemplate:
    request_id: str
    contract: TemplatePerformanceContract
    selected_slots: dict[str, str]
    usage_context: str
    resolution_hash: str

    def to_replay_record(self) -> "TemplateReplayRecord":
        return TemplateReplayRecord(
            request_id=self.request_id,
            contract=self.contract,
            selected_slots=dict(self.selected_slots),
            usage_context=self.usage_context,
            resolution_hash=self.resolution_hash,
        )


@dataclass(frozen=True)
class TemplateReplayRecord:
    request_id: str
    contract: TemplatePerformanceContract
    selected_slots: Mapping[str, str]
    usage_context: str
    resolution_hash: str

    def verify(self) -> None:
        self.contract.verify_integrity()
        resolved_slots = validate_selected_slots(self.contract, self.selected_slots)
        if resolved_slots != dict(self.selected_slots):
            raise ValueError("replay record contains unresolved slot defaults")
        expected = resolution_hash(self.contract, resolved_slots, self.usage_context)
        if expected != self.resolution_hash:
            raise ValueError("template replay resolution hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "contract": self.contract.to_dict(),
            "selected_slots": dict(self.selected_slots),
            "usage_context": self.usage_context,
            "resolution_hash": self.resolution_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TemplateReplayRecord":
        contract = data.get("contract")
        selected = data.get("selected_slots")
        if not isinstance(contract, Mapping) or not isinstance(selected, Mapping):
            raise ValueError("replay record requires contract and selected_slots objects")
        record = cls(
            request_id=str(data.get("request_id", "")),
            contract=TemplatePerformanceContract.from_dict(contract),
            selected_slots={str(key): str(value) for key, value in selected.items()},
            usage_context=str(data.get("usage_context", "")),
            resolution_hash=str(data.get("resolution_hash", "")),
        )
        record.verify()
        return record


def resolve_template(
    provider: TemplateProvider,
    request: TemplateResolveRequest,
) -> ResolvedTemplate:
    """Resolve one exact version and reject any mutable or unauthorized response."""
    response = provider.resolve(request)
    if response.protocol_version != PROTOCOL_VERSION:
        raise TemplateProtocolError("protocol_error", "provider returned an unsupported protocol version")
    if response.request_id != request.request_id:
        raise TemplateProtocolError("protocol_error", "provider response request_id mismatch")
    if response.status != "ok":
        assert response.fault is not None
        raise TemplateProtocolError(response.fault.code, response.fault.message, retryable=response.fault.retryable)
    contract = response.contract
    assert contract is not None
    if contract.template_id != request.template_id or contract.version != request.version:
        raise TemplateProtocolError("version_conflict", "provider returned a different template identity or version")
    try:
        contract.verify_integrity()
    except ValueError as exc:
        raise TemplateProtocolError("integrity_error", str(exc)) from exc
    if not contract.rights.allows(request.usage_context):
        raise TemplateProtocolError("rights_restricted", "template rights do not allow the requested usage")
    try:
        resolved_slots = validate_selected_slots(contract, response.selected_slots)
    except ValueError as exc:
        raise TemplateProtocolError("invalid_slots", str(exc)) from exc
    if resolved_slots != dict(response.selected_slots):
        raise TemplateProtocolError("protocol_error", "provider returned unresolved slot defaults")
    requested = dict(request.selected_slots)
    if any(resolved_slots.get(key) != value for key, value in requested.items()):
        raise TemplateProtocolError("protocol_error", "provider changed a requested slot value")
    return ResolvedTemplate(
        request_id=request.request_id,
        contract=contract,
        selected_slots=resolved_slots,
        usage_context=request.usage_context,
        resolution_hash=resolution_hash(contract, resolved_slots, request.usage_context),
    )


class InMemoryTemplateProvider:
    """Deterministic fixture/provider; production transports implement TemplateProvider."""

    def __init__(self, contracts: Sequence[TemplatePerformanceContract]) -> None:
        self._contracts: dict[tuple[str, str], TemplatePerformanceContract] = {}
        for contract in contracts:
            contract.verify_integrity()
            key = (contract.template_id, contract.version)
            if key in self._contracts:
                raise ValueError("duplicate template id and version")
            self._contracts[key] = contract

    def resolve(self, request: TemplateResolveRequest) -> TemplateResolveResponse:
        key = (request.template_id, request.version)
        contract = self._contracts.get(key)
        if contract is None:
            known_versions = sorted(
                version for template_id, version in self._contracts
                if template_id == request.template_id
            )
            if known_versions:
                return _error_response(
                    request,
                    "version_conflict",
                    "requested template version is unavailable",
                    details={"available_versions": known_versions},
                )
            return _error_response(request, "not_found", "template was not found")
        if not contract.rights.allows(request.usage_context):
            return _error_response(request, "rights_restricted", "template rights do not allow the requested usage")
        try:
            slots = validate_selected_slots(contract, request.selected_slots)
        except ValueError as exc:
            return _error_response(request, "invalid_slots", str(exc))
        return TemplateResolveResponse(
            request_id=request.request_id,
            status="ok",
            selected_slots=slots,
            contract=contract,
        )


def _error_response(
    request: TemplateResolveRequest,
    code: str,
    message: str,
    *,
    details: dict | None = None,
) -> TemplateResolveResponse:
    return TemplateResolveResponse(
        request_id=request.request_id,
        status=code if code in {
            "not_found", "version_conflict", "rights_restricted", "invalid_slots", "protocol_error", "unavailable"
        } else "protocol_error",
        selected_slots={},
        fault=ProtocolFault(code=code, message=message, details=details or {}),
    )
