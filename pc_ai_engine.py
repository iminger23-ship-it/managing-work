
# ============================================================
# APPLICATION RESOLUTION
# ============================================================
def _find_executable(name, candidates=()):
    name=str(name).strip()
    if not name:
        return None
    found=shutil.which(name)
    if found:
        return found
    if os.name!="nt":
        return None
    roots=[os.environ.get("PROGRAMFILES",""),
           os.environ.get("PROGRAMFILES(X86)",""),
           os.environ.get("LOCALAPPDATA",""),
           os.environ.get("APPDATA","")]
    for rel in candidates:
        for root in roots:
            if root:
                p=os.path.join(root,rel)
                if os.path.isfile(p):
                    return p
                if any(ch in rel for ch in "*?["):
                    try:
                        import glob
                        matches=glob.glob(p,recursive=True)
                        for match in matches:
                            if os.path.isfile(match):
                                return match
                    except Exception:
                        pass
    return None

def _launch_app(name,candidates=()):
    path=_find_executable(name,candidates)
    try:
        if path and os.name=="nt":
            os.startfile(path); return True,path
        if path:
            subprocess.Popen([path],**hidden_subprocess_kwargs()); return True,path
        if os.name=="nt":
            os.startfile(name); return True,name
    except OSError as e:
        return False,str(e)
    return False,"Application not found"

import difflib
import ipaddress
import json
import os
import platform
import psutil
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

OPEN_ALIASES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "open_aliases.json")

# Windows subprocess stability: never allow background helpers to create console windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
STARTUPINFO = None
if os.name == "nt":
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    STARTUPINFO.wShowWindow = 0

def hidden_subprocess_kwargs():
    """Shared flags for silent, non-interactive Windows subprocesses."""
    if os.name == "nt":
        return {"creationflags": CREATE_NO_WINDOW, "startupinfo": STARTUPINFO}
    return {}

from openai import AsyncOpenAI
from agents import (
    Agent,
    Runner,
    OpenAIChatCompletionsModel,
    set_tracing_disabled,
)

# ────────────────────────────────────────────
# OLLAMA / QWEN
# ────────────────────────────────────────────

set_tracing_disabled(True)

ollama_client = AsyncOpenAI(
    base_url="http://localhost:11434/v1/",
    api_key="ollama",
)

model = OpenAIChatCompletionsModel(
    model="qwen3:8b",
    openai_client=ollama_client,
)

# ────────────────────────────────────────────
# POWERSHELL HELPER
# ────────────────────────────────────────────

def run_powershell(command: str) -> str:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            **hidden_subprocess_kwargs(),
        )

        if result.returncode == 0:
            return result.stdout.strip()

        return f"PowerShell error: {result.stderr.strip()}"

    except Exception as e:
        return f"PowerShell unavailable: {e}"


def powershell_json(command: str):
    raw = run_powershell(command)

    if not raw or raw.startswith(("PowerShell error:", "PowerShell unavailable:")):
        return None

    try:
        value = json.loads(raw)
        return value
    except (json.JSONDecodeError, TypeError):
        return None


# ────────────────────────────────────────────
# HARDWARE COLLECTION
# ────────────────────────────────────────────

def get_cpu_raw():
    command = """
    Get-CimInstance Win32_Processor |
    Select-Object Name, Manufacturer, NumberOfCores,
    NumberOfLogicalProcessors, MaxClockSpeed,
    CurrentClockSpeed, L2CacheSize, L3CacheSize,
    SocketDesignation, Architecture, AddressWidth, DataWidth |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_gpu_raw():
    """NVIDIA values come directly from nvidia-smi."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,uuid,memory.total,memory.used,memory.free,"
                "temperature.gpu,utilization.gpu,utilization.memory,"
                "power.draw,power.limit,clocks.current.graphics,"
                "clocks.current.sm,clocks.current.memory,driver_version,"
                "pcie.link.gen.current,pcie.link.gen.max,"
                "pcie.link.width.current,pcie.link.width.max",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            **hidden_subprocess_kwargs(),
        )

        if result.returncode != 0:
            return None

        output = result.stdout.strip()
        if not output:
            return None

        rows = []
        for line in output.splitlines():
            rows.append([x.strip() for x in line.split(",")])
        return rows

    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None


def get_motherboard_raw():
    command = """
    Get-CimInstance Win32_BaseBoard |
    Select-Object Manufacturer, Product, Version, SerialNumber, PartNumber |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_bios_raw():
    command = """
    Get-CimInstance Win32_BIOS |
    Select-Object Manufacturer, Name, Version, SMBIOSBIOSVersion,
    ReleaseDate, BIOSVersion |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_ram_raw():
    command = """
    Get-CimInstance Win32_PhysicalMemory |
    Select-Object Manufacturer, PartNumber, Capacity, Speed,
    ConfiguredClockSpeed, DataWidth, TotalWidth, DeviceLocator,
    BankLabel, FormFactor, SMBIOSMemoryType |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_windows_raw():
    command = """
    Get-CimInstance Win32_OperatingSystem |
    Select-Object Caption, Version, BuildNumber, OSArchitecture,
    InstallDate, LastBootUpTime, WindowsDirectory |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_system_raw():
    command = """
    Get-CimInstance Win32_ComputerSystem |
    Select-Object Manufacturer, Model, SystemType,
    TotalPhysicalMemory, NumberOfLogicalProcessors |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_physical_disks_raw():
    command = """
    Get-CimInstance Win32_DiskDrive |
    Select-Object Model, Manufacturer, InterfaceType,
    MediaType, Size, FirmwareRevision, DeviceID, Status |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


# ────────────────────────────────────────────
# CPU TEMPERATURE
# ────────────────────────────────────────────
#
# Win32_Processor / Win32_TemperatureProbe do not reliably report CPU
# temperature on modern hardware. LibreHardwareMonitor (or the older
# OpenHardwareMonitor) exposes it via a WMI namespace, but ONLY while
# their background service/app is running. If neither is installed or
# running, we report None rather than guessing — never invent a
# temperature.


