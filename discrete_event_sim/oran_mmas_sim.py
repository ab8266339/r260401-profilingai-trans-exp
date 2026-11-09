"""
Tier B: SimPy Discrete-Event Simulator for mMAS O-RAN Evaluation.

Models the complete O-RAN control loop:
  - Agent flow generators (14 types across 3 model tiers)
  - O-DU radio scheduler with TDD UL/DL adaptation
  - Edge GPU cluster with continuous batching and KV-cache
  - AJS-xApp (near-RT RIC, 100ms loop) with dimensionality-reduced admission
  - BPR-rApp (non-RT RIC, 5s loop) with barrel-principle optimizer
  - E2SM-AIC message flow between components

Usage:
    python oran_mmas_sim.py --agents 50 --seeds 50 --duration 60 --outdir results
"""

import simpy
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import json
import os
import argparse
import time


# ════════════════════════════════════════════════════════════
# Configuration & Parameters (Table III of the paper)
# ════════════════════════════════════════════════════════════

@dataclass
class NetworkConfig:
    """Network and hardware parameters matching Table III."""
    cell_ul_mbps: float = 50.0       # Raw cell UL capacity
    cell_dl_mbps: float = 200.0      # Raw cell DL capacity
    tdd_ul_ratio: float = 0.20       # Baseline TDD UL ratio
    backhaul_rtt_ms: float = 2.0     # Backhaul RTT
    core_rtt_ms: float = 5.0         # N2/N3 RTT
    cloud_rtt_ms: float = 40.0       # Cloud RTT
    sched_overhead_ms: float = 4.0   # 5G NR scheduling overhead (SR + grant)


@dataclass
class GPUConfig:
    """Edge GPU cluster parameters."""
    num_gpus: int = 4
    tflops_per_gpu: float = 312.0     # A100 FP16 peak, matching the paper
    # One aggregate service resource per GPU. This is a simulator abstraction,
    # not a claim that a serving engine cannot host concurrent sequences.
    # It preserves the physical cluster-work capacity exactly.
    max_concurrent: int = 1
    kv_hit_standalone: float = 0.70
    kv_hit_pipeline: float = 0.95


@dataclass
class ModelTier:
    """LLM model tier parameters."""
    name: str
    alpha: float    # Prefill TFLOPs/token
    rho: float      # Decode TFLOPs/token
    gamma: float    # KV-cache MB/token
    weight_gb: float


# Three model tiers from Table III
MODEL_TIERS = [
    ModelTier('7B',  0.014, 0.030, 0.1, 14),
    ModelTier('33B', 0.066, 0.066, 0.5, 66),
    ModelTier('70B', 0.140, 0.140, 1.0, 140),
]

# R1 fix F03 (2026-10-01): one service price for execution and admission.
SERVICE_FLOOR_S = 0.005  # per-invocation floor (kernel launch, memory transfers)
_GPU_DEFAULTS = GPUConfig()
SERVICE_FLOOR_TFLOPS = SERVICE_FLOOR_S * _GPU_DEFAULTS.tflops_per_gpu


def stage_service_tflops(tier: ModelTier, tau_in: float, tau_out: float,
                         hit_rate: float,
                         prefill_tokens: Optional[float] = None) -> float:
    """Effective accelerator work (TFLOPs) of one invocation.

    The hit-rate factor 1 - 0.3 h models partial prefix reuse and multiplies
    prefill and decode on the replay path. A pinned later stage replaces only
    the prefill term by its new tokens; decode keeps the same factor, so the
    two paths differ in prefill alone.
    """
    disc = 1.0 - hit_rate * 0.3
    if prefill_tokens is None:
        return (tier.alpha * tau_in + tier.rho * tau_out) * disc
    return tier.alpha * prefill_tokens + tier.rho * tau_out * disc


def pinned_state_tokens(stages, increments) -> float:
    """Append-only final context held by a pinned session (R1 fix F01/F04):
    first prompt, every output and every later instruction."""
    return stages[0][0] + sum(to for _, to in stages) + sum(increments)

BETA = 4  # bytes per token


@dataclass
class AgentType:
    """Agent type definition."""
    name: str
    tier_idx: int           # Index into MODEL_TIERS
    lambda_hz: float        # Request rate
    tau_in_mu: float        # Log-normal mean for input tokens
    tau_in_sigma: float     # Log-normal sigma
    tau_out_mu: float       # Log-normal mean for output tokens
    tau_out_sigma: float
    tau_max: int = 2048
    is_pipeline: bool = False
    pipeline_depth: int = 1
    sigma_retention: float = 1.0  # Summarization retention rate
    latency_sla_ms: float = 1000.0


def create_agent_types() -> List[AgentType]:
    """Create 14 agent types spanning 3 model tiers."""
    types = [
        # Tier 0 (7B) - lightweight agents
        AgentType("text_chat_7b",      0, 0.5,  5.0, 1.2, 4.5, 1.0, latency_sla_ms=500),
        AgentType("code_assist_7b",    0, 0.3,  5.5, 1.3, 5.0, 1.2, latency_sla_ms=800),
        AgentType("translation_7b",    0, 0.4,  4.8, 1.0, 4.8, 1.0, latency_sla_ms=400),
        AgentType("summarizer_7b",     0, 0.2,  6.0, 1.5, 4.0, 0.8, latency_sla_ms=600),
        AgentType("pipeline_7b",       0, 0.3,  5.0, 1.2, 4.5, 1.0,
                  is_pipeline=True, pipeline_depth=3, sigma_retention=0.8, latency_sla_ms=1500),
        # Tier 1 (33B) - medium agents
        AgentType("reasoning_33b",     1, 0.2,  5.5, 1.4, 5.5, 1.4, latency_sla_ms=2000),
        AgentType("vlm_33b",           1, 0.15, 6.5, 1.0, 5.0, 1.2, latency_sla_ms=1500),
        AgentType("planning_33b",      1, 0.1,  6.0, 1.3, 6.0, 1.3, latency_sla_ms=3000),
        AgentType("pipeline_33b",      1, 0.15, 5.5, 1.2, 5.0, 1.1,
                  is_pipeline=True, pipeline_depth=5, sigma_retention=0.5, latency_sla_ms=5000),
        AgentType("multimodal_33b",    1, 0.1,  7.0, 1.0, 5.5, 1.0, latency_sla_ms=2500),
        # Tier 2 (70B) - heavy agents
        AgentType("frontier_70b",      2, 0.08, 6.0, 1.5, 6.0, 1.5, latency_sla_ms=5000),
        AgentType("deep_reason_70b",   2, 0.05, 7.0, 1.2, 7.0, 1.2, latency_sla_ms=8000),
        AgentType("pipeline_70b",      2, 0.08, 6.0, 1.3, 5.5, 1.2,
                  is_pipeline=True, pipeline_depth=4, sigma_retention=0.5, latency_sla_ms=10000),
        AgentType("orchestrator_70b",  2, 0.03, 5.5, 1.0, 6.5, 1.5, latency_sla_ms=6000),
    ]
    return types


# ════════════════════════════════════════════════════════════
# E2SM-AIC Message Definitions
# ════════════════════════════════════════════════════════════

@dataclass
class E2SM_AIC_Indication:
    """E2SM-AIC Indication message (O-DU → xApp)."""
    timestamp: float
    agent_id: int
    agent_type_name: str
    token_var: float
    buffer_occupancy_ul: float  # bytes
    pipeline_state: Tuple[int, int]  # (current_stage, total_stages)
    ul_dl_flow_count: Tuple[int, int]
    demand_vector: np.ndarray   # 4D: (b_ul, b_dl, g, c)


@dataclass
class E2SM_AIC_Control:
    """E2SM-AIC Control message (xApp → O-DU)."""
    timestamp: float
    prb_mask: Dict[int, float]   # agent_id -> allocation fraction
    tdd_ul_ratio: float


@dataclass
class MEC_Control:
    """MEC Control message (xApp → MEC GPU via REST)."""
    timestamp: float
    gpu_priority_weights: Dict[int, float]  # tier_idx -> priority
    pipeline_budgets: Dict[int, float]      # agent_id -> GPU time budget ms


# ════════════════════════════════════════════════════════════
# Core Simulation Components
# ════════════════════════════════════════════════════════════

