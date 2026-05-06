# Changelog

## v0.3.0 - 2026-05-06

- Added installable Python package layout under `src/solana_grpc_bootstrap_toolkit`
- Added `pyproject.toml` metadata for setuptools-based packaging
- Added console script entrypoint: `solana-grpc-toolkit`
- Updated CI to install package and run smoke test through installed command
- Preserved backward-compatible source execution via `solana_grpc_toolkit.py`
- Added PyPI-ready metadata, license file, and publish workflow
- Added local build + `twine check` publishing guidance

## v0.2.0 - 2026-05-06

- Added provider profiles for Helius/Triton/QuickNode/custom baseline
- Added backpressure controls (`bounded_queue_size`, `drop_policy`, burst limit)
- Added security hygiene fields (TLS/mTLS, API key env indirection, redaction)
- Upgraded `doctor` with weighted scoring and prioritized remediation hints
- Added benchmark methodology controls: warmup/steady-state + p95/p99 metrics
- Added stream simulation mode with packet loss/disconnect scenarios
- Added Prometheus metrics snapshot export for doctor/bench outputs

## v0.1.0 - 2026-05-06

- Initial release of solana-grpc-bootstrap-toolkit
- Added `init` command for baseline config generation
- Added `doctor` command for config quality checks
- Added `tune` command for workload-based suggestions
- Added `bench` command for endpoint latency/drop/reconnect metrics
- Added unit tests, self-test mode, and CI workflow