def _query_hwmonitor_namespace(namespace: str):
    command = f"""
    Get-CimInstance -Namespace {namespace} -ClassName Sensor -ErrorAction Stop |
    Where-Object {{ $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU Package|CPU Total|Core \\(Tctl' }} |
    Select-Object Name, Value |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_cpu_temperature_raw():
    for namespace in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
        readings = as_list(_query_hwmonitor_namespace(namespace))
        if readings:
            return readings
    return None


def get_cpu_temperature_c():
    """Best-effort single CPU temperature reading in Celsius, or None."""
    readings = get_cpu_temperature_raw()
    if not readings:
        return None

    values = []
    for reading in readings:
        try:
            values.append(float(reading.get("Value")))
        except (TypeError, ValueError):
            continue

    if not values:
        return None

    # Prefer the package/total reading if present; otherwise average cores.
    return round(sum(values) / len(values), 1)


def temperature_status(temp_c):
    if temp_c is None:
        return None
    if temp_c >= 90:
        return "CRITICAL"
    if temp_c >= 80:
        return "HIGH"
    return "OK"


# ────────────────────────────────────────────
# SAFE FORMATTING HELPERS
# ────────────────────────────────────────────

def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"null", "unknown"} else None


def gb_from_bytes(value):
    try:
        return round(float(value) / (1024 ** 3), 2)
    except (TypeError, ValueError):
        return None


def format_gb(value):
    number = gb_from_bytes(value)
    return f"{number:.2f} GB" if number is not None else "Unavailable"


def memory_type_name(code):
    # SMBIOS memory type values used by Windows.
    names = {
        20: "DDR",
        21: "DDR2",
        22: "DDR2 FB-DIMM",
        24: "DDR3",
        26: "DDR4",
        27: "LPDDR",
        28: "LPDDR2",
        29: "LPDDR3",
        30: "LPDDR4",
        34: "DDR5",
        35: "LPDDR5",
    }
    try:
        return names.get(int(code))
    except (TypeError, ValueError):
        return None


def convert_wmi_datetime(value):
    """Convert a WMI DMTF date safely; never invent a BIOS/Windows date."""
    if not value:
        return None

    text = str(value).strip()

    # Already ISO-like.
    try:
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
    except ValueError:
        pass

    # WMI DMTF: yyyymmddHHMMSS.mmmmmmsUUU
    match = re.match(r"^(\d{14})\.(\d{6})([+-]\d{3})", text)
    if match:
        try:
            return datetime.strptime(
                match.group(1), "%Y%m%d%H%M%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    return None


# ────────────────────────────────────────────
# DETERMINISTIC PC SPECS
# ────────────────────────────────────────────

def collect_pc_specs():
    memory = psutil.virtual_memory()

    cpu = get_cpu_raw()
    if isinstance(cpu, list):
        cpu = cpu[0] if cpu else None

    motherboard = get_motherboard_raw()
    if isinstance(motherboard, list):
        motherboard = motherboard[0] if motherboard else None

    bios = get_bios_raw()
    if isinstance(bios, list):
        bios = bios[0] if bios else None

    windows = get_windows_raw()
    if isinstance(windows, list):
        windows = windows[0] if windows else None

    system = get_system_raw()
    if isinstance(system, list):
        system = system[0] if system else None

    ram = as_list(get_ram_raw())
    disks = as_list(get_physical_disks_raw())
    gpu = get_gpu_raw()

    ram_modules = []
    for module in ram:
        capacity = gb_from_bytes(module.get("Capacity"))
        speed = module.get("ConfiguredClockSpeed") or module.get("Speed")
        mem_type = memory_type_name(module.get("SMBIOSMemoryType"))

        ram_modules.append(
            {
                "manufacturer": clean(module.get("Manufacturer")),
                "part_number": clean(module.get("PartNumber")),
                "capacity_gb": capacity,
                "speed_mt_s": speed,
                "type": mem_type,
                "slot": clean(module.get("DeviceLocator")),
            }
        )

    gpu_rows = []
    gpu_fields = [
        "name", "uuid", "memory_total_mb", "memory_used_mb", "memory_free_mb",
        "temperature_c", "gpu_utilization_percent", "memory_utilization_percent",
        "power_draw_w", "power_limit_w", "graphics_clock_mhz", "sm_clock_mhz",
        "memory_clock_mhz", "driver_version", "pcie_gen_current", "pcie_gen_max",
        "pcie_width_current", "pcie_width_max",
    ]

    for row in gpu or []:
        item = {}
        for index, field in enumerate(gpu_fields):
            item[field] = clean(row[index]) if index < len(row) else None
        gpu_rows.append(item)

    disk_list = []
    for disk in disks:
        disk_list.append(
            {
                "model": clean(disk.get("Model")),
                "manufacturer": clean(disk.get("Manufacturer")),
                "interface": clean(disk.get("InterfaceType")),
                "media_type": clean(disk.get("MediaType")),
                "size_gb": gb_from_bytes(disk.get("Size")),
                "firmware": clean(disk.get("FirmwareRevision")),
                "status": clean(disk.get("Status")),
            }
        )

    return {
        "computer_name": platform.node(),
        "architecture": platform.machine(),
        "cpu": cpu,
        "gpu": gpu_rows,
        "motherboard": motherboard,
        "bios": bios,
        "ram_modules": ram_modules,
        "ram_summary": {
            "total_gb": round(memory.total / (1024 ** 3), 2),
            "used_gb": round(memory.used / (1024 ** 3), 2),
            "available_gb": round(memory.available / (1024 ** 3), 2),
            "usage_percent": memory.percent,
        },
        "windows": windows,
        "system": system,
        "physical_disks": disk_list,
    }


def format_pc_specs():
    """Human-readable hardware report generated ONLY by Python."""
    data = collect_pc_specs()

    lines = []
    lines.append("=" * 60)
    lines.append("PC HARDWARE")
    lines.append("=" * 60)

    lines.append(f"Computer: {data['computer_name']}")
    lines.append(f"Architecture: {data['architecture']}")

    cpu = data["cpu"]
    lines.append("\nCPU:")
    if cpu:
        lines.append(f"  Model: {clean(cpu.get('Name')) or 'Unavailable'}")
        lines.append(f"  Cores: {cpu.get('NumberOfCores') or 'Unavailable'}")
        lines.append(
            f"  Threads: {cpu.get('NumberOfLogicalProcessors') or 'Unavailable'}"
        )
        if cpu.get("CurrentClockSpeed"):
            lines.append(f"  Current Clock: {cpu['CurrentClockSpeed']} MHz")
        if cpu.get("MaxClockSpeed"):
            lines.append(f"  Max Clock: {cpu['MaxClockSpeed']} MHz")
    else:
        lines.append("  Unavailable")

    lines.append("\nGPU:")
    if data["gpu"]:
        for index, gpu in enumerate(data["gpu"], 1):
            lines.append(f"  GPU {index}: {gpu.get('name') or 'Unavailable'}")
            if gpu.get("memory_total_mb"):
                lines.append(f"    VRAM: {gpu['memory_total_mb']} MB")
            if gpu.get("driver_version"):
                lines.append(f"    Driver: {gpu['driver_version']}")
            if gpu.get("pcie_gen_current"):
                lines.append(
                    f"    PCIe Link: Gen {gpu['pcie_gen_current']} x"
                    f"{gpu.get('pcie_width_current') or '?'}"
                )
    else:
        lines.append("  NVIDIA GPU unavailable")

    lines.append("\nRAM:")
    summary = data["ram_summary"]
    lines.append(f"  Total: {summary['total_gb']:.2f} GB")
    lines.append(f"  Used: {summary['used_gb']:.2f} GB")
    lines.append(f"  Available: {summary['available_gb']:.2f} GB")
    lines.append(f"  Usage: {summary['usage_percent']:.1f}%")

    if data["ram_modules"]:
        lines.append(f"  Modules: {len(data['ram_modules'])}")
        for index, module in enumerate(data["ram_modules"], 1):
            capacity = format_gb(
                module["capacity_gb"] * (1024 ** 3)
                if module["capacity_gb"] is not None
                else None
            )
            speed = (
                f"{module['speed_mt_s']} MT/s"
                if module["speed_mt_s"] is not None
                else "Unavailable"
            )
            mem_type = module["type"] or "Unavailable"
            slot = module["slot"] or "Unknown slot"
            lines.append(
                f"  Module {index}: {capacity} | {mem_type} | {speed} | {slot}"
            )
    else:
        lines.append("  Modules: Unavailable")

    motherboard = data["motherboard"]
    lines.append("\nMotherboard:")
    if motherboard:
        lines.append(
            f"  Manufacturer: {clean(motherboard.get('Manufacturer')) or 'Unavailable'}"
        )
        lines.append(
            f"  Model: {clean(motherboard.get('Product')) or 'Unavailable'}"
        )
        lines.append(
            f"  Version: {clean(motherboard.get('Version')) or 'Unavailable'}"
        )
    else:
        lines.append("  Unavailable")

    bios = data["bios"]
    lines.append("\nBIOS:")
    if bios:
        lines.append(
            f"  Manufacturer: {clean(bios.get('Manufacturer')) or 'Unavailable'}"
        )
        lines.append(f"  Version: {clean(bios.get('Version')) or 'Unavailable'}")
        date = convert_wmi_datetime(bios.get("ReleaseDate"))
        lines.append(f"  Release Date: {date or 'Unavailable'}")
    else:
        lines.append("  Unavailable")

    windows = data["windows"]
    lines.append("\nWindows:")
    if windows:
        lines.append(f"  Edition: {clean(windows.get('Caption')) or 'Unavailable'}")
        lines.append(f"  Version: {clean(windows.get('Version')) or 'Unavailable'}")
        lines.append(f"  Build: {clean(windows.get('BuildNumber')) or 'Unavailable'}")
        lines.append(
            f"  Architecture: {clean(windows.get('OSArchitecture')) or 'Unavailable'}"
        )
    else:
        lines.append("  Unavailable")

    lines.append("\nPhysical Disks:")
    if data["physical_disks"]:
        for index, disk in enumerate(data["physical_disks"], 1):
            size = (
                f"{disk['size_gb']:.2f} GB"
                if disk["size_gb"] is not None
                else "Unavailable"
            )
            lines.append(
                f"  Disk {index}: {disk['model'] or 'Unavailable'} | "
                f"{size} | {disk['interface'] or 'Unknown interface'}"
            )
    else:
        lines.append("  Unavailable")

    lines.append("=" * 60)
    lines.append("")
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


# ────────────────────────────────────────────
# CURRENT PERFORMANCE
# ────────────────────────────────────────────

def get_storage_info():
    drives = []

    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)

            drives.append(
                {
                    "drive": partition.device,
                    "mount": partition.mountpoint,
                    "filesystem": partition.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 2),
                    "used_gb": round(usage.used / (1024 ** 3), 2),
                    "free_gb": round(usage.free / (1024 ** 3), 2),
                    "usage_percent": usage.percent,
                }
            )
        except (PermissionError, OSError):
            pass

    return drives


def get_network_info():
    interfaces = []
    stats = psutil.net_if_stats()
    addresses = psutil.net_if_addrs()

    for interface, addr_list in addresses.items():
        info = {
            "name": interface,
            "status": (
                "Up"
                if stats.get(interface) and stats[interface].isup
                else "Down"
            ),
            "speed_mbps": stats[interface].speed if interface in stats else None,
            "ipv4": [],
            "ipv6": [],
        }

        for address in addr_list:
            if address.family == socket.AF_INET:
                if not address.address.startswith("169.254."):
                    info["ipv4"].append(address.address)
            elif address.family == socket.AF_INET6:
                info["ipv6"].append(address.address)

        interfaces.append(info)

    return interfaces


def get_process_info():
    processes = []

    for process in psutil.process_iter(["pid", "name"]):
        try:
            process.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    time.sleep(0.5)

    for process in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            cpu = process.cpu_percent(None)
            memory = process.info["memory_percent"] or 0

            processes.append(
                {
                    "pid": process.info["pid"],
                    "name": process.info["name"],
                    "cpu_percent": round(cpu, 1),
                    "memory_percent": round(memory, 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return processes[:15]


def collect_diagnostics():
    cpu_usage = psutil.cpu_percent(interval=1)
    cpu_frequency = psutil.cpu_freq()
    cpu_temp = get_cpu_temperature_c()
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time

    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)

    return {
        "cpu_usage_percent": cpu_usage,
        "cpu_frequency_mhz": cpu_frequency.current if cpu_frequency else None,
        "cpu_temperature_c": cpu_temp,
        "cpu_temperature_status": temperature_status(cpu_temp),
        "ram": {
            "total_gb": round(memory.total / (1024 ** 3), 2),
            "used_gb": round(memory.used / (1024 ** 3), 2),
            "available_gb": round(memory.available / (1024 ** 3), 2),
            "usage_percent": memory.percent,
        },
        "swap": {
            "total_gb": round(swap.total / (1024 ** 3), 2),
            "used_gb": round(swap.used / (1024 ** 3), 2),
            "free_gb": round(swap.free / (1024 ** 3), 2),
            "usage_percent": swap.percent,
        },
        "gpu": get_gpu_raw(),
        "storage": get_storage_info(),
        "network": get_network_info(),
        "physical_disks": get_physical_disks_raw(),
        "processes": get_process_info(),
        "uptime": {
            "last_boot": time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(boot_time),
            ),
            "days": days,
            "hours": hours,
            "minutes": minutes,
        },
    }


def format_diagnostics():
    data = collect_diagnostics()

    lines = [
        "=" * 60,
        "PC PERFORMANCE — PYTHON VERIFIED",
        "=" * 60,
        f"CPU Usage: {data['cpu_usage_percent']:.1f}%",
    ]

    if data["cpu_frequency_mhz"] is not None:
        lines.append(f"CPU Frequency: {data['cpu_frequency_mhz']:.0f} MHz")

    if data["cpu_temperature_c"] is not None:
        lines.append(
            f"CPU Temperature: {data['cpu_temperature_c']:.1f} °C "
            f"[{data['cpu_temperature_status']}]"
        )
    else:
        lines.append(
            "CPU Temperature: Unavailable "
            "(requires LibreHardwareMonitor or OpenHardwareMonitor running)"
        )

    ram = data["ram"]
    lines += [
        f"RAM: {ram['used_gb']:.2f} / {ram['total_gb']:.2f} GB "
        f"({ram['usage_percent']:.1f}%)",
    ]

    if data["gpu"]:
        for index, row in enumerate(data["gpu"], 1):
            lines.append(f"\nGPU {index}:")
            if len(row) >= 1:
                lines.append(f"  Name: {row[0]}")
            if len(row) >= 6:
                lines.append(f"  Temperature: {row[5]} °C")
            if len(row) >= 7:
                lines.append(f"  GPU Usage: {row[6]} %")
            if len(row) >= 8:
                lines.append(f"  Memory Usage: {row[7]} %")
            if len(row) >= 9:
                lines.append(f"  Power Draw: {row[8]} W")
            if len(row) >= 10:
                lines.append(f"  Power Limit: {row[9]} W")

    lines.append("\nStorage:")
    for drive in data["storage"]:
        lines.append(
            f"  {drive['drive']} {drive['mount']} — "
            f"{drive['used_gb']:.2f} / {drive['total_gb']:.2f} GB "
            f"({drive['usage_percent']:.1f}%)"
        )

    up = data["uptime"]
    lines.append(
        f"\nUptime: {up['days']}d {up['hours']}h {up['minutes']}m"
    )
    lines.append(f"Last Boot: {up['last_boot']}")

    lines.append("=" * 60)
    lines.append("Qwen did not generate or modify these measurements.")
    lines.append("=" * 60)

    return "\n".join(lines)


# ────────────────────────────────────────────
# COMPONENT COMMANDS
# ────────────────────────────────────────────
# Each of these combines the static specs for one component with
# its current live reading. All numbers still come from Python only.


def format_cpu():
    cpu = get_cpu_raw()
    if isinstance(cpu, list):
        cpu = cpu[0] if cpu else None

    usage = psutil.cpu_percent(interval=1)
    freq = psutil.cpu_freq()
    temp = get_cpu_temperature_c()

    lines = ["=" * 60, "CPU", "=" * 60]
    if cpu:
        lines.append(f"Model: {clean(cpu.get('Name')) or 'Unavailable'}")
        lines.append(f"Cores: {cpu.get('NumberOfCores') or 'Unavailable'}")
        lines.append(
            f"Threads: {cpu.get('NumberOfLogicalProcessors') or 'Unavailable'}"
        )
        if cpu.get("MaxClockSpeed"):
            lines.append(f"Max Clock: {cpu['MaxClockSpeed']} MHz")
    else:
        lines.append("Static info unavailable")

    lines.append("")
    lines.append(f"Current Usage: {usage:.1f}%")
    if freq:
        lines.append(f"Current Frequency: {freq.current:.0f} MHz")
    if temp is not None:
        lines.append(f"Temperature: {temp:.1f} °C [{temperature_status(temp)}]")
    else:
        lines.append(
            "Temperature: Unavailable "
            "(requires LibreHardwareMonitor or OpenHardwareMonitor running)"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


def format_gpu():
    gpu_rows = get_gpu_raw()
    lines = ["=" * 60, "GPU", "=" * 60]

    if not gpu_rows:
        lines.append("No NVIDIA GPU detected (nvidia-smi unavailable)")
        lines.append("=" * 60)
        return "\n".join(lines)

    for index, row in enumerate(gpu_rows, 1):
        lines.append(f"GPU {index}: {row[0] if len(row) > 0 else 'Unavailable'}")
        if len(row) >= 3:
            lines.append(f"  VRAM Total: {row[2]} MB")
        if len(row) >= 4:
            lines.append(f"  VRAM Used: {row[3]} MB")
        if len(row) >= 6:
            lines.append(f"  Temperature: {row[5]} °C")
        if len(row) >= 7:
            lines.append(f"  GPU Usage: {row[6]} %")
        if len(row) >= 9:
            lines.append(f"  Power Draw: {row[8]} W")
        if len(row) >= 10:
            lines.append(f"  Power Limit: {row[9]} W")
        if len(row) >= 14:
            lines.append(f"  Driver: {row[13]}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_ram():
    memory = psutil.virtual_memory()
    ram_modules = as_list(get_ram_raw())

    lines = ["=" * 60, "RAM", "=" * 60]
    lines.append(f"Total: {memory.total / (1024 ** 3):.2f} GB")
    lines.append(f"Used: {memory.used / (1024 ** 3):.2f} GB")
    lines.append(f"Available: {memory.available / (1024 ** 3):.2f} GB")
    lines.append(f"Usage: {memory.percent:.1f}%")
    lines.append("")

    if ram_modules:
        lines.append(f"Modules ({len(ram_modules)}):")
        for index, module in enumerate(ram_modules, 1):
            capacity = format_gb(module.get("Capacity"))
            mem_type = memory_type_name(module.get("SMBIOSMemoryType")) or "Unavailable"
            speed = module.get("ConfiguredClockSpeed") or module.get("Speed") or "Unavailable"
            slot = clean(module.get("DeviceLocator")) or "Unknown slot"
            lines.append(f"  {index}. {capacity} | {mem_type} | {speed} MT/s | {slot}")
    else:
        lines.append("Module info unavailable")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_storage():
    drives = get_storage_info()
    disks = as_list(get_physical_disks_raw())

    lines = ["=" * 60, "STORAGE", "=" * 60, "Volumes:"]
    if drives:
        for drive in drives:
            lines.append(
                f"  {drive['drive']} {drive['mount']} — "
                f"{drive['used_gb']:.2f} / {drive['total_gb']:.2f} GB "
                f"({drive['usage_percent']:.1f}% used, {drive['free_gb']:.2f} GB free)"
            )
    else:
        lines.append("  Unavailable")

    lines.append("")
    lines.append("Physical Disks:")
    if disks:
        for index, disk in enumerate(disks, 1):
            size = format_gb(disk.get("Size"))
            lines.append(
                f"  Disk {index}: {clean(disk.get('Model')) or 'Unavailable'} | "
                f"{size} | {clean(disk.get('InterfaceType')) or 'Unknown interface'} | "
                f"{clean(disk.get('MediaType')) or 'Unknown media'}"
            )
    else:
        lines.append("  Unavailable")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_network():
    interfaces = get_network_info()
    lines = ["=" * 60, "NETWORK", "=" * 60]

    if not interfaces:
        lines.append("Unavailable")

    for interface in interfaces:
        lines.append(f"{interface['name']} — {interface['status']}")
        if interface["speed_mbps"]:
            lines.append(f"  Link Speed: {interface['speed_mbps']} Mbps")
        if interface["ipv4"]:
            lines.append(f"  IPv4: {', '.join(interface['ipv4'])}")
        if interface["ipv6"]:
            lines.append(f"  IPv6: {', '.join(interface['ipv6'])}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_processes():
    processes = get_process_info()
    lines = ["=" * 60, "TOP PROCESSES", "=" * 60]
    lines.append(f"{'PID':<8}{'CPU %':<10}{'MEM %':<10}NAME")
    for process in processes:
        lines.append(
            f"{process['pid']:<8}{process['cpu_percent']:<10}"
            f"{process['memory_percent']:<10}{process['name']}"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


COMMAND_DESCRIPTIONS = {
    "specs": "Complete hardware report",
    "diagnostics": "Live PC performance dashboard",
    "cpu": "CPU details + current usage",
    "gpu": "GPU details + temperature/usage",
    "ram": "RAM details + usage",
    "storage": "Drives + free space",
    "network": "Network adapters",
    "processes": "Top running processes",
    "check": "Quick PC health check",
    "security": "Defender status, ports, startup items",
    "help": "Show this list",
}


def format_help():
    lines = ["=" * 60, "AVAILABLE COMMANDS", "=" * 60]

    for index, command in enumerate(COMMAND_ORDER, 1):
        shortcuts = sorted(
            alias for alias in COMMAND_ALIASES[command]
            if len(alias) <= 2 and alias != command
        )
        shortcut_text = f"  (or: {', '.join(shortcuts)})" if shortcuts else ""
        lines.append(
            f"{index}. /{command:<12}{COMMAND_DESCRIPTIONS[command]}{shortcut_text}"
        )

    lines += [
        "=" * 60,
        'Run a command by typing it ("/ram"), its number ("3"), or its',
        'shortcut ("r"). Small typos are fine too — "stroage" still finds',
        "/storage.",
        'You can also just ask in plain English, e.g. "what GPU do I have"',
        "— that still gets routed automatically.",
        "",
        'Other commands (no leading slash needed):',
        '  scan <folder>          Scan a folder for suspicious files',
        '  list files <folder>    List a folder\'s contents',
        '  activity log           Show recently approved/cancelled actions',
        '  remember <fact>        Save something for the AI to keep in mind',
        '  what do you remember   List saved facts',
        '  forget <text>          Remove saved facts matching text',
        '  note <text> / notes    Save / list scratch notes',
        '  add task: <text> / tasks / complete task <n>',
        '  create project: <name> / projects',
        '  open spotify / discord / steam / chrome / edge / firefox',
        '  open google / youtube / github / reddit / twitch / gmail / outlook',
        '  open website <url> / open https://example.com / open example.com',
        '  open game <name> / open steam game <app_id>',
        '  open notepad / calculator / task manager / settings',
        '  open folder <path>     Requires approve/cancel',
        '  kill process <pid>     Requires approve/cancel',
        "",
        "The AI remembers your conversation across turns. Type '/reset'",
        "(or 'clear', 'new') to wipe that memory and start fresh.",
        "=" * 60,
    ]
    return "\n".join(lines)


# ────────────────────────────────────────────
# HEALTH CHECK
# ────────────────────────────────────────────
# Python decides every status and threshold here. Qwen is never
# consulted for the check itself — only, later, to explain results
# the user already sees.

HEALTH_THRESHOLDS = {
    "cpu_usage_warn": 85,
    "cpu_usage_crit": 97,
    "cpu_temp_warn": 80,
    "cpu_temp_crit": 90,
    "ram_usage_warn": 85,
    "ram_usage_crit": 95,
    "gpu_temp_warn": 80,
    "gpu_temp_crit": 90,
    "storage_warn": 85,
    "storage_crit": 95,
    "process_cpu_warn": 80,
}

STATUS_SYMBOLS = {"OK": "✓", "WARN": "⚠", "CRIT": "✗"}


def collect_health_check():
    data = collect_diagnostics()
    results = {}

    # CPU — usage and temperature both count, worse of the two wins.
    cpu_usage = data["cpu_usage_percent"]
    cpu_temp = data["cpu_temperature_c"]
    status, detail = "OK", "Normal"

    if cpu_usage >= HEALTH_THRESHOLDS["cpu_usage_crit"]:
        status, detail = "CRIT", f"{cpu_usage:.0f}% usage"
    elif cpu_usage >= HEALTH_THRESHOLDS["cpu_usage_warn"]:
        status, detail = "WARN", f"{cpu_usage:.0f}% usage"

    if cpu_temp is not None:
        if cpu_temp >= HEALTH_THRESHOLDS["cpu_temp_crit"]:
            status, detail = "CRIT", f"{cpu_temp:.0f}°C"
        elif cpu_temp >= HEALTH_THRESHOLDS["cpu_temp_warn"] and status != "CRIT":
            status, detail = "WARN", f"{cpu_temp:.0f}°C"

    results["CPU"] = (status, detail)

    # RAM
    ram_usage = data["ram"]["usage_percent"]
    if ram_usage >= HEALTH_THRESHOLDS["ram_usage_crit"]:
        results["RAM"] = ("CRIT", f"{ram_usage:.0f}% used")
    elif ram_usage >= HEALTH_THRESHOLDS["ram_usage_warn"]:
        results["RAM"] = ("WARN", f"{ram_usage:.0f}% used")
    else:
        results["RAM"] = ("OK", "Normal")

    # GPU
    gpu_rows = data["gpu"] or []
    status, detail = "OK", "Normal"
    if not gpu_rows:
        status, detail = "WARN", "No NVIDIA GPU detected"
    else:
        for row in gpu_rows:
            try:
                temp = float(row[5])
            except (IndexError, ValueError):
                temp = None
            if temp is not None:
                if temp >= HEALTH_THRESHOLDS["gpu_temp_crit"]:
                    status, detail = "CRIT", f"{temp:.0f}°C"
                elif temp >= HEALTH_THRESHOLDS["gpu_temp_warn"] and status != "CRIT":
                    status, detail = "WARN", f"{temp:.0f}°C"
    results["GPU"] = (status, detail)

    # Storage — flag the worst volume, if any.
    status, detail = "OK", "Normal"
    for drive in data["storage"]:
        pct = drive["usage_percent"]
        if pct >= HEALTH_THRESHOLDS["storage_crit"]:
            status, detail = "CRIT", f"{pct:.0f}% full ({drive['drive']})"
            break
        elif pct >= HEALTH_THRESHOLDS["storage_warn"] and status != "CRIT":
            status, detail = "WARN", f"{pct:.0f}% full ({drive['drive']})"
    results["Storage"] = (status, detail)

    # Network
    connected = any(i["status"] == "Up" and i["ipv4"] for i in data["network"])
    results["Network"] = ("OK", "Connected") if connected else ("WARN", "No active connection")

    # Processes — flag a single process pinning the CPU.
    processes = data["processes"]
    if processes and processes[0]["cpu_percent"] >= HEALTH_THRESHOLDS["process_cpu_warn"]:
        results["Processes"] = (
            "WARN",
            f"{processes[0]['name']} at {processes[0]['cpu_percent']:.0f}% CPU",
        )
    else:
        results["Processes"] = ("OK", "Normal")

    # Security — lightweight overview; full breakdown via /security.
    defender = get_defender_status_raw()
    suspicious = get_suspicious_processes()
    status, detail = "OK", "Normal"
    if defender:
        if defender.get("AntivirusEnabled") is False or defender.get("RealTimeProtectionEnabled") is False:
            status, detail = "WARN", "Real-time protection is off"
    else:
        status, detail = "WARN", "Defender status unavailable"
    if suspicious:
        status = "WARN" if status == "OK" else status
        detail = f"{len(suspicious)} process(es) running from Temp/Public"
    results["Security"] = (status, detail)

    return results


def format_health_check():
    results = collect_health_check()

    lines = ["PC HEALTH CHECK", "─" * 32]

    concerns = 0
    for component, (status, detail) in results.items():
        symbol = STATUS_SYMBOLS.get(status, "?")
        lines.append(f"{component:<15} {symbol} {detail}")
        if status != "OK":
            concerns += 1

    lines.append("─" * 32)
    if concerns == 0:
        lines.append("Everything looks normal")
    else:
        noun = "thing" if concerns == 1 else "things"
        lines.append(f"{concerns} {noun} worth checking")

    return "\n".join(lines)


# ────────────────────────────────────────────
# SECURITY: SYSTEM HEALTH
# ────────────────────────────────────────────
# Everything here is read-only — status checks and inventory, never
# a fix or a change. Real remediation (running a scan, changing a
# setting) stays with Windows Security / Defender directly.

def get_defender_status_raw():
    command = """
    Get-MpComputerStatus -ErrorAction Stop |
    Select-Object AMServiceEnabled, AntivirusEnabled, AntispywareEnabled,
    RealTimeProtectionEnabled, NISEnabled, IoavProtectionEnabled,
    AntivirusSignatureAge, FullScanAge, QuickScanAge |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_defender_threats_raw(limit=10):
    command = f"""
    Get-MpThreatDetection -ErrorAction Stop |
    Sort-Object InitialDetectionTime -Descending |
    Select-Object -First {limit} ProcessName, DetectionSourceTypeName,
    InitialDetectionTime |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_listening_ports_raw():
    command = """
    Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Select-Object LocalAddress, LocalPort, OwningProcess,
    @{Name='ProcessName';Expression={
        (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    }} |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_established_connections_raw():
    command = """
    Get-NetTCPConnection -State Established -ErrorAction Stop |
    Select-Object LocalPort, RemoteAddress, RemotePort, OwningProcess,
    @{Name='ProcessName';Expression={
        (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    }} |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def get_startup_items_raw():
    command = """
    Get-CimInstance Win32_StartupCommand -ErrorAction Stop |
    Select-Object Name, Command, Location, User |
    ConvertTo-Json -Compress
    """
    return powershell_json(command)


def is_private_or_local(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # unparseable — don't flag it, avoid false alarms
    return ip.is_private or ip.is_loopback or ip.is_link_local


SUSPICIOUS_PROCESS_PATH_MARKERS = (
    "\\appdata\\local\\temp\\",
    "\\windows\\temp\\",
    "\\users\\public\\",
)


def get_suspicious_processes():
    """Heuristic only — flags processes running from a location commonly
    abused (Temp, Public), NOT a malware verdict. Many legitimate
    installers pass through Temp briefly too."""
    flagged = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            exe = process.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if not exe:
            continue
        lowered = exe.lower()
        if any(marker in lowered for marker in SUSPICIOUS_PROCESS_PATH_MARKERS):
            flagged.append({"pid": process.info["pid"], "name": process.info["name"], "path": exe})
    return flagged


def collect_security_check():
    established = as_list(get_established_connections_raw())
    remote_connections = [
        c for c in established
        if not is_private_or_local(clean(c.get("RemoteAddress")) or "")
    ]

    return {
        "defender": get_defender_status_raw(),
        "recent_threats": as_list(get_defender_threats_raw()),
        "listening_ports": as_list(get_listening_ports_raw()),
        "remote_connections": remote_connections,
        "startup_items": as_list(get_startup_items_raw()),
        "suspicious_processes": get_suspicious_processes(),
    }


def format_security():
    data = collect_security_check()
    lines = ["=" * 60, "SECURITY", "=" * 60]

    defender = data["defender"]
    lines.append("Windows Defender:")
    if defender:
        def yn(value):
            if value is True:
                return "Yes"
            if value is False:
                return "No"
            return "Unavailable"
        lines.append(f"  Real-Time Protection: {yn(defender.get('RealTimeProtectionEnabled'))}")
        lines.append(f"  Antivirus Enabled: {yn(defender.get('AntivirusEnabled'))}")
        if defender.get("AntivirusSignatureAge") is not None:
            lines.append(f"  Signature Age: {defender['AntivirusSignatureAge']} day(s)")
        if defender.get("FullScanAge") is not None:
            lines.append(f"  Last Full Scan: {defender['FullScanAge']} day(s) ago")
        if defender.get("QuickScanAge") is not None:
            lines.append(f"  Last Quick Scan: {defender['QuickScanAge']} day(s) ago")
    else:
        lines.append("  Unavailable (Defender may not be the active antivirus, or")
        lines.append("  this needs to run as Administrator)")

    lines.append("")
    lines.append("Recent Defender Detections:")
    threats = data["recent_threats"]
    if threats:
        for threat in threats:
            when = convert_wmi_datetime(threat.get("InitialDetectionTime")) or clean(threat.get("InitialDetectionTime")) or "Unknown time"
            lines.append(
                f"  {clean(threat.get('ProcessName')) or 'Unknown'} — "
                f"{clean(threat.get('DetectionSourceTypeName')) or 'Unknown source'} ({when})"
            )
    else:
        lines.append("  None on record")

    lines.append("")
    lines.append("Listening Ports:")
    listening = data["listening_ports"]
    if listening:
        for entry in listening[:20]:
            lines.append(
                f"  {clean(entry.get('LocalAddress'))}:{entry.get('LocalPort')} — "
                f"{clean(entry.get('ProcessName')) or 'Unknown process'} "
                f"(PID {entry.get('OwningProcess')})"
            )
        if len(listening) > 20:
            lines.append(f"  ... and {len(listening) - 20} more")
    else:
        lines.append("  None detected")

    lines.append("")
    remote = data["remote_connections"]
    lines.append(f"Active Connections to Public IPs: {len(remote)}")
    for entry in remote[:15]:
        lines.append(
            f"  {clean(entry.get('ProcessName')) or 'Unknown process'} → "
            f"{clean(entry.get('RemoteAddress'))}:{entry.get('RemotePort')}"
        )
    if len(remote) > 15:
        lines.append(f"  ... and {len(remote) - 15} more")

    lines.append("")
    suspicious = data["suspicious_processes"]
    lines.append(f"Processes Running From Temp/Public Folders: {len(suspicious)}")
    for process in suspicious[:15]:
        lines.append(f"  {process['name']} (PID {process['pid']}) — {process['path']}")
    if suspicious:
        lines.append("  Note: this is a location heuristic, not a malware verdict.")

    lines.append("")
    lines.append("Startup Items:")
    startup = data["startup_items"]
    if startup:
        for item in startup[:20]:
            lines.append(f"  {clean(item.get('Name')) or 'Unknown'} — {clean(item.get('Command')) or 'Unavailable'}")
        if len(startup) > 20:
            lines.append(f"  ... and {len(startup) - 20} more")
    else:
        lines.append("  Unavailable")

    lines.append("=" * 60)
    lines.append("This is a system overview, not a virus scan. For a folder scan,")
    lines.append('use "scan <folder>". For a real AV scan, use Windows Security.')
    lines.append("=" * 60)
    return "\n".join(lines)


# ────────────────────────────────────────────
# FOLDER SCAN
# ────────────────────────────────────────────
# Read-only inspection of a folder. Never opens, runs, deletes, or
# moves anything it finds — it only reports.

EXECUTABLE_EXTENSIONS = {
    ".exe", ".scr", ".pif", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".msi", ".ps1", ".jar", ".hta", ".com",
}
DOCUMENT_LIKE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
    ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4", ".zip", ".rar",
}
MACRO_EXTENSIONS = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".xlsb", ".ppsm"}

MAX_FILES_WALKED = 20000
MAX_SIGNATURE_CHECKS = 200


def _has_double_extension(filename):
    parts = filename.lower().split(".")
    if len(parts) < 3:
        return False
    return ("." + parts[-1]) in EXECUTABLE_EXTENSIONS and ("." + parts[-2]) in DOCUMENT_LIKE_EXTENSIONS


def _check_signatures(paths):
    """Batch Authenticode check via a temp path-list file — one
    PowerShell call instead of one per file."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as handle:
            for path in paths:
                handle.write(path + "\n")
            list_path = handle.name
    except OSError:
        return [], 0

    command = f"""
    Get-Content -LiteralPath '{list_path}' | ForEach-Object {{
        if (Test-Path -LiteralPath $_) {{
            $sig = Get-AuthenticodeSignature -LiteralPath $_
            [PSCustomObject]@{{ Path = $_; Status = $sig.Status.ToString() }}
        }}
    }} | ConvertTo-Json -Compress
    """

    try:
        results = as_list(powershell_json(command))
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass

    unsigned = [r for r in results if clean(r.get("Status")) not in (None, "Valid")]
    return unsigned, len(results)


def scan_folder(root_path):
    root_path = os.path.expanduser(os.path.expandvars(root_path.strip().strip('"')))

    if not os.path.isdir(root_path):
        return {"error": f'"{root_path}" is not a folder that exists.'}

    double_extension_files, macro_files, executable_files = [], [], []
    files_walked = 0
    truncated = False

    for dirpath, _dirnames, filenames in os.walk(root_path):
        for filename in filenames:
            files_walked += 1
            if files_walked > MAX_FILES_WALKED:
                truncated = True
                break

            full_path = os.path.join(dirpath, filename)
            ext = os.path.splitext(filename)[1].lower()

            if _has_double_extension(filename):
                double_extension_files.append(full_path)
            if ext in MACRO_EXTENSIONS:
                macro_files.append(full_path)
            if ext in EXECUTABLE_EXTENSIONS:
                executable_files.append(full_path)

        if truncated:
            break

    unsigned, signature_checked = [], 0
    to_check = executable_files[:MAX_SIGNATURE_CHECKS]
    if to_check:
        unsigned, signature_checked = _check_signatures(to_check)

    return {
        "root": root_path,
        "files_walked": files_walked,
        "truncated": truncated,
        "double_extension_files": double_extension_files,
        "macro_files": macro_files,
        "executable_files": executable_files,
        "signature_checked": signature_checked,
        "unsigned_executables": unsigned,
    }


def format_scan(path):
    data = scan_folder(path)
    if "error" in data:
        return data["error"]

    lines = ["=" * 60, f"FOLDER SCAN: {data['root']}", "=" * 60]
    lines.append(f"Files scanned: {data['files_walked']}")
    if data["truncated"]:
        lines.append(f"  (stopped at {MAX_FILES_WALKED} files — folder is larger than that)")

    lines.append("")
    lines.append(f"Double-extension files: {len(data['double_extension_files'])}")
    for p in data["double_extension_files"][:15]:
        lines.append(f"  {p}")
    if data["double_extension_files"]:
        lines.append("  These end in an executable extension after what looks like a")
        lines.append("  document/image name — a common disguise trick.")

    lines.append("")
    lines.append(f"Macro-enabled Office files: {len(data['macro_files'])}")
    for p in data["macro_files"][:15]:
        lines.append(f"  {p}")

    lines.append("")
    lines.append(f"Executable files found: {len(data['executable_files'])}")
    lines.append(f"Signature-checked: {data['signature_checked']}")
    lines.append(f"Unsigned / untrusted: {len(data['unsigned_executables'])}")
    for entry in data["unsigned_executables"][:15]:
        lines.append(f"  {clean(entry.get('Path'))} — {clean(entry.get('Status'))}")
    if len(data["unsigned_executables"]) > 15:
        lines.append(f"  ... and {len(data['unsigned_executables']) - 15} more")

    lines.append("=" * 60)
    lines.append("This flags files worth a closer look — it is NOT a virus scan")
    lines.append("and does not open, run, or delete anything.")
    lines.append("=" * 60)
    return "\n".join(lines)


# ────────────────────────────────────────────
# LEARNING / EVOLUTION
# ────────────────────────────────────────────
TEACH_PATTERN = re.compile(
    r"^(?:teach|learn)\s+when\s+i\s+say\s+(.+?)\s+(?:then\s+|to\s+|do\s+)(.+)$",
    re.IGNORECASE,
)
TEACH_ARROW_PATTERN = re.compile(r"^(?:teach|learn)\s+(.+?)\s*(?:=>|->)\s*(.+)$", re.IGNORECASE)
FORGET_LESSON_PATTERN = re.compile(r"^(?:forget|unlearn)\s+(?:lesson|rule)\s+(.+)$", re.IGNORECASE)
SUGGESTIONS_ALIASES = {"evolution", "learning suggestions", "suggestions", "suggested skills", "review learning", "what should you learn"}
APPROVE_SUGGESTION_PATTERN = re.compile(r"^approve\s+(?:suggestion|skill)\s+(\d+)(?:\s+as\s+[\"\']?(.+?)[\"\']?)?$", re.IGNORECASE)
REJECT_SUGGESTION_PATTERN = re.compile(r"^(?:reject|dismiss)\s+(?:suggestion|skill)\s+(\d+)$", re.IGNORECASE)


def _strip_quotes(text):
    return str(text or "").strip().strip('"\'').strip()



WEB_SEARCH_RE = re.compile(r'^(?:search\s+(?:the\s+)?web|web\s+search)\s+(.+)$', re.I)
STUDY_URL_RE = re.compile(r'^(?:study|learn\s+from)\s+(https?://\S+)$', re.I)
STUDY_GITHUB_RE = re.compile(r'^(?:study|learn\s+from)\s+(?:github\s+)?([\w.-]+/[\w.-]+)(?:\s+branch\s+(\S+))?$', re.I)
KNOWLEDGE_RE = re.compile(r'^(?:knowledge|what\s+do\s+you\s+know\s+about)\s+(.+)$', re.I)
RESEARCH_AI_RE = re.compile(r'^research\s+ai(?:\s+(?:latest|refresh|august\s+2026|through\s+august\s+2026))?$', re.I)
SCREEN_RE = re.compile(r'^screen(?:\s+learning)?\s+(on|off|status|snapshot|transitions|analyze)$', re.I)
INTERNET_RE = re.compile(r'^internet\s+(on|off|status)$', re.I)
AUTO_WEB_RE = re.compile(r'^(?:auto\s+web|live\s+web|web\s+autopilot)\s+(on|off|status)$', re.I)
RESEARCH_AUTOPILOT_RE = re.compile(r'^research\s+autopilot\s+(on|off|status|now)$', re.I)
EVOLUTION_AUTOPILOT_RE = re.compile(r'^evolution\s+autopilot\s+(on|off|status|now)$', re.I)
AUTONOMOUS_LAB_RE = re.compile(r'^(?:(?:autonomous\s+lab|ai\s+lab)\s+(on|off|status|now)|(?:start|begin|launch|run)\s+(?:the\s+)?(?:autonomous\s+lab|ai\s+lab))$', re.I)
INTERNET_TEST_RE = re.compile(r'^(?:internet\s+test|web\s+test)$', re.I)
SELF_IMPROVE_RE = re.compile(r'^(?:self\s+improve|improve\s+yourself|self\s+optimization)(?:\s+status)?$', re.I)
SELF_STATUS_RE = re.compile(r'^self\s+status$', re.I)
SELF_EVOLUTION_MODE_RE = re.compile(r'^self\s+evolution\s+(on|off|guided|autonomous|status)$', re.I)
SELF_EVOLVE_RE = re.compile(r'^(?:self\s+evolve|evolve\s+yourself|run\s+self\s+evolution|make\s+yourself\s+better|build\s+a\s+better\s+ai|become\s+smarter)$', re.I)
SELF_APPROVE_RE = re.compile(r'^self\s+approve\s+(\d+)$', re.I)
SELF_ROLLBACK_RE = re.compile(r'^self\s+rollback(?:\s+(\S+))?$', re.I)


def matches_external_command(text):
    low=(text or '').strip().lower()
    return bool(WEB_SEARCH_RE.match(text or '') or STUDY_URL_RE.match(text or '') or STUDY_GITHUB_RE.match(text or '') or KNOWLEDGE_RE.match(text or '') or RESEARCH_AI_RE.match(text or '') or SCREEN_RE.match(text or '') or INTERNET_RE.match(text or '') or AUTO_WEB_RE.match(text or '') or RESEARCH_AUTOPILOT_RE.match(text or '') or AUTONOMOUS_LAB_RE.match(text or '') or INTERNET_TEST_RE.match(text or '') or SELF_IMPROVE_RE.match(text or '') or SELF_STATUS_RE.match(text or '') or SELF_EVOLUTION_MODE_RE.match(text or '') or SELF_EVOLVE_RE.match(text or '') or SELF_APPROVE_RE.match(text or '') or SELF_ROLLBACK_RE.match(text or '') or low in {'web','internet','screen learning','research ai','auto web','live web','research autopilot','evolution autopilot','self evolution','prime directive','mission','evolution report','objective report','measure yourself','what is your prime directive'})


def handle_external_command(text):
    global _INTERNET_ENABLED, _AUTO_WEB_SEARCH
    raw=(text or '').strip(); low=raw.lower()
    m=INTERNET_RE.match(raw)
    if m:
        mode=m.group(1).lower()
        if mode=='on':
            _INTERNET_ENABLED=True
            try: _RESEARCH_MANAGER.start_autopilot()
            except Exception: pass
            return 'system','Internet access is ON. MyLocalAI can access public HTTP/HTTPS hosts; local/private address targets are blocked.'
        if mode=='off':
            _INTERNET_ENABLED=False
            try: _RESEARCH_MANAGER.stop_autopilot()
            except Exception: pass
            return 'system','Internet access is OFF.'
        return 'system',f'Internet access: {"ON" if _INTERNET_ENABLED else "OFF"}; automatic live web research: {"ON" if _AUTO_WEB_SEARCH else "OFF"}; autonomous research: {"ON" if _RESEARCH_MANAGER.autopilot_status().get("autopilot_enabled", True) else "OFF"}'
    m=AUTO_WEB_RE.match(raw)
    if m:
        mode=m.group(1).lower()
        if mode=='on':
            _AUTO_WEB_SEARCH=True
            return 'system','Automatic live web research is ON. Normal factual/current questions will be grounded with fresh public web sources whenever internet access is ON.'
        if mode=='off':
            _AUTO_WEB_SEARCH=False
            return 'system','Automatic live web research is OFF. Explicit web searches still work when internet access is ON.'
        return 'system',f'Automatic live web research: {"ON" if _AUTO_WEB_SEARCH else "OFF"}'
    m=EVOLUTION_AUTOPILOT_RE.match(raw)
    if m:
        mode=m.group(1).lower()
        try:
            if mode == 'on':
                ok, msg = _SELF_EVOLUTION.set_mode('autonomous')
                return ('system' if ok else 'error'), msg
            if mode == 'off':
                ok, msg = _SELF_EVOLUTION.set_mode('guided')
                return ('system' if ok else 'error'), msg
            if mode == 'now':
                diagnosis = _evolution_diagnosis()
                result = _SELF_EVOLUTION.evolve(diagnosis, autonomous=True)
                return 'system', _format_evolution_result(result)
            return 'system', json.dumps(_SELF_EVOLUTION.status(), indent=2)
        except Exception as e:
            return 'error', f'Evolution autopilot failed ({type(e).__name__}): {e}'
    m=RESEARCH_AUTOPILOT_RE.match(raw)
    if m:
        if not _INTERNET_ENABLED: return 'system','Internet access is OFF. Type "internet on" first.'
        mode=m.group(1).lower()
        try:
            if mode == 'on':
                st=_RESEARCH_MANAGER.set_autopilot(True)
                return 'system',f'Autonomous internet research is ON. MyLocalAI will search for new AI/coding/agent research in the background. Interval: {st.get("autopilot_interval_hours", 4)} hours.'
            if mode == 'off':
                _RESEARCH_MANAGER.set_autopilot(False)
                return 'system','Autonomous internet research is OFF. Live web research for conversations remains available.'
            if mode == 'now':
                result=_RESEARCH_MANAGER.autonomous_refresh(reason='manual')
                return 'system',f'Autonomous research completed now: searched {result["searched"]} sources, indexed {result["indexed_sources"]} source(s), {result["chunks"]} knowledge chunks.'
            return 'system',json.dumps(_RESEARCH_MANAGER.autopilot_status(), indent=2)
        except Exception as e:
            return 'system',f'Autonomous research failed ({type(e).__name__}): {e}'
    m=AUTONOMOUS_LAB_RE.match(raw)
    if m:
        action=(m.group(1) or 'now').lower()
        lab_state_path=os.path.join(DATA_DIR,'autonomous_lab.json')
        try:
            current=json.loads(open(lab_state_path,'r',encoding='utf-8').read()) if os.path.exists(lab_state_path) else {}
        except Exception: current={}
        root = os.path.dirname(os.path.abspath(__file__))
        worker_path = os.path.join(root, 'services', 'autonomous_lab.py')
        launcher_path = os.path.join(root, 'Run MyLocalAI Autonomous Lab.bat')
        installed = os.path.isfile(worker_path) and os.path.isfile(launcher_path)
        pid = current.get('pid')
        alive = False
        if isinstance(pid, int) and pid > 0:
            try:
                import psutil
                alive = psutil.pid_exists(pid)
            except Exception:
                alive = False
        if action=='status':
            current['installed'] = installed
            current['running'] = alive
            current['pid'] = pid if alive else None
            if alive:
                current['message'] = 'Autonomous AI Lab is running in the background.'
            elif installed:
                current['message'] = 'Autonomous AI Lab is installed but not currently running.'
            else:
                current['message'] = 'Autonomous AI Lab files are missing.'
            return 'system', json.dumps(current, indent=2)
        if action=='now':
            if not installed:
                return 'error', 'Autonomous AI Lab is not installed correctly. Missing services\\autonomous_lab.py or its launcher.'
            if alive:
                return 'system', f'Autonomous AI Lab is already running (PID {pid}).'
            try:
                current.setdefault('config', {})['enabled'] = True
                tmp = lab_state_path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as fh:
                    json.dump(current, fh, indent=2)
                os.replace(tmp, lab_state_path)
                proc = subprocess.Popen([sys.executable, worker_path], cwd=root, **hidden_subprocess_kwargs())
                current.update({
                    'installed': True,
                    'running': True,
                    'pid': int(proc.pid),
                    'started_at': datetime.now().isoformat(timespec='seconds'),
                    'message': 'Autonomous AI Lab is starting in the background.'
                })
                tmp = lab_state_path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as fh:
                    json.dump(current, fh, indent=2)
                os.replace(tmp, lab_state_path)
                return 'system', f'Autonomous AI Lab started in the background (PID {proc.pid}).'
            except Exception as e:
                return 'error',f'Could not start Autonomous AI Lab ({type(e).__name__}): {e}'
        enabled=action=='on'
        current.setdefault('config',{})['enabled']=enabled
        try:
            tmp=lab_state_path+'.tmp'; open(tmp,'w',encoding='utf-8').write(json.dumps(current,indent=2)); os.replace(tmp,lab_state_path)
        except Exception as e: return 'error',f'Could not update Autonomous AI Lab setting ({type(e).__name__}): {e}'
        return 'system',f'Autonomous AI Lab is {"ON" if enabled else "OFF"}. Running worker processes read this setting between cycles.'
    m=INTERNET_TEST_RE.match(raw)
    if m:
        if not _INTERNET_ENABLED: return 'system','Internet access is OFF. Type "internet on" first.'
        try:
            from services.internet import network_self_test
            result=network_self_test()
            return 'system', json.dumps(result, indent=2)
        except Exception as e:
            return 'system',f'Internet self-test failed ({type(e).__name__}): {e}'
    m=WEB_SEARCH_RE.match(raw)
    if m:
        if not _INTERNET_ENABLED: return 'system','Internet access is OFF. Type "internet on" first.'
        try:
            rows=web_search(m.group(1),6)
            return 'system', ('No results found.' if not rows else '\n'.join(f'{i}. {r["title"]}\n   {r["url"]}' for i,r in enumerate(rows,1)))
        except Exception as e: return 'system',f'Web search failed ({type(e).__name__}): {e}'
    m=STUDY_URL_RE.match(raw)
    if m:
        if not _INTERNET_ENABLED: return 'system','Internet access is OFF. Type "internet on" first.'
        try:
            title,url,count=_KNOWLEDGE_STORE.ingest_url(m.group(1)); return 'system',f'Studied {title}. Indexed {count} local knowledge chunk(s).'
        except Exception as e: return 'system',f'Could not study URL ({type(e).__name__}): {e}'
    m=STUDY_GITHUB_RE.match(raw)
    if m:
        if not _INTERNET_ENABLED: return 'system','Internet access is OFF. Type "internet on" first.'
        try:
            files,chunks=_KNOWLEDGE_STORE.ingest_github(m.group(1),m.group(2) or ''); return 'system',f'Studied {m.group(1)}: {files} file(s), {chunks} chunk(s) indexed locally.'
        except Exception as e: return 'system',f'Could not study GitHub repo ({type(e).__name__}): {e}'
    m=KNOWLEDGE_RE.match(raw)
    if m:
        rows=_KNOWLEDGE_STORE.search(m.group(1),6)
        return 'system', ('No matching local knowledge yet.' if not rows else '\n\n'.join(f'{i}. {r["title"]}\n{r["content"][:900]}' for i,r in enumerate(rows,1)))
    m=RESEARCH_AI_RE.match(raw)
    if m:
        if not _INTERNET_ENABLED: return 'system','Internet access is OFF. Type "internet on" first.'
        try:
            result=_RESEARCH_MANAGER.refresh()
            return 'system',f'AI research refresh complete. Searched {result["searched"]} sources and indexed {result["indexed_sources"]} source(s) into local knowledge ({result["chunks"]} chunks). Scope: research through August 2026.'
        except Exception as e: return 'system',f'AI research refresh failed ({type(e).__name__}): {e}'
    m=SELF_STATUS_RE.match(raw)
    if m or low == 'self evolution':
        return 'system', json.dumps(_SELF_EVOLUTION.status(), indent=2)
    if low in {'prime directive','mission','what is your prime directive'}:
        return 'system', mission_context()
    if low in {'evolution report','objective report','measure yourself'}:
        st = _SELF_EVOLUTION.status()
        try:
            from services.objectives import snapshot
            obj = snapshot(_LEARNING_STORE)
            obj_lines=[f"Objective score: {obj.get('composite_score', 0):.2f}/100 (coverage {obj.get('coverage', 0):.0f}%)"]
            for name, item in obj.get('categories', {}).items():
                metrics=item.get('metrics', {})
                details=', '.join(f"{k}={v}" for k,v in list(metrics.items())[:4])
                obj_lines.append(f"{name}: {item.get('score', 0):.1f} [{item.get('confidence')}] {details}")
            objective_text='\n'.join(obj_lines)
        except Exception:
            objective_text='Objective telemetry unavailable.'
        return 'system', (
            f"Prime directive: {PRIME_DIRECTIVE}\n"
            f"Mode: {st.get('mode')}\n"
            f"Cycles: {st.get('evolution_cycles', 0)} | Accepted: {st.get('accepted_improvements', 0)} | "
            f"Rejected/no gain: {st.get('rejected_regressions', 0)}\n"
            f"Benchmark: baseline={st.get('baseline_score')} last={st.get('last_score')} "
            f"delta={st.get('last_score_delta')} best={st.get('best_score')}\n"
            f"Measured objective change: {((st.get('last_objective_comparison') or {}).get('delta', 0)):+.2f} "
            f"({((st.get('last_objective_comparison') or {}).get('percent', 0)):+.2f}%)\n\n"
            + objective_text
        )
    m=SELF_EVOLUTION_MODE_RE.match(raw)
    if m:
        mode=m.group(1).lower()
        if mode=='status':
            return 'system', json.dumps(_SELF_EVOLUTION.status(), indent=2)
        ok,msg=_SELF_EVOLUTION.set_mode(mode)
        return 'system', msg
    m=SELF_APPROVE_RE.match(raw)
    if m:
        ok,msg=_SELF_EVOLUTION.apply_proposal_id(m.group(1))
        return ('system' if ok else 'error'), msg
    m=SELF_ROLLBACK_RE.match(raw)
    if m:
        ok,msg=_SELF_EVOLUTION.rollback(m.group(1))
        return ('system' if ok else 'error'), msg
    m=SELF_IMPROVE_RE.match(raw) or SELF_EVOLVE_RE.match(raw)
    if m:
        diagnosis=_evolution_diagnosis()
        result=_SELF_EVOLUTION.evolve(diagnosis, autonomous=(_SELF_EVOLUTION.status().get('mode')=='autonomous'))
        return 'system', _format_evolution_result(result)
    m=SCREEN_RE.match(raw)
    if m:
        action=m.group(1).lower()
        if action=='on': _SCREEN_OBSERVER.start(); return 'system','Screen learning is ON. MyLocalAI observes foreground app/title plus in-memory OCR/optional local vision; protected-looking windows are excluded and raw screenshots are not retained.'
        if action=='off': _SCREEN_OBSERVER.stop(); return 'system','Screen learning is OFF.'
        if action=='status': return 'system',json.dumps(_SCREEN_OBSERVER.status(),indent=2)
        if action=='transitions':
            rows=_SCREEN_OBSERVER.transitions(); return 'system', ('No repeated app transitions yet.' if not rows else 'Repeated app transitions:\n'+'\n'.join(f'- {r["from"]} → {r["to"]} ({r["count"]}x)' for r in rows))
        if action=='snapshot':
            try: return 'system',f'One-time screen preview saved locally at: {_SCREEN_OBSERVER.capture_preview()}'
            except Exception as e: return 'system',f'Screen preview blocked: {e}'
        if action=='analyze':
            if _SCREEN_OBSERVER.status().get('last',{}).get('process'):
                try:
                    result=_VISION.capture_and_analyze('Analyze the current desktop only for workflow/habit context. Identify active apps, visible UI state, and likely workflow step. Do not report secrets, credentials, messages, financial data, personal identifiers, or private content. Keep it concise.')
                    return 'system', result or 'No compatible local vision model is installed; OCR/metadata screen learning is still available.'
                except Exception as e: return 'system',f'Vision analysis failed ({type(e).__name__}): {e}'
            return 'system','Start screen learning first with "screen on".'
    if low in {'web','internet','screen learning'}:
        return 'system','internet on/off/status • auto web on/off/status • research autopilot on/off/status/now • evolution autopilot on/off/status/now • autonomous lab on/off/status/now • internet test • search web <query> • research ai • study <url> • study github owner/repo • knowledge <topic> • screen on/off/status/transitions/snapshot/analyze • self evolve • build a better ai • become smarter • prime directive • evolution report • objective report • measure yourself • self evolution on/off/status • self approve <id> • self rollback [backup]'
    return 'system','No external command matched.'


def external_knowledge_context(text):
    try: return _KNOWLEDGE_STORE.context(text,5)
    except Exception: return ''

def matches_learning_command(text):
    normalized = (text or "").strip()
    low = normalized.lower()
    return bool(
        TEACH_PATTERN.match(normalized)
        or TEACH_ARROW_PATTERN.match(normalized)
        or FORGET_LESSON_PATTERN.match(normalized)
        or APPROVE_SUGGESTION_PATTERN.match(normalized)
        or REJECT_SUGGESTION_PATTERN.match(normalized)
        or low in {"learning stats", "what have you learned", "what have you learned?", "learned rules"}
        or low in SUGGESTIONS_ALIASES
    )


def _format_evolution_suggestions():
    rows = _LEARNING_STORE.pending_candidates(limit=12)
    if not rows:
        return (
            "No new evolution suggestions yet. I watch successful local actions and look for "
            "repeated routines; suggestions appear after a pattern is repeated several times."
        )
    lines = ["Evolution suggestions (nothing is active until you approve it):"]
    for i, row in enumerate(rows, 1):
        steps = json.loads(row.get("steps_json") or "[]")
        if row["candidate_type"] == "workflow":
            detail = " → ".join(str(x) for x in steps)
            lines.append(f"{i}. WORKFLOW: '{row['proposed_trigger']}' → {detail} • evidence {row['evidence_count']}")
        else:
            detail = steps[0] if steps else ""
            lines.append(f"{i}. SHORTCUT: '{row['proposed_trigger']}' → {detail} • evidence {row['evidence_count']}")
    lines.append("Approve one with: approve suggestion 1")
    lines.append("Or choose a name: approve suggestion 1 as gaming setup")
    return "\n".join(lines)


def _approve_evolution_suggestion(index_text, custom_trigger=None):
    try:
        index = int(index_text)
    except ValueError:
        return "That suggestion number is invalid."
    rows = _LEARNING_STORE.pending_candidates(limit=50)
    if index < 1 or index > len(rows):
        return "I couldn't find that pending suggestion. Use 'suggestions' to refresh the list."
    row = rows[index - 1]
    steps = json.loads(row.get("steps_json") or "[]")
    trigger = _strip_quotes(custom_trigger or row["proposed_trigger"])
    if row["candidate_type"] == "workflow":
        if len(steps) < 2 or any(not str(x).strip() for x in steps[:2]):
            return "That workflow suggestion is incomplete and cannot be promoted safely."
        command = "workflow:" + json.dumps(steps, ensure_ascii=False, separators=(",", ":"))
    else:
        if not steps or not str(steps[0]).strip():
            return "That shortcut suggestion is incomplete and cannot be promoted safely."
        command = str(steps[0]).strip()
    ok, msg = _LEARNING_STORE.learn_command(trigger, command)
    if not ok:
        return msg
    _LEARNING_STORE.set_candidate_status(row["id"], "approved")
    return f"Approved suggestion {index}. Learned '{trigger}'."


def _reject_evolution_suggestion(index_text):
    try:
        index = int(index_text)
    except ValueError:
        return "That suggestion number is invalid."
    rows = _LEARNING_STORE.pending_candidates(limit=50)
    if index < 1 or index > len(rows):
        return "I couldn't find that pending suggestion. Use 'suggestions' to refresh the list."
    row = rows[index - 1]
    _LEARNING_STORE.set_candidate_status(row["id"], "rejected")
    return f"Dismissed suggestion {index}. I won't promote it."


def handle_learning_command(text):
    normalized = (text or "").strip()
    low = normalized.lower()
    match = TEACH_PATTERN.match(normalized) or TEACH_ARROW_PATTERN.match(normalized)
    if match:
        trigger = _strip_quotes(match.group(1))
        command = _strip_quotes(match.group(2))
        ok, msg = _LEARNING_STORE.learn_command(trigger, command)
        return msg

    match = FORGET_LESSON_PATTERN.match(normalized)
    if match:
        trigger = _strip_quotes(match.group(1))
        removed = _LEARNING_STORE.remove_lesson(trigger)
        return f"Unlearned '{trigger}'." if removed else f"I don't have a lesson for '{trigger}'."

    match = APPROVE_SUGGESTION_PATTERN.match(normalized)
    if match:
        return _approve_evolution_suggestion(match.group(1), match.group(2))

    match = REJECT_SUGGESTION_PATTERN.match(normalized)
    if match:
        return _reject_evolution_suggestion(match.group(1))

    if low in {"learning stats", "what have you learned", "what have you learned?"}:
        stats = _LEARNING_STORE.stats()
        return (
            f"Learning: {stats['lessons']} active rule(s), {stats['interactions']} recorded interaction(s), "
            f"{stats['observations']} successful behavior observation(s), {stats['feedback']} feedback signal(s) "
            f"({stats['positive']} positive / {stats['negative']} negative), {stats['pending']} pending suggestion(s)."
        )

    if low == "learned rules":
        rows = _LEARNING_STORE.recent_lessons()
        if not rows:
            return "No learned rules yet. Try: teach when I say 'gaming time' do 'open steam'."
        lines = ["Learned rules:"]
        for i, row in enumerate(rows, 1):
            command = row['command']
            if command.startswith("workflow:"):
                try:
                    command = "workflow: " + " → ".join(json.loads(command[len("workflow:"):]))
                except Exception:
                    pass
            lines.append(f"{i}. '{row['trigger']}' -> '{command}' • uses {row['uses']} • +{row['successes']}/-{row['failures']}")
        return "\n".join(lines)

    if low in SUGGESTIONS_ALIASES:
        return _format_evolution_suggestions()
    return "No learning command matched."


def match_learned_rule(text):
    return _LEARNING_STORE.lookup_lesson(text)


def execute_learned_rule(session, command):
    """Execute a learned command through the normal safety/action/router path."""
    command = str(command or "").strip()
    if not command:
        return "system", "The learned command was empty."
    if command.startswith("workflow:"):
        try:
            steps = json.loads(command[len("workflow:"):])
        except (TypeError, ValueError, json.JSONDecodeError):
            return "system", "The learned workflow data is invalid."
        if not isinstance(steps, list) or not steps or len(steps) > 5:
            return "system", "The learned workflow is invalid or too large."
        for step in steps:
            step = str(step).strip()
            action, match = match_action(step)
            route = route_user_input(step)
            if not action and route not in COMMAND_HANDLERS:
                return "system", f"The workflow contains an unsupported step: {step}"
        descriptions = []
        for step in steps:
            action, match = match_action(step)
            descriptions.append(action["describe"](match) if action else step)
        session.pending_action = {
            "description": "Run learned workflow: " + " → ".join(descriptions),
            "run": lambda: _execute_workflow_steps(steps),
            "learned_trigger": getattr(session, "last_learned_trigger", None),
            "learning_workflow_steps": list(steps),
        }
        return "action_pending", f"CONFIRM: Run learned workflow: {' → '.join(descriptions)}\napprove / cancel"

    action, match = match_action(command)
    if action:
        description = action["describe"](match)
        session.pending_action = {
            "description": description,
            "run": lambda: action["execute"](match),
            "learned_trigger": getattr(session, "last_learned_trigger", None),
            "learning_text": command,
            "learning_intent": "SYSTEM_ACTION",
        }
        return "action_pending", f"CONFIRM: {description}\napprove / cancel"
    route = route_user_input(command)
    if route in COMMAND_HANDLERS:
        return "pc", COMMAND_HANDLERS[route]()
    return "system", f"I learned the mapping, but '{command}' is not a supported deterministic tool/action yet."


def _execute_workflow_steps(steps):
    results = []
    for step in steps:
        action, match = match_action(step)
        if action:
            result = action["execute"](match)
            signature = "action:" + re.sub(r"\s+", " ", str(action["describe"](match)).strip().lower())
        else:
            route = route_user_input(step)
            if route not in COMMAND_HANDLERS:
                return f"Stopped: unsupported workflow step '{step}'."
            result = COMMAND_HANDLERS[route]()
            signature = "command:" + route
        results.append(str(result))
        try:
            _LEARNING_STORE.observe(step, signature, True)
        except Exception:
            pass
    return "Workflow complete:\n" + "\n".join(f"• {r}" for r in results)


def feedback_learning(session, score, note=""):
    interaction_id = getattr(session, "last_interaction_id", None)
    ok = _LEARNING_STORE.feedback(interaction_id, score, note)
    trigger = getattr(session, "last_learned_trigger", None)
    if ok and trigger:
        _LEARNING_STORE.lesson_feedback(trigger, score > 0)
    if ok:
        session.last_learned_trigger = None
        return "Thanks — I'll use that feedback to improve future responses."
    return "There isn't a recent interaction available to rate."


def learning_stats_text():
    stats = _LEARNING_STORE.stats()
    return (
        f"Interactions: {stats['interactions']}\n"
        f"Learned rules: {stats['lessons']}\n"
        f"Feedback: {stats['feedback']} ({stats['positive']} positive / {stats['negative']} negative)"
    )

# ────────────────────────────────────────────
# PERSONAL MEMORY / DATA STORAGE
# ────────────────────────────────────────────
# Simple JSON files on disk under ./data — no database, nothing
# fancy. Deterministic storage and retrieval; Qwen only sees these
# facts as background context, it never writes to these files itself.

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

from core.learning import LearningStore
from services.internet import search as web_search
from services.live_research import research as live_research, should_search as should_auto_search, compact_query as compact_live_query
from services.knowledge import KnowledgeStore
from services.screen_observer import ScreenObserver
from services.research import ResearchManager
from services.vision import LocalVision
from services.self_evolution import SelfEvolution
from core.mission import PRIME_DIRECTIVE, mission_context
_LEARNING_STORE = LearningStore(DATA_DIR)
_KNOWLEDGE_STORE = KnowledgeStore(DATA_DIR)
_SCREEN_OBSERVER = ScreenObserver(DATA_DIR)
_RESEARCH_MANAGER = ResearchManager(DATA_DIR, _KNOWLEDGE_STORE)
_VISION = LocalVision(model='auto')
_SCREEN_OBSERVER.configure_vision(_VISION, 15)
_SELF_EVOLUTION = SelfEvolution(os.path.dirname(os.path.abspath(__file__)), DATA_DIR)
_INTERNET_ENABLED = True
_AUTO_WEB_SEARCH = True
_LAST_LIVE_WEB = {}


def _data_path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


def _load_json_list(name):
    path = _data_path(name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []


def _save_json_list(name, items):
    try:
        with open(_data_path(name), "w", encoding="utf-8") as handle:
            json.dump(items, handle, indent=2)
    except OSError:
        pass


def _now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


REMEMBER_PATTERN = re.compile(r"^remember\s+(.+)$", re.IGNORECASE)
FORGET_PATTERN = re.compile(r"^forget\s+(.+)$", re.IGNORECASE)
NOTE_PATTERN = re.compile(r"^note\s+(.+)$", re.IGNORECASE)
ADD_TASK_PATTERN = re.compile(r"^add\s+task:\s*(.+)$", re.IGNORECASE)
COMPLETE_TASK_PATTERN = re.compile(r"^complete\s+task\s+(\d+)$", re.IGNORECASE)
CREATE_PROJECT_PATTERN = re.compile(r"^create\s+project:\s*(.+)$", re.IGNORECASE)


def handle_memory_command(text):
    """Returns a response string if `text` matched a memory/notes/
    tasks/projects command, or None if it didn't match anything here."""
    normalized = text.strip()
    lowered = normalized.lower()

    match = REMEMBER_PATTERN.match(normalized)
    if match:
        facts = _load_json_list("memory_facts.json")
        facts.append({"text": match.group(1).strip(), "saved_at": _now_iso()})
        _save_json_list("memory_facts.json", facts)
        return f"Remembered: {match.group(1).strip()}"

    if lowered in ("what do you remember", "what do you remember?"):
        facts = _load_json_list("memory_facts.json")
        if not facts:
            return 'Nothing saved yet. Say "remember <fact>" to add something.'
        lines = ["Here's what I have saved:"]
        for index, fact in enumerate(facts, 1):
            lines.append(f"  {index}. {fact['text']}")
        return "\n".join(lines)

    match = FORGET_PATTERN.match(normalized)
    if match:
        query = match.group(1).strip().lower()
        facts = _load_json_list("memory_facts.json")
        kept = [f for f in facts if query not in f["text"].lower()]
        removed = len(facts) - len(kept)
        _save_json_list("memory_facts.json", kept)
        return f"Forgot {removed} matching item(s)." if removed else f'Nothing matched "{query}".'

    match = NOTE_PATTERN.match(normalized)
    if match:
        notes = _load_json_list("notes.json")
        notes.append({"text": match.group(1).strip(), "saved_at": _now_iso()})
        _save_json_list("notes.json", notes)
        return "Note saved."

    if lowered == "notes":
        notes = _load_json_list("notes.json")
        if not notes:
            return 'No notes yet. Say "note <text>" to add one.'
        lines = ["Notes:"]
        for index, note in enumerate(notes, 1):
            lines.append(f"  {index}. {note['text']}")
        return "\n".join(lines)

    match = ADD_TASK_PATTERN.match(normalized)
    if match:
        tasks = _load_json_list("tasks.json")
        tasks.append({"text": match.group(1).strip(), "done": False, "saved_at": _now_iso()})
        _save_json_list("tasks.json", tasks)
        return f"Task added (#{len(tasks)}): {match.group(1).strip()}"

    if lowered == "tasks":
        tasks = _load_json_list("tasks.json")
        if not tasks:
            return 'No tasks yet. Say "add task: <text>" to add one.'
        lines = ["Tasks:"]
        for index, task in enumerate(tasks, 1):
            box = "[x]" if task["done"] else "[ ]"
            lines.append(f"  {index}. {box} {task['text']}")
        return "\n".join(lines)

    match = COMPLETE_TASK_PATTERN.match(normalized)
    if match:
        index = int(match.group(1))
        tasks = _load_json_list("tasks.json")
        if 1 <= index <= len(tasks):
            tasks[index - 1]["done"] = True
            _save_json_list("tasks.json", tasks)
            return f"Marked task #{index} complete: {tasks[index - 1]['text']}"
        return f"No task #{index}."

    match = CREATE_PROJECT_PATTERN.match(normalized)
    if match:
        projects = _load_json_list("projects.json")
        projects.append({"name": match.group(1).strip(), "created_at": _now_iso()})
        _save_json_list("projects.json", projects)
        return f"Project created: {match.group(1).strip()}"

    if lowered == "projects":
        projects = _load_json_list("projects.json")
        if not projects:
            return 'No projects yet. Say "create project: <name>" to add one.'
        lines = ["Projects:"]
        for index, project in enumerate(projects, 1):
            lines.append(f"  {index}. {project['name']}")
        return "\n".join(lines)

    return None


def matches_memory_command(text):
    """Side-effect-free check: True if `text` would be handled by
    handle_memory_command(). The router needs to test whether a message is
    a memory/notes/tasks/projects command *before* deciding how to route
    it, but handle_memory_command() itself reads and writes JSON files
    (saving facts, deleting facts, adding notes/tasks). Calling it just to
    check the result — then calling it again to actually execute — would
    run every memory operation twice (duplicate "remember"/"note"/"add
    task" entries, and "forget" reporting "nothing matched" on its second,
    now-empty pass). This mirrors the same pattern checks with none of the
    side effects, so it's safe to call purely for classification."""
    normalized = (text or "").strip()
    lowered = normalized.lower()
    if REMEMBER_PATTERN.match(normalized):
        return True
    if lowered in ("what do you remember", "what do you remember?"):
        return True
    if FORGET_PATTERN.match(normalized):
        return True
    if NOTE_PATTERN.match(normalized):
        return True
    if lowered == "notes":
        return True
    if ADD_TASK_PATTERN.match(normalized):
        return True
    if lowered == "tasks":
        return True
    if COMPLETE_TASK_PATTERN.match(normalized):
        return True
    if CREATE_PROJECT_PATTERN.match(normalized):
        return True
    if lowered == "projects":
        return True
    return False


# ────────────────────────────────────────────
# ACTIVITY LOG
# ────────────────────────────────────────────
# Every system action — approved or cancelled — is written here so
# there's always a record of what actually ran.

def log_activity(entry):
    try:
        with open(_data_path("activity_log.txt"), "a", encoding="utf-8") as handle:
            handle.write(f"[{_now_iso()}] {entry}\n")
    except OSError:
        pass


def format_activity_log(limit=25):
    path = _data_path("activity_log.txt")
    if not os.path.exists(path):
        return "No actions logged yet."
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return "Could not read the activity log."
    recent = lines[-limit:]
    return "Recent activity:\n" + "".join(recent) if recent else "No actions logged yet."


# ────────────────────────────────────────────
# FILE LISTING (read-only)
# ────────────────────────────────────────────

LIST_FILES_PATTERN = re.compile(r"^list\s+files\s+(.+)$", re.IGNORECASE)


def format_list_files(path):
    target = os.path.expanduser(os.path.expandvars((path or "~").strip().strip('"')))

    if not os.path.isdir(target):
        return f'"{target}" is not a folder that exists.'

    try:
        entries = sorted(os.listdir(target))
    except OSError as e:
        return f"Could not read that folder: {e}"

    lines = [f"Contents of {target}:"]
    shown = 0
    for name in entries:
        full = os.path.join(target, name)
        if os.path.isdir(full):
            lines.append(f"  [DIR]  {name}")
        else:
            try:
                size = os.path.getsize(full)
                size_text = f"{size / 1024:.1f} KB"
            except OSError:
                size_text = "?"
            lines.append(f"  [FILE] {name}  ({size_text})")
        shown += 1
        if shown >= 200:
            lines.append(f"  ... and {len(entries) - shown} more")
            break

    return "\n".join(lines)


# ────────────────────────────────────────────
# ACTION APPROVAL SYSTEM
# ────────────────────────────────────────────
# A fixed, explicit whitelist of system actions — not a general
# "run any command" facility. Every action here still requires a
# separate approve/cancel step before it runs. Qwen is never involved
# in triggering these: matching is done with plain regexes in
# handle_message, so nothing the AI generates can execute anything —
# only text the user actually types can match an action pattern.

CRITICAL_PROCESS_NAMES = {
    "system", "system idle process", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "smss.exe",
    "svchost.exe", "explorer.exe", "python.exe", "pythonw.exe",
}


def _launch_executable(name):
    try:
        subprocess.Popen([name], **hidden_subprocess_kwargs())
        return f"Launched {name}."
    except FileNotFoundError:
        return f"Could not find {name} — it may not be on your PATH."
    except Exception as e:
        return f"Failed to launch {name}: {e}"


def _open_settings():
    try:
        os.startfile("ms-settings:")
        return "Opened Windows Settings."
    except Exception as e:
        return f"Failed to open Settings: {e}"


def _open_folder_action(path):
    expanded = os.path.expanduser(os.path.expandvars(path.strip().strip('"')))
    if not os.path.isdir(expanded):
        return f'"{expanded}" is not a folder that exists.'
    try:
        os.startfile(expanded)
        return f"Opened folder: {expanded}"
    except Exception as e:
        return f"Failed to open folder: {e}"


def _kill_process_action(pid):
    if pid <= 4:
        return f"Refused: PID {pid} is a core Windows system process."
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return f"No process with PID {pid} is currently running."
    except psutil.Error as e:
        return f"Could not inspect PID {pid}: {e}"

    process_name = process.name() or "unknown"
    name = process_name.lower()
    if name in CRITICAL_PROCESS_NAMES or pid == os.getpid():
        return f"Refused: '{process_name}' (PID {pid}) is a protected process and won't be terminated here."

    try:
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            return f"Termination requested for {process_name} (PID {pid}), but it is still running."
        return f"Terminated: {process_name} (PID {pid})"
    except psutil.Error as e:
        return f"Failed to terminate PID {pid}: {e}"


def _open_url_action(value):
    """Open a validated web URL in the user's default browser."""
    raw = str(value).strip().strip('"\'')
    if not raw:
        return "No website was provided."
    url = raw if re.match(r"^https?://", raw, re.IGNORECASE) else "https://" + raw
    if not re.match(r"^https?://[^\s]+$", url, re.IGNORECASE):
        return "That does not look like a valid website URL."
    try:
        # Keep browser launches behind the same public-host policy used by
        # live research. This prevents an approved-looking "open website"
        # action from navigating to localhost or private network services.
        from services.internet import validate_url
        url = validate_url(url)
        import webbrowser
        if webbrowser.open(url, new=2):
            return f"Opened website: {url}"
        return f"Could not open website: {url}"
    except Exception as e:
        return f"Failed to open website: {e}"


WEBSITE_ALIASES = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "twitch": "https://www.twitch.tv",
    "discord web": "https://discord.com/app",
    "spotify web": "https://open.spotify.com",
    "gmail": "https://mail.google.com",
    "outlook": "https://outlook.live.com",
    "chatgpt": "https://chatgpt.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "x": "https://x.com",
    "twitter": "https://x.com",
    "amazon": "https://www.amazon.com",
    "netflix": "https://www.netflix.com",
}


# Steam game aliases use Steam's public app-launch protocol. Users can always
# bypass the alias list with: open steam game <numeric_app_id>.
STEAM_GAME_IDS = {
    "counter strike 2": "730",
    "cs2": "730",
    "terraria": "105600",
    "stardew valley": "413150",
    "portal 2": "620",
    "grand theft auto v": "271590",
    "gta v": "271590",
    "apex legends": "1172470",
    "rocket league": "252950",
    "lethal company": "1966720",
    "helldivers 2": "553850",
    "baldurs gate 3": "1086940",
    "baldur's gate 3": "1086940",
    "cyberpunk 2077": "1091500",
    "elden ring": "1245620",
    "the witcher 3": "292030",
    "among us": "945360",
    "phasmophobia": "739630",
}


def _load_open_aliases():
    """Merge optional user-defined open aliases without letting bad JSON break Chat."""
    websites = {}
    games = {}
    try:
        with open(OPEN_ALIASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, value in (data.get("websites") or {}).items():
                if isinstance(key, str) and isinstance(value, str) and re.match(r"^https?://[^\s]+$", value, re.IGNORECASE):
                    websites[key.strip().lower()] = value.strip()
            for key, value in (data.get("steam_games") or {}).items():
                if isinstance(key, str) and (str(value).isdigit()):
                    games[key.strip().lower()] = str(value).strip()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return websites, games


_CUSTOM_WEBSITES, _CUSTOM_STEAM_GAMES = _load_open_aliases()
WEBSITE_ALIASES.update(_CUSTOM_WEBSITES)
STEAM_GAME_IDS.update(_CUSTOM_STEAM_GAMES)


def _find_start_menu_shortcut(name):
    """Find an installed app/game shortcut without executing arbitrary commands."""
    if os.name != "nt":
        return None
    wanted = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()
    if not wanted:
        return None
    roots = [
        os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    ]
    exact = []
    partial = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for base, _, files in os.walk(root):
            for filename in files:
                if not filename.lower().endswith(".lnk"):
                    continue
                label = re.sub(r"[^a-z0-9]+", " ", filename[:-4].lower()).strip()
                if label == wanted:
                    exact.append(os.path.join(base, filename))
                elif wanted in label or label in wanted:
                    partial.append(os.path.join(base, filename))
    return (exact or partial or [None])[0]


def _open_start_menu_game_action(value):
    name = str(value).strip()
    steam_id = STEAM_GAME_IDS.get(name.lower())
    if steam_id:
        return _open_steam_game_action(steam_id)
    shortcut = _find_start_menu_shortcut(name)
    if shortcut:
        try:
            os.startfile(shortcut)
            return f"Launched game/app shortcut: {name}"
        except Exception as e:
            return f"Failed to launch '{name}': {e}"
    return (f"Could not find '{name}' in the Steam alias list or Windows Start Menu. "
            "Use 'open steam game <app_id>' for a Steam game or add the game to the Start Menu.")


def _open_steam_game_action(value):
    token = str(value).strip().lower()
    app_id = token if token.isdigit() else STEAM_GAME_IDS.get(token)
    if not app_id:
        return f"I don't have a Steam ID for '{value}'. Use: open steam game <app id>."
    try:
        os.startfile(f"steam://rungameid/{app_id}")
        return f"Launched Steam game {app_id}."
    except Exception as e:
        return f"Failed to launch Steam game {app_id}: {e}"


LAUNCHER_ALIASES = {
    "steam": "steam",
    "epic": "epic",
    "epic games": "epic",
    "ea": "ea",
    "ea app": "ea",
    "ubisoft": "ubisoft",
    "ubisoft connect": "ubisoft",
    "battle.net": "battle.net",
    "battlenet": "battle.net",
    "gog": "gog",
    "gog galaxy": "gog",
    "xbox": "xbox",
    "xbox app": "xbox",
    "riot": "riot",
    "riot client": "riot",
}


# Known games that commonly depend on a specific launcher. These hints are
# only used to decide which *known launcher* to open first; the actual game
# launch still goes through the safe local resolver below.
GAME_LAUNCHER_HINTS = {
    "fortnite": "epic",
    "fall guys": "epic",
    "rocket league": "epic",
    "valorant": "riot",
    "league of legends": "riot",
    "overwatch": "battle.net",
    "overwatch 2": "battle.net",
    "diablo iv": "battle.net",
    "world of warcraft": "battle.net",
    "starcraft ii": "battle.net",
    "assassin's creed": "ubisoft",
    "rainbow six siege": "ubisoft",
    "the division 2": "ubisoft",
    "ea sports fc": "ea",
    "apex legends": "steam",
}


def _launch_launcher(name):
    """Launch a known game launcher using fixed safe paths/URI handlers."""
    key = LAUNCHER_ALIASES.get(str(name).strip().lower())
    if not key:
        return False, f"Unknown launcher: {name}"
    try:
        if key == "steam":
            try:
                os.startfile("steam:")
                return True, "Steam"
            except Exception:
                ok, detail = _launch_app("steam.exe", [os.path.join("Steam", "steam.exe")])
                return (ok, "Steam") if ok else (False, f"Could not find Steam: {detail}")
        if key == "epic":
            ok, detail = _launch_app("EpicGamesLauncher.exe", [
                os.path.join("Epic Games", "Launcher", "Portal", "Binaries", "Win64", "EpicGamesLauncher.exe"),
            ])
            return (ok, "Epic Games") if ok else (False, f"Could not find Epic Games Launcher: {detail}")
        if key == "ea":
            ok, detail = _launch_app("EADesktop.exe", [
                os.path.join("Electronic Arts", "EA Desktop", "EA Desktop", "EADesktop.exe"),
                os.path.join("EA Desktop", "EA Desktop", "EADesktop.exe"),
            ])
            return (ok, "EA App") if ok else (False, f"Could not find EA App: {detail}")
        if key == "ubisoft":
            ok, detail = _launch_app("upc.exe", [os.path.join("Ubisoft", "Ubisoft Game Launcher", "upc.exe")])
            return (ok, "Ubisoft Connect") if ok else (False, f"Could not find Ubisoft Connect: {detail}")
        if key == "battle.net":
            ok, detail = _launch_app("Battle.net Launcher.exe", [
                os.path.join("Battle.net", "Battle.net Launcher.exe"),
                os.path.join("Blizzard Entertainment", "Battle.net", "Battle.net Launcher.exe"),
            ])
            return (ok, "Battle.net") if ok else (False, f"Could not find Battle.net: {detail}")
        if key == "gog":
            ok, detail = _launch_app("GalaxyClient.exe", [
                os.path.join("GOG Galaxy", "GalaxyClient.exe"),
                os.path.join("GOG Galaxy", "GalaxyClient", "GalaxyClient.exe"),
            ])
            return (ok, "GOG Galaxy") if ok else (False, f"Could not find GOG Galaxy: {detail}")
        if key == "xbox":
            try:
                os.startfile("ms-xbox:")
                return True, "Xbox App"
            except Exception:
                return False, "Could not open the Xbox app URI."
        if key == "riot":
            ok, detail = _launch_app("RiotClientServices.exe", [
                os.path.join("Riot Vanguard", "RiotClient", "RiotClientServices.exe"),
                os.path.join("Riot Games", "Riot Client", "RiotClientServices.exe"),
            ])
            return (ok, "Riot Client") if ok else (False, f"Could not find Riot Client: {detail}")
    except Exception as e:
        return False, f"Failed to launch {name}: {e}"
    return False, f"Unsupported launcher: {name}"


def _normalized_open_name(value):
    return re.sub(r"\s+", " ", str(value).strip().strip('"\'')).strip().lower()


def _open_path_action(value):
    expanded = os.path.expanduser(os.path.expandvars(str(value).strip().strip('"\'')))
    if not os.path.exists(expanded):
        return False, None
    try:
        os.startfile(expanded)
        label = "folder" if os.path.isdir(expanded) else "file/app"
        return True, f"Opened {label}: {expanded}"
    except Exception as e:
        return False, f"Failed to open '{expanded}': {e}"


def _launch_game_with_known_launcher(value):
    name = _normalized_open_name(value)
    launcher = GAME_LAUNCHER_HINTS.get(name)
    if not launcher:
        return False, None
    ok, launcher_result = _launch_launcher(launcher)
    if not ok:
        return False, launcher_result
    # Give the launcher a moment to register itself before checking for the
    # installed game shortcut/URI. This is intentionally modest so the UI
    # remains responsive while still helping slow-starting launchers.
    time.sleep(1.25)
    steam_id = STEAM_GAME_IDS.get(name)
    if steam_id and launcher == "steam":
        try:
            os.startfile(f"steam://rungameid/{steam_id}")
            return True, f"Opened {launcher_result}, then launched {value}."
        except Exception as e:
            return False, f"Opened {launcher_result}, but Steam could not launch '{value}': {e}"
    shortcut = _find_start_menu_shortcut(value)
    if shortcut:
        try:
            os.startfile(shortcut)
            return True, f"Opened {launcher_result}, then launched {value}."
        except Exception as e:
            return False, f"Opened {launcher_result}, but could not launch '{value}': {e}"
    return False, f"Opened {launcher_result}, but no Start Menu shortcut was found for '{value}'."


def _launch_open_target(target):
    """Resolve a game/app/website/path without executing arbitrary shell commands."""
    value = str(target).strip().strip('"\'')
    if not value:
        return False, "No game or app was provided."
    name = _normalized_open_name(value)

    # Launcher names are first-class open targets.
    if name in LAUNCHER_ALIASES:
        ok, result = _launch_launcher(name)
        return ok, result

    # Explicit website URL or a configured website alias.
    if re.match(r"^https?://[^\s]+$", value, re.IGNORECASE) or re.match(r"^(?:www\.)[^\s]+$", value, re.IGNORECASE):
        return True, _open_url_action(value)
    if name in WEBSITE_ALIASES:
        return True, _open_url_action(WEBSITE_ALIASES[name])

    # Common registered desktop apps.
    if name in {"spotify", "discord", "steam", "chrome", "edge", "firefox"}:
        result = _launch_registered_app(name)
        return not result.lower().startswith(("could not", "failed", "unsupported")), result

    # Known Steam titles can launch directly, which is reliable even without
    # a Start Menu shortcut.
    steam_id = STEAM_GAME_IDS.get(name)
    if steam_id:
        try:
            os.startfile(f"steam://rungameid/{steam_id}")
            return True, f"Launched Steam game: {value}"
        except Exception as e:
            return False, f"Failed to launch Steam game '{value}': {e}"

    # If we know which launcher normally owns the title, open the launcher
    # first and then try the local installed-game shortcut.
    if name in GAME_LAUNCHER_HINTS:
        ok, result = _launch_game_with_known_launcher(value)
        if ok or result:
            return ok, result

    # Existing path/file/folder.
    ok, result = _open_path_action(value)
    if ok:
        return True, result
    if result:
        return False, result

    # Windows Start Menu is the safest generic resolver for installed apps
    # and games because the shortcut has already been registered by the OS or
    # installer.
    shortcut = _find_start_menu_shortcut(value)
    if shortcut:
        try:
            os.startfile(shortcut)
            return True, f"Launched Start Menu shortcut: {value}"
        except Exception as e:
            return False, f"Failed to launch '{value}': {e}"

    return False, (f"I couldn't find '{value}' as a website, known app, game, "
                    "launcher, file, folder, or Windows Start Menu shortcut. "
                    "Try 'open <name>', or add a custom alias to data/open_aliases.json.")


def _open_any_target_action(value):
    """Handle natural 'open X' / 'launch X' input, including launcher chains."""
    text = str(value).strip()
    # Explicit chain forms: 'open steam then terraria' / 'open epic and fortnite'.
    chain = re.match(r"^(.*?)\s+(?:then|and)\s+(?:game\s+|app\s+)?(.+)$", text, re.IGNORECASE)
    if chain:
        first, second = chain.group(1).strip(), chain.group(2).strip()
        if _normalized_open_name(first).startswith("launcher "):
            first = re.sub(r"^launcher\s+", "", first, flags=re.IGNORECASE).strip()
        if _normalized_open_name(first) in LAUNCHER_ALIASES:
            return _open_launcher_chain_action(first, second)

    # 'open Fortnite' can infer a known launcher.
    if _normalized_open_name(text) in GAME_LAUNCHER_HINTS:
        return _open_launcher_chain_action(GAME_LAUNCHER_HINTS[_normalized_open_name(text)], text)

    return_result = _launch_open_target(text)
    return return_result[1]


def _open_launcher_chain_action(launcher, target):
    """Open a launcher first, wait briefly for it to initialize, then open a game/app."""
    ok, launcher_result = _launch_launcher(launcher)
    if not ok:
        return launcher_result
    # Give the launcher a moment to register its protocol/process before the target call.
    time.sleep(1.5)
    target_ok, target_result = _launch_open_target(target)
    if not target_ok:
        return f"Opened {launcher_result}, but could not launch '{target}': {target_result}"
    return f"Opened {launcher_result}, then {target_result}"


def _launch_registered_app(app):
    """Launch a safe, allow-listed set of common Windows apps."""
    app = app.lower()
    try:
        if app == "spotify":
            ok, detail = _launch_app("Spotify.exe", [
                os.path.join("Spotify", "Spotify.exe"),
                os.path.join("Spotify", "Spotify.exe"),
            ])
            if ok:
                return "Launched Spotify."
            try:
                os.startfile("spotify:")
                return "Launched Spotify."
            except Exception:
                return f"Could not find Spotify: {detail}"
        if app == "discord":
            local = os.environ.get("LOCALAPPDATA", "")
            update = os.path.join(local, "Discord", "Update.exe")
            if os.path.isfile(update):
                subprocess.Popen([update, "--processStart", "Discord.exe"], **hidden_subprocess_kwargs())
                return "Launched Discord."
            ok, detail = _launch_app("Discord.exe", [
                os.path.join("Discord", "app-*/Discord.exe"),
            ])
            return "Launched Discord." if ok else f"Could not find Discord: {detail}"
        if app == "steam":
            try:
                os.startfile("steam:")
                return "Launched Steam."
            except Exception:
                ok, detail = _launch_app("steam.exe", [os.path.join("Steam", "steam.exe")])
                return "Launched Steam." if ok else f"Could not find Steam: {detail}"
        if app == "chrome":
            ok, detail = _launch_app("chrome.exe", [
                os.path.join("Google", "Chrome", "Application", "chrome.exe"),
            ])
            return "Launched Chrome." if ok else f"Could not find Chrome: {detail}"
        if app == "edge":
            ok, detail = _launch_app("msedge.exe", [
                os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
            ])
            return "Launched Edge." if ok else f"Could not find Edge: {detail}"
        if app == "firefox":
            ok, detail = _launch_app("firefox.exe", [
                os.path.join("Mozilla Firefox", "firefox.exe"),
            ])
            return "Launched Firefox." if ok else f"Could not find Firefox: {detail}"
        return f"Unsupported application: {app}."
    except Exception as e:
        return f"Failed to launch {app.title()}: {e}"


def _open_website_alias_action(alias):
    key = str(alias).strip().lower()
    url = WEBSITE_ALIASES.get(key)
    if not url:
        return f"Unknown website shortcut: {alias}."
    return _open_url_action(url)


ACTION_DEFINITIONS = [
    {
        "pattern": re.compile(r"^open\s+notepad$", re.IGNORECASE),
        "describe": lambda m: "Open Notepad",
        "execute": lambda m: _launch_executable("notepad.exe"),
    },
    {
        "pattern": re.compile(r"^open\s+calculator$", re.IGNORECASE),
        "describe": lambda m: "Open Calculator",
        "execute": lambda m: _launch_executable("calc.exe"),
    },
    {
        "pattern": re.compile(r"^open\s+task\s*manager$", re.IGNORECASE),
        "describe": lambda m: "Open Task Manager",
        "execute": lambda m: _launch_executable("taskmgr.exe"),
    },
    {
        "pattern": re.compile(r"^open\s+settings$", re.IGNORECASE),
        "describe": lambda m: "Open Windows Settings",
        "execute": lambda m: _open_settings(),
    },
    {
        "pattern": re.compile(r"^open\s+(spotify|discord|steam|chrome|edge|firefox)$", re.IGNORECASE),
        "describe": lambda m: f"Open {m.group(1).title()}",
        "execute": lambda m: _launch_registered_app(m.group(1)),
    },
    {
        "pattern": re.compile(r"^open\s+(?:website|site)\s+(.+)$", re.IGNORECASE),
        "describe": lambda m: f"Open website: {m.group(1).strip()}",
        "execute": lambda m: _open_url_action(m.group(1).strip()),
    },
    {
        "pattern": re.compile(r"^open\s+(google|youtube|github|reddit|twitch|discord web|spotify web|gmail|outlook|chatgpt)$", re.IGNORECASE),
        "describe": lambda m: f"Open website: {m.group(1)}",
        "execute": lambda m: _open_website_alias_action(m.group(1)),
    },
    {
        "pattern": re.compile(r"^open\s+https?://[^\s]+$", re.IGNORECASE),
        "describe": lambda m: f"Open website: {m.group(0)[5:].strip()}",
        "execute": lambda m: _open_url_action(m.group(0)[5:].strip()),
    },
    {
        "pattern": re.compile(r"^open\s+(?:www\.)[^\s]+$", re.IGNORECASE),
        "describe": lambda m: f"Open website: {m.group(0)[5:].strip()}",
        "execute": lambda m: _open_url_action(m.group(0)[5:].strip()),
    },
    {
        "pattern": re.compile(r"^open\s+[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?$", re.IGNORECASE),
        "describe": lambda m: f"Open website: {m.group(0)[5:].strip()}",
        "execute": lambda m: _open_url_action(m.group(0)[5:].strip()),
    },
    {
        "pattern": re.compile(r"^open\s+(?:steam\s+game|steam\s+app)\s+(\d+)$", re.IGNORECASE),
        "describe": lambda m: f"Launch Steam game/app ID {m.group(1)}",
        "execute": lambda m: _open_steam_game_action(m.group(1)),
    },
    {
        "pattern": re.compile(r"^(?:open|launch)\s+(?:launcher\s+)?(steam|epic|epic games|ea|ea app|ubisoft|ubisoft connect|battle\.net|battlenet|gog|gog galaxy|xbox|xbox app|riot|riot client)$", re.IGNORECASE),
        "describe": lambda m: f"Open {m.group(1)} launcher",
        "execute": lambda m: _launch_launcher(m.group(1))[1],
    },
    {
        "pattern": re.compile(r"^(?:open|launch)\s+(?:launcher\s+)?(steam|epic|epic games|ea|ea app|ubisoft|ubisoft connect|battle\.net|battlenet|gog|gog galaxy|xbox|xbox app|riot|riot client)\s+(?:then|and)\s+(?:game\s+|app\s+)?(.+)$", re.IGNORECASE),
        "describe": lambda m: f"Open {m.group(1)} launcher, then {m.group(2).strip()}",
        "execute": lambda m: _open_launcher_chain_action(m.group(1), m.group(2).strip()),
    },
    {
        "pattern": re.compile(r"^(?:open|launch)\s+game\s+(.+?)\s+(?:in|on|with)\s+(steam|epic|epic games|ea|ea app|ubisoft|ubisoft connect|battle\.net|battlenet|gog|gog galaxy|xbox|xbox app|riot|riot client)$", re.IGNORECASE),
        "describe": lambda m: f"Open {m.group(2)} launcher, then {m.group(1).strip()}",
        "execute": lambda m: _open_launcher_chain_action(m.group(2), m.group(1).strip()),
    },
    {
        "pattern": re.compile(r"^open\s+game\s+(.+)$", re.IGNORECASE),
        "describe": lambda m: f"Launch game: {m.group(1).strip()}",
        "execute": lambda m: _open_start_menu_game_action(m.group(1).strip()),
    },
    {
        "pattern": re.compile(r"^(?:open|launch)\s+(?:launcher\s+)?(.+?)(?:\s+(?:then|and)\s+(?:game\s+|app\s+)?(.+))$", re.IGNORECASE),
        "describe": lambda m: f"Open {m.group(1).strip()}, then {m.group(2).strip()}",
        "execute": lambda m: _open_any_target_action(f"{m.group(1).strip()} then {m.group(2).strip()}"),
    },
    {
        "pattern": re.compile(r"^(?:open|launch)\s+(.+)$", re.IGNORECASE),
        "describe": lambda m: f"Open {m.group(1).strip()}",
        "execute": lambda m: _open_any_target_action(m.group(1).strip()),
    },
    {
        "pattern": re.compile(r"^open\s+folder\s+(.+)$", re.IGNORECASE),
        "describe": lambda m: f"Open folder: {m.group(1).strip()}",
        "execute": lambda m: _open_folder_action(m.group(1).strip()),
    },
    {
        "pattern": re.compile(r"^kill\s+process\s+(\d+)$", re.IGNORECASE),
        "describe": lambda m: f"Terminate process PID {m.group(1)}",
        "execute": lambda m: _kill_process_action(int(m.group(1))),
    },
]

APPROVE_WORDS = {"approve", "yes", "confirm", "y"}
CANCEL_WORDS = {"cancel", "no", "n", "deny"}


def match_action(text):
    normalized = text.strip()
    for action in ACTION_DEFINITIONS:
        m = action["pattern"].match(normalized)
        if m:
            return action, m
    return None, None


# ────────────────────────────────────────────
# QUERY ROUTING
# ────────────────────────────────────────────

SECURITY_TERMS = [
    "virus", "malware", "antivirus", "defender", "infected", "infection",
    "hacked", "security status", "firewall",
]

HARDWARE_TERMS = [
    "specs", "specifications", "hardware",
    "cpu", "processor",
    "gpu", "graphics card", "graphics",
    "ram", "memory", "ddr3", "ddr4", "ddr5",
    "motherboard", "mainboard",
    "bios", "uefi",
    "windows version", "windows build",
    "storage", "disk", "drive", "ssd", "nvme", "hdd",
    "how much ram", "what ram", "what gpu", "what cpu",
]

PERFORMANCE_TERMS = [
    "cpu usage", "cpu load", "cpu temp", "cpu temperature",
    "ram usage", "memory usage",
    "gpu usage", "gpu temperature", "gpu temp",
    "gpu power", "power draw",
    "disk usage", "storage usage",
    "network", "internet adapter",
    "running processes", "processes",
    "uptime", "last boot",
    "performance", "performance right now",
]


def contains_term(text, terms):
    text = text.lower()
    return any(term in text for term in terms)


# Every explicit command, plus the plain-English phrases and short
# shortcuts that should resolve to it. All of these are deterministic —
# no AI involved in deciding which one fires.
COMMAND_ALIASES = {
    "specs": {"specs", "pc specs", "pc info", "hardware", "hardware specs", "system specs", "s"},
    "diagnostics": {
        "diagnostics", "diagnostic", "diag", "performance", "pc performance", "d",
    },
    "cpu": {"cpu", "c"},
    "gpu": {"gpu", "g"},
    "ram": {"ram", "memory", "r"},
    "storage": {"storage", "disks", "drives", "st"},
    "network": {"network", "net", "n"},
    "processes": {"processes", "process", "procs", "running processes", "p"},
    "check": {"check", "healthcheck", "pc health", "pc check", "k"},
    "security": {"security", "sec", "threats", "virus check", "av", "v"},
    "help": {"help", "commands", "?", "h"},
}

# Fixed order used for numbered-menu selection in /help — index 0 is "1".
COMMAND_ORDER = [
    "specs", "diagnostics", "cpu", "gpu", "ram",
    "storage", "network", "processes", "check", "security", "help",
]

COMMAND_HANDLERS = {
    "specs": format_pc_specs,
    "diagnostics": format_diagnostics,
    "cpu": format_cpu,
    "gpu": format_gpu,
    "ram": format_ram,
    "storage": format_storage,
    "network": format_network,
    "processes": format_processes,
    "check": format_health_check,
    "security": format_security,
    "help": format_help,
}

# Cutoff is intentionally strict — high enough that "cpu" and "gpu" (a
# single-character difference) never get confused for each other, but
# still catches ordinary typos like "stroage" or "diagnostcs".
FUZZY_MATCH_CUTOFF = 0.75


def fuzzy_match_command(normalized_text):
    """Typo-tolerant fallback. Only ever called on short, command-shaped
    input — never on ordinary sentences — to avoid hijacking real
    questions meant for the AI."""
    if not (3 <= len(normalized_text) <= 20):
        return None
    if len(normalized_text.split()) > 2:
        return None

    alias_to_command = {
        alias: command
        for command, aliases in COMMAND_ALIASES.items()
        for alias in aliases
        if len(alias) >= 3  # skip 1-2 letter shortcuts; too easy to false-match
    }

    matches = difflib.get_close_matches(
        normalized_text, alias_to_command.keys(), n=1, cutoff=FUZZY_MATCH_CUTOFF
    )
    return alias_to_command[matches[0]] if matches else None


def match_command(text):
    """Resolve user input to a command name via, in order: exact
    alias/shortcut match, numbered-menu selection, then a typo-tolerant
    fuzzy match. Returns None if nothing reasonably matches."""
    normalized = text.lower().strip().lstrip("/")

    if not normalized:
        return None

    # 1. Exact alias or shortcut match.
    for command, aliases in COMMAND_ALIASES.items():
        if normalized in aliases:
            return command

    # 2. Numbered menu selection, e.g. typing "3" after /help.
    if normalized.isdigit():
        index = int(normalized) - 1
        if 0 <= index < len(COMMAND_ORDER):
            return COMMAND_ORDER[index]
        return None  # a real number that isn't a valid choice

    # 3. Typo-tolerant fuzzy match.
    return fuzzy_match_command(normalized)


def route_user_input(user_input):
    # Exact commands (typed with or without a leading "/") are always
    # deterministic and take priority.
    command = match_command(user_input)
    if command:
        return command

    # Hardware facts mentioned in natural language must never be
    # answered by Qwen.
    if contains_term(user_input, HARDWARE_TERMS):
        return "specs"

    # Live measurements mentioned in natural language must never be
    # answered by Qwen.
    if contains_term(user_input, PERFORMANCE_TERMS):
        return "diagnostics"

    # Security questions in natural language get the real overview,
    # not a Qwen guess.
    if contains_term(user_input, SECURITY_TERMS):
        return "security"

    return "ai"


# ────────────────────────────────────────────
# AI ASSISTANT
# ────────────────────────────────────────────

assistant = Agent(
    name="My Personal AI",
    model=model,
    instructions=(
        "You are a local PC and project assistant. "
        "Hardware and performance facts are handled outside the AI. "
        "Do not invent computer specifications or measurements. "
        "You are used for explanations, troubleshooting, recommendations, "
        "project help, Minecraft help, and general questions. "
        "If factual PC information is supplied in a user message, treat it "
        "as authoritative context and do not alter its numbers or models. "
        "Keep facts separate from analysis. "
        "Do not claim to have performed an action unless a tool actually "
        "performed it. "
        "Never modify, delete, install, or execute files without explicit "
        "user approval. "
        "The host application may supply LIVE WEB RESEARCH blocks retrieved immediately before your response. "
        "When such a block is present, you DO have live web context even though the local model itself may not have direct network access; never claim that you cannot browse unless the host reports retrieval failure. "
        "Use the live sources when relevant and identify uncertainty when sources conflict or are incomplete. "
        "For current questions, prefer the live sources over stale model memory and include a concise Sources section with the retrieved URLs. "
        "Never repeat a generic training-cutoff disclaimer when live web context is present."
    ),
)


evolution_agent = Agent(
    name="MyLocalAI Evolution Engineer",
    model=model,
    instructions=(
        "You are the local self-improvement engineer for MyLocalAI. "
        "You only propose small, evidence-based improvements to the files and paths explicitly allowed by the prompt. "
        "Return JSON exactly as requested. Never include executable shell commands, secrets, credentials, destructive operations, or persistence mechanisms. "
        "Prefer measurable intelligence, reliability, knowledge, tool-use, and efficiency improvements over feature expansion. " + mission_context()
    ),
)


# ────────────────────────────────────────────
# SHARED MESSAGE HANDLING
# ────────────────────────────────────────────
# handle_message() is the single place that decides what happens to a
# message. Both the terminal CLI (run_cli, below) and the GUI
# (gui.py) call this exact function against the same Session object,
# so they can never drift apart in behavior — a command, a typo, a
# reset, an action approval, all resolve identically in either
# interface.


# Runner/session calls are serialized at the engine boundary. The GUI already
# prevents duplicate sends, but command pages and background actions can call
# this module concurrently.
import threading
_ENGINE_LOCK = threading.Lock()

MAX_HISTORY_ITEMS = 40
RESET_ALIASES = {"reset", "clear", "new", "new chat", "restart"}
# NOTE: "forget" is intentionally NOT a reset alias. "forget <text>" is a
# distinct memory command (see FORGET_PATTERN) that deletes matching saved
# facts; bare "forget" used to alias to a full conversation reset, which
# meant the two "forget" behaviors silently collided depending on whether
# the user typed an argument.


def is_reset_command(text):
    normalized = text.lower().strip().lstrip("/")
    return normalized in RESET_ALIASES


class Session:
    """Everything that needs to persist between messages in one run of
    the assistant. Conversation memory resets with /reset; a pending
    action is cleared once approved or cancelled. Personal
    memory/notes/tasks/projects live on disk instead (see
    PERSONAL MEMORY section) so those survive even after a restart."""

    def __init__(self):
        self.conversation_history = []
        self.pending_action = None  # {"description": str, "run": callable}
        self.last_interaction_id = None
        self.last_learned_trigger = None


def _build_agent_input(user_input, session):
    """Prior turns are replayed so the AI has real conversation memory
    instead of treating every message as a fresh start. On a brand-new
    session (first message, or right after /reset), saved personal
    facts are seeded in once so the AI has that context without
    re-sending it every turn."""
    if session.conversation_history:
        return session.conversation_history + [{"role": "user", "content": user_input}]

    facts = _load_json_list("memory_facts.json")
    if not facts:
        return user_input

    fact_lines = "\n".join(f"- {f['text']}" for f in facts[-15:])
    return [
        {
            "role": "user",
            "content": (
                "Background context to keep in mind about me (I'm not saying "
                "this right now, it's just standing context):\n" + fact_lines
            ),
        },
        {"role": "assistant", "content": "Got it, I'll keep that in mind."},
        {"role": "user", "content": user_input},
    ]


def _legacy_handle_message(user_input, session):
    """Process one message. Mutates `session` in place (conversation
    history, pending action). Returns (source, text):

      "system"         — informational, no special styling needed
      "pc"              — deterministic Python-collected data
      "ai"              — Qwen's response
      "action_pending"  — an action needs 'approve' or 'cancel' next
      "action_result"   — an action just ran (or was refused)
      "exit"            — close the program
    """
    user_input = user_input.strip()

    if not user_input:
        return ("system", "")

    if user_input.lower() in ("exit", "quit"):
        return ("exit", "Goodbye!")

    # A pending action blocks everything else until it's resolved, so
    # a stray follow-up message can never slip an action through.
    if session.pending_action:
        normalized = user_input.strip().lower()
        if normalized in APPROVE_WORDS:
            action = session.pending_action
            session.pending_action = None
            result = action["run"]()
            log_activity(f"APPROVED: {action['description']} -> {result}")
            return ("action_result", result)
        if normalized in CANCEL_WORDS:
            action = session.pending_action
            session.pending_action = None
            log_activity(f"CANCELLED: {action['description']}")
            return ("system", f"Cancelled: {action['description']}")
        return (
            "action_pending",
            f"An action is still waiting for approval: {session.pending_action['description']}\n"
            "Type 'approve' or 'cancel' first.",
        )

    if is_reset_command(user_input):
        session.conversation_history = []
        return ("system", "AI conversation memory cleared. Hardware/performance commands are unaffected.")

    memory_response = handle_memory_command(user_input)
    if memory_response is not None:
        return ("system", memory_response)

    if user_input.strip().lower() in ("list files",):
        return ("pc", format_list_files(None))
    list_files_match = LIST_FILES_PATTERN.match(user_input)
    if list_files_match:
        return ("pc", format_list_files(list_files_match.group(1)))

    if user_input.strip().lower() in ("activity log", "log"):
        return ("pc", format_activity_log())

    scan_match = re.match(r"^/?(scan|filescan)\s+(.+)$", user_input, re.IGNORECASE)
    if scan_match:
        return ("pc", format_scan(scan_match.group(2)))
    if user_input.strip().lower().lstrip("/") in ("scan", "filescan"):
        return ("system", 'Usage: scan <folder path>  (e.g. "scan C:\\Users\\Me\\Downloads")')

    # System actions require approval — never run immediately, and
    # never triggered by anything Qwen generates, only by the user's
    # own literal text matching a fixed pattern.
    action, match = match_action(user_input)
    if action:
        description = action["describe"](match)
        session.pending_action = {
            "description": description,
            "run": lambda: action["execute"](match),
            "learned_trigger": getattr(session, "last_learned_trigger", None),
        }
        return (
            "action_pending",
            f"CONFIRM: {description}\napprove / cancel",
        )

    route = route_user_input(user_input)
    if route in COMMAND_HANDLERS:
        return ("pc", COMMAND_HANDLERS[route]())

    agent_input = _build_agent_input(user_input, session)
    with _ENGINE_LOCK:
        result = Runner.run_sync(assistant, agent_input)

    session.conversation_history = result.to_input_list()
    if len(session.conversation_history) > MAX_HISTORY_ITEMS:
        session.conversation_history = session.conversation_history[-MAX_HISTORY_ITEMS:]

    return ("ai", result.final_output)

# ────────────────────────────────────────────
# MYLOCALAI CORE v1 — COMPATIBILITY BRIDGE
# ────────────────────────────────────────────
# The existing public API remains unchanged for gui.py and CLI.
# Core owns routing; this adapter exposes proven legacy operations.

class _LegacyCoreAdapter:
    def __init__(self, session):
        self.session = session

    def learning_data_dir(self):
        return DATA_DIR

    def learning_store(self):
        return _LEARNING_STORE

    def matches_learning_command(self, text):
        return matches_learning_command(text)

    def learning_command(self, text):
        return handle_learning_command(text)

    def match_learned_rule(self, text):
        return match_learned_rule(text)

    def execute_learned_rule(self, command):
        return execute_learned_rule(self.session, command)

    def append_learning_context(self, model_input, learning_context):
        prefix = {"role": "system", "content": "Local learning context:\n" + learning_context}
        if isinstance(model_input, list):
            return [prefix] + model_input
        return [prefix, {"role": "user", "content": str(model_input)}]

    def learning_signature(self, text, intent):
        """Return a stable action signature for automatic pattern learning."""
        raw = str(text or "").strip()
        if intent in {"SYSTEM_ACTION", "LEARNED_COMMAND"}:
            action, match = match_action(raw)
            if action:
                try:
                    return "action:" + re.sub(r"\s+", " ", str(action["describe"](match)).strip().lower())
                except Exception:
                    pass
        route = route_user_input(raw)
        if route and route != "ai":
            return "command:" + route
        return ""

    def is_reset_command(self, text):
        return is_reset_command(text)

    def has_pending_action(self):
        return self.session.pending_action is not None

    def resolve_pending_action(self, text):
        normalized = text.strip().lower()
        if normalized in APPROVE_WORDS:
            action = self.session.pending_action
            self.session.pending_action = None
            result = action["run"]()
            learned_trigger = action.get("learned_trigger")
            if learned_trigger:
                _LEARNING_STORE.lesson_feedback(learned_trigger, True)
            # Approved direct actions are the strongest signal for automatic
            # learning. Capture the original command, not the word "approve".
            learning_text = action.get("learning_text")
            if learning_text:
                try:
                    signature = self.learning_signature(learning_text, action.get("learning_intent", "SYSTEM_ACTION"))
                    if signature:
                        _LEARNING_STORE.observe(learning_text, signature, True)
                except Exception:
                    pass
            log_activity(f"APPROVED: {action['description']} -> {result}")
            return ("action_result", result)
        if normalized in CANCEL_WORDS:
            action = self.session.pending_action
            self.session.pending_action = None
            learned_trigger = action.get("learned_trigger")
            if learned_trigger:
                _LEARNING_STORE.lesson_feedback(learned_trigger, False)
            log_activity(f"CANCELLED: {action['description']}")
            return ("system", f"Cancelled: {action['description']}")
        return (
            "action_pending",
            f"An action is still waiting for approval: {self.session.pending_action['description']}\n"
            "Type 'approve' or 'cancel' first.",
        )

    def matches_memory_command(self, text):
        return matches_memory_command(text)

    def memory_command(self, text):
        return handle_memory_command(text)

    def matches_file_command(self, text):
        low = text.strip().lower()
        return low in ("list files",) or LIST_FILES_PATTERN.match(text) is not None or low in ("activity log", "log")

    def execute_file_command(self, text):
        low = text.strip().lower()
        if low == "list files":
            return ("pc", format_list_files(None))
        match = LIST_FILES_PATTERN.match(text)
        if match:
            return ("pc", format_list_files(match.group(1)))
        if low in ("activity log", "log"):
            return ("pc", format_activity_log())
        return ("system", "No file command matched.")

    def matches_scan_command(self, text):
        low = text.strip().lower().lstrip("/")
        return re.match(r"^(scan|filescan)\s+(.+)$", text, re.IGNORECASE) is not None or low in ("scan", "filescan")

    def execute_scan_command(self, text):
        match = re.match(r"^/?(scan|filescan)\s+(.+)$", text, re.IGNORECASE)
        if match:
            return ("pc", format_scan(match.group(2)))
        return ("system", 'Usage: scan <folder path>  (e.g. "scan C:\\Users\\Me\\Downloads")')

    def matches_action(self, text):
        action, match = match_action(text)
        return action is not None

    def create_pending_action(self, text):
        action, match = match_action(text)
        description = action["describe"](match)
        self.session.pending_action = {
            "description": description,
            "run": lambda: action["execute"](match),
            "learning_text": str(text).strip(),
            "learning_intent": "SYSTEM_ACTION",
        }
        return description

    def route_command(self, text):
        route = route_user_input(text)
        return route if route in COMMAND_HANDLERS else None

    def execute_command(self, route):
        return COMMAND_HANDLERS[route]()

    def build_agent_input(self, text):
        return _build_agent_input(text, self.session)

    def web_context(self, text):
        """Automatically research the user's message before model generation.

        This is the normal live-web path: the model does not decide whether it
        has internet access; the host application performs retrieval first and
        injects the fresh results as reference context.
        """
        global _LAST_LIVE_WEB
        if not _INTERNET_ENABLED or not _AUTO_WEB_SEARCH:
            return ''
        raw = str(text or '').strip()
        if not should_auto_search(raw, _INTERNET_ENABLED, _AUTO_WEB_SEARCH):
            return ''
        try:
            query = compact_live_query(raw)
            result = live_research(query, limit=5, fetch_pages=4)
            if not result.results:
                _LAST_LIVE_WEB = {'query': query, 'status': 'no-results', 'elapsed_ms': result.elapsed_ms}
                return ''
            _LAST_LIVE_WEB = {
                'query': query,
                'status': 'ok',
                'provider': result.provider,
                'results': len(result.results),
                'fetched': result.fetched,
                'elapsed_ms': result.elapsed_ms,
            }
            return (
                result.context()
                + '\n\nIMPORTANT: MyLocalAI itself performed this web retrieval immediately before the answer. '
                'Do not say you cannot access the internet. Use the supplied live sources when relevant, '
                'and clearly distinguish them from older/model knowledge. If the live sources do not answer '
                'the question, say that rather than inventing a result.'
            )
        except Exception as exc:
            _LAST_LIVE_WEB = {'status': 'error', 'error': type(exc).__name__}
            return (
                'LIVE WEB RESEARCH ATTEMPTED just now, but retrieval failed: ' + type(exc).__name__ + '. '
                'Do not claim the model has internet access; instead say MyLocalAI\'s live retrieval layer is temporarily unavailable if the user asked for current information.'
            )


    def screen_context(self):
        try:
            status=_SCREEN_OBSERVER.status()
            if not status.get('enabled'):
                return ''
            rows=_SCREEN_OBSERVER.recent_observations(4)
            if not rows:
                return ''
            parts=['Current local screen context (private, use only for workflow understanding):']
            for r in rows:
                text=str(r.get('ocr_text') or '').strip()
                if text:
                    parts.append(f'App: {r.get("process")}\nWindow: {r.get("title")}\nVisible context: {text[:1200]}')
                else:
                    parts.append(f'App: {r.get("process")}\nWindow: {r.get("title")}')
            return '\n\n'.join(parts)[:7000]
        except Exception:
            return ''

    def trim_history(self, session):
        if len(session.conversation_history) > MAX_HISTORY_ITEMS:
            session.conversation_history = session.conversation_history[-MAX_HISTORY_ITEMS:]


    def matches_external_command(self, text):
        return matches_external_command(text)

    def external_command(self, text):
        return handle_external_command(text)

    def knowledge_context(self, text):
        return external_knowledge_context(text)

def _build_core(session):
    """Construct Core with dependency injection; avoids GUI API changes."""
    from core.orchestrator import CoreOrchestrator
    from models.ollama_backend import LegacyOllamaBackend
    from tools.registry import ToolRegistry

    adapter = _LegacyCoreAdapter(session)
    backend = LegacyOllamaBackend(Runner, assistant, _ENGINE_LOCK)
    registry = ToolRegistry()
    for name, handler in COMMAND_HANDLERS.items():
        registry.register(name, handler, description=f"Legacy PC command: {name}")
    return CoreOrchestrator(adapter, backend, registry)




def _evolution_diagnosis():
    """Build evidence for the self-improvement loop from logs, screen habits, and current research."""
    screen_text = ''
    try:
        rows = _SCREEN_OBSERVER.recent_observations(6)
        bits=[]
        for r in rows:
            bits.append(f"{r.get('process','')} | {r.get('title','')} | {str(r.get('ocr_text') or '')[:700]}")
        screen_text='\n'.join(bits)
    except Exception:
        pass
    knowledge_text=''
    try:
        knowledge_text=_KNOWLEDGE_STORE.context('AI coding agents efficiency self improvement tool use orchestration August 2026', 6)
    except Exception:
        pass
    # When internet is available, refresh just the small evolution-relevant research slice.
    if _INTERNET_ENABLED:
        try:
            live = live_research('latest AI coding agent efficiency self improvement computer use research August 2026', limit=4, fetch_pages=3)
            if live.results:
                knowledge_text = (knowledge_text + '\n\n' + live.context(max_chars=7000))[-12000:]
        except Exception:
            pass
    diagnosis = _SELF_EVOLUTION.analyze(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),'logs','mylocalai.log'),
        screen_context=screen_text,
        knowledge_context=knowledge_text,
    )
    try:
        from services.objectives import snapshot
        diagnosis["objective_snapshot"] = snapshot(_LEARNING_STORE)
    except Exception:
        diagnosis["objective_snapshot"] = {}
    return diagnosis