class MetricsCollector:
    """Collects simulation metrics for analysis."""

    def __init__(self):
        self.latencies: List[Dict] = []
        self.generated_count = 0     # post-warmup request generations
        self.e2_indications: List[Dict] = []
        self.e2_controls: List[Dict] = []
        self.xapp_decisions: List[Dict] = []
        self.rapp_decisions: List[Dict] = []
        self.gpu_utilization: List[Tuple[float, float]] = []
        self.ul_utilization: List[Tuple[float, float]] = []
        self.admissions: List[Dict] = []

    def record_latency(self, agent_id, agent_type, timestamp, components):
        self.latencies.append({
            'agent_id': agent_id,
            'agent_type': agent_type,
            'timestamp': timestamp,
            **components
        })

    def to_dict(self):
        return {
            'latencies': self.latencies,
            'e2_indications': len(self.e2_indications),
            'e2_controls': len(self.e2_controls),
            'xapp_decisions': self.xapp_decisions,
            'rapp_decisions': self.rapp_decisions,
            'gpu_utilization': self.gpu_utilization,
            'ul_utilization': self.ul_utilization,
        }


class ODU:
    """O-DU: Radio scheduler with M/G/1 queuing model and TDD adaptation.

    Models UL/DL as M/G/1 queues on a shared cell link.  Tracks offered
    load analytically so the xApp sees realistic utilization and triggers
    TDD adaptation when UL becomes saturated.

    Key dynamics:
      - UL service time = payload / effective_UL_rate
      - M/G/1 waiting time from Pollaczek-Khinchine formula
      - Effective UL rate scales with TDD ratio
    """

    def __init__(self, env: simpy.Environment, net_cfg: NetworkConfig,
                 metrics: MetricsCollector):
        self.env = env
        self.cfg = net_cfg
        self.metrics = metrics
        self.tdd_ul_ratio = net_cfg.tdd_ul_ratio
        self._update_rates()

        # M/G/1 queue for UL (serialized transmissions on shared uplink)
        self.ul_queue = simpy.Resource(env, capacity=1)
        self.dl_queue = simpy.Resource(env, capacity=1)

        # Load tracking: list of (timestamp, service_duration) pairs
        self.ul_service_log: List[Tuple[float, float]] = []
        self.dl_service_log: List[Tuple[float, float]] = []
        self._window_s = 2.0
        self.active_ul_flows = 0
        self.active_dl_flows = 0
        self.agent_buffers: Dict[int, float] = {}

    def _update_rates(self):
        self.effective_ul_bps = self.cfg.cell_ul_mbps * self.tdd_ul_ratio * 1e6 / 8
        self.effective_dl_bps = self.cfg.cell_dl_mbps * (1 - self.tdd_ul_ratio) * 1e6 / 8

    def update_tdd_ratio(self, new_ratio: float):
        """Update TDD UL ratio (from xApp control)."""
        self.tdd_ul_ratio = np.clip(new_ratio, 0.10, 0.70)
        self._update_rates()

    def transmit_ul(self, agent_id: int, payload_bytes: float):
        """Transmit uplink via M/G/1 queue on shared UL channel."""
        self.agent_buffers[agent_id] = self.agent_buffers.get(agent_id, 0) + payload_bytes
        self.active_ul_flows += 1

        # Queue for UL channel access (M/G/1: one transmission at a time)
        with self.ul_queue.request() as req:
            yield req

            # 5G NR scheduling overhead (SR + BSR + grant)
            yield self.env.timeout(self.cfg.sched_overhead_ms / 1000.0)

            # Service time: payload / effective UL rate
            service_time = payload_bytes / self.effective_ul_bps
            self.ul_service_log.append((self.env.now, service_time))
            yield self.env.timeout(service_time)

        self.active_ul_flows = max(0, self.active_ul_flows - 1)
        self.agent_buffers[agent_id] = max(0, self.agent_buffers.get(agent_id, 0) - payload_bytes)

    def transmit_dl(self, payload_bytes: float):
        """Transmit downlink via M/G/1 queue on shared DL channel."""
        self.active_dl_flows += 1

        with self.dl_queue.request() as req:
            yield req
            service_time = payload_bytes / self.effective_dl_bps
            self.dl_service_log.append((self.env.now, service_time))
            yield self.env.timeout(service_time)

        self.active_dl_flows = max(0, self.active_dl_flows - 1)

    def ul_utilization(self) -> float:
        """Sliding-window UL utilization = sum(service_times) / window."""
        cutoff = self.env.now - self._window_s
        # Prune old entries
        while self.ul_service_log and self.ul_service_log[0][0] < cutoff:
            self.ul_service_log.pop(0)
        if not self.ul_service_log:
            return 0.0
        total_busy = sum(s for _, s in self.ul_service_log)
        return min(total_busy / self._window_s, 1.0)

    def dl_utilization(self) -> float:
        """Sliding-window DL utilization = sum(service_times) / window."""
        cutoff = self.env.now - self._window_s
        while self.dl_service_log and self.dl_service_log[0][0] < cutoff:
            self.dl_service_log.pop(0)
        if not self.dl_service_log:
            return 0.0
        total_busy = sum(s for _, s in self.dl_service_log)
        return min(total_busy / self._window_s, 1.0)


class EdgeGPUCluster:
    """Capacity-conserving edge GPU work queue with compatible-prefix cache."""

    def __init__(self, env: simpy.Environment, gpu_cfg: GPUConfig,
                 metrics: MetricsCollector):
        self.env = env
        self.cfg = gpu_cfg
        self.metrics = metrics
        total_slots = gpu_cfg.num_gpus * gpu_cfg.max_concurrent
        self.gpu_slots = simpy.Resource(env, capacity=total_slots)
        self.total_tflops = gpu_cfg.num_gpus * gpu_cfg.tflops_per_gpu

        # Priority weights from xApp (default: equal)
        self.tier_priority: Dict[int, float] = {0: 1.0, 1: 1.0, 2: 1.0}
        self.pipeline_budgets: Dict[int, float] = {}

        # Service state. The analytical μ(n) law is evaluated separately; it
        # must not be multiplied into per-request service while every request
        # simultaneously receives full-cluster throughput.
        self.current_batch_size = 0
        self.kv_cache_used_mb = 0.0
        self.kv_cache_total_mb = gpu_cfg.num_gpus * 80_000  # 80GB per A100

        # Finite application-state store for AFS-C persistence (None = no cap).
        # Reservations model retained pipeline context, separate from the
        # per-inference KV accounting above.
        self.state_store_mb: Optional[float] = None
        self.state_used_mb = 0.0
        self.state_reject_times: List[float] = []

    def try_reserve_state(self, mb: float, limit_frac: float = 1.0) -> bool:
        """Reserve persistent context state; False when headroom is exhausted.
        limit_frac < 1 keeps a safety margin (pinned KV state, R1 fix F01)."""
        if self.state_store_mb is not None and \
                self.state_used_mb + mb > limit_frac * self.state_store_mb:
            self.state_reject_times.append(self.env.now)
            return False
        self.state_used_mb += mb
        return True

    def state_rejects_since(self, t: float) -> int:
        """Reservation failures after time t (headroom-exhaustion signal)."""
        self.state_reject_times = [x for x in self.state_reject_times if x >= t]
        return len(self.state_reject_times)

    def release_state(self, mb: float):
        self.state_used_mb = max(0.0, self.state_used_mb - mb)

    def batching_coefficient(self, n: int) -> float:
        """μ(n): convex decreasing batching multiplexing coefficient.
        μ(n) = 1/(1 + β·ln(n)) — convex, decreasing, μ(1)=1.
        Calibrated so μ(64)≈0.25 (4× throughput at full batch).
        """
        if n <= 1:
            return 1.0
        beta_coeff = 1.0 / np.log(64) * 3  # calibrate: μ(64) ≈ 0.25
        return 1.0 / (1.0 + beta_coeff * np.log(n))

    def infer(self, agent_id: int, tier_idx: int, tau_in: float,
              tau_out: float, is_pipeline_stage: bool = False,
              prefill_tokens: Optional[float] = None):
        """Run inference. Yields GPU queue wait + compute time."""
        tier = MODEL_TIERS[tier_idx]

        kv_mem = tier.gamma * (tau_in + tau_out)              # MB

        # KV-cache hit reduces effective compute (partial reduction). A pinned
        # stage (prefill_tokens given) prefills only its new tokens; decode is
        # priced identically on both paths (R1 fix F03).
        hit_rate = self.cfg.kv_hit_pipeline if is_pipeline_stage else self.cfg.kv_hit_standalone
        effective_g = stage_service_tflops(tier, tau_in, tau_out, hit_rate,
                                           prefill_tokens)

        queue_start = self.env.now

        with self.gpu_slots.request() as req:
            yield req
            queue_wait = self.env.now - queue_start

            # Each request owns one simulated GPU service resource. Dividing
            # by per-GPU (not full-cluster) throughput keeps aggregate work
            # service at or below num_gpus * tflops_per_gpu.
            self.current_batch_size += 1

            # Compute time (seconds)
            compute_time = effective_g / self.cfg.tflops_per_gpu
            # Minimum latency floor (kernel launch, memory transfers)
            compute_time = max(compute_time, 0.005)  # 5ms floor

            # Track KV-cache
            self.kv_cache_used_mb += kv_mem
            yield self.env.timeout(compute_time)
            self.kv_cache_used_mb = max(0, self.kv_cache_used_mb - kv_mem)
            self.current_batch_size = max(0, self.current_batch_size - 1)

        return queue_wait, compute_time


