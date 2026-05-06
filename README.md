# solana-grpc-bootstrap-toolkit

[![CI](https://github.com/morier/solana-grpc-bootstrap-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/morier/solana-grpc-bootstrap-toolkit/actions/workflows/ci.yml)

Opinionated and measurable toolkit for operating Solana gRPC streams with a practical CLI.

## Why

Many teams overpay for gRPC services because they lack a tuned baseline config and objective health measurements. This toolkit makes setup repeatable and benchmarkable.

## Commands

- `init`: create baseline config for light/standard/heavy workload
- `doctor`: evaluate current config quality and identify weak settings
- `tune`: generate workload-based tuning suggestions (optionally apply)
- `bench`: measure connect latency, drop rate, and reconnect success rate

## Install

```bash
python -m pip install -r requirements.txt
```

(There are no third-party runtime dependencies in v0.1.0.)

## Usage

Initialize baseline config:

```bash
python solana_grpc_toolkit.py init \
  --config grpc_config.json \
  --endpoint grpc.mainnet.provider.com:443 \
  --workload standard
```

Run config doctor:

```bash
python solana_grpc_toolkit.py doctor --config grpc_config.json
```

Suggest tune profile:

```bash
python solana_grpc_toolkit.py tune --config grpc_config.json --workload heavy
```

Apply suggestions:

```bash
python solana_grpc_toolkit.py tune --config grpc_config.json --workload heavy --apply
```

Run benchmark:

```bash
python solana_grpc_toolkit.py bench \
  --endpoint grpc.mainnet.provider.com:443 \
  --samples 30 \
  --timeout-seconds 2
```

Offline self-test:

```bash
python solana_grpc_toolkit.py --self-test
```

## Example Output (bench)

```json
{
  "endpoint": "grpc.mainnet.provider.com:443",
  "samples": 20,
  "success_count": 19,
  "failure_count": 1,
  "drop_rate": 0.05,
  "reconnect_success_rate": 0.95,
  "latency_ms": {
    "p50": 32.11,
    "avg": 34.28,
    "min": 28.77,
    "max": 57.44
  }
}
```

## CI

GitHub Actions runs unit tests and the offline self-test on every push.

## Roadmap

- Add gRPC-native stream benchmark mode (message throughput and lag)
- Add provider profile templates (Helius/Triton/custom)
- Add YAML config support and policy presets
