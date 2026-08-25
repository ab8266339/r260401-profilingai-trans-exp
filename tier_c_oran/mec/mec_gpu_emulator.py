"""
MEC GPU Emulator: Emulates edge GPU cluster with realistic inference timing.

Models:
  - Continuous batching (vLLM-style)
  - Prefill/decode phase timing per model tier
  - KV-cache memory tracking and eviction
  - Batching multiplexing coefficient μ(n)
  - REST API for xApp control and agent inference requests

Ports:
  8080: Main REST API
"""

import os
import sys
import time
import json
import logging
import threading
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from collections import deque

try:
    from flask import Flask, request, jsonify
except ImportError:
    print("pip install flask numpy")
    sys.exit(1)

import numpy as np

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [MEC] %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
# Configuration
# ════════════════════════════════════════════════════════════

NUM_GPUS = int(os.environ.get('NUM_GPUS', '4'))
TFLOPS_PER_GPU = float(os.environ.get('TFLOPS_PER_GPU', '312'))
MAX_CONCURRENT = int(os.environ.get('MAX_CONCURRENT', '64'))
KV_CACHE_PER_GPU_MB = 80_000  # 80GB per H100

MODEL_TIERS = {
    '7B':  {'alpha': 0.014, 'rho': 0.030, 'gamma': 0.1, 'weight_gb': 14},
    '33B': {'alpha': 0.066, 'rho': 0.066, 'gamma': 0.5, 'weight_gb': 66},
    '70B': {'alpha': 0.140, 'rho': 0.140, 'gamma': 1.0, 'weight_gb': 140},
}


# ════════════════════════════════════════════════════════════
# GPU State
# ══════════════════════════��════════════════════════════���════

@dataclass
class InferenceRequest:
    request_id: str
    agent_id: int
    tier: str
    tau_in: float
    tau_out: float
    is_pipeline: bool
    arrival_time: float
    start_time: float = 0.0
    finish_time: float = 0.0
    queue_wait_ms: float = 0.0
    compute_ms: float = 0.0


class GPUCluster:
    def __init__(self, num_gpus: int, tflops_per_gpu: float,
                 max_concurrent: int):
        self.num_gpus = num_gpus
        self.tflops_per_gpu = tflops_per_gpu
        self.total_tflops = num_gpus * tflops_per_gpu
        self.max_total = num_gpus * max_concurrent

        self.active_count = 0
        self.queue: deque = deque()
        self.kv_cache_used_mb = 0.0
        self.kv_cache_total_mb = num_gpus * KV_CACHE_PER_GPU_MB

        # Priority weights from xApp
        self.tier_priority: Dict[str, float] = {'7B': 1.0, '33B': 1.0, '70B': 1.0}
        self.pipeline_budgets: Dict[int, List[float]] = {}

        # Metrics
        self.completed: List[Dict] = []
        self.lock = threading.Lock()

    def batching_coefficient(self, n: int) -> float:
        """μ(n) = 1/(1 + β·ln(n)), convex decreasing, μ(1)=1."""
        if n <= 1:
            return 1.0
        beta = 3.0 / np.log(64)
        return 1.0 / (1.0 + beta * np.log(n))

    def estimate_compute_time_ms(self, tier: str, tau_in: float,
                                  tau_out: float, is_pipeline: bool) -> float:
        """Estimate compute time including batching and KV-cache effects."""
        params = MODEL_TIERS.get(tier, MODEL_TIERS['33B'])

        # Base compute demand (TFLOPs)
        g = params['alpha'] * tau_in + params['rho'] * tau_out

        # KV-cache hit reduces effective compute
        hit_rate = 0.95 if is_pipeline else 0.70
        g_effective = g * (1.0 - hit_rate * 0.3)

        # Batching coefficient
        mu = self.batching_coefficient(max(self.active_count, 1))

        # Compute time in ms
        compute_ms = g_effective / self.total_tflops * mu * 1000
        return max(compute_ms, 5.0)  # 5ms floor

    def process_request(self, req: InferenceRequest) -> Dict:
        """Process inference request (blocking emulation)."""
        req.arrival_time = time.time()

        # Queue wait (simplified: if at capacity, wait)
        with self.lock:
            while self.active_count >= self.max_total:
                pass  # In real impl, use condition variable
            self.active_count += 1
            req.start_time = time.time()
            req.queue_wait_ms = (req.start_time - req.arrival_time) * 1000

        # KV-cache allocation
        params = MODEL_TIERS.get(req.tier, MODEL_TIERS['33B'])
        kv_mem = params['gamma'] * (req.tau_in + req.tau_out)
        self.kv_cache_used_mb += kv_mem

        # Compute time emulation
        compute_ms = self.estimate_compute_time_ms(
            req.tier, req.tau_in, req.tau_out, req.is_pipeline)
        time.sleep(compute_ms / 1000.0)

        req.compute_ms = compute_ms
        req.finish_time = time.time()

        # Release
        with self.lock:
            self.active_count -= 1
            self.kv_cache_used_mb = max(0, self.kv_cache_used_mb - kv_mem)

        result = {
            'request_id': req.request_id,
            'queue_wait_ms': round(req.queue_wait_ms, 2),
            'compute_ms': round(req.compute_ms, 2),
            'total_ms': round((req.finish_time - req.arrival_time) * 1000, 2),
            'batch_size': self.active_count + 1,
            'mu': round(self.batching_coefficient(self.active_count + 1), 4),
        }

        self.completed.append(result)
        return result


