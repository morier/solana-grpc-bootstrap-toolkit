# solana-grpc-bootstrap-toolkit

[![CI](https://github.com/morier/solana-grpc-bootstrap-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/morier/solana-grpc-bootstrap-toolkit/actions/workflows/ci.yml)

Opinionated and measurable toolkit for operating Solana gRPC streams with a practical CLI.

## Why

Many teams overpay for gRPC services because they lack a tuned baseline config and objective health measurements. This toolkit makes setup repeatable and benchmarkable.

## Commands

- `init`: create baseline config for light/standard/heavy workload
- `doctor`: weighted scoring + remediation hints + optional score threshold
- `tune`: generate workload/provider-based suggestions (optionally apply)
- `bench`: benchmark with methodology controls (warmup vs steady-state)

## Enterprise-Oriented Coverage (Offline-Friendly)

- Provider-specific behavior: Helius/Triton/QuickNode profile templates
- Backpressure and memory safety: bounded queue + drop policy + burst limit
- Security hygiene: TLS/mTLS fields, API key env indirection, secret redaction
- Observability: Prometheus snapshot export + structured output + taxonomy-ready config
- SLO/SLA doctor: weighted score + prioritized remediation list
- Benchmark methodology: p50/p95/p99, warmup/steady-state, packet loss/disconnect simulation

## Install

```bash
python -m pip install .
```

Once published to PyPI:

```bash
python -m pip install solana-grpc-bootstrap-toolkit
```

Editable install during development:

```bash
python -m pip install -e .
```

(There are no third-party runtime dependencies in v0.2.0.)

## Usage

Cross-platform recommendation:

```bash
python -m solana_grpc_bootstrap_toolkit --self-test
```

If the console script is available on your PATH, you can also use:

Initialize baseline config:

```bash
solana-grpc-toolkit init \
  --config grpc_config.json \
  --endpoint grpc.mainnet.provider.com:443 \
  --workload standard \
  --provider-profile quicknode-balanced
```

Run config doctor:

```bash
solana-grpc-toolkit doctor \
  --config grpc_config.json \
  --fail-on-score-below 80 \
  --prometheus-file doctor_metrics.prom
```

Suggest tune profile:

```bash
solana-grpc-toolkit tune \
  --config grpc_config.json \
  --workload heavy \
  --provider-profile helius-low-latency
```

Apply suggestions:

```bash
solana-grpc-toolkit tune --config grpc_config.json --workload heavy --apply
```

Run benchmark:

```bash
solana-grpc-toolkit bench \
  --mode stream-sim \
  --endpoint grpc.mainnet.provider.com:443 \
  --warmup-samples 20 \
  --steady-samples 300 \
  --simulate-packet-loss 0.03 \
  --simulate-disconnect-every 75 \
  --prometheus-file bench_metrics.prom
```

Offline self-test:

```bash
solana-grpc-toolkit --self-test
```

Backward-compatible source execution still works:

```bash
python solana_grpc_toolkit.py --self-test
```

## Example Output (bench)

```json
{
  "endpoint": "grpc.mainnet.provider.com:443",
  "mode": "stream-sim",
  "drop_rate": 0.03,
  "message_flow": {
    "throughput_msgs_per_sec": 49.8
  },
  "reconnect": {
    "success_rate": 1.0
  },
  "latency_ms": {
    "p50": 32.11,
    "p95": 55.92,
    "p99": 64.73,
    "avg": 34.28
  }
}
```

## CI

GitHub Actions runs unit tests and the offline self-test on every push.

## Publishing

Build distributions locally:

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

The repository also includes a PyPI publish workflow that is designed for trusted publishing.
To activate it, configure this GitHub repository as a trusted publisher in PyPI.

## Roadmap

- Add live gRPC bench mode (currently offline simulation-first)
- Add provider SDK adapters for stream subscription checks
- Add YAML config support and policy presets
