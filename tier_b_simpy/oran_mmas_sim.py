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

    def try_reserve_state(self, mb: float) -> bool:
        """Reserve persistent context state; False when headroom is exhausted."""
        if self.state_store_mb is not None and \
                self.state_used_mb + mb > self.state_store_mb:
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
              tau_out: float, is_pipeline_stage: bool = False):
        """Run inference. Yields GPU queue wait + compute time."""
        tier = MODEL_TIERS[tier_idx]

        # Compute demand
        g_compute = tier.alpha * tau_in + tier.rho * tau_out  # TFLOPs
        kv_mem = tier.gamma * (tau_in + tau_out)              # MB

        # KV-cache hit reduces effective compute
        hit_rate = self.cfg.kv_hit_pipeline if is_pipeline_stage else self.cfg.kv_hit_standalone
        effective_g = g_compute * (1.0 - hit_rate * 0.3)  # partial reduction from cache hits

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
        self.evaluated_agents: set = set()   # agents covered by the last admission run
        # Joint-gate margin bookkeeping (per control tick with UL pressure)
        self.margin_log = {'ul_triggers': 0, 'raises': 0,
                           'holds_compute': 0, 'holds_dl': 0,
                           'holds_state': 0}

        # State
        self.demand_vectors: Dict[int, np.ndarray] = {}
        self.agent_rates: Dict[int, float] = {}
        self.agent_tiers: Dict[int, int] = {}
        self.admitted_agents: set = set()

        # TDD adaptation state with hysteresis
        self.ul_util_history: List[float] = []
        self.tdd_hysteresis_high = 0.40
        self.tdd_hysteresis_low = 0.15

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
            self.gpu.kv_cache_total_mb,                  # KV-cache MB
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
            self.odu.update_tdd_ratio(max(current_ratio - 0.05, 0.20))
            return

        if avg_ul_util > self.tdd_hysteresis_high:
            gate_on = self.enable_gpu_gate
            self.margin_log['ul_triggers'] += 1
            gpu_ok = gpu_util < 0.80
            new_ratio = min(current_ratio + 0.05, 0.55)
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
            new_ratio = max(current_ratio - 0.05, 0.20)
            self.odu.update_tdd_ratio(new_ratio)

    def _pipeline_budget(self):
        """Decision 3: Pipeline budget allocation (Theorem 3 weights)."""
        budgets = {}
        for aid, tier_idx in self.agent_tiers.items():
            # Pipeline weights W_k from Theorem 3
            # Simplified: allocate proportionally to expected compute
            budgets[aid] = 1.0  # equal budget by default
        self.gpu.pipeline_budgets = budgets

    def control_loop(self):
        """Main xApp control loop (100ms)."""
        while True:
            yield self.env.timeout(self.interval_s)
            t_start = self.env.now

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
    g = tier.alpha * tau_in + tier.rho * tau_out
    c = tier.gamma * (tau_in + tau_out)
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
               dl_artifact: bool = False):
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

        # Compute demand vector and register with xApp
        demand = compute_demand_vector(agent_type, tau_in, tau_out, sense_bytes)
        xapp.register_agent(agent_id, agent_type.tier_idx, demand,
                            agent_type.lambda_hz)

        # Enforced admission: block excluded agents' requests on the data path
        if enforce_admission and agent_id in xapp.evaluated_agents \
                and agent_id not in xapp.admitted_agents:
            if env.now > warmup_s:
                metrics.admissions.append({
                    'time': env.now, 'agent_id': agent_id,
                    'agent_type': agent_type.name, 'blocked': True,
                    'sla_ms': agent_type.latency_sla_ms})
            continue

        if agent_type.is_pipeline:
            # Pipeline topology: cascading stages
            stages = pipeline_cascade(agent_type, tau_in, rng)
            total_ul_delay = 0
            total_gpu_queue = 0
            total_gpu_compute = 0
            total_dl_delay = 0
            total_ul_bytes = 0

            # Finite state store: reserve the cycle's peak retained context at
            # session start; on failure the session falls back to stateless.
            use_cached = mec_context_persist
            state_reserved_mb = 0.0
            if use_cached and gpu.state_store_mb is not None:
                tier = MODEL_TIERS[agent_type.tier_idx]
                peak_tokens = max(ti + to for ti, to in stages)
                state_reserved_mb = tier.gamma * peak_tokens
                if not gpu.try_reserve_state(state_reserved_mb):
                    use_cached = False
                    state_reserved_mb = 0.0

            for stage_k, (tau_in_k, tau_out_k) in enumerate(stages):
                # ── UL transmission ──
                if stage_k == 0:
                    # First stage: always send full context + sensing data
                    ul_bytes = sense_bytes + tau_in_k * BETA
                elif use_cached:
                    # Cached mode: send only delta instruction (Prop. 4)
                    # MEC already has authoritative context from previous stages
                    delta = rng.uniform(DELTA_TOKENS * 0.5, DELTA_TOKENS * 1.5)
                    ul_bytes = delta * BETA
                else:
                    # Naive mode: retransmit full accumulated context
                    ul_bytes = tau_in_k * BETA

                total_ul_bytes += ul_bytes
                ul_start = env.now
                yield env.process(odu.transmit_ul(agent_id, ul_bytes))
                ul_delay = env.now - ul_start
                total_ul_delay += ul_delay

                # Core network RTT
                yield env.timeout(odu.cfg.core_rtt_ms / 1000.0)

                # GPU inference (MEC still processes full tau_in_k regardless
                # of UL mode — context is reconstructed from KV-cache)
                gpu_result = yield env.process(
                    gpu.infer(agent_id, agent_type.tier_idx,
                              tau_in_k, tau_out_k, is_pipeline_stage=True))
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
                   state_store_mb: Optional[float] = None) -> Dict:
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
    run_loops = mode not in ('baseline', 'persist_only')
    persist = mode in ('full', 'joint_full', 'persist_only')
    enforce = mode == 'tdd_joint_enforced'

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
                                    'joint', 'joint_full')
    xapp.enable_admission = xapp.enable_gpu_gate
    xapp.enable_state_guard = mode in ('joint', 'joint_full')
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
                                            and i % 10 < 3)))

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
    if pipe:
        ptot = [m['total_ms'] for m in pipe]
        result['pipeline_mean_ms'] = float(np.mean(ptot))
        result['pipeline_p95_ms'] = float(np.percentile(ptot, 95))
        result['pipeline_sla'] = float(np.mean([m['sla_met'] for m in pipe]))
    result['margin_log'] = dict(xapp.margin_log)
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
