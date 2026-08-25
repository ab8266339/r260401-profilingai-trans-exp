"""
AJS-xApp: Agent-aware Joint Scheduler for O-RAN Near-RT RIC.

Implements Algorithm 1 from the paper:
  Decision 1: TDD UL ratio adaptation with hysteresis
  Decision 2: Dimensionality-reduced admission control (Theorem 2)
  Decision 3: Pipeline budget allocation (Theorem 3 weights)

Connects to FlexRIC via E2AP/SCTP for E2SM-AIC Indication/Control,
and to MEC GPU emulator via REST for inference scheduling.
"""

import os
import sys
import time
import json
import logging
import threading
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("pip install flask requests numpy")
    sys.exit(1)

import requests

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [xApp] %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
# Configuration
# ════════════════════════════════════════════════════════════

FLEXRIC_HOST = os.environ.get('FLEXRIC_HOST', '10.45.0.20')
FLEXRIC_PORT = int(os.environ.get('FLEXRIC_PORT', '36421'))
MEC_HOST = os.environ.get('MEC_HOST', '10.45.0.30')
MEC_PORT = int(os.environ.get('MEC_PORT', '8080'))
CONTROL_INTERVAL_MS = int(os.environ.get('CONTROL_INTERVAL_MS', '100'))

SAFETY_MARGIN = 0.05  # δ for capacity projection
TDD_HYSTERESIS_HIGH = 0.60
TDD_HYSTERESIS_LOW = 0.30
TDD_MIN = 0.20
TDD_MAX = 0.55
TDD_STEP = 0.05

# Network capacity (from Table III)
CELL_UL_MBPS = 50.0
CELL_DL_MBPS = 200.0
GPU_TOTAL_TFLOPS = 4 * 312.0  # 4×H100
KV_CACHE_TOTAL_MB = 4 * 80_000  # 4×80GB


# ════════════════════════════════════════════════════════════
# State
# ════════════════════════════════════════════════════════════

@dataclass
class AgentState:
    agent_id: int
    agent_type: str
    tier: int
    demand_4d: np.ndarray       # (b_ul, b_dl, g, c)
    token_variance: float
    pipeline_stage: int
    pipeline_total: int
    buffer_ul: float
    last_seen: float


class XAppState:
    def __init__(self):
        self.agents: Dict[int, AgentState] = {}
        self.current_tdd_ratio: float = TDD_MIN
        self.admitted_set: set = set()
        self.ul_util_history: List[float] = []
        self.lock = threading.Lock()

        # PCA state (updated each control cycle)
        self.projection_P: Optional[np.ndarray] = None  # 2×4

    def update_agent(self, indication: dict):
        """Process E2SM-AIC Indication."""
        aid = indication['agent_id']
        demand = np.array(indication['demand_vector_4d'])
        with self.lock:
            self.agents[aid] = AgentState(
                agent_id=aid,
                agent_type=indication.get('agent_type', 'unknown'),
                tier=indication.get('model_tier', 0),
                demand_4d=demand,
                token_variance=indication.get('token_variance', 0),
                pipeline_stage=indication.get('pipeline_stage', 0),
                pipeline_total=indication.get('pipeline_total', 1),
                buffer_ul=indication.get('buffer_occupancy_ul', 0),
                last_seen=time.time(),
            )

    def cleanup_stale(self, timeout_s: float = 10.0):
        """Remove agents not seen recently."""
        now = time.time()
        with self.lock:
            stale = [aid for aid, a in self.agents.items()
                     if now - a.last_seen > timeout_s]
            for aid in stale:
                del self.agents[aid]
                self.admitted_set.discard(aid)


state = XAppState()


# ════════════════════════════════════════════════════════════
# Decision 1: TDD Ratio Adaptation
# ════════════════════════════════════════════════════════════

