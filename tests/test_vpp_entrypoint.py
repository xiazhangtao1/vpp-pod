import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vpp_entrypoint", ROOT / "scripts/vpp-entrypoint.py")
ENTRYPOINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENTRYPOINT)


class EntrypointTests(unittest.TestCase):
    def test_parse_cpuset(self):
        self.assertEqual(ENTRYPOINT.parse_cpuset("4,8-10,12"), [4, 8, 9, 10, 12])

    def test_cpu_config(self):
        self.assertEqual(ENTRYPOINT.cpu_config([44]), "    main-core 44")
        self.assertEqual(
            ENTRYPOINT.cpu_config([44, 116, 117]),
            "    main-core 44\n    corelist-workers 116,117",
        )

    def test_pci_requires_exactly_one_device(self):
        self.assertEqual(ENTRYPOINT.parse_pci("a9:0b.0"), "0000:a9:0b.0")
        with self.assertRaises(ENTRYPOINT.ConfigError):
            ENTRYPOINT.parse_pci("0000:a9:0b.0,0000:a9:0c.0")

    def test_single_and_range_addresses(self):
        self.assertEqual(ENTRYPOINT.parse_addresses("10.2.0.222/20"), ["10.2.0.222/20"])
        self.assertEqual(
            ENTRYPOINT.parse_addresses("10.2.0.222-10.2.0.225/20"),
            [
                "10.2.0.222/20",
                "10.2.0.223/20",
                "10.2.0.224/20",
                "10.2.0.225/20",
            ],
        )

    def test_invalid_address_ranges(self):
        for value in ("10.2.0.225-10.2.0.222/20", "10.2.15.254-10.2.16.1/20"):
            with self.subTest(value=value), self.assertRaises(ENTRYPOINT.ConfigError):
                ENTRYPOINT.parse_addresses(value)
        with self.assertRaises(ENTRYPOINT.ConfigError):
            ENTRYPOINT.parse_addresses("10.2.0.1-10.2.0.4/20", maximum=2)

    def test_gateway_must_be_in_subnet(self):
        self.assertEqual(
            ENTRYPOINT.validate_gateway("10.2.7.254", ["10.2.0.222/20"]), "10.2.7.254"
        )
        with self.assertRaises(ENTRYPOINT.ConfigError):
            ENTRYPOINT.validate_gateway("10.3.0.1", ["10.2.0.222/20"])

    def test_gateway_may_be_empty_or_omitted(self):
        self.assertIsNone(ENTRYPOINT.validate_gateway(None, ["10.2.0.222/20"]))
        self.assertIsNone(ENTRYPOINT.validate_gateway("", ["10.2.0.222/20"]))
        self.assertIsNone(ENTRYPOINT.validate_gateway("  ", ["10.2.0.222/20"]))

    def test_generate_single_cpu_and_address_range(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            ENTRYPOINT.generate(
                ROOT / "config",
                output,
                [44],
                "0000:a9:0b.0",
                ["10.2.0.222/20", "10.2.0.223/20"],
                "10.2.7.254",
            )
            startup = (output / "startup.conf").read_text()
            cli = (output / "cli-commands.conf").read_text()
            self.assertIn("main-core 44", startup)
            self.assertNotIn("corelist-workers", startup)
            self.assertIn("dev 0000:a9:0b.0", startup)
            self.assertEqual(cli.count("set interface ip address dpdk0"), 2)
            self.assertIn("ip route add 0.0.0.0/0 via 10.2.7.254", cli)
            self.assertNotIn("{{", startup + cli)

    def test_generate_without_default_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            ENTRYPOINT.generate(
                ROOT / "config",
                output,
                [44],
                "0000:a9:0b.0",
                ["10.2.0.222/20"],
                None,
            )
            cli = (output / "cli-commands.conf").read_text()
            self.assertNotIn("ip route add 0.0.0.0/0", cli)
            self.assertNotIn("{{", cli)


if __name__ == "__main__":
    unittest.main()