class AJS_xApp:
    """Agent-aware Joint Scheduler (near-RT RIC, 100ms loop).

    Implements:
      - Decision 1: TDD ratio adaptation
      - Decision 2: Dimensionality-reduced admission control (Theorem 2)
      - Decision 3: Pipeline budget allocation (Theorem 3 weights)
    """

    def __init__(self, env: simpy.Environment, odu: ODU,
                 gpu_cluster: EdgeGPUCluster, metrics: MetricsCollector,
                 net_cfg: NetworkConfig):
        self.env = env
        self.odu = odu
        self.gpu = gpu_cluster
        self.metrics = metrics
        self.net_cfg = net_cfg
        self.interval_s = 0.1  # 100ms control loop
        self.safety_margin = 0.05  # δ for capacity projection

        # Ablation switches (defaults preserve the original full behavior)
        self.enable_gpu_gate = True    # joint radio-compute TDD gating
        self.enable_admission = True   # Decision 2/3 (advisory in this simulator)
        self.enable_dl_guard = True    # downlink-margin projection in the gate
        self.enable_state_guard = False  # state-margin projection (joint gate)
        self.dl_util_threshold = 0.85  # projected DL utilization ceiling
        self.state_util_threshold = 0.95  # state-store occupancy ceiling
        # R1 (TNSM-2026-11960, Reviewer 2 Comment 4): compute-aware AFS-C
        # eligibility. Slot utilization sampled once per control tick and
        # averaged over compute_window_ticks; the ceiling reuses the gate's
        # own compute criterion (0.80). Pure bookkeeping: no RNG draws and no
        # scheduled events, so pre-existing arms stay bit-exact.
        self.compute_util_ceiling = 0.80
        # R1 sensitivity sweep: gate literals exposed as attributes (defaults unchanged)
        self.gate_compute_ceiling = 0.80
        self.tdd_step = 0.05
        self.tdd_ratio_cap = 0.55
        self.compute_window_ticks = 20
        self.gpu_util_ticks: List[float] = []
        self.evaluated_agents: set = set()   # agents covered by the last admission run
        # Joint-gate margin bookkeeping (per control tick with UL pressure)
        self.margin_log = {'ul_triggers': 0, 'raises': 0,
                           'holds_compute': 0, 'holds_dl': 0,
                           'holds_state': 0}

        # State
        self.demand_vectors: Dict[int, np.ndarray] = {}
        # R1 joint provisioning: replay-version demand and pinned-state size
        self.alt_demands: Dict[int, tuple] = {}
        self.provisioning = False
        self.pin_ok: set = set()
        self.agent_rates: Dict[int, float] = {}
        self.agent_tiers: Dict[int, int] = {}
        self.admitted_agents: set = set()
        # R1 fix F01: committed offered load (unnormalized) of admitted agents
        self.committed: Dict[int, np.ndarray] = {}

        # TDD adaptation state with hysteresis
        self.ul_util_history: List[float] = []
        self.tdd_hysteresis_high = 0.40
        self.tdd_hysteresis_low = 0.15

    def register_alt(self, agent_id: int, replay_vec: np.ndarray, kv_mb: float):
        """R1: stateless-replay demand and pinned-state size of the same session."""
        self.alt_demands[agent_id] = (replay_vec, kv_mb)

    def register_agent(self, agent_id: int, tier_idx: int,
                       demand_vec: np.ndarray, lambda_hz: float):
        """Register agent demand for admission control."""
        self.demand_vectors[agent_id] = demand_vec
        self.agent_rates[agent_id] = lambda_hz
        self.agent_tiers[agent_id] = tier_idx

    def unregister_agent(self, agent_id: int):
        self.demand_vectors.pop(agent_id, None)
        self.agent_rates.pop(agent_id, None)
        self.agent_tiers.pop(agent_id, None)
        self.admitted_agents.discard(agent_id)

    def _capacities(self) -> np.ndarray:
        return np.array([
            self.odu.effective_ul_bps,
            self.odu.effective_dl_bps,
            self.gpu.total_tflops,
            (self.gpu.state_store_mb if self.gpu.state_store_mb
             else self.gpu.kv_cache_total_mb),
        ])

    def admit_session(self, agent_id: int, lambda_hz: float, versions):
        """R1 fix F01: decide one arriving session with its own descriptor.

        `versions` is an ordered list of (kind, demand_vec, kv_mb). The agent's
        previous commitment is withdrawn, then each version is tested in turn:
        its offered load lambda * r is added to the committed load of the other
        admitted agents and every rate coordinate must stay at or below
        1 - delta. A 'pinned' version is checked on the state coordinate
        against the reserved occupancy of the store, used + kv <= (1 - delta)
        store, and its state is reserved in the same step. Returns
        (kind, reserved_mb), or ('block', 0.0) when no version fits.
        """
        self.committed.pop(agent_id, None)
        base = (np.sum(list(self.committed.values()), axis=0)
                if self.committed else np.zeros(4))
        C = self._capacities()
        limit = 1.0 - self.safety_margin
        state_declined = False
        decision = ('block', 0.0)
        for kind, vec, kv in versions:
            off = np.asarray(vec, dtype=float) * lambda_hz
            if kind in ('pinned', 'replay', 'stateless'):
                # replay and stateless versions retain no state; pinned state
                # is checked against the store's occupancy below
                off[3] = 0.0
            if not np.all((base + off) / C <= limit):
                continue
            reserved = 0.0
            if kind == 'pinned' and self.gpu.state_store_mb:
                if self.gpu.state_used_mb + kv > limit * self.gpu.state_store_mb:
                    state_declined = True
                    continue
                self.gpu.state_used_mb += kv
                reserved = kv
            self.committed[agent_id] = off
            decision = (kind, reserved)
            break
        if state_declined:
            self.gpu.state_reject_times.append(self.env.now)
        return decision

    def _pca_project(self, demands: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Project demands to 2D via PCA (Theorem 2)."""
        if demands.shape[0] < 2:
            return demands[:, :2], np.eye(2, 4)
        centered = demands - demands.mean(axis=0)
        cov = np.cov(centered, rowvar=False)
        eigvals, eigvecs = np.linalg.eigh(cov)
        # Top-2 eigenvectors
        idx = np.argsort(eigvals)[::-1][:2]
        P = eigvecs[:, idx]  # 4x2 projection matrix
        projected = centered @ P
        return projected, P.T

    def _admission_control(self):
        """Decision 2: Dimensionality-reduced admission (Algorithm 1)."""
        if not self.demand_vectors:
            return

        agent_ids = list(self.demand_vectors.keys())
        demands = np.array([self.demand_vectors[aid] for aid in agent_ids])
        rates = np.array([self.agent_rates[aid] for aid in agent_ids])

        if len(agent_ids) < 3:
            # Too few agents for meaningful PCA, admit all
            self.admitted_agents = set(agent_ids)
            return

        # Convert per-request descriptors to normalized offered-load
        # contributions. For this advisory diagnostic only, memory uses a
        # documented 1 s state-holding reference; production code must use
        # measured q_i = lambda_i E[H_i].
        C_4d = np.array([
            self.odu.effective_ul_bps,                  # UL bytes/s
            self.odu.effective_dl_bps,                   # DL bytes/s
            self.gpu.total_tflops,                       # GPU TFLOPs
            (self.gpu.state_store_mb if self.gpu.state_store_mb
             else self.gpu.kv_cache_total_mb),           # state capacity MB
        ])
        offered = demands.copy()
        offered[:, :3] *= rates[:, None]
        offered[:, 3] *= rates
        normalized = offered / C_4d

        # Project normalized (unitless) loads to 2D for ranking only.
        projected, _ = self._pca_project(normalized)

        # Utility density: U_i / ||v_tilde_i||
        # U_i = 1/latency_sla (higher priority for tighter SLA)
        utilities = np.ones(len(agent_ids))
        norms = np.linalg.norm(projected, axis=1) + 1e-10
        density = utilities / norms

        # Greedy ranking, exact 4D normalized commit.
        order = np.argsort(density)[::-1]
        admitted = set()
        R_4d = np.zeros(4)
        if self.provisioning:
            # R1 joint provisioning: pin when the store can hold the state,
            # otherwise fall back to replay; reject only if neither fits.
            budget = self.gpu.state_store_mb if self.gpu.state_store_mb else float("inf")
            pin_ok = set()
            limit = 1.0 - self.safety_margin
            for i in order:
                aid = agent_ids[i]
                alt = self.alt_demands.get(aid)
                pinned_vec = normalized[i].copy()
                pinned_vec[3] = 0.0
                if alt is None:
                    if np.all(R_4d + pinned_vec <= limit):
                        admitted.add(aid)
                        R_4d += pinned_vec
                    continue
                replay_vec = alt[0].copy()
                replay_vec[:3] *= rates[i]
                replay_vec = replay_vec / C_4d
                replay_vec[3] = 0.0
                kv = alt[1]
                if kv <= budget and np.all(R_4d + pinned_vec <= limit):
                    admitted.add(aid)
                    pin_ok.add(aid)
                    R_4d += pinned_vec
                    budget -= kv
                elif np.all(R_4d + replay_vec <= limit):
                    admitted.add(aid)
                    R_4d += replay_vec
            self.pin_ok = pin_ok
        else:
            for i in order:
                if np.all(R_4d + normalized[i] <= 1.0 - self.safety_margin):
                    admitted.add(agent_ids[i])
                    R_4d += normalized[i]

        self.admitted_agents = admitted
        self.evaluated_agents = set(agent_ids)

    def _tdd_adaptation(self):
        """Decision 1: Joint radio-compute TDD adaptation.

        Key insight: Unlike standard dynamic TDD which only monitors UL PRB
        utilization, mMAS-aware TDD must also check GPU headroom.  Increasing
        UL capacity when GPU is already saturated wastes spectrum — the extra
        traffic arrives faster but queues at the GPU, yielding no end-to-end
        gain.  Conversely, when GPU is idle but UL is congested, increasing
        UL ratio is highly effective.

        Rule:
          UL congested AND GPU has headroom → increase TDD UL ratio
          UL congested AND GPU saturated   → do NOT increase (rely on admission)
          UL underloaded                   → decrease TDD UL ratio
        """
        if self.odu.effective_ul_bps <= 0:
            return

        ul_util = self.odu.ul_utilization()
        self.ul_util_history.append(ul_util)
        if len(self.ul_util_history) > 5:
            self.ul_util_history = self.ul_util_history[-5:]
        avg_ul_util = np.mean(self.ul_util_history)

        # GPU utilization: fraction of slots in use
        total_gpu_slots = self.gpu.cfg.num_gpus * self.gpu.cfg.max_concurrent
        gpu_util = self.gpu.current_batch_size / total_gpu_slots

        current_ratio = self.odu.tdd_ul_ratio

        # Downlink-pressure step-down: measured DL utilization above its
        # ceiling returns the uplink share toward baseline, so an earlier
        # uplink grant cannot keep hiding downlink congestion.
        if self.enable_gpu_gate and self.enable_dl_guard \
                and current_ratio > 0.20 \
                and self.odu.dl_utilization() > self.dl_util_threshold:
            self.odu.update_tdd_ratio(max(current_ratio - self.tdd_step, 0.20))
            return

        if avg_ul_util > self.tdd_hysteresis_high:
            gate_on = self.enable_gpu_gate
            self.margin_log['ul_triggers'] += 1
            gpu_ok = gpu_util < self.gate_compute_ceiling
            new_ratio = min(current_ratio + self.tdd_step, self.tdd_ratio_cap)
            # Projected DL utilization after shrinking the DL share: current
            # DL busy fraction scaled by the capacity ratio (Eq. 1 projection).
            dl_ok = True
            if gate_on and self.enable_dl_guard and new_ratio > current_ratio:
                dl_util = self.odu.dl_utilization()
                projected = dl_util * (1.0 - current_ratio) / (1.0 - new_ratio)
                dl_ok = projected < self.dl_util_threshold
            # State-margin check (final coordinate of Eq. 1): headroom is
            # exhausted when session reservations failed within the recent
            # window. Unbounded stores leave the margin trivially positive.
            state_ok = True
            if gate_on and self.enable_state_guard and \
                    self.gpu.state_store_mb:
                state_ok = self.gpu.state_rejects_since(
                    self.env.now - 2.0) == 0
            if not gate_on or (gpu_ok and dl_ok and state_ok):
                # UL congested + all projected margins positive → more UL
                # (gate disabled = plain UL-triggered dynamic TDD, KPM-style)
                self.odu.update_tdd_ratio(new_ratio)
                self.margin_log['raises'] += 1
            elif gate_on and not gpu_ok:
                self.margin_log['holds_compute'] += 1
            elif gate_on and not dl_ok:
                self.margin_log['holds_dl'] += 1
            elif gate_on:
                self.margin_log['holds_state'] += 1
            # a held target leaves admission control (Decision 2) to shed load
        elif avg_ul_util < self.tdd_hysteresis_low and current_ratio > 0.20:
            new_ratio = max(current_ratio - self.tdd_step, 0.20)
            self.odu.update_tdd_ratio(new_ratio)

    def _pipeline_budget(self):
        """Decision 3: Pipeline budget allocation (Theorem 3 weights)."""
        budgets = {}
        for aid, tier_idx in self.agent_tiers.items():
            # Pipeline weights W_k from Theorem 3
            # Simplified: allocate proportionally to expected compute
            budgets[aid] = 1.0  # equal budget by default
        self.gpu.pipeline_budgets = budgets

    def compute_headroom(self) -> bool:
        """True when smoothed accelerator utilization is below the ceiling."""
        if not self.gpu_util_ticks:
            return True
        w = self.gpu_util_ticks[-self.compute_window_ticks:]
        return float(np.mean(w)) < self.compute_util_ceiling

    def control_loop(self):
        """Main xApp control loop (100ms)."""
        while True:
            yield self.env.timeout(self.interval_s)
            t_start = self.env.now
            total_slots = self.gpu.cfg.num_gpus * self.gpu.cfg.max_concurrent
            self.gpu_util_ticks.append(self.gpu.current_batch_size / total_slots)
            if len(self.gpu_util_ticks) > 100:
                self.gpu_util_ticks = self.gpu_util_ticks[-100:]

            # Run decisions
            self._tdd_adaptation()
            if self.enable_admission:
                self._admission_control()
                self._pipeline_budget()

            # Generate E2SM-AIC Control message
            ctrl = E2SM_AIC_Control(
                timestamp=self.env.now,
                prb_mask={aid: 1.0 / max(len(self.admitted_agents), 1)
                          for aid in self.admitted_agents},
                tdd_ul_ratio=self.odu.tdd_ul_ratio,
            )
            self.metrics.e2_controls.append({
                'time': self.env.now,
                'tdd_ratio': ctrl.tdd_ul_ratio,
                'admitted': len(self.admitted_agents),
                'total': len(self.demand_vectors),
                'decision_time_ms': (self.env.now - t_start) * 1000,
            })

            # Record xApp decision
            self.metrics.xapp_decisions.append({
                'time': self.env.now,
                'tdd_ratio': self.odu.tdd_ul_ratio,
                'admitted_count': len(self.admitted_agents),
                'ul_util': self.ul_util_history[-1] if self.ul_util_history else 0,
            })


class BPR_rApp:
    """Barrel-Principle Resource Optimizer (non-RT RIC, 5s loop).

    Computes longer-horizon policies:
      - Bottleneck identification
      - Amdahl gap computation
      - A1 policy generation
    """

    def __init__(self, env: simpy.Environment, xapp: AJS_xApp,
                 metrics: MetricsCollector):
        self.env = env
        self.xapp = xapp
        self.metrics = metrics
        self.interval_s = 5.0

    def control_loop(self):
        """Main rApp control loop (5s)."""
        while True:
            yield self.env.timeout(self.interval_s)

            # Estimate delay shares from recent metrics
            recent = [m for m in self.metrics.latencies
                      if m['timestamp'] > self.env.now - self.interval_s]
            if not recent:
                continue

            # Compute per-resource delay fractions
            total_ul = sum(m.get('ul_delay_ms', 0) for m in recent)
            total_gpu = sum(m.get('gpu_queue_ms', 0) + m.get('gpu_compute_ms', 0)
                           for m in recent)
            total_core = sum(m.get('core_rtt_ms', 0) for m in recent)
            total_dl = sum(m.get('dl_delay_ms', 0) for m in recent)
            total = total_ul + total_gpu + total_core + total_dl + 1e-10

            alpha_ul = total_ul / total
            alpha_gpu = total_gpu / total
            alpha_core = total_core / total
            alpha_dl = total_dl / total

            # Amdahl gap: coordinated vs single-resource at 5× investment
            x = 5.0
            speedup_coord = x
            speedup_single = max(
                1 / (1 - alpha_ul + alpha_ul / x),
                1 / (1 - alpha_gpu + alpha_gpu / x),
                1 / (1 - alpha_core + alpha_core / x),
                1 / (1 - alpha_dl + alpha_dl / x),
            )
            amdahl_gap = speedup_coord / speedup_single if speedup_single > 0 else 1.0

            self.metrics.rapp_decisions.append({
                'time': self.env.now,
                'alpha_ul': alpha_ul,
                'alpha_gpu': alpha_gpu,
                'alpha_core': alpha_core,
                'alpha_dl': alpha_dl,
                'amdahl_gap': amdahl_gap,
            })


# ════════════════════════════════════════════════════════════
# Agent Flow Process
# ════════════════════════════════════════════════════════════

def compute_demand_vector(agent_type: AgentType, tau_in: float,
                          tau_out: float, sense_bytes: float) -> np.ndarray:
    """Compute 4D demand vector v_i = b_i + A(M_i) * tau_i."""
    tier = MODEL_TIERS[agent_type.tier_idx]
    b_ul = sense_bytes + tau_in * BETA
    b_dl = tau_out * BETA
    # R1 fix F03: the executed service price, including its floor
    g = max(stage_service_tflops(tier, tau_in, tau_out,
                                 _GPU_DEFAULTS.kv_hit_standalone),
            SERVICE_FLOOR_TFLOPS)
    c = tier.gamma * (tau_in + tau_out)
    return np.array([b_ul, b_dl, g, c])


def compute_session_demand(agent_type: AgentType, stages, sense_bytes: float,
                           persist: bool, delta_tokens: float = 50.0,
                           pinned: bool = False,
                           increments=None) -> np.ndarray:
    """4D demand of one pipeline session: totals over every stage (R1).

    Uplink counts the first-stage prompt and sensing bytes, then either the
    replayed context (stateless) or the instruction increment (AFS-C): the
    session's own increments when given, else the mean. Service uses the
    executed price per stage, floor included (R1 fix F03). State is the peak
    retained context; a pinned session holds its append-only final context
    (R1 fix F01/F04).
    """
    tier = MODEL_TIERS[agent_type.tier_idx]
    incs = (list(increments) if increments is not None
            else [delta_tokens] * (len(stages) - 1))
    b_ul = sense_bytes + stages[0][0] * BETA
    for (tau_in_k, _), inc in zip(stages[1:], incs):
        b_ul += (inc if persist else tau_in_k) * BETA
    b_dl = sum(to for _, to in stages) * BETA
    hit = _GPU_DEFAULTS.kv_hit_pipeline
    g = 0.0
    for k, (ti, to) in enumerate(stages):
        pre = incs[k - 1] if (pinned and k > 0) else None
        g += max(stage_service_tflops(tier, ti, to, hit, pre),
                 SERVICE_FLOOR_TFLOPS)
    if pinned:
        c = tier.gamma * pinned_state_tokens(stages, incs)
    else:
        c = tier.gamma * max(ti + to for ti, to in stages)
    return np.array([b_ul, b_dl, g, c])


def pipeline_cascade(agent_type: AgentType, tau_in_base: float,
                      rng: np.random.RandomState) -> List[Tuple[float, float]]:
    """Generate (tau_in, tau_out) for each pipeline stage using L(sigma)."""
    d = agent_type.pipeline_depth
    sigma = agent_type.sigma_retention

    # Generate i.i.d. output tokens per stage
    tau_outs = np.minimum(
        rng.lognormal(agent_type.tau_out_mu, agent_type.tau_out_sigma, d),
        agent_type.tau_max
    )

    stages = []
    for k in range(d):
        # tau_in_k = tau_in_base + sum_{j<k} sigma^{k-j} * tau_out_j
        accumulated = sum(sigma ** (k - j) * tau_outs[j] for j in range(k))
        tau_in_k = tau_in_base + accumulated
        stages.append((tau_in_k, tau_outs[k]))

    return stages


def agent_flow(env: simpy.Environment, agent_id: int,
               agent_type: AgentType, odu: ODU,
               gpu: EdgeGPUCluster, xapp: AJS_xApp,
               metrics: MetricsCollector, rng: np.random.RandomState,
               warmup_s: float = 10.0,
               mec_context_persist: bool = False,
               enforce_admission: bool = False,
               dl_artifact: bool = False,
               compute_aware: bool = False,
               paced: bool = False,
               delta_rng: Optional[np.random.RandomState] = None,
               pinned_kv: bool = False,
               threshold_admission: bool = False,
               provisioning: bool = False):
    """Single agent's sense-infer-act cycle.

    Args:
        mec_context_persist: If True, pipeline stages use authoritative
            MEC-side token/context persistence (Proposition 4). Stage k>1 sends only delta tokens
            over UL instead of full accumulated context. Converts pipeline
            UL from O(d²) to O(d). Compatible-prefix cache hits are a
            separate serving optimization.
        enforce_admission: If True, a request from an agent that the latest
            admission evaluation covered but excluded is blocked before the
            uplink and recorded, instead of being served.
        dl_artifact: If True, every response carries an artifact payload of
            Uniform(0.5, 2.0) MB (dl_heavy scenario).
    """
    # Mean delta instruction size (tokens) for cached pipeline stages
    DELTA_TOKENS = 50  # small instruction, not full context

    while True:
        inter_arrival = rng.exponential(1.0 / agent_type.lambda_hz)
        yield env.timeout(inter_arrival)

        cycle_start = env.now
        if env.now > warmup_s:
            metrics.generated_count += 1

        # Sample token counts
        tau_in = min(rng.lognormal(agent_type.tau_in_mu, agent_type.tau_in_sigma),
                     agent_type.tau_max)
        tau_out = min(rng.lognormal(agent_type.tau_out_mu, agent_type.tau_out_sigma),
                      agent_type.tau_max)
        sense_bytes = rng.uniform(0, 50000)

        # Compute demand vector and register with xApp. A pipeline session
        # registers the totals over all its stages (R1 accounting).
        stages = None
        increments = None
        replay_demand = None
        if agent_type.is_pipeline:
            stages = pipeline_cascade(agent_type, tau_in, rng)
            if pinned_kv and mec_context_persist:
                # R1 fix F01/F04: a pinned-capable session draws its instruction
                # increments once, from the separate stream, so admission,
                # prefill, uplink and state size use the same tokens.
                assert delta_rng is not None, "pinned arms require crn_delta"
                increments = [delta_rng.uniform(DELTA_TOKENS * 0.5,
                                                DELTA_TOKENS * 1.5)
                              for _ in stages[1:]]
            demand = compute_session_demand(agent_type, stages, sense_bytes,
                                            mec_context_persist,
                                            pinned=pinned_kv and mec_context_persist,
                                            increments=increments)
            if provisioning:
                replay_demand = compute_session_demand(agent_type, stages,
                                                       sense_bytes, False)
                if dl_artifact:
                    replay_demand = replay_demand.copy()
                    replay_demand[1] += 1.25e6 * len(stages)
                xapp.register_alt(agent_id, replay_demand, float(demand[3]))
        else:
            demand = compute_demand_vector(agent_type, tau_in, tau_out, sense_bytes)
        if dl_artifact:
            # R1: response artifacts are part of the downlink bytes the profile
            # records; register their mean size, once per response.
            n_resp = len(stages) if stages is not None else 1
            demand = demand.copy()
            demand[1] += 1.25e6 * n_resp
        xapp.register_agent(agent_id, agent_type.tier_idx, demand,
                            agent_type.lambda_hz)

        # Enforced admission (R1 fix F01): decided now, for this session, with
        # its own descriptor; joint provisioning tries pinned, then replay.
        decision, prov_reserved_mb = None, 0.0
        if enforce_admission:
            if provisioning and replay_demand is not None:
                versions = [('pinned', demand, float(demand[3])),
                            ('replay', replay_demand, 0.0)]
            elif provisioning:
                versions = [('stateless', demand, 0.0)]   # single inference
            else:
                versions = [('own', demand, 0.0)]
            decision, prov_reserved_mb = xapp.admit_session(
                agent_id, agent_type.lambda_hz, versions)
            if decision == 'block':
                if env.now > warmup_s:
                    metrics.admissions.append({
                        'time': env.now, 'agent_id': agent_id,
                        'agent_type': agent_type.name, 'blocked': True,
                        'sla_ms': agent_type.latency_sla_ms})
                continue

        # R1 baseline: compute-threshold admission, no profile
        if threshold_admission and not xapp.compute_headroom():
            if env.now > warmup_s:
                metrics.admissions.append({
                    'time': env.now, 'agent_id': agent_id,
                    'agent_type': agent_type.name, 'blocked': True,
                    'sla_ms': agent_type.latency_sla_ms})
            continue

        if agent_type.is_pipeline:
            # Pipeline topology: cascading stages (drawn above)
            total_ul_delay = 0
            total_gpu_queue = 0
            total_gpu_compute = 0
            total_dl_delay = 0
            total_ul_bytes = 0

            # Finite state store: reserve the cycle's peak retained context at
            # session start; on failure the session falls back to stateless.
            use_cached = mec_context_persist
            state_reserved_mb = 0.0
            total_pace = 0.0
            ca_declined = False
            pinned = False
            # R1: compute-aware eligibility. Without compute headroom the
            # session stays on stateless replay (decided once per session).
            if provisioning:
                # R1 fix F01: the admission decision fixes the version and
                # has already reserved the pinned state atomically.
                use_cached = use_cached and decision == 'pinned'
                state_reserved_mb = prov_reserved_mb
            if use_cached and compute_aware and not xapp.compute_headroom():
                use_cached = False
                ca_declined = True
            if use_cached and gpu.state_store_mb is not None and not provisioning:
                tier = MODEL_TIERS[agent_type.tier_idx]
                if pinned_kv:
                    # pinned KV: append-only final context, same safety
                    # margin as joint provisioning (R1 fix F01)
                    state_reserved_mb = tier.gamma * pinned_state_tokens(
                        stages, increments)
                    ok = gpu.try_reserve_state(
                        state_reserved_mb, 1.0 - xapp.safety_margin)
                else:
                    peak_tokens = max(ti + to for ti, to in stages)
                    state_reserved_mb = tier.gamma * peak_tokens
                    ok = gpu.try_reserve_state(state_reserved_mb)
                if not ok:
                    use_cached = False
                    state_reserved_mb = 0.0

            pinned = pinned_kv and use_cached

            for stage_k, (tau_in_k, tau_out_k) in enumerate(stages):
                # ── UL transmission ──
                if stage_k == 0:
                    # First stage: always send full context + sensing data
                    ul_bytes = sense_bytes + tau_in_k * BETA
                elif use_cached:
                    # Cached mode: send only delta instruction (Prop. 4)
                    # MEC already has authoritative context from previous stages
                    # R1: with delta_rng the increment comes from a separate
                    # stream, so the agent's main stream (arrivals, token
                    # lengths) stays identical across arms (common random
                    # numbers). delta_rng=None keeps the original behavior.
                    if increments is not None:
                        delta = increments[stage_k - 1]   # pinned arms (R1 fix)
                    else:
                        delta = (delta_rng if delta_rng is not None else rng).uniform(
                            DELTA_TOKENS * 0.5, DELTA_TOKENS * 1.5)
                    ul_bytes = delta * BETA
                else:
                    # Naive mode: retransmit full accumulated context
                    ul_bytes = tau_in_k * BETA

                total_ul_bytes += ul_bytes
                ul_start = env.now
                yield env.process(odu.transmit_ul(agent_id, ul_bytes))
                ul_delay = env.now - ul_start
                total_ul_delay += ul_delay

                # R1: stage-release pacing. With persistence on and no compute
                # headroom, the edge holds stage k>0 for the serialization
                # time that replaying the accumulated context would have
                # taken at the current uplink rate.
                if (paced and use_cached and stage_k > 0
                        and not xapp.compute_headroom()):
                    pace = max(0.0, (tau_in_k * BETA - ul_bytes)
                               / odu.effective_ul_bps)
                    total_pace += pace
                    yield env.timeout(pace)

                # Core network RTT
                yield env.timeout(odu.cfg.core_rtt_ms / 1000.0)

                # GPU inference (MEC still processes full tau_in_k regardless
                # of UL mode — context is reconstructed from KV-cache)
                gpu_result = yield env.process(
                    gpu.infer(agent_id, agent_type.tier_idx,
                              tau_in_k, tau_out_k, is_pipeline_stage=True,
                              prefill_tokens=(delta if (pinned and stage_k > 0)
                                              else None)))
                q_wait, compute = gpu_result
                total_gpu_queue += q_wait
                total_gpu_compute += compute

                # DL transmission (response)
                dl_bytes = tau_out_k * BETA
                if dl_artifact:
                    dl_bytes += rng.uniform(0.5e6, 2.0e6)
                dl_start = env.now
                yield env.process(odu.transmit_dl(dl_bytes))
                dl_delay = env.now - dl_start
                total_dl_delay += dl_delay

            if state_reserved_mb:
                gpu.release_state(state_reserved_mb)

            # Record metrics (skip warmup)
            if env.now > warmup_s:
                metrics.record_latency(agent_id, agent_type.name, env.now, {
                    'ul_delay_ms': total_ul_delay * 1000,
                    'core_rtt_ms': odu.cfg.core_rtt_ms * len(stages),
                    'gpu_queue_ms': total_gpu_queue * 1000,
                    'gpu_compute_ms': total_gpu_compute * 1000,
                    'dl_delay_ms': total_dl_delay * 1000,
                    'total_ms': (env.now - cycle_start) * 1000,
                    'pipeline_depth': len(stages),
                    'ul_bytes': total_ul_bytes,
                    'mec_cached': use_cached,
                    'ca_declined': ca_declined,
                    'pinned': pinned,
                    'pace_ms': total_pace * 1000,
                    'start': cycle_start,
                    'sla_ms': agent_type.latency_sla_ms,
                    'sla_met': (env.now - cycle_start) * 1000 <= agent_type.latency_sla_ms,
                })
        else:
            # Star topology: single inference
            # 1. UL transmission
            ul_bytes = sense_bytes + tau_in * BETA
            ul_start = env.now
            yield env.process(odu.transmit_ul(agent_id, ul_bytes))
            ul_delay = env.now - ul_start

            # 2. Core network RTT
            yield env.timeout(odu.cfg.core_rtt_ms / 1000.0)

            # 3. GPU inference
            gpu_result = yield env.process(
                gpu.infer(agent_id, agent_type.tier_idx, tau_in, tau_out))
            gpu_queue, gpu_compute = gpu_result

            # 4. DL transmission
            dl_bytes = tau_out * BETA
            if dl_artifact:
                dl_bytes += rng.uniform(0.5e6, 2.0e6)
            dl_start = env.now
            yield env.process(odu.transmit_dl(dl_bytes))
            dl_delay = env.now - dl_start

            # Record (skip warmup)
            if env.now > warmup_s:
                metrics.record_latency(agent_id, agent_type.name, env.now, {
                    'ul_delay_ms': ul_delay * 1000,
                    'core_rtt_ms': odu.cfg.core_rtt_ms,
                    'gpu_queue_ms': gpu_queue * 1000,
                    'gpu_compute_ms': gpu_compute * 1000,
                    'dl_delay_ms': dl_delay * 1000,
                    'total_ms': (env.now - cycle_start) * 1000,
                    'pipeline_depth': 1,
                    'start': cycle_start,
                    'sla_ms': agent_type.latency_sla_ms,
                    'sla_met': (env.now - cycle_start) * 1000 <= agent_type.latency_sla_ms,
                })


# ════════════════════════════════════════════════════════════
# GPU / UL Utilization Monitors
# ════════════════════════════════════════════════════════════

def gpu_monitor(env: simpy.Environment, gpu: EdgeGPUCluster,
                metrics: MetricsCollector, interval: float = 0.5):
    """Periodically record GPU utilization."""
    total_slots = gpu.cfg.num_gpus * gpu.cfg.max_concurrent
    while True:
        yield env.timeout(interval)
        util = gpu.current_batch_size / total_slots
        metrics.gpu_utilization.append((env.now, util))


def ul_monitor(env: simpy.Environment, odu: ODU,
               metrics: MetricsCollector, interval: float = 0.5):
    """Periodically record UL utilization."""
    while True:
        yield env.timeout(interval)
        cap = odu.effective_ul_bps
        util = odu.ul_utilization()
        metrics.ul_utilization.append((env.now, util))


# ════════════════════════════════════════════════════════════
# Run Single Simulation
# ════════════════════════════════════════════════════════════

def run_simulation(N: int, seed: int, duration: float = 60.0,
                   warmup: float = 10.0,
                   use_oran: bool = True,
                   mode: str = None,
                   scenario: str = 'default',
                   state_store_mb: Optional[float] = None,
                   ca_window_ticks: Optional[int] = None,
                   gate_overrides: Optional[Dict] = None,
                   crn_delta: bool = False) -> Dict:
    """Run one simulation instance.

    Args:
        N: Number of agents
        seed: Random seed
        duration: Simulation duration (seconds)
        warmup: Warmup period (seconds)
        use_oran: If True, enable xApp/rApp; if False, use baseline
        mode: Ablation arm overriding use_oran when given:
            'baseline'  — no control loops, no persistence
            'tdd'       — UL-triggered dynamic TDD only (KPM-style, no GPU gate)
            'tdd_joint' — joint radio-compute TDD gate + advisory admission
            'tdd_joint_enforced' — tdd_joint with admission enforced on the
                data path (excluded agents' requests are blocked)
            'full'      — gate + advisory admission + AFS-C context persistence
            'joint'     — the four-margin gate: uplink trigger with projected
                downlink, compute, and state margins evaluated jointly
            'joint_full' — joint gate + AFS-C context persistence
            'persist_only' — no control loops at all; AFS-C persistence only
                (isolates the state path against the plain baseline)
        scenario: 'default', or 'dl_heavy' (agents with index mod 10 < 3
            attach a Uniform(0.5, 2.0) MB artifact to every response)
        state_store_mb: finite application-state store capacity; None = no cap
    """
    if mode is None:
        mode = 'full' if use_oran else 'baseline'
    # R1 arms: 'full_ca' (compute-aware AFS-C eligibility), 'full_paced'
    # (stage-release pacing), and their '_enforced' variants plus
    # 'full_enforced' (persistence with enforced admission).
    run_loops = mode not in ('baseline', 'persist_only')
    persist = mode in ('full', 'joint_full', 'persist_only', 'full_ca',
                       'full_paced', 'full_enforced', 'full_ca_enforced',
                       'full_pinned', 'full_pinned_enforced', 'joint_full_pinned',
                       'joint_full_pinned_enforced', 'full_thr', 'full_pinned_thr',
                       'joint_full_pinned_thr', 'joint_full_pinned_prov')
    pinned_kv = 'pinned' in mode
    provisioning = mode == 'joint_full_pinned_prov'
    threshold_admission = mode in ('thr_enforced', 'full_thr', 'full_pinned_thr',
                                   'joint_full_pinned_thr')
    enforce = mode in ('tdd_joint_enforced', 'full_enforced',
                       'full_ca_enforced', 'full_pinned_enforced',
                       'joint_full_pinned_enforced', 'joint_full_pinned_prov')
    compute_aware = mode in ('full_ca', 'full_ca_enforced')
    paced = mode == 'full_paced'

    rng = np.random.RandomState(seed)
    env = simpy.Environment()
    metrics = MetricsCollector()

    # Instantiate components
    net_cfg = NetworkConfig()
    gpu_cfg = GPUConfig()

    odu = ODU(env, net_cfg, metrics)
    gpu = EdgeGPUCluster(env, gpu_cfg, metrics)
    gpu.state_store_mb = state_store_mb
    xapp = AJS_xApp(env, odu, gpu, metrics, net_cfg)
    xapp.enable_gpu_gate = mode in ('tdd_joint', 'tdd_joint_enforced', 'full',
                                    'joint', 'joint_full', 'full_ca',
                                    'full_paced', 'full_enforced',
                                    'full_ca_enforced', 'full_pinned',
                                    'full_pinned_enforced', 'joint_full_pinned',
                                    'joint_full_pinned_enforced', 'thr_enforced',
                                    'full_thr', 'full_pinned_thr',
                                    'joint_full_pinned_thr',
                                    'joint_full_pinned_prov')
    if ca_window_ticks is not None:
        xapp.compute_window_ticks = ca_window_ticks
    for k, v in (gate_overrides or {}).items():
        assert hasattr(xapp, k), k
        setattr(xapp, k, v)
    xapp.enable_admission = xapp.enable_gpu_gate
    xapp.enable_state_guard = mode in ('joint', 'joint_full', 'joint_full_pinned',
                                       'joint_full_pinned_enforced',
                                       'joint_full_pinned_thr',
                                       'joint_full_pinned_prov')
    xapp.provisioning = provisioning
    rapp = BPR_rApp(env, xapp, metrics)

    # Start O-RAN control loops
    if run_loops:
        env.process(xapp.control_loop())
        env.process(rapp.control_loop())

    # Start monitors
    env.process(gpu_monitor(env, gpu, metrics))
    env.process(ul_monitor(env, odu, metrics))

    # Create agent flows
    agent_types = create_agent_types()
    for i in range(N):
        atype = agent_types[i % len(agent_types)]
        agent_seed = rng.randint(0, 2**31)
        agent_rng = np.random.RandomState(agent_seed)
        # Only the full arm enables MEC context persistence for pipeline agents
        mec_cached = persist and atype.is_pipeline
        env.process(agent_flow(env, i, atype, odu, gpu, xapp,
                               metrics, agent_rng, warmup,
                               mec_context_persist=mec_cached,
                               enforce_admission=enforce,
                               dl_artifact=(scenario == 'dl_heavy'
                                            and i % 10 < 3),
                               compute_aware=compute_aware,
                               paced=paced,
                               pinned_kv=pinned_kv,
                               threshold_admission=threshold_admission,
                               provisioning=provisioning,
                               delta_rng=(np.random.RandomState(
                                   (agent_seed + 0x5bd1e995) % (2**31))
                                   if crn_delta else None)))

    # Run
    env.run(until=duration + warmup)

    # Aggregate results
    latencies = metrics.latencies
    if not latencies:
        return {'seed': seed, 'N': N, 'use_oran': mode == 'full', 'mode': mode,
                'count': 0, 'mean_total_ms': 0, 'p50_ms': 0,
                'p95_ms': 0, 'p99_ms': 0, 'sla_rate': 0}

    totals = [m['total_ms'] for m in latencies]
    sla_met = [m['sla_met'] for m in latencies]

    # Latency components
    ul_delays = [m['ul_delay_ms'] for m in latencies]
    gpu_queues = [m['gpu_queue_ms'] for m in latencies]
    gpu_computes = [m['gpu_compute_ms'] for m in latencies]
    dl_delays = [m['dl_delay_ms'] for m in latencies]
    core_rtts = [m['core_rtt_ms'] for m in latencies]

    result = {
        'seed': seed,
        'N': N,
        'use_oran': mode == 'full',
        'mode': mode,
        'count': len(totals),
        'mean_total_ms': float(np.mean(totals)),
        'p50_ms': float(np.percentile(totals, 50)),
        'p95_ms': float(np.percentile(totals, 95)),
        'p99_ms': float(np.percentile(totals, 99)),
        'sla_rate': float(np.mean(sla_met)),
        # Component breakdown
        'mean_ul_ms': float(np.mean(ul_delays)),
        'mean_gpu_queue_ms': float(np.mean(gpu_queues)),
        'mean_gpu_compute_ms': float(np.mean(gpu_computes)),
        'mean_dl_ms': float(np.mean(dl_delays)),
        'mean_core_rtt_ms': float(np.mean(core_rtts)),
        # TDD final
        'final_tdd_ratio': odu.tdd_ul_ratio,
        # xApp stats
        'xapp_decisions': len(metrics.xapp_decisions),
        'rapp_decisions': len(metrics.rapp_decisions),
    }

    # Enforcement, persistence, and state-store diagnostics
    blocked = sum(1 for a in metrics.admissions if a.get('blocked'))
    met = sum(1 for m in latencies if m['sla_met'])
    result['blocked'] = blocked
    result['sla_rate_all'] = float(met / (len(latencies) + blocked)) \
        if (latencies or blocked) else 0.0
    # Post-warmup generations; unfinished = generated - completed - blocked.
    # sla_rate_gen counts blocked AND unfinished requests as misses.
    gen = metrics.generated_count
    result['generated'] = gen
    result['sla_rate_gen'] = float(met / gen) if gen else 0.0
    pipe = [m for m in latencies if m.get('pipeline_depth', 1) > 1]
    result['pipeline_cycles'] = len(pipe)
    result['pipeline_cached'] = sum(1 for m in pipe if m.get('mec_cached'))
    result['pipeline_ul_bytes'] = float(sum(m.get('ul_bytes', 0) for m in pipe))
    result['pipeline_ca_declined'] = sum(1 for m in pipe if m.get('ca_declined'))
    result['pipeline_pinned'] = sum(1 for m in pipe if m.get('pinned'))
    util_post = [u for (tt, u) in metrics.gpu_utilization if tt > warmup]
    result['mean_gpu_util'] = float(np.mean(util_post)) if util_post else 0.0
    result['mean_gpu_compute_all_ms'] = float(np.mean(gpu_computes))
    result['pipeline_pace_ms_mean'] = float(np.mean(
        [m.get('pace_ms', 0.0) for m in pipe])) if pipe else 0.0
    if pipe:
        ptot = [m['total_ms'] for m in pipe]
        result['pipeline_mean_ms'] = float(np.mean(ptot))
        result['pipeline_p95_ms'] = float(np.percentile(ptot, 95))
        result['pipeline_sla'] = float(np.mean([m['sla_met'] for m in pipe]))
    result['margin_log'] = dict(xapp.margin_log)

    # R1 fix F02: cohort attainment. The cohort is every session generated
    # after warm-up and no later than the window end. Every field above was
    # computed at the window end; the run now continues for the largest SLA
    # with arrivals still running, so each cohort session completes or has
    # exceeded every SLA. Blocked and unresolved sessions count as misses.
    end = duration + warmup
    drain = max(t.latency_sla_ms for t in create_agent_types()) / 1000.0
    env.run(until=end + drain)
    cohort = [m for m in metrics.latencies if warmup < m['start'] <= end]
    c_met = sum(1 for m in cohort if m['sla_met'])
    result['cohort_generated'] = gen
    result['cohort_blocked'] = blocked
    result['cohort_completed'] = len(cohort)
    result['cohort_met'] = c_met
    result['cohort_unresolved'] = gen - blocked - len(cohort)
    result['sla_rate_cohort'] = float(c_met / gen) if gen else 0.0
    return result


# ════════════════════════════════════════════════════════════
# Batch Runner
# ════════════════════════════════════════════════════════════

def run_sweep(agent_counts: List[int], n_seeds: int = 50,
              duration: float = 60.0, warmup: float = 10.0,
              outdir: str = 'results'):
    """Run parameter sweep across agent counts and seeds."""
    os.makedirs(outdir, exist_ok=True)
    all_results = []

    for N in agent_counts:
        print(f"\n{'='*50}")
        print(f"Agent count N = {N}")
        print(f"{'='*50}")

        for mode_name, use_oran in [('baseline', False), ('oran', True)]:
            mode_results = []
            for seed in range(n_seeds):
                t0 = time.time()
                result = run_simulation(N, seed, duration, warmup, use_oran)
                elapsed = time.time() - t0
                mode_results.append(result)

                if seed % 10 == 0:
                    print(f"  [{mode_name}] seed {seed}/{n_seeds}: "
                          f"mean={result['mean_total_ms']:.1f}ms, "
                          f"SLA={result['sla_rate']:.2%} ({elapsed:.1f}s)")

            all_results.extend(mode_results)

            # Summary for this (N, mode) combination
            means = [r['mean_total_ms'] for r in mode_results if r['count'] > 0]
            slas = [r['sla_rate'] for r in mode_results if r['count'] > 0]
            if means:
                print(f"  [{mode_name}] N={N}: "
                      f"mean_latency={np.mean(means):.1f}±{np.std(means):.1f}ms, "
                      f"SLA_rate={np.mean(slas):.2%}")

    # Save all results
    outfile = os.path.join(outdir, 'tier_b_results.json')
    with open(outfile, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {outfile}")
    return all_results


# ════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════

def run_ablation(agent_counts: List[int], n_seeds: int = 50,
                 duration: float = 60.0, warmup: float = 10.0,
                 outdir: str = 'results_ablation'):
    """Four-arm ablation: baseline / tdd / tdd_joint / full."""
    os.makedirs(outdir, exist_ok=True)
    arms = ['baseline', 'tdd', 'tdd_joint', 'full']
    all_results = []
    for N in agent_counts:
        print(f"\n{'='*50}\nAblation, N = {N}\n{'='*50}")
        for arm in arms:
            arm_results = []
            for seed in range(n_seeds):
                r = run_simulation(N, seed, duration, warmup, mode=arm)
                arm_results.append(r)
            all_results.extend(arm_results)
            means = [r['mean_total_ms'] for r in arm_results if r['count'] > 0]
            uls = [r['mean_ul_ms'] for r in arm_results if r['count'] > 0]
            slas = [r['sla_rate'] for r in arm_results if r['count'] > 0]
            tdds = [r['final_tdd_ratio'] for r in arm_results if r['count'] > 0]
            print(f"  [{arm:9s}] N={N}: total={np.mean(means):.1f}±{np.std(means):.1f}ms, "
                  f"UL={np.mean(uls):.1f}ms, SLA={np.mean(slas):.2%}, TDD={np.mean(tdds):.2f}")
    outfile = os.path.join(outdir, 'ablation_results.json')
    with open(outfile, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nAblation results saved to {outfile}")
    return all_results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Tier B: SimPy O-RAN mMAS Simulator')
    parser.add_argument('--agents', type=str, default='10,50,100',
                        help='Comma-separated agent counts')
    parser.add_argument('--seeds', type=int, default=50,
                        help='Number of independent seeds')
    parser.add_argument('--duration', type=float, default=60.0,
                        help='Simulation duration (seconds)')
    parser.add_argument('--warmup', type=float, default=10.0,
                        help='Warmup period (seconds)')
    parser.add_argument('--outdir', type=str, default='results',
                        help='Output directory')
    parser.add_argument('--ablation', action='store_true',
                        help='Run the four-arm ablation instead of the sweep')

    args = parser.parse_args()
    agent_counts = [int(x) for x in args.agents.split(',')]

    print("Tier B SimPy O-RAN mMAS Simulator")
    print(f"  Agent counts: {agent_counts}")
    print(f"  Seeds: {args.seeds}")
    print(f"  Duration: {args.duration}s + {args.warmup}s warmup")
    print(f"  Output: {args.outdir}/")

    if args.ablation:
        results = run_ablation(agent_counts, args.seeds, args.duration,
                               args.warmup, args.outdir)
    else:
        results = run_sweep(agent_counts, args.seeds, args.duration,
                            args.warmup, args.outdir)