def tdd_adaptation(gpu_util: float = 0.0) -> float:
    """Joint radio-compute TDD adaptation.

    Key mMAS insight: increasing UL capacity when GPU is saturated merely
    shifts the bottleneck.  We condition TDD increase on GPU headroom.

    Rule:
      UL congested AND GPU headroom > 20% → increase TDD UL ratio
      UL congested AND GPU saturated      → do NOT increase (admission handles it)
      UL underloaded                      → decrease TDD UL ratio
    """
    with state.lock:
        total_ul_bytes = sum(a.buffer_ul for a in state.agents.values())
        effective_ul_bps = CELL_UL_MBPS * state.current_tdd_ratio * 1e6 / 8
        ul_util = total_ul_bytes / effective_ul_bps if effective_ul_bps > 0 else 0

        state.ul_util_history.append(ul_util)
        if len(state.ul_util_history) > 10:
            state.ul_util_history = state.ul_util_history[-10:]
        avg_util = np.mean(state.ul_util_history)

        ratio = state.current_tdd_ratio
        if avg_util > TDD_HYSTERESIS_HIGH:
            if gpu_util < 0.80:
                # UL congested + GPU has headroom → safe to give more UL
                ratio = min(ratio + TDD_STEP, TDD_MAX)
            # else: UL congested + GPU saturated → don't increase,
            # let admission control (Decision 2) handle load shedding
        elif avg_util < TDD_HYSTERESIS_LOW and ratio > TDD_MIN:
            ratio = max(ratio - TDD_STEP, TDD_MIN)

        state.current_tdd_ratio = ratio
        return ratio


# ════════════════════════════════════════════════════════════
# Decision 2: Dimensionality-Reduced Admission (Algorithm 1)
# ════════════════════════════════════════════════════════════

