"""Local-only psutil measurements and an explicitly synthetic eight-GPU node."""

import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

import psutil


def utcnow():
    return datetime.now(timezone.utc)


def collect_local(asset_id="local-host", disk_path=None, interval=0.1):
    if not 0 < interval <= 5:
        raise ValueError("CPU sampling interval must be in (0, 5] seconds")
    disk_path = str(disk_path or Path.cwd().anchor)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(disk_path)
    temperatures = []
    sensor_reader = getattr(psutil, "sensors_temperatures", None)
    if sensor_reader:
        try:
            for group, readings in sensor_reader().items():
                for reading in readings:
                    if reading.current is not None and math.isfinite(reading.current):
                        temperatures.append({"sensor": f"{group}:{reading.label or 'unnamed'}",
                                             "celsius": reading.current})
        except (OSError, NotImplementedError, RuntimeError):
            pass
    return {"asset_id": asset_id, "observed_at": utcnow().isoformat(), "source": "local_psutil",
            "cpu_percent": psutil.cpu_percent(interval=interval),
            "ram_percent": memory.percent, "ram_total_gib": round(memory.total / 2**30, 2),
            "disk_percent": disk.percent, "disk_path": disk_path,
            "disk_total_gib": round(disk.total / 2**30, 2),
            "temperatures": temperatures,
            "thermal_available": bool(temperatures),
            "max_temperature_c": max((t["celsius"] for t in temperatures), default=None)}


class GPUNodeSimulator:
    """Synthetic node, not a GPU hardware driver or a remote collector."""

    def __init__(self, seed=None):
        self.random = random.Random(seed)

    def collect(self, asset_id="gpu-demo-01", scenario="normal"):
        if scenario not in {"normal", "thermal_spike", "load_spike"}:
            raise ValueError("Unknown simulation scenario")
        gpus = []
        for index in range(8):
            load = self.random.uniform(97, 100) if scenario == "load_spike" else self.random.uniform(35, 82)
            temperature = self.random.uniform(88, 98) if scenario == "thermal_spike" else 35 + 0.45 * load
            vram = self.random.uniform(77, 80) if scenario == "load_spike" else self.random.uniform(20, 66)
            gpus.append({"index": index, "utilization_percent": round(load, 2),
                         "vram_used_gib": round(vram, 2), "vram_total_gib": 80,
                         "temperature_c": round(temperature, 2),
                         "power_kw": round(0.12 + 0.0055 * load, 3)})
        gpu_power = round(sum(g["power_kw"] for g in gpus), 3)
        return {"asset_id": asset_id, "observed_at": utcnow().isoformat(), "source": "simulated",
                "scenario": scenario, "gpu_count": 8, "gpus": gpus,
                "gpu_power_kw": gpu_power, "node_power_kw": round(gpu_power + 0.45, 3),
                "rack_id": "demo-rack-a", "rack_nodes": 1,
                "rack_power_kw": round(gpu_power + 0.45, 3),
                "max_temperature_c": max(g["temperature_c"] for g in gpus)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["normal", "thermal_spike", "load_spike"], default="normal")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    print(json.dumps([collect_local(), GPUNodeSimulator(args.seed).collect(scenario=args.scenario)], indent=2))


if __name__ == "__main__":
    main()
