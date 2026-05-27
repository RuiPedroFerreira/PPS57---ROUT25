# PPS57 C-ITS/TSP Protocol ICD

This document is the simulation Interface Control Document for the C-ITS/TSP
asset. It describes the JSON simulation profile used in SUMO. It is not a
claim of certified ETSI conformance and does not replace ASN.1/OER/UPER
encoding, ITS-G5/PC5 transport, ETSI PKI, or operational back-office systems.

## Scope

The asset simulates traffic-signal priority for public transport and emergency
vehicles in a SUMO corridor. The protocol flow is:

1. `MAPEM`: RSU broadcasts intersection topology.
2. `SPATEM`: RSU broadcasts signal state.
3. `SREM`: OBU requests, updates, or cancels priority.
4. `SSEM`: RSU acknowledges processing, rejects, grants, or reports final status.
5. `TSPDecision`: internal decision-engine output.
6. `ActuationResult`: simulated or TraCI controller result.

The externally visible C-ITS messages are `MAPEM`, `SPATEM`, `SREM`, and
`SSEM`. `TSPDecision` and `ActuationResult` are internal audit objects.

## Standards Reference Model

The JSON shape follows the concepts of:

- ETSI TS 103 301: MAPEM/SPATEM/SREM/SSEM facilities layer concepts.
- ETSI TS 102 894-2: CDD identifiers, station types, timestamps, and units.
- ETSI TS 103 097: security envelope concept.
- ISO 19091: traffic-signal information and priority request concepts.

Simulation assumptions:

- Encoding is JSON, not ASN.1/OER/UPER.
- Transport is an in-memory broker with optional simulated loss, latency,
  duplication, and reordering.
- Security is a structural envelope plus configurable simulated trust policy,
  not operational ETSI PKI.
- Geometry may use synthetic WGS84-like reference points derived from SUMO
  configuration, not surveyed operational geometry.

## Common Envelope

All C-ITS PDUs include:

| Field | Required | Source | Notes |
| --- | --- | --- | --- |
| `message_id` | yes | simulation envelope | UUID for audit and dedupe. |
| `correlation_id` | optional | simulation envelope | Previous related message id. Must not carry human reason text. |
| `protocol_version` | yes | simulation envelope | Current profile is `0.4.0`. |
| `message_type` | yes | standard-like | One of `MAPEM`, `SPATEM`, `SREM`, `SSEM`. |
| `station_id` | yes | CDD-like | uint32 derived deterministically in simulation. |
| `station_type` | yes | CDD-like | ETSI CDD subset. |
| `source_id` | yes | broker extension | OBU/RSU operational id. |
| `destination_id` | yes | broker extension | RSU id, OBU id, or `BROADCAST`. |
| `moy` | yes | CDD-like | Minute-of-year, range `0..527040`. |
| `timestamp_ms` | yes | CDD-like | Milliseconds inside minute, range `0..59999`. |
| `generation_delta_time_ms` | yes | CDD-like | Milliseconds modulo `65536`. |
| `security` | yes | TS 103 097-like | Structural signer/certificate/timing envelope. |

## Message Catalogue

### MAPEM

Purpose: broadcast intersection topology and priority approaches.

Producer: RSU.

Consumer: OBU, dashboard, audit tools.

Required fields:

- Common envelope.
- `intersection_ref_id`.
- `intersection_alias`.
- `tls_id`.
- `rsu_id`.
- `revision`.
- `ref_point`.
- `approaches[]`.

Simulation extensions:

- `intersection_alias`, `tls_id`, `rsu_id`, SUMO lane ids.
- Synthetic `ref_point` when no surveyed geometry exists.

Failure modes:

- Missing approaches.
- Invalid station/timestamp/security envelope.
- Inconsistent intersection reference.

### SPATEM

Purpose: publish signal state by movement/signal group.

Producer: RSU/SUMO adapter.

Consumer: OBU, TSP, dashboard, audit tools.

Required fields:

- Common envelope.
- `intersection_ref_id`.
- `intersection_alias`.
- `tls_id`.
- `revision`.
- `movement_events[]`.
- `intersection_status`.

Simulation extensions:

- `debug_sumo_state` for raw SUMO red/yellow/green string correlation.

Validation:

- `signal_group_id` range `1..255`.
- `event_state` in the MovementPhaseState subset.
- Timing windows must be non-negative and ordered.
- Confidence range `0..15`.

### SREM

Purpose: request, update, or cancel signal priority.

Producer: OBU.

Consumer: RSU/TSP.

Required fields:

- Common envelope.
- `sequence_number`.
- `requests[]`.
- `requestor`.
- `operator_telemetry`.

Required `requests[]` fields:

- `intersection_ref_id`.
- `request_id`, range `1..255`.
- `request_type`: `priorityRequest`, `priorityRequestUpdate`, or
  `priorityCancellation`.
- `in_bound_lane_id` for request/update.
- `eta_min_minute`.
- `eta_min_second_ms`.
- `duration_ms`.

Simulation extensions:

- `operator_telemetry.schedule_delay_s`.
- `operator_telemetry.headway_deviation_s`.
- `operator_telemetry.distance_to_stopline_m`.
- `operator_telemetry.eta_to_stopline_s`.
- `operator_telemetry.operator_priority_class`.
- `operator_telemetry.cancellation_reason`.
- `expires_at_s`.

Lifecycle:

- `priorityRequest` opens a request.
- `priorityRequestUpdate` updates the same `request_id` with a newer
  `sequence_number`.
- `priorityCancellation` closes the active request. Its `correlation_id`
  points to the previous SREM `message_id`.

Failure modes:

- Invalid schema/range.
- Unknown or unauthorized signer.
- Wrong destination/intersection.
- Duplicate or replayed `(station_id, request_id, sequence_number)`.
- Out-of-order sequence number.
- Expired request or security envelope.
- Ineligible ETA or priority condition.

### SSEM

Purpose: respond to an SREM and close the request lifecycle.

Producer: RSU and TSP controller.

Consumer: OBU, dashboard, audit tools.

Required fields:

- Common envelope.
- `intersection_ref_id`.
- `intersection_alias`.
- `tls_id`.
- `rsu_id`.
- `response`.
- `audit`.

`response.response_status` values used by this simulation:

- `processing`: RSU accepted the SREM for downstream TSP decision.
- `rejected`: RSU/TSP/safety/controller rejected the request.
- `granted`: TSP granted priority or determined no physical actuation was
  required because green was already sufficient.
- `unknown`: idempotent cancellation acknowledgement.

Simulation extensions:

- `audit.granted_strategy`.
- `audit.rejection_reason`.
- `audit.confidence`.
- `audit.notes`.

Lifecycle requirement:

Every accepted `SREM` should have:

1. One gateway `SSEM` with `response_status=processing`.
2. One final `SSEM` with `response_status=granted` or `rejected`.

## Request State Machine

Allowed conceptual states:

| State | Meaning |
| --- | --- |
| `created` | SREM observed. |
| `processing` | Gateway RSU forwarded to TSP. |
| `granted` | Priority was granted or no actuation was needed. |
| `rejected` | Request was rejected by RSU/TSP/safety/controller. |
| `cancelled` | OBU cancelled the request. |
| `expired` | Request timed out before closure. |

Allowed transitions:

- `created -> processing`
- `created -> rejected`
- `created -> cancelled`
- `created -> expired`
- `processing -> granted`
- `processing -> rejected`
- `processing -> cancelled`
- `processing -> expired`

Terminal states are `granted`, `rejected`, `cancelled`, and `expired`.

## Synthetic Data Policy

The asset must distinguish:

- Standard-like protocol fields.
- Operator extensions.
- SUMO artifacts.
- Synthetic assumptions.

Synthetic values are acceptable when:

- Units and ranges are documented.
- Values are deterministic or seeded.
- Generated values are plausible for corridor simulation.
- Logs retain enough metadata to replay lifecycle decisions.

Real operational data is not required for this simulation profile.

## Audit Expectations

For each request chain, audit tooling should reconstruct:

`SREM -> SSEM processing -> TSPDecision -> ActuationResult -> SSEM final`

Protocol KPIs should include:

- SREM to processing latency.
- SREM to final SSEM latency.
- Missing final SSEM count.
- Duplicate/replayed request count.
- Out-of-order request count.
- Rejections by reason.
- Final SSEM by status.
- Controller NACK and actuation error counts.