def _format_evolution_result(result):
    if not isinstance(result, dict):
        return str(result)
    status=str(result.get('status','unknown'))
    lines=[f"Self-evolution: {status}"]
    if result.get('summary'):
        lines.append(str(result['summary']))
    if result.get('message'):
        lines.append(str(result['message']))
    if result.get('objective_comparison'):
        oc=result['objective_comparison']
        lines.append(f"Measured objective change: {float(oc.get('delta', 0)):+.2f} ({float(oc.get('percent', 0)):+.2f}%)")
    proposal=result.get('proposal') or {}
    if proposal.get('id'):
        lines.append(f"Proposal: {proposal['id']}")
    if proposal.get('paths'):
        lines.append('Files: ' + ', '.join(proposal['paths']))
    if proposal.get('applyable_paths'):
        lines.append('Auto-eligible: ' + ', '.join(proposal['applyable_paths']))
    if status == 'validated_pending':
        lines.append(f"Review with: self approve {proposal.get('id')}")
    if status == 'applied':
        lines.append('Restart MyLocalAI so the new source code is loaded.')
    return '\n'.join(lines)


def _evolution_generate(prompt):
    """Use the local model as a patch generator, with no conversation history."""
    with _ENGINE_LOCK:
        result = Runner.run_sync(evolution_agent, prompt)
    return result.final_output