cluster = GPUCluster(NUM_GPUS, TFLOPS_PER_GPU, MAX_CONCURRENT)


# ═══��════════════════════════════════════════════════════════
# REST API
# ════════════════��═══════════════════════════════════════════

app = Flask(__name__)


@app.route('/infer', methods=['POST'])
def infer():
    """Submit inference request."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    req = InferenceRequest(
        request_id=data.get('request_id', f"req-{time.time_ns()}"),
        agent_id=data.get('agent_id', 0),
        tier=data.get('tier', '33B'),
        tau_in=data.get('tau_in', 500),
        tau_out=data.get('tau_out', 200),
        is_pipeline=data.get('is_pipeline', False),
        arrival_time=time.time(),
    )

    result = cluster.process_request(req)
    return jsonify(result), 200


@app.route('/control', methods=['POST'])
def receive_control():
    """Receive control from xApp (GPU priority + pipeline budgets)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    if 'gpu_priority_weights' in data:
        cluster.tier_priority = data['gpu_priority_weights']
    if 'pipeline_budgets' in data:
        cluster.pipeline_budgets = {
            int(k): v for k, v in data['pipeline_budgets'].items()
        }

    return jsonify({'status': 'ok'}), 200


@app.route('/status', methods=['GET'])
def status():
    """Query cluster status."""
    return jsonify({
        'active': cluster.active_count,
        'max_total': cluster.max_total,
        'kv_cache_used_mb': round(cluster.kv_cache_used_mb, 1),
        'kv_cache_total_mb': cluster.kv_cache_total_mb,
        'completed_count': len(cluster.completed),
        'mu_current': round(cluster.batching_coefficient(
            max(cluster.active_count, 1)), 4),
    })


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200


@app.route('/metrics', methods=['GET'])
def metrics():
    """Return recent completion metrics."""
    recent = cluster.completed[-100:]
    if not recent:
        return jsonify({'count': 0}), 200

    queue_waits = [r['queue_wait_ms'] for r in recent]
    computes = [r['compute_ms'] for r in recent]
    totals = [r['total_ms'] for r in recent]

    return jsonify({
        'count': len(recent),
        'queue_wait_ms': {'mean': np.mean(queue_waits),
                          'p95': np.percentile(queue_waits, 95)},
        'compute_ms': {'mean': np.mean(computes),
                       'p95': np.percentile(computes, 95)},
        'total_ms': {'mean': np.mean(totals),
                     'p95': np.percentile(totals, 95)},
    })


if __name__ == '__main__':
    log.info(f"Starting MEC GPU Emulator: {NUM_GPUS}×H100 "
             f"({TFLOPS_PER_GPU} TFLOPs each)")
    log.info(f"  Max concurrent: {cluster.max_total}")
    log.info(f"  KV-cache: {cluster.kv_cache_total_mb/1000:.0f} GB")
    app.run(host='0.0.0.0', port=8080, debug=False)
