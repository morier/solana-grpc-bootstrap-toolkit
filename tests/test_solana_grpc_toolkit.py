import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solana_grpc_bootstrap_toolkit import cli as toolkit


class SolanaGrpcToolkitTests(unittest.TestCase):
    def test_init_and_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "grpc.json"
            conf = toolkit.init_config(
                cfg,
                "localhost:443",
                "standard",
                "custom-default",
            )
            self.assertTrue(cfg.exists())
            result = toolkit.doctor_config(conf)
            self.assertIn(result["overall"], {"healthy", "needs-attention", "unhealthy"})
            self.assertIn("score", result)

    def test_tune_changes(self):
        conf = toolkit.deep_copy_config(toolkit.DEFAULT_CONFIG)
        conf["stream"]["max_inflight_messages"] = 50
        tuned = toolkit.tune_config(conf, "heavy", "helius-low-latency")
        self.assertTrue(tuned["suggestions"])
        self.assertEqual(tuned["config"]["stream"]["max_inflight_messages"], 2000)

    def test_parse_endpoint(self):
        self.assertEqual(toolkit._parse_endpoint_host_port("example.com:123"), ("example.com", 123))
        self.assertEqual(toolkit._parse_endpoint_host_port("https://a.b:443"), ("a.b", 443))
        self.assertEqual(toolkit._parse_endpoint_host_port("dns:///x.y:444"), ("x.y", 444))

    def test_bench_invalid_endpoint(self):
        with self.assertRaises(ValueError):
            toolkit.bench_endpoint("", 1, 0.1)

    def test_stream_sim_bench(self):
        out = toolkit.bench_stream_sim(
            endpoint="example.com:443",
            warmup_samples=5,
            steady_samples=50,
            packet_loss_rate=0.1,
            disconnect_every=10,
            seed=42,
        )
        self.assertEqual(out["mode"], "stream-sim")
        self.assertIn("p99", out["latency_ms"])
        self.assertIn("throughput_msgs_per_sec", out["message_flow"])

    def test_prometheus_export(self):
        out = toolkit.bench_stream_sim(
            endpoint="example.com:443",
            warmup_samples=2,
            steady_samples=20,
            packet_loss_rate=0.05,
            disconnect_every=0,
            seed=1,
        )
        text = toolkit.metrics_to_prometheus(out)
        self.assertIn("grpc_drop_rate", text)
        self.assertIn("grpc_latency_ms_p99", text)

    def test_self_test(self):
        self.assertEqual(toolkit.run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