_SELF_EVOLUTION.set_generator(_evolution_generate)
# Autonomous research is a background enrichment service. It searches public
# internet sources periodically when enabled and never blocks model generation.
try:
    if _INTERNET_ENABLED and os.environ.get('MYLOCALAI_AUTONOMOUS_LAB') != '1':
        _RESEARCH_MANAGER.start_autopilot()
except Exception:
    logging.getLogger(__name__).exception('Could not start research autopilot')


def screen_evolution_tick():
    """Turn repeated observed app transitions into reviewable learning candidates."""
    try:
        for row in _SCREEN_OBSERVER.transitions():
            a=re.sub(r'\.exe$','',str(row['from']),flags=re.I)
            b=re.sub(r'\.exe$','',str(row['to']),flags=re.I)
            if re.search(r'(password|bank|auth|wallet|credential)',f'{a} {b}',re.I): continue
            trigger=f'{a} then {b}'
            _LEARNING_STORE.add_observer_candidate(trigger,[f'open {a}',f'open {b}'],int(row['count']),'screen_workflow')
    except Exception: pass



def _background_evolution_cycle():
    try:
        diagnosis=_evolution_diagnosis()
        _SELF_EVOLUTION.evolve(diagnosis, autonomous=True)
    except Exception:
        logging.getLogger(__name__).exception('Background self-evolution failed')

