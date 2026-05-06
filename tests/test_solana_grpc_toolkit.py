import tempfile
import unittest
from pathlib import Path

import solana_grpc_toolkit as toolkit


class SolanaGrpcToolkitTests(unittest.TestCase):
    def test_init_and_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "grpc.json"
            conf = toolkit.init_config(cfg, "localhost:443", "standard")
            self.assertTrue(cfg.exists())
            result = toolkit.doctor_config(conf)
            self.assertIn(result["overall"], {"healthy", "needs-attention"})

    def test_tune_changes(self):
        conf = toolkit.deep_copy_config(toolkit.DEFAULT_CONFIG)
        conf["stream"]["max_inflight_messages"] = 50
        tuned = toolkit.tune_config(conf, "heavy")
        self.assertTrue(tuned["suggestions"])
        self.assertEqual(tuned["config"]["stream"]["max_inflight_messages"], 2000)

    def test_parse_endpoint(self):
        self.assertEqual(toolkit._parse_endpoint_host_port("example.com:123"), ("example.com", 123))
        self.assertEqual(toolkit._parse_endpoint_host_port("https://a.b:443"), ("a.b", 443))
        self.assertEqual(toolkit._parse_endpoint_host_port("dns:///x.y:444"), ("x.y", 444))

    def test_bench_invalid_endpoint(self):
        with self.assertRaises(ValueError):
            toolkit.bench_endpoint("", 1, 0.1)

    def test_self_test(self):
        self.assertEqual(toolkit.run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
