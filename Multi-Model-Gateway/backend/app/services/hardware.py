"""GPU/VRAM + system-RAM probe: pynvml first, `nvidia-smi` parsing as
fallback, clean degradation to CPU-only when neither works (no NVIDIA driver,
or the container has no GPU access — needs nvidia-container-toolkit + a
`gpus`/deploy.resources grant in compose). Total system RAM is read from
`/proc/meminfo` (Linux only) so fit scoring can factor RAM offload.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


def _probe_pynvml() -> list[dict] | None:
    try:
        import pynvml
        pynvml.nvmlInit()
    except Exception as e:
        logger.debug("pynvml unavailable: %r", e)
        return None
    try:
        gpus = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append({
                "index": i,
                "name": name,
                "vram_total_mb": mem.total // (1024 * 1024),
                "vram_free_mb": mem.free // (1024 * 1024),
            })
        return gpus
    except Exception as e:
        logger.debug("pynvml probe failed: %r", e)
        return None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


async def _probe_nvidia_smi() -> list[dict] | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (FileNotFoundError, asyncio.TimeoutError, OSError) as e:
        logger.debug("nvidia-smi unavailable: %r", e)
        return None
    if proc.returncode != 0:
        return None

    gpus = []
    for i, line in enumerate(stdout.decode().strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            gpus.append({
                "index": i,
                "name": parts[0],
                "vram_total_mb": int(parts[1]),
                "vram_free_mb": int(parts[2]),
            })
        except ValueError:
            continue
    return gpus or None


def _probe_ram() -> int | None:
    """Total system RAM in MB from `/proc/meminfo` (Linux only).

    Returns `None` on any failure or when the file is absent (non-Linux
    hosts) — RAM is an offload hint, never a hard requirement.
    """
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    # e.g. "MemTotal:       65872964 kB"
                    return int(line.split()[1]) // 1024
    except Exception as e:  # noqa: BLE001 — missing file / odd format → None
        logger.debug("RAM probe failed: %r", e)
    return None


async def probe_hardware() -> dict:
    gpus = _probe_pynvml()
    if gpus is None:
        gpus = await _probe_nvidia_smi()
    return {
        "gpu_available": bool(gpus),
        "gpus": gpus or [],
        "ram_total_mb": _probe_ram(),
    }