def handle_message(user_input, session):
    """
    Public compatibility API.

    GUI and CLI continue using this function, while routing now flows
    through MyLocalAI Core v1.

    High-priority deterministic controls are intercepted here before Core
    so critical local controls cannot accidentally fall through to the model.
    """
    text = (user_input or '').strip()
    if matches_external_command(text):
        try:
            return handle_external_command(text)
        except Exception:
            logging.getLogger(__name__).exception('External command failed')
            return ('error', 'MyLocalAI could not complete that control command. Check logs\\mylocalai.log.')

    core = _build_core(session)
    response = core.handle(user_input, session)
    try:
        if _SELF_EVOLUTION.note_interaction(response.source not in {'error'}):
            threading.Thread(target=_background_evolution_cycle, daemon=True, name='MyLocalAI-Evolution').start()
    except Exception:
        pass
    return response.as_legacy_tuple()


def record_feedback(session, score, note=""):
    return feedback_learning(session, score, note)

def get_learning_stats():
    return learning_stats_text()

# ────────────────────────────────────────────
# TERMINAL CLI
# ────────────────────────────────────────────

def run_cli():
    print("================================================")
    print("          MY LOCAL AI ASSISTANT")
    print("================================================")
    print("Powered by Ollama + Qwen 3 8B")
    print("Python Hardware Protection: ENABLED")
    print("Python Performance Protection: ENABLED")
    print("Qwen Hardware Hallucination Protection: ENABLED")
    print("================================================")
    print()
    print(format_help())
    print()
    print("Type 'exit' to close, '/reset' to clear the AI's conversation memory.")
    print("================================================")
    print()

    session = Session()

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        try:
            source, text = handle_message(user_input, session)
        except Exception as e:
            print()
            print(f"Error: {e}")
            print()
            continue

        if source == "exit":
            print(text)
            break

        if source == "system":
            if text:
                print()
                print(text)
                print()
            continue

        if source == "pc":
            print()
            print("PC:")
            print(text)
            print()
            continue

        if source == "action_pending":
            print()
            print("ACTION:")
            print(text)
            print()
            continue

        if source == "action_result":
            print()
            print("RESULT:")
            print(text)
            print()
            continue

        if source == "ai":
            print()
            print("AI:")
            print(text)
            print()


if __name__ == "__main__":
    run_cli()