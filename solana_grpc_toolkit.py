#!/usr/bin/env python3
"""solana-grpc-bootstrap-toolkit

Opinionated toolkit for Solana gRPC operational setup with four commands:
- init: create baseline config
- doctor: evaluate config quality
- tune: suggest adjustments by workload
- bench: run network-level connectivity benchmark
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_CONFIG = {
    "version": 1,
    "provider": {
        "endpoint": "grpc.mainnet.example.com:443",
        "tls": True,
        "timeout_seconds": 10,
    },
    "stream": {
        "type": "transactions",
        "commitment": "confirmed",
        "filter_program_ids": [],
        "max_inflight_messages": 500,
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


def init_config(path: Path, endpoint: str, workload: str) -> dict[str, Any]:
    conf = deep_copy_config(DEFAULT_CONFIG)
    conf["provider"]["endpoint"] = endpoint

    preset = WORKLOAD_PRESETS[workload]
    for key, val in preset.items():
        set_nested(conf, key, val)

    write_json(path, conf)
    return conf


def doctor_config(conf: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    endpoint = ((conf.get("provider") or {}).get("endpoint") or "").strip()
    if endpoint:
        checks.append({"name": "endpoint_present", "status": "pass", "detail": endpoint})
    else:
        checks.append({"name": "endpoint_present", "status": "fail", "detail": "provider.endpoint missing"})

    retries = int(((conf.get("reconnect") or {}).get("max_retries") or 0))
    if retries >= 5:
        checks.append({"name": "retries", "status": "pass", "detail": f"max_retries={retries}"})
    else:
        checks.append({"name": "retries", "status": "warn", "detail": f"max_retries too low ({retries})"})

    inflight = int(((conf.get("stream") or {}).get("max_inflight_messages") or 0))
    if inflight >= 200:
        checks.append({"name": "inflight", "status": "pass", "detail": f"max_inflight_messages={inflight}"})
    else:
        checks.append({"name": "inflight", "status": "warn", "detail": f"inflight likely too low ({inflight})"})

    keepalive = int(((conf.get("health") or {}).get("keepalive_seconds") or 0))
    if 5 <= keepalive <= 60:
        checks.append({"name": "keepalive_range", "status": "pass", "detail": f"keepalive_seconds={keepalive}"})
    else:
        checks.append({"name": "keepalive_range", "status": "warn", "detail": f"keepalive unusual ({keepalive})"})

    fail_count = sum(1 for c in checks if c["status"] == "fail")
    warn_count = sum(1 for c in checks if c["status"] == "warn")

    overall = "healthy"
    if fail_count > 0:
        overall = "unhealthy"
    elif warn_count > 0:
        overall = "needs-attention"

    return {
        "overall": overall,
        "checks": checks,
        "fail_count": fail_count,
        "warn_count": warn_count,
    }


def tune_config(conf: dict[str, Any], workload: str) -> dict[str, Any]:
    tuned = deep_copy_config(conf)
    preset = WORKLOAD_PRESETS[workload]

    suggestions: list[dict[str, Any]] = []
    for key, val in preset.items():
        keys = key.split(".")
        cur: Any = tuned
        for k in keys:
            cur = (cur or {}).get(k) if isinstance(cur, dict) else None
        if cur != val:
            set_nested(tuned, key, val)
            suggestions.append({"key": key, "from": cur, "to": val})

    return {
        "workload": workload,
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
        "endpoint": endpoint,
        "samples": samples,
        "success_count": success,
        "failure_count": failures,
        "drop_rate": round((failures / total) if total else 0.0, 4),
        "reconnect_success_rate": round((reconnect_success / total) if total else 0.0, 4),
        "latency_ms": {
            "p50": round(statistics.median(latencies_ms), 2) if latencies_ms else None,
            "avg": round(statistics.mean(latencies_ms), 2) if latencies_ms else None,
            "min": round(min(latencies_ms), 2) if latencies_ms else None,
            "max": round(max(latencies_ms), 2) if latencies_ms else None,
        },
    }
    return result


def print_text(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2))


def run_self_test() -> int:
    conf = init_config(Path("tmp_self_test_config.json"), "example.com:443", "standard")
    diagnosis = doctor_config(conf)
    tune = tune_config(conf, "heavy")

    Path("tmp_self_test_config.json").unlink(missing_ok=True)

    if diagnosis["overall"] not in {"healthy", "needs-attention"}:
        return 1
    if not tune["suggestions"]:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Solana gRPC bootstrap + tuning toolkit")
    sub = parser.add_subparsers(dest="cmd", required=False)

    init_p = sub.add_parser("init", help="Create baseline config")
    init_p.add_argument("--config", default="grpc_config.json", help="Config file path")
    init_p.add_argument("--endpoint", required=True, help="gRPC endpoint host:port")
    init_p.add_argument("--workload", choices=["light", "standard", "heavy"], default="standard")
    init_p.add_argument("--output", choices=["text", "json"], default="text")

    doctor_p = sub.add_parser("doctor", help="Evaluate config")
    doctor_p.add_argument("--config", default="grpc_config.json", help="Config file path")
    doctor_p.add_argument("--output", choices=["text", "json"], default="text")

    tune_p = sub.add_parser("tune", help="Suggest config tuning")
    tune_p.add_argument("--config", default="grpc_config.json", help="Config file path")
    tune_p.add_argument("--workload", choices=["light", "standard", "heavy"], required=True)
    tune_p.add_argument("--apply", action="store_true", help="Apply suggestions to config file")
    tune_p.add_argument("--output", choices=["text", "json"], default="text")

    bench_p = sub.add_parser("bench", help="Benchmark endpoint connectivity")
    bench_p.add_argument("--endpoint", required=True, help="gRPC endpoint host:port")
    bench_p.add_argument("--samples", type=int, default=20, help="Connect attempts")
    bench_p.add_argument("--timeout-seconds", type=float, default=2.0, help="Per-attempt timeout")
    bench_p.add_argument("--output", choices=["text", "json"], default="text")

    parser.add_argument("--self-test", action="store_true", help="Run offline self-test")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    if args.cmd == "init":
        conf = init_config(Path(args.config), args.endpoint, args.workload)
        data = {"mode": "init", "config_path": args.config, "config": conf}
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)
        return 0

    if args.cmd == "doctor":
        conf = read_json(Path(args.config))
        data = doctor_config(conf)
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)
        return 0 if data["fail_count"] == 0 else 1

    if args.cmd == "tune":
        conf = read_json(Path(args.config))
        data = tune_config(conf, args.workload)
        if args.apply:
            write_json(Path(args.config), data["config"])
            data["applied"] = True
        else:
            data["applied"] = False
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)
        return 0

    if args.cmd == "bench":
        if args.samples <= 0:
            print("Error: --samples must be > 0")
            return 2
        if args.timeout_seconds <= 0:
            print("Error: --timeout-seconds must be > 0")
            return 2

        data = bench_endpoint(args.endpoint, args.samples, args.timeout_seconds)
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            print_text(data)

        return 0 if data["drop_rate"] < 0.25 else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
