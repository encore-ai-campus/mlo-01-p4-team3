"""Read-only laptop 5 VMware inventory check for D-05-01; never boot or reconfigure a VM.

Reads VMX/VMSD and small VMDK descriptors plus the running VM list. Disk usage here
means host VMDK file lengths (including snapshots), not guest filesystem usage.
No addresses, accounts, UUIDs or physical VM paths are written to evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from tools.evidence_result import write_result
from tools.jsonlog import log

VM_NAMES = {"kafka-03": "pj3-kafka03", "spark-worker-02": "pj3-spark-worker02",
            "mysql-02": "pj3-mysql02", "mongo-02": "pj3-mongo02"}


def read_inventory(vmrun: Path, inventory: Path) -> dict:
    """Read running status and allocation/file-size metadata for exactly the four assigned VMs."""
    process = subprocess.run([str(vmrun), "list"], capture_output=True, text=True, check=True)
    if not process.stdout.startswith("Total running VMs:"):
        raise RuntimeError("VMware running-list query failed")
    running = {str(Path(line).resolve()).casefold() for line in process.stdout.splitlines()[1:] if line.strip()}
    registration = inventory.read_text(encoding="utf-8")
    paths = [Path(path) for path in re.findall(r'^vmlist\d+\.config\s*=\s*"([^"]+)"', registration, re.M)]
    report = {}
    for logical, registered in VM_NAMES.items():
        matches = [path for path in paths if path.stem == registered]
        if len(matches) != 1:
            raise ValueError(f"{logical}: expected one registered configuration")
        path = matches[0]
        config = path.read_text(encoding="utf-8")
        values = dict(re.findall(r'^([\w:.]+)\s*=\s*"([^"]*)"', config, re.M))
        snapshot_file = path.with_suffix(".vmsd")
        snapshot_text = snapshot_file.read_text(encoding="utf-8") if snapshot_file.exists() else ""
        snapshot_count = re.search(r'^snapshot\.numSnapshots\s*=\s*"(\d+)"', snapshot_text, re.M)
        if snapshot_count is None:
            raise ValueError(f"{logical}: snapshot count not verified")
        files = list(path.parent.glob("*.vmdk"))
        capacities = []
        for disk in files:
            if disk.stat().st_size > 65536:
                continue
            header = disk.read_bytes().decode("ascii", errors="replace")
            if header.startswith("# Disk DescriptorFile"):
                capacities.append(sum(int(n) for n in re.findall(r'^RW\s+(\d+)\s+', header, re.M)) * 512)
        if not capacities:
            raise ValueError(f"{logical}: no disk capacity descriptor")
        report[logical] = {"running": str(path.resolve()).casefold() in running,
                           "vcpu": int(values["numvcpus"]), "ram_mib": int(values["memsize"]),
                           "largest_disk_capacity_bytes": max(capacities),
                           "host_vmdk_file_bytes": sum(disk.stat().st_size for disk in files),
                           "snapshots": int(snapshot_count.group(1))}
    return report


def main(argv=None) -> int:
    """Write sanitized logical-name-only VM results and numeric evidence metrics."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vmrun", type=Path, default=Path("C:/Program Files/VMware/VMware Workstation/vmrun.exe"))
    ap.add_argument("--inventory", type=Path,
                    default=Path(os.environ.get("APPDATA", "")) / "VMware/inventory.vmls")
    args = ap.parse_args(argv)
    log("intent_vm_check", "info", "VM 점검 시작", expected_vms=4)
    report = read_inventory(args.vmrun, args.inventory)
    metrics = {"vm_checked": {"value": len(report), "unit": "대"},
               "vm_running": {"value": sum(vm["running"] for vm in report.values()), "unit": "대"}}
    for name, row in report.items():
        for key, value in row.items():
            metrics[f"{name.replace('-', '_')}_{key}"] = {"value": int(value), "unit": ""}
    result = write_result("D-05-01", "vm", metrics, "python -m tools.perf.intent_vm_check")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    log("intent_vm_check", "info", "VM 점검 완료", checked=len(report), running=metrics["vm_running"]["value"])
    log("intent_vm_check", "info", "결과 JSON 기록", result=result.name, disk_measure="host_vmdk_file_bytes")
    return 0 if all(vm["running"] for vm in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