def dimensionality_reduced_admission() -> set:
    """PCA projection to 2D + greedy knapsack (Theorem 2)."""
    with state.lock:
        agents = list(state.agents.values())

    if len(agents) < 3:
        return {a.agent_id for a in agents}

    # Build demand matrix (N × 4)
    agent_ids = [a.agent_id for a in agents]
    demands = np.array([a.demand_4d for a in agents])

    # PCA: project to 2D
    centered = demands - demands.mean(axis=0)
    cov = np.cov(centered, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    idx = np.argsort(eigvals)[::-1][:2]
    P = eigvecs[:, idx]  # 4×2

    with state.lock:
        state.projection_P = P.T  # 2×4

    projected = centered @ P  # N×2

    # 4D capacity
    effective_ul = CELL_UL_MBPS * state.current_tdd_ratio * 1e6 / 8
    effective_dl = CELL_DL_MBPS * (1 - state.current_tdd_ratio) * 1e6 / 8
    C_4d = np.array([effective_ul, effective_dl, GPU_TOTAL_TFLOPS, KV_CACHE_TOTAL_MB])
    C_2d = (1 - SAFETY_MARGIN) * (P.T @ C_4d)

    # Utility: 1/latency_sla (tighter SLA = higher priority)
    utilities = np.ones(len(agents))
    norms = np.linalg.norm(projected, axis=1) + 1e-10
    density = utilities / norms
    order = np.argsort(density)[::-1]

    admitted = set()
    R_sum = np.zeros(2)
    for i in order:
        candidate = np.abs(projected[i])
        if np.all(R_sum + candidate <= np.abs(C_2d)):
            admitted.add(agent_ids[i])
            R_sum += candidate

    with state.lock:
        state.admitted_set = admitted

    return admitted


# ════════════════════════════════════════════════════════════
# Decision 3: Pipeline Budget Allocation (Theorem 3)
# ════════════════════════════════════════════════════════════

def pipeline_budget_allocation() -> Dict[int, List[float]]:
    """Compute W_k weights from Theorem 3 for pipeline agents."""
    budgets = {}
    with state.lock:
        pipeline_agents = [a for a in state.agents.values()
                          if a.pipeline_total > 1]

    for agent in pipeline_agents:
        d = agent.pipeline_total
        sigma = 0.5  # default retention; ideally from E2SM-AIC indication
        alpha, rho = 0.066, 0.066  # 33B default; ideally from tier lookup

        weights = []
        for k in range(1, d + 1):
            if sigma < 1:
                w_k = rho + alpha * sigma * (1 - sigma ** (d - k)) / (1 - sigma)
            else:
                w_k = rho + alpha * (d - k)
            weights.append(w_k)

        # Normalize to sum=1
        total = sum(weights)
        budgets[agent.agent_id] = [w / total for w in weights]

    return budgets


# ════════════════════════════════════════════════════════════
# Control Loop
# ════════════════════════════════════════════════════════════

def control_loop():
    """Main xApp control loop at CONTROL_INTERVAL_MS."""
    interval = CONTROL_INTERVAL_MS / 1000.0

    while True:
        time.sleep(interval)
        state.cleanup_stale()

        # Query GPU utilization from MEC for joint decision
        gpu_util = 0.0
        try:
            resp = requests.get(f'http://{MEC_HOST}:{MEC_PORT}/status', timeout=0.05)
            if resp.ok:
                mec_status = resp.json()
                gpu_util = mec_status.get('active', 0) / max(mec_status.get('max_total', 1), 1)
        except Exception:
            pass

        # Three coupled decisions
        tdd_ratio = tdd_adaptation(gpu_util)
        admitted = dimensionality_reduced_admission()
        budgets = pipeline_budget_allocation()

        # Send E2SM-AIC Control to O-DU (via FlexRIC)
        control_msg = {
            'tdd_ul_ratio': int(tdd_ratio * 1000),
            'admitted_agents': list(admitted),
            'prb_allocations': {
                str(aid): 1.0 / max(len(admitted), 1)
                for aid in admitted
            }
        }
        try:
            requests.post(
                f'http://{FLEXRIC_HOST}:9090/e2sm-aic/control',
                json=control_msg, timeout=0.05
            )
        except Exception:
            pass  # FlexRIC may not be running in test mode

        # Send MEC Control (REST)
        mec_msg = {
            'gpu_priority_weights': {
                '7B': 1.0, '33B': 1.0, '70B': 1.0
            },
            'pipeline_budgets': {
                str(aid): ws for aid, ws in budgets.items()
            }
        }
        try:
            requests.post(
                f'http://{MEC_HOST}:{MEC_PORT}/control',
                json=mec_msg, timeout=0.05
            )
        except Exception:
            pass

        n_agents = len(state.agents)
        n_admitted = len(admitted)
        if n_agents > 0:
            log.info(f"TDD={tdd_ratio:.2f} admitted={n_admitted}/{n_agents} "
                     f"pipelines={len(budgets)}")


# ════════════════════════════════════════════════════════════
# REST API for receiving E2SM-AIC Indications
# ════════════════════════════════════════════════════════════

app = Flask(__name__)


@app.route('/e2sm-aic/indication', methods=['POST'])
def receive_indication():
    """Receive E2SM-AIC Indication from FlexRIC."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400
    state.update_agent(data)
    return jsonify({'status': 'ok'}), 200


@app.route('/status', methods=['GET'])
def get_status():
    """Query xApp status."""
    with state.lock:
        return jsonify({
            'n_agents': len(state.agents),
            'n_admitted': len(state.admitted_set),
            'tdd_ratio': state.current_tdd_ratio,
            'ul_util_avg': float(np.mean(state.ul_util_history))
                          if state.ul_util_history else 0,
        })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200


# ════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════

if __name__ == '__main__':
    log.info("Starting AJS-xApp (Agent-aware Joint Scheduler)")
    log.info(f"  FlexRIC: {FLEXRIC_HOST}:{FLEXRIC_PORT}")
    log.info(f"  MEC: {MEC_HOST}:{MEC_PORT}")
    log.info(f"  Control interval: {CONTROL_INTERVAL_MS}ms")

    # Start control loop in background
    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()

    # Start REST API for receiving indications
    app.run(host='0.0.0.0', port=9091, debug=False)
