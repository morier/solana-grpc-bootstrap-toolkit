#!/usr/bin/env python3
"""solana-grpc-bootstrap-toolkit.

Opinionated toolkit for Solana gRPC operational setup with four commands:
- init: create baseline config
- doctor: evaluate config quality with weighted scoring/remediation
- tune: suggest adjustments by workload/provider profile
- bench: benchmark connectivity with warmup + percentiles + simulations

Design note:
Real gRPC streaming benchmarks are usually network-dependent. This toolkit
supports offline-friendly simulation so teams can validate logic in restricted
environments and CI, then switch to live checks when connectivity is available.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import socket
import statistics
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_CONFIG = {
    "version": 1,
    "provider": {
        "name": "custom",
        "endpoint": "grpc.mainnet.example.com:443",
        "tls": True,
        "mtls": False,
        "cert_file": None,
        "key_file": None,
        "api_key_env": "SOLANA_GRPC_API_KEY",
        "timeout_seconds": 10,
    },
    "stream": {
        "type": "transactions",
        "commitment": "confirmed",
        "filter_program_ids": [],
        "max_inflight_messages": 500,
    },
    "backpressure": {
        "bounded_queue_size": 10000,
        "drop_policy": "drop-oldest",
        "burst_limit_per_sec": 2500,
    },
    "reconnect": {
        "max_retries": 8,
        "backoff_initial_ms": 250,
        "backoff_max_ms": 8000,
        "jitter": True,
    },
    "health": {
        "keepalive_seconds": 20,
        "heartbeat_timeout_seconds": 60,
    },
    "metrics": {
        "enable": True,
        "sample_window_seconds": 60,
    },
    "observability": {
        "prometheus_enabled": True,
        "prometheus_port": 9108,
        "structured_logging": True,
        "error_taxonomy": True,
    },
    "security": {
        "redact_secrets": True,
    },
    "slo": {
        "target_drop_rate": 0.02,
        "target_p99_latency_ms": 250,
        "target_reconnect_success_rate": 0.99,
    },
}


WORKLOAD_PRESETS = {
    "light": {
        "stream.max_inflight_messages": 250,
        "reconnect.max_retries": 5,
        "health.keepalive_seconds": 30,
    },
    "standard": {
        "stream.max_inflight_messages": 500,
        "reconnect.max_retries": 8,
        "health.keepalive_seconds": 20,
    },
    "heavy": {
        "stream.max_inflight_messages": 2000,
        "reconnect.max_retries": 20,
        "health.keepalive_seconds": 10,
    },
}


PROVIDER_PROFILES = {
    "custom-default": {
        "provider.name": "custom",
        "provider.timeout_seconds": 10,
        "health.keepalive_seconds": 20,
        "stream.max_inflight_messages": 500,
    },
    "helius-low-latency": {
        "provider.name": "helius",
        "provider.timeout_seconds": 6,
        "health.keepalive_seconds": 10,
        "stream.max_inflight_messages": 1200,
        "backpressure.bounded_queue_size": 20000,
    },
    "triton-cost-optimized": {
        "provider.name": "triton",
        "provider.timeout_seconds": 12,
        "health.keepalive_seconds": 25,
        "stream.max_inflight_messages": 400,
        "backpressure.bounded_queue_size": 8000,
    },
    "quicknode-balanced": {
        "provider.name": "quicknode",
        "provider.timeout_seconds": 8,
        "health.keepalive_seconds": 15,
        "stream.max_inflight_messages": 900,
        "backpressure.bounded_queue_size": 12000,
    },
}


DROP_POLICIES = {"drop-oldest", "drop-newest", "block"}


def deep_copy_config(conf: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(conf))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def set_nested(conf: dict[str, Any], dotted_key: str, value: Any) -> None:
    keys = dotted_key.split(".")
    obj = conf
    for k in keys[:-1]:
        if k not in obj or not isinstance(obj[k], dict):
            obj[k] = {}
        obj = obj[k]
    obj[keys[-1]] = value


def get_nested(conf: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = conf
    for k in dotted_key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def apply_profile(conf: dict[str, Any], profile: str) -> None:
    for key, val in PROVIDER_PROFILES[profile].items():
        set_nested(conf, key, val)


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("secret", "token", "password", "cert", "key")):
        if isinstance(value, str) and value:
            return "***REDACTED***"
    return value


def redact_secrets(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: redact_value(k, redact_secrets(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_secrets(v) for v in data]
    return data


def init_config(path: Path, endpoint: str, workload: str, provider_profile: str) -> dict[str, Any]:
    conf = deep_copy_config(DEFAULT_CONFIG)
    conf["provider"]["endpoint"] = endpoint

    apply_profile(conf, provider_profile)

    preset = WORKLOAD_PRESETS[workload]
    for key, val in preset.items():
        set_nested(conf, key, val)

    write_json(path, conf)
    return conf


def _doctor_check(
    name: str,
    status: str,
    detail: str,
    remediation: str,
    weight: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "remediation": remediation,
        "weight": weight,
    }


def doctor_config(conf: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    endpoint = ((conf.get("provider") or {}).get("endpoint") or "").strip()
    if endpoint:
        checks.append(
            _doctor_check(
                "endpoint_present",
                "pass",
                endpoint,
                "none",
                25,
            )
        )
    else:
        checks.append(
            _doctor_check(
                "endpoint_present",
                "fail",
                "provider.endpoint missing",
                "Set provider.endpoint to a reachable host:port.",
                25,
            )
        )

    retries = int(((conf.get("reconnect") or {}).get("max_retries") or 0))
    if retries >= 5:
        checks.append(_doctor_check("retries", "pass", f"max_retries={retries}", "none", 10))
    else:
        checks.append(
            _doctor_check(
                "retries",
                "warn",
                f"max_retries too low ({retries})",
                "Increase reconnect.max_retries to >= 5 for transient outage resilience.",
                10,
            )
        )

    inflight = int(((conf.get("stream") or {}).get("max_inflight_messages") or 0))
    if inflight >= 200:
        checks.append(_doctor_check("inflight", "pass", f"max_inflight_messages={inflight}", "none", 10))
    else:
        checks.append(
            _doctor_check(
                "inflight",
                "warn",
                f"inflight likely too low ({inflight})",
                "Raise stream.max_inflight_messages for bursty programs.",
                10,
            )
        )

    keepalive = int(((conf.get("health") or {}).get("keepalive_seconds") or 0))
    if 5 <= keepalive <= 60:
        checks.append(_doctor_check("keepalive_range", "pass", f"keepalive_seconds={keepalive}", "none", 5))
    else:
        checks.append(
            _doctor_check(
                "keepalive_range",
                "warn",
                f"keepalive unusual ({keepalive})",
                "Use health.keepalive_seconds in range 5..60.",
                5,
            )
        )

    drop_policy = str(((conf.get("backpressure") or {}).get("drop_policy") or "")).strip()
    if drop_policy in DROP_POLICIES:
        checks.append(_doctor_check("drop_policy", "pass", f"drop_policy={drop_policy}", "none", 5))
    else:
        checks.append(
            _doctor_check(
                "drop_policy",
                "fail",
                f"invalid drop policy: {drop_policy}",
                "Set backpressure.drop_policy to one of drop-oldest, drop-newest, block.",
                5,
            )
        )

    queue_size = int(((conf.get("backpressure") or {}).get("bounded_queue_size") or 0))
    if queue_size >= 1000:
        checks.append(_doctor_check("queue_size", "pass", f"bounded_queue_size={queue_size}", "none", 10))
    else:
        checks.append(
            _doctor_check(
                "queue_size",
                "warn",
                f"bounded_queue_size too low ({queue_size})",
                "Increase backpressure.bounded_queue_size to >= 1000.",
                10,
            )
        )

    tls_enabled = bool(((conf.get("provider") or {}).get("tls")))
    if tls_enabled:
        checks.append(_doctor_check("tls", "pass", "TLS enabled", "none", 10))
    else:
        checks.append(_doctor_check("tls", "fail", "TLS disabled", "Enable provider.tls for production.", 10))

    api_key_env = str(((conf.get("provider") or {}).get("api_key_env") or "")).strip()
    if api_key_env:
        checks.append(_doctor_check("api_key_env", "pass", f"api_key_env={api_key_env}", "none", 5))
    else:
        checks.append(
            _doctor_check(
                "api_key_env",
                "warn",
                "API key env var not set",
                "Set provider.api_key_env and load key from environment, not config file.",
                5,
            )
        )

    prometheus_enabled = bool(((conf.get("observability") or {}).get("prometheus_enabled")))
    if prometheus_enabled:
        checks.append(_doctor_check("prometheus", "pass", "Prometheus enabled", "none", 5))
    else:
        checks.append(
            _doctor_check(
                "prometheus",
                "warn",
                "Prometheus disabled",
                "Enable observability.prometheus_enabled for operational visibility.",
                5,
            )
        )

    fail_count = sum(1 for c in checks if c["status"] == "fail")
    warn_count = sum(1 for c in checks if c["status"] == "warn")

    score = 100
    for c in checks:
        if c["status"] == "fail":
            score -= c["weight"]
        elif c["status"] == "warn":
            score -= math.ceil(c["weight"] * 0.5)
    score = max(0, score)

    overall = "healthy"
    if fail_count > 0:
        overall = "unhealthy"
    elif warn_count > 0:
        overall = "needs-attention"

    remediations = [c for c in checks if c["status"] in {"fail", "warn"}]
    remediations = sorted(remediations, key=lambda x: x["weight"], reverse=True)

    return {
        "overall": overall,
        "score": score,
        "checks": checks,
        "remediations": [
            {
                "name": r["name"],
                "status": r["status"],
                "remediation": r["remediation"],
                "weight": r["weight"],
            }
            for r in remediations
        ],
        "fail_count": fail_count,
        "warn_count": warn_count,
    }


def tune_config(conf: dict[str, Any], workload: str, provider_profile: str | None = None) -> dict[str, Any]:
    tuned = deep_copy_config(conf)
    if provider_profile:
        apply_profile(tuned, provider_profile)
    preset = WORKLOAD_PRESETS[workload]

    suggestions: list[dict[str, Any]] = []
    for key, val in preset.items():
        cur = get_nested(tuned, key)
        if cur != val:
            set_nested(tuned, key, val)
            suggestions.append({"key": key, "from": cur, "to": val})

    if provider_profile:
        for key, val in PROVIDER_PROFILES[provider_profile].items():
            cur = get_nested(conf, key)
            if cur != val:
                suggestions.append({"key": key, "from": cur, "to": val})

    return {
        "workload": workload,
        "provider_profile": provider_profile,
        "suggestions": suggestions,
        "config": tuned,
    }


def _parse_endpoint_host_port(endpoint: str) -> tuple[str, int]:
    # Accept forms: host:port, https://host:port, dns:///host:port.
    raw = endpoint.strip()
    if raw.startswith("dns:///"):
        raw = raw.replace("dns:///", "", 1)

    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        port = parsed.port or 443
        return host, port

    if ":" in raw:
        host, p = raw.rsplit(":", 1)
        return host, int(p)

    return raw, 443


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (len(s) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return s[low]
    frac = rank - low
    return s[low] + (s[high] - s[low]) * frac


def bench_endpoint(endpoint: str, samples: int, timeout_seconds: float) -> dict[str, Any]:
    host, port = _parse_endpoint_host_port(endpoint)
    if not host:
        raise ValueError("invalid endpoint host")

    latencies_ms: list[float] = []
    failures = 0
    reconnect_success = 0

    for _ in range(samples):
        start = time.perf_counter()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_seconds)
        try:
            sock.connect((host, port))
            elapsed = (time.perf_counter() - start) * 1000.0
            latencies_ms.append(elapsed)
            reconnect_success += 1
        except OSError:
            failures += 1
        finally:
            sock.close()

    success = len(latencies_ms)
    total = samples

    result = {
        "mode": "tcp",
        "endpoint": endpoint,
        "samples": samples,
        "success_count": success,
        "failure_count": failures,
        "drop_rate": round((failures / total) if total else 0.0, 4),
        "reconnect_success_rate": round((reconnect_success / total) if total else 0.0, 4),
        "latency_ms": {
            "p50": round(statistics.median(latencies_ms), 2) if latencies_ms else None,
            "p95": round(_percentile(latencies_ms, 0.95), 2) if latencies_ms else None,
            "p99": round(_percentile(latencies_ms, 0.99), 2) if latencies_ms else None,
            "avg": round(statistics.mean(latencies_ms), 2) if latencies_ms else None,
            "min": round(min(latencies_ms), 2) if latencies_ms else None,
            "max": round(max(latencies_ms), 2) if latencies_ms else None,
        },
    }
    return result


def bench_stream_sim(
    endpoint: str,
    warmup_samples: int,
    steady_samples: int,
    packet_loss_rate: float,
    disconnect_every: int,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    latencies: list[float] = []
    failures = 0
    reconnect_events = 0
    reconnect_success = 0
    dropped_messages = 0
    delivered_messages = 0

    total_samples = warmup_samples + steady_samples
    for i in range(total_samples):
        in_warmup = i < warmup_samples

        # Simulate disconnect events.
        if disconnect_every > 0 and (i + 1) % disconnect_every == 0:
            reconnect_events += 1
            if rng.random() < 0.9:
                reconnect_success += 1
            else:
                failures += 1

        # Simulate packet loss.
        if rng.random() < packet_loss_rate:
            dropped_messages += 1
            failures += 1
            continue

        # Simulate message latency.
        base = 20.0 + rng.random() * 15.0
        jitter = rng.gauss(0, 4)
        latency = max(1.0, base + jitter)
        if not in_warmup:
            latencies.append(latency)
            delivered_messages += 1

    steady_seconds = max(1.0, steady_samples / 50.0)
    throughput = delivered_messages / steady_seconds
    reconnect_success_rate = (
        reconnect_success / reconnect_events if reconnect_events > 0 else 1.0
    )
    drop_rate = dropped_messages / max(1, total_samples)

    return {
        "mode": "stream-sim",
        "endpoint": endpoint,
        "methodology": {
            "warmup_samples": warmup_samples,
            "steady_state_samples": steady_samples,
            "seed": seed,
            "packet_loss_rate": packet_loss_rate,
            "disconnect_every": disconnect_every,
        },
        "message_flow": {
            "delivered_messages": delivered_messages,
            "dropped_messages": dropped_messages,
            "throughput_msgs_per_sec": round(throughput, 2),
        },
        "reconnect": {
            "events": reconnect_events,
            "success_count": reconnect_success,
            "success_rate": round(reconnect_success_rate, 4),
        },
        "drop_rate": round(drop_rate, 4),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 2) if latencies else None,
            "p99": round(_percentile(latencies, 0.99), 2) if latencies else None,
            "avg": round(statistics.mean(latencies), 2) if latencies else None,
            "jitter_stddev": round(statistics.pstdev(latencies), 2) if len(latencies) > 1 else 0.0,
        },
    }


def metrics_to_prometheus(report: dict[str, Any]) -> str:
    lines: list[str] = []
    if "drop_rate" in report:
        lines.append(f"grpc_drop_rate {report['drop_rate']}")

    latency = report.get("latency_ms") or {}
    for key in ("p50", "p95", "p99", "avg"):
        val = latency.get(key)
        if val is not None:
            lines.append(f"grpc_latency_ms_{key} {val}")

    reconnect = report.get("reconnect") or {}
    if isinstance(reconnect, dict):
        rate = reconnect.get("success_rate")
        if rate is not None:
            lines.append(f"grpc_reconnect_success_rate {rate}")

    msg = report.get("message_flow") or {}
    if isinstance(msg, dict):
        tps = msg.get("throughput_msgs_per_sec")
        if tps is not None:
            lines.append(f"grpc_throughput_msgs_per_sec {tps}")

    return "\n".join(lines) + ("\n" if lines else "")


def print_text(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2))


def run_self_test() -> int:
    conf = init_config(
        Path("tmp_self_test_config.json"),
        "example.com:443",
        "standard",
        "custom-default",
    )
    diagnosis = doctor_config(conf)
    tune = tune_config(conf, "heavy", "helius-low-latency")
    sim = bench_stream_sim("example.com:443", 5, 20, 0.1, 7, 42)

    Path("tmp_self_test_config.json").unlink(missing_ok=True)

    if diagnosis["overall"] not in {"healthy", "needs-attention", "unhealthy"}:
        return 1
    if not tune["suggestions"]:
        return 1
    if sim["latency_ms"]["p95"] is None:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Solana gRPC bootstrap + tuning toolkit")
    sub = parser.add_subparsers(dest="cmd", required=False)

    init_p = sub.add_parser("init", help="Create baseline config")
    init_p.add_argument("--config", default="grpc_config.json", help="Config file path")
    init_p.add_argument("--endpoint", required=True, help="gRPC endpoint host:port")
    init_p.add_argument("--workload", choices=["light", "standard", "heavy"], default="standard")
    init_p.add_argument(
        "--provider-profile",
        choices=sorted(PROVIDER_PROFILES.keys()),
        default="custom-default",
        help="Provider profile template",
    )
    init_p.add_argument("--output", choices=["text", "json"], default="text")

    doctor_p = sub.add_parser("doctor", help="Evaluate config")
    doctor_p.add_argument("--config", default="grpc_config.json", help="Config file path")
    doctor_p.add_argument("--fail-on-score-below", type=int, default=0, help="Exit non-zero if score is below threshold")
    doctor_p.add_argument("--prometheus-file", default=None, help="Optional path to write Prometheus metrics snapshot")
    doctor_p.add_argument("--output", choices=["text", "json"], default="text")

    tune_p = sub.add_parser("tune", help="Suggest config tuning")
    tune_p.add_argument("--config", default="grpc_config.json", help="Config file path")
    tune_p.add_argument("--workload", choices=["light", "standard", "heavy"], required=True)
    tune_p.add_argument("--provider-profile", choices=sorted(PROVIDER_PROFILES.keys()), default=None)
    tune_p.add_argument("--apply", action="store_true", help="Apply suggestions to config file")
    tune_p.add_argument("--output", choices=["text", "json"], default="text")

    bench_p = sub.add_parser("bench", help="Benchmark endpoint connectivity")
    bench_p.add_argument("--endpoint", required=True, help="gRPC endpoint host:port")
    bench_p.add_argument("--mode", choices=["tcp", "stream-sim"], default="tcp", help="Benchmark mode")
    bench_p.add_argument("--samples", type=int, default=20, help="Connect attempts for tcp mode")
    bench_p.add_argument("--timeout-seconds", type=float, default=2.0, help="Per-attempt timeout")
    bench_p.add_argument("--warmup-samples", type=int, default=20, help="Warmup samples for stream-sim")
    bench_p.add_argument("--steady-samples", type=int, default=200, help="Steady-state samples for stream-sim")
    bench_p.add_argument("--simulate-packet-loss", type=float, default=0.0, help="Packet loss ratio for stream-sim")
    bench_p.add_argument("--simulate-disconnect-every", type=int, default=0, help="Disconnect every N samples for stream-sim")
    bench_p.add_argument("--seed", type=int, default=42, help="Random seed for stream simulation")
    bench_p.add_argument("--prometheus-file", default=None, help="Optional path to write Prometheus metrics snapshot")
    bench_p.add_argument("--output", choices=["text", "json"], default="text")

    parser.add_argument("--self-test", action="store_true", help="Run offline self-test")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    if args.cmd == "init":
        conf = init_config(Path(args.config), args.endpoint, args.workload, args.provider_profile)
        data = {
            "mode": "init",
            "config_path": args.config,
            "provider_profile": args.provider_profile,
            "config": conf,
        }
        if conf.get("security", {}).get("redact_secrets", True):
            data = redact_secrets(data)
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)
        return 0

    if args.cmd == "doctor":
        conf = read_json(Path(args.config))
        data = doctor_config(conf)
        if args.prometheus_file:
            Path(args.prometheus_file).write_text(metrics_to_prometheus(data), encoding="utf-8")

        if conf.get("security", {}).get("redact_secrets", True):
            data = redact_secrets(data)

        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)
        if args.fail_on_score_below > 0 and data["score"] < args.fail_on_score_below:
            return 1
        return 0 if data["fail_count"] == 0 else 1

    if args.cmd == "tune":
        conf = read_json(Path(args.config))
        data = tune_config(conf, args.workload, args.provider_profile)
        if args.apply:
            write_json(Path(args.config), data["config"])
            data["applied"] = True
        else:
            data["applied"] = False

        if conf.get("security", {}).get("redact_secrets", True):
            data = redact_secrets(data)
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)
        return 0

    if args.cmd == "bench":
        if args.mode == "tcp" and args.samples <= 0:
            print("Error: --samples must be > 0")
            return 2
        if args.timeout_seconds <= 0:
            print("Error: --timeout-seconds must be > 0")
            return 2

        if args.mode == "tcp":
            data = bench_endpoint(args.endpoint, args.samples, args.timeout_seconds)
        else:
            if args.warmup_samples < 0 or args.steady_samples <= 0:
                print("Error: warmup/steady samples must be valid")
                return 2
            if args.simulate_packet_loss < 0 or args.simulate_packet_loss > 1:
                print("Error: --simulate-packet-loss must be in [0,1]")
                return 2

            data = bench_stream_sim(
                endpoint=args.endpoint,
                warmup_samples=args.warmup_samples,
                steady_samples=args.steady_samples,
                packet_loss_rate=args.simulate_packet_loss,
                disconnect_every=args.simulate_disconnect_every,
                seed=args.seed,
            )

        if args.prometheus_file:
            Path(args.prometheus_file).write_text(metrics_to_prometheus(data), encoding="utf-8")

        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)

        return 0 if data["drop_rate"] < 0.25 else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
