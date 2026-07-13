#!/usr/bin/env python3
"""Discover Pod resources, render VPP configuration, and start VPP."""

import ipaddress
import os
import re
import sys
import tempfile
import time
from pathlib import Path


CPUSET_FILES = (
    Path("/sys/fs/cgroup/cpuset.cpus.effective"),
    Path("/sys/fs/cgroup/cpuset/cpuset.cpus"),
)
PCI_RE = re.compile(r"^(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
TOKEN_RE = re.compile(r"{{[A-Z0-9_]+}}")


class ConfigError(RuntimeError):
    pass


def parse_cpuset(value):
    cpus = set()
    for item in value.strip().split(","):
        if not item:
            continue
        if "-" in item:
            first, last = (int(part) for part in item.split("-", 1))
            if first > last:
                raise ConfigError(f"invalid CPU range: {item}")
            cpus.update(range(first, last + 1))
        else:
            cpus.add(int(item))
    if not cpus:
        raise ConfigError("empty cpuset")
    return sorted(cpus)


def read_allowed_cpus():
    for path in CPUSET_FILES:
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if value:
            return parse_cpuset(value), str(path)

    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("Cpus_allowed_list:"):
                return parse_cpuset(line.split(":", 1)[1]), "/proc/self/status"
    except OSError as exc:
        raise ConfigError(f"cannot read container cpuset: {exc}") from exc
    raise ConfigError("cannot find container cpuset")


def parse_cpu_limit(value):
    if not value or not value.isdigit() or int(value) < 1:
        raise ConfigError("VPP_CPU_LIMIT must be a positive integer")
    return int(value)


def wait_for_cpus(limit, interval=0.1):
    last = None
    while True:
        cpus, source = read_allowed_cpus()
        state = (tuple(cpus), source)
        if len(cpus) == limit:
            print(f"CPU allocation ready from {source}: {cpus}", flush=True)
            return cpus
        if state != last:
            print(
                f"waiting for CPU Manager: expected {limit} CPUs, "
                f"currently see {cpus} from {source}",
                flush=True,
            )
            last = state
        time.sleep(interval)


def cpu_config(cpus):
    lines = [f"    main-core {cpus[0]}"]
    if len(cpus) >= 2:
        lines.append(f"    corelist-workers {','.join(str(cpu) for cpu in cpus[1:])}")
    return "\n".join(lines)


def parse_pci(value):
    devices = [item.strip() for item in (value or "").split(",") if item.strip()]
    if len(devices) != 1:
        raise ConfigError("exactly one external_network PCI device is required")
    if not PCI_RE.fullmatch(devices[0]):
        raise ConfigError(f"invalid PCI address: {devices[0]}")
    if devices[0].count(":") == 1:
        devices[0] = "0000:" + devices[0]
    return devices[0].lower()


def parse_addresses(value, maximum=1024):
    if not value:
        raise ConfigError("VPP_INTERFACE_ADDRESSES is required")
    try:
        address_part, prefix_text = value.strip().rsplit("/", 1)
        prefix = int(prefix_text)
        if "-" in address_part:
            first_text, last_text = address_part.split("-", 1)
        else:
            first_text = last_text = address_part
        first = ipaddress.IPv4Address(first_text)
        last = ipaddress.IPv4Address(last_text)
        if int(first) > int(last):
            raise ConfigError("IP range start is greater than its end")
        network = ipaddress.IPv4Network(f"{first}/{prefix}", strict=False)
        if last not in network:
            raise ConfigError("IP range crosses the configured subnet")
    except ConfigError:
        raise
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"invalid interface address specification: {value}") from exc

    count = int(last) - int(first) + 1
    if count > maximum:
        raise ConfigError(f"IP range contains {count} addresses; limit is {maximum}")
    return [f"{ipaddress.IPv4Address(int(first) + offset)}/{prefix}" for offset in range(count)]


def validate_gateway(value, addresses):
    try:
        gateway = ipaddress.IPv4Address(value)
        network = ipaddress.IPv4Interface(addresses[0]).network
    except ValueError as exc:
        raise ConfigError(f"invalid default gateway: {value}") from exc
    if gateway not in network:
        raise ConfigError(f"default gateway {gateway} is outside {network}")
    return str(gateway)


def render_template(text, replacements, template_name):
    for token, replacement in replacements.items():
        marker = "{{" + token + "}}"
        count = text.count(marker)
        if count != 1:
            raise ConfigError(f"{template_name}: expected one {marker}, found {count}")
        text = text.replace(marker, replacement)
    unresolved = TOKEN_RE.findall(text)
    if unresolved:
        raise ConfigError(f"{template_name}: unresolved tokens: {', '.join(unresolved)}")
    return text


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def generate(template_dir, output_dir, cpus, pci, addresses, gateway):
    values = {
        "CPU_CONFIG": cpu_config(cpus),
        "PCI_ADDRESS": pci,
        "INTERFACE_ADDRESS_COMMANDS": "\n".join(
            f"set interface ip address dpdk0 {address}" for address in addresses
        ),
        "DEFAULT_GATEWAY": gateway,
    }
    templates = {
        "startup.conf.template": ("startup.conf", ("CPU_CONFIG", "PCI_ADDRESS")),
        "vcl.conf.template": ("vcl.conf", ()),
        "cli-commands.conf.template": (
            "cli-commands.conf",
            ("INTERFACE_ADDRESS_COMMANDS", "DEFAULT_GATEWAY"),
        ),
    }
    for source_name, (destination_name, tokens) in templates.items():
        source = template_dir / source_name
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"cannot read template {source}: {exc}") from exc
        rendered = render_template(text, {name: values[name] for name in tokens}, source_name)
        atomic_write(output_dir / destination_name, rendered)


def main():
    try:
        limit = parse_cpu_limit(os.environ.get("VPP_CPU_LIMIT"))
        cpus = wait_for_cpus(limit)
        pci = parse_pci(os.environ.get("PCIDEVICE_INTEL_COM_EXTERNAL_NETWORK"))
        maximum = int(os.environ.get("VPP_MAX_INTERFACE_ADDRESSES", "1024"))
        addresses = parse_addresses(os.environ.get("VPP_INTERFACE_ADDRESSES"), maximum)
        gateway = validate_gateway(os.environ.get("VPP_DEFAULT_GATEWAY", "10.2.7.254"), addresses)
        template_dir = Path(os.environ.get("VPP_TEMPLATE_DIR", "/usr/share/vpp/templates"))
        output_dir = Path(os.environ.get("VPP_CONFIG_DIR", "/run/vpp/config"))
        generate(template_dir, output_dir, cpus, pci, addresses, gateway)
        Path("/run/vpp").mkdir(parents=True, exist_ok=True)
        print(
            f"starting VPP: main CPU {cpus[0]}, "
            f"worker CPU {cpus[1] if len(cpus) > 1 else 'none'}, PCI {pci}, "
            f"addresses {addresses}",
            flush=True,
        )
        os.execvp("vpp", ("vpp", "-c", str(output_dir / "startup.conf")))
    except (ConfigError, ValueError) as exc:
        print(f"vpp-entrypoint: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
