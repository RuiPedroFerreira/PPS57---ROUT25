#!/usr/bin/env python3
"""Agente RSU (Road-Side Unit) para processamento de SREMs (ETSI TS 103 301-3).

Responsabilidade do RSU em duas camadas (alinhada com TS 103 097 + TS 103 301):
1. Camada de **segurança / identidade**: validar o envelope ETSI TS 103 097
   (certificado conhecido, validade temporal, source consistente com requestor).
2. Camada de **elegibilidade C-ITS**: validar que o SREM se destina a esta
   interseção, dentro da janela ETA, e que não excede limites de prioridade
   simultânea ou cooldown pós-concessão.

O RSU **não** decide a estratégia (greenExtension/earlyGreen). Quando aceita,
devolve `ResponseStatus.PROCESSING` — significa "encaminhado ao motor de
decisão". O motor TSP, a jusante, é que preenche o `audit.granted_strategy`
no SSEM final ou emite um SSEM de granted/rejected. Esta separação espelha
a arquitetura real: o RSU é gateway, o TMC/TSP é o decisor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .config import CITSConfig, IntersectionConfig
from .messages import (
    CITSMessage,
    GrantedStrategy,
    MessageType,
    OperatorPriorityClass,
    PrioritizationResponse,
    ResponseStatus,
    SSEMAudit,
    SSEMLike,
    SREMLike,
    SecurityEnvelope,
    StationType,
    build_security_envelope,
    derive_station_id,
    parse_intersection_ref_id,
    sim_time_to_cdd,
    RequestType,
    validate_cits_message,
)


@dataclass
class RSUAgent:
    config: CITSConfig
    intersection: IntersectionConfig
    last_grant_time_by_vehicle: Dict[str, float] = field(default_factory=dict)
    known_certificate_ids: Set[str] = field(default_factory=set)
    processed_request_keys: Dict[tuple[int, int, int], float] = field(default_factory=dict)
    last_sequence_by_request: Dict[tuple[int, int], tuple[int, float]] = field(default_factory=dict)
    # Pedidos em PROCESSING ainda não fechados: (station_id, request_id) ->
    # (expires_at_s, vehicle_id). Persiste entre ticks para que o limite
    # max_active_requests_per_rsu conte o que está realmente ativo, não só o
    # batch do tick corrente. Sai por SSEM final (granted via
    # mark_priority_granted, rejected, cancelled) ou por expiração do TTL.
    processing_request_expiry: Dict[tuple[int, int], tuple[float, str]] = field(default_factory=dict)

    @property
    def rsu_id(self) -> str:
        return self.intersection.rsu_id

    def handle_messages(
        self, messages: Iterable[CITSMessage], sim_time_s: float
    ) -> List[SSEMLike]:
        responses: List[SSEMLike] = []
        max_active = int(self.config.rsu_policy.get("max_active_requests_per_rsu", 4))
        seen_request_keys: Set[tuple] = set()  # dedupe por (station_id, request_id, sequence_number)
        self._prune_replay_cache(sim_time_s)
        self._prune_processing_requests(sim_time_s)

        for message in messages:
            if message.message_type != MessageType.SREM.value:
                continue
            if not isinstance(message, SREMLike):
                # SREM marcada mas dataclass não corresponde — ex.: reconstruída
                # incorretamente. Rejeita explicitamente em vez de descartar.
                responses.append(self._make_response_for_malformed(message, sim_time_s, "request_type_mismatch"))
                continue
            request = message

            dedupe_key = (
                request.station_id,
                request.requests[0].request_id if request.requests else None,
                request.sequence_number,
            )
            # Cancelamentos são idempotentes por contrato: a retransmissão da
            # mesma priorityCancellation devolve o mesmo ack em vez de uma
            # rejeição por duplicado/replay.
            if not request.is_cancellation:
                if dedupe_key in seen_request_keys:
                    responses.append(self._reject(request, sim_time_s, "duplicate_request_in_batch"))
                    continue
                replay_problem = self._replay_or_ordering_problem(request, sim_time_s)
                if replay_problem is not None:
                    responses.append(self._reject(request, sim_time_s, replay_problem))
                    continue
            seen_request_keys.add(dedupe_key)

            active_key = self._active_request_key(request)
            too_many_active = (
                active_key not in self.processing_request_expiry
                and len(self.processing_request_expiry) >= max_active
            )
            decision = self._evaluate_request(request, sim_time_s, too_many_active=too_many_active)
            self._remember_request(request, sim_time_s)
            self._track_processing_request(request, decision, sim_time_s)
            responses.append(self._wrap_response(request, decision, sim_time_s))
        return responses

    # ------------------------------------------------------------------
    # Avaliação por camadas.
    # ------------------------------------------------------------------

    def _evaluate_request(
        self,
        request: SREMLike,
        sim_time_s: float,
        too_many_active: bool = False,
    ) -> "_DecisionPair":
        if not request.requests:
            return _DecisionPair.reject("malformed_request_no_signal_requests")
        signal_request = request.requests[0]

        validation_errors = validate_cits_message(request)
        if validation_errors:
            return _DecisionPair.reject(f"message_validation_failed:{validation_errors[0]}")

        # 1. Camada de segurança (ETSI TS 103 097).
        security_problem = self._validate_security(request.security, sim_time_s)
        if security_problem is not None:
            return _DecisionPair.reject(security_problem)

        # 2. Identidade do requestor (RSU vs broker source vs CDD station_id).
        identity_problem = self._validate_identity(request)
        if identity_problem is not None:
            return _DecisionPair.reject(identity_problem)

        # 3. Tipo de pedido: cancelamento é sempre aceite (idempotente).
        if signal_request.request_type == RequestType.PRIORITY_CANCELLATION.value:
            return _DecisionPair.cancelled()

        # 4. Endereçamento da interseção.
        expected_ref_id = parse_intersection_ref_id(self.intersection.intersection_id)
        if signal_request.intersection_ref_id != expected_ref_id:
            return _DecisionPair.reject("request_not_for_this_intersection")
        if not self.intersection.signal_controlled:
            return _DecisionPair.reject("intersection_not_signal_controlled")

        # 5. TTL.
        if request.expires_at_s is not None and sim_time_s > request.expires_at_s:
            return _DecisionPair.reject("request_expired")

        # 6. Limite simultâneo.
        if too_many_active:
            return _DecisionPair.reject("rsu_active_request_limit_exceeded")

        # 7. Cooldown pós-concessão.
        operational_vehicle_id = (
            request.requestor.operational_vehicle_id if request.requestor else ""
        )
        if self._cooldown_active(operational_vehicle_id, sim_time_s):
            return _DecisionPair.reject("cooldown_active_for_vehicle")

        # 8. Janela ETA.
        if not self._eta_in_window(request):
            return _DecisionPair.reject("request_eta_out_of_window")

        # 9. Condição operacional de prioridade.
        if not self._priority_condition_met(request):
            return _DecisionPair.reject("not_eligible_for_priority")

        return _DecisionPair.processing()

    def evaluate_request(
        self, request: SREMLike, sim_time_s: float, *, too_many_active: bool = False
    ) -> SSEMLike:
        """Convenience wrapper para testes — equivalente a tratar um único SREM.

        Produção usa `handle_messages` que faz dedupe in-batch e enforça limites
        de pedidos activos. Este método não conta o pedido para o limite e
        ignora o passe de dedupe, por isso é estritamente uma API de unidade.
        """
        decision = self._evaluate_request(request, sim_time_s, too_many_active=too_many_active)
        return self._wrap_response(request, decision, sim_time_s)

    def mark_priority_granted(self, vehicle_id: str, sim_time_s: float) -> None:
        """Mark a real downstream priority grant, not merely RSU forwarding."""
        self.last_grant_time_by_vehicle[vehicle_id] = sim_time_s
        # SSEM final (granted) fecha o pedido: liberta a quota de ativos.
        self.processing_request_expiry = {
            key: value
            for key, value in self.processing_request_expiry.items()
            if value[1] != vehicle_id
        }

    # ------------------------------------------------------------------
    # Helpers de validação.
    # ------------------------------------------------------------------

    def _validate_security(
        self, security: Optional[SecurityEnvelope], sim_time_s: float
    ) -> Optional[str]:
        if security is None:
            return "security_envelope_missing"
        now_ms = int(round(sim_time_s * 1000))
        if security.valid_until_ms <= now_ms:
            return "certificate_expired"
        if security.valid_until_ms <= security.generation_time_ms:
            return "certificate_validity_window_invalid"
        if security.generation_time_ms > now_ms + 1000:
            return "generation_time_in_future"
        signer_required = bool(self.config.rsu_policy.get("require_signed_messages", False))
        if signer_required and not security.signature_b64:
            return "signature_missing"
        trust_problem = self._validate_simulated_trust(security)
        if trust_problem is not None:
            return trust_problem
        # Aprende certificados nunca vistos (TOFU simplificado).
        # Numa pilha real isto seria substituído por validação ECDSA contra a
        # CA da hierarquia ETSI TS 102 941.
        if self._trust_mode() == "tofu":
            self.known_certificate_ids.add(security.certificate_id)
        return None

    def _validate_identity(self, request: SREMLike) -> Optional[str]:
        if request.requestor is None:
            return "requestor_missing"
        if not request.requestor.operational_vehicle_id:
            return "vehicle_id_missing"
        expected_source = f"OBU_{request.requestor.operational_vehicle_id}"
        if request.source_id != expected_source:
            return "source_id_does_not_match_vehicle"
        if request.security and request.security.signer_id != request.source_id:
            return "security_signer_does_not_match_source"
        expected_station_id = derive_station_id(request.requestor.operational_vehicle_id)
        if request.station_id != expected_station_id:
            return "station_id_does_not_match_vehicle"
        if request.requestor.station_id != expected_station_id:
            return "requestor_station_id_mismatch"
        telemetry_rsu = request.operator_telemetry.rsu_id if request.operator_telemetry else ""
        if telemetry_rsu and telemetry_rsu != self.intersection.rsu_id:
            return "request_rsu_id_mismatch"
        return None

    def _validate_simulated_trust(self, security: SecurityEnvelope) -> Optional[str]:
        trust_store = self.config.raw.get("trust_store", {})
        if not isinstance(trust_store, dict):
            return "trust_store_invalid"
        if security.certificate_id in set(trust_store.get("revoked_certificate_ids", [])):
            return "certificate_revoked"
        mode = self._trust_mode()
        if mode == "tofu":
            return None
        allowed_certificates = set(trust_store.get("allowed_certificate_ids", []))
        if security.certificate_id in allowed_certificates:
            return None
        allowed_signers = set(trust_store.get("allowed_signer_ids", []))
        if security.signer_id in allowed_signers:
            return None
        allowed_prefixes = tuple(str(item) for item in trust_store.get("allowed_signer_prefixes", []))
        if allowed_prefixes and security.signer_id.startswith(allowed_prefixes):
            return None
        if mode == "prefix_allowlist":
            return "security_signer_not_authorized"
        return "trust_mode_unsupported"

    def _trust_mode(self) -> str:
        trust_store = self.config.raw.get("trust_store", {})
        if not isinstance(trust_store, dict):
            return "tofu"
        return str(trust_store.get("mode", "tofu"))

    def _make_response_for_malformed(
        self, message: CITSMessage, sim_time_s: float, reason: str
    ) -> SSEMLike:
        request_id = 0
        sequence_number = 0
        requestor_station_id = getattr(message, "station_id", 0)
        moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
        response = PrioritizationResponse(
            request_id=request_id,
            sequence_number=sequence_number,
            requestor_station_id=requestor_station_id,
            response_status=ResponseStatus.REJECTED.value,
            granted_signal_group=None,
            valid_until_ms=int(round(sim_time_s * 1000))
            + int(round(float(self.config.rsu_policy.get("response_ttl_s", 15)) * 1000)),
        )
        audit = SSEMAudit(
            granted_strategy=GrantedStrategy.NONE.value,
            rejection_reason=reason,
            confidence=1.0,
            notes=["Mensagem SREM-marcada mas tipo inconsistente; rejeitada."],
        )
        return SSEMLike(
            message_type=MessageType.SSEM.value,
            station_id=derive_station_id(self.intersection.rsu_id),
            station_type=StationType.ROAD_SIDE_UNIT.value,
            source_id=self.intersection.rsu_id,
            destination_id=getattr(message, "source_id", "UNKNOWN"),
            generation_delta_time_ms=generation_delta,
            moy=moy,
            timestamp_ms=timestamp_ms,
            security=build_security_envelope(self.intersection.rsu_id, sim_time_s),
            intersection_ref_id=parse_intersection_ref_id(self.intersection.intersection_id),
            intersection_alias=self.intersection.intersection_id,
            tls_id=self.intersection.tls_id,
            rsu_id=self.intersection.rsu_id,
            response=response,
            audit=audit,
            correlation_id=getattr(message, "message_id", None),
        )

    def _reject(self, request: SREMLike, sim_time_s: float, reason: str) -> SSEMLike:
        return self._wrap_response(
            request, _DecisionPair.reject(reason), sim_time_s
        )

    def _wrap_response(
        self, request: SREMLike, decision: "_DecisionPair", sim_time_s: float
    ) -> SSEMLike:
        response_ttl_ms = int(round(float(self.config.rsu_policy.get("response_ttl_s", 15)) * 1000))
        moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
        request_id = request.requests[0].request_id if request.requests else 0

        response = PrioritizationResponse(
            request_id=request_id,
            sequence_number=request.sequence_number,
            requestor_station_id=request.station_id,
            response_status=decision.response_status,
            granted_signal_group=None,
            valid_until_ms=int(round(sim_time_s * 1000)) + response_ttl_ms,
        )
        confidence = 0.95 if decision.response_status == ResponseStatus.PROCESSING.value else 1.0
        audit = SSEMAudit(
            granted_strategy=GrantedStrategy.NONE.value,
            rejection_reason=decision.rejection_reason,
            confidence=confidence,
            notes=[],
        )
        return SSEMLike(
            message_type=MessageType.SSEM.value,
            station_id=derive_station_id(self.intersection.rsu_id),
            station_type=StationType.ROAD_SIDE_UNIT.value,
            source_id=self.intersection.rsu_id,
            destination_id=request.source_id,
            generation_delta_time_ms=generation_delta,
            moy=moy,
            timestamp_ms=timestamp_ms,
            security=build_security_envelope(self.intersection.rsu_id, sim_time_s),
            intersection_ref_id=parse_intersection_ref_id(self.intersection.intersection_id),
            intersection_alias=self.intersection.intersection_id,
            tls_id=self.intersection.tls_id,
            rsu_id=self.intersection.rsu_id,
            response=response,
            audit=audit,
            correlation_id=request.message_id,
        )

    def _eta_in_window(self, request: SREMLike) -> bool:
        policy = self.config.obu_policy
        eta = request.operator_telemetry.eta_to_stopline_s if request.operator_telemetry else 0.0
        is_emergency = request.priority_level == OperatorPriorityClass.EMERGENCY.value
        eta_min = 0.0 if is_emergency else float(policy.get("request_eta_min_s", 8))
        eta_max = (
            float(policy.get("emergency_request_eta_max_s", policy.get("request_eta_max_s", 45)))
            if is_emergency
            else float(policy.get("request_eta_max_s", 45))
        )
        return eta_min <= eta <= eta_max

    def _priority_condition_met(self, request: SREMLike) -> bool:
        policy = self.config.obu_policy
        if bool(policy.get("allow_nominal_priority_requests", False)):
            return True
        telemetry = request.operator_telemetry
        if telemetry is None:
            return False
        return (
            telemetry.schedule_delay_s >= float(policy.get("delay_threshold_s", 60))
            or abs(telemetry.headway_deviation_s)
            >= float(policy.get("headway_deviation_threshold_s", 120))
        )

    def _cooldown_active(self, vehicle_id: str, sim_time_s: float) -> bool:
        if not vehicle_id:
            return False
        last = self.last_grant_time_by_vehicle.get(vehicle_id)
        if last is None:
            return False
        cooldown_s = float(self.config.rsu_policy.get("cooldown_after_grant_s", 90))
        return sim_time_s - last < cooldown_s

    def _replay_or_ordering_problem(
        self, request: SREMLike, sim_time_s: float
    ) -> Optional[str]:
        key = self._request_key(request)
        if key is None:
            return None
        if key in self.processed_request_keys:
            return "duplicate_request_replayed"
        station_id, request_id, sequence_number = key
        last = self.last_sequence_by_request.get((station_id, request_id))
        if last is None:
            return None
        last_sequence, _ = last
        if _sequence_not_newer(sequence_number, last_sequence):
            return "out_of_order_request_sequence"
        return None

    def _remember_request(self, request: SREMLike, sim_time_s: float) -> None:
        key = self._request_key(request)
        if key is None:
            return
        station_id, request_id, sequence_number = key
        self.processed_request_keys[key] = sim_time_s
        self.last_sequence_by_request[(station_id, request_id)] = (sequence_number, sim_time_s)

    def _request_key(self, request: SREMLike) -> Optional[tuple[int, int, int]]:
        if not request.requests:
            return None
        signal_request = request.requests[0]
        return (request.station_id, signal_request.request_id, request.sequence_number)

    def _active_request_key(self, request: SREMLike) -> Optional[tuple[int, int]]:
        """Chave do pedido lógico (sem sequence_number): updates do mesmo
        pedido partilham a mesma entrada na quota de ativos."""
        if not request.requests:
            return None
        return (request.station_id, request.requests[0].request_id)

    def _track_processing_request(
        self, request: SREMLike, decision: "_DecisionPair", sim_time_s: float
    ) -> None:
        key = self._active_request_key(request)
        if key is None:
            return
        if decision.response_status == ResponseStatus.PROCESSING.value:
            self.processing_request_expiry[key] = (
                self._request_expiry(request, sim_time_s),
                request.vehicle_id,
            )
        elif request.is_cancellation or decision.response_status == ResponseStatus.REJECTED.value:
            # SSEM final (rejected/cancelled) fecha a cadeia deste pedido.
            self.processing_request_expiry.pop(key, None)

    def _request_expiry(self, request: SREMLike, sim_time_s: float) -> float:
        if request.expires_at_s is not None:
            return float(request.expires_at_s)
        if request.requests and request.requests[0].duration_ms > 0:
            return sim_time_s + request.requests[0].duration_ms / 1000.0
        return sim_time_s + float(
            self.config.rsu_policy.get(
                "dedupe_cache_ttl_s",
                self.config.obu_policy.get("request_lifecycle_ttl_s", 30.0),
            )
        )

    def _prune_processing_requests(self, sim_time_s: float) -> None:
        self.processing_request_expiry = {
            key: value
            for key, value in self.processing_request_expiry.items()
            if value[0] > sim_time_s
        }

    def _prune_replay_cache(self, sim_time_s: float) -> None:
        ttl_s = float(
            self.config.rsu_policy.get(
                "dedupe_cache_ttl_s",
                self.config.obu_policy.get("request_lifecycle_ttl_s", 30.0),
            )
        )
        cutoff = sim_time_s - ttl_s
        self.processed_request_keys = {
            key: timestamp
            for key, timestamp in self.processed_request_keys.items()
            if timestamp >= cutoff
        }
        self.last_sequence_by_request = {
            key: value
            for key, value in self.last_sequence_by_request.items()
            if value[1] >= cutoff
        }


# ---------------------------------------------------------------------------
# Helper interno: decisão (response_status, rejection_reason).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DecisionPair:
    response_status: str
    rejection_reason: Optional[str] = None

    @staticmethod
    def processing() -> "_DecisionPair":
        return _DecisionPair(response_status=ResponseStatus.PROCESSING.value)

    @staticmethod
    def cancelled() -> "_DecisionPair":
        # PrioritizationResponseStatus não tem "cancelled"; o standard fecha
        # o ciclo do pedido marcando o subsequente como "unknown" (não há
        # mais nada a reportar). Usamos `unknown` que é o mais próximo.
        return _DecisionPair(response_status=ResponseStatus.UNKNOWN.value, rejection_reason="priority_request_cancelled")

    @staticmethod
    def reject(reason: str) -> "_DecisionPair":
        return _DecisionPair(response_status=ResponseStatus.REJECTED.value, rejection_reason=reason)


def build_rsu_agents(config: CITSConfig) -> Dict[str, RSUAgent]:
    return {
        intersection.rsu_id: RSUAgent(config=config, intersection=intersection)
        for intersection in config.intersections
    }


def _sequence_not_newer(candidate: int, previous: int) -> bool:
    if candidate == previous:
        return True
    delta = (candidate - previous) % 65536
    return delta == 0 or delta > 32767
