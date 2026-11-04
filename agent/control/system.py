"""Windows system control: CPU, RAM, disk, network, processes, services, env."""

import os
import platform
from typing import Dict, List, Optional

import psutil


class SystemControl:
    def cpu_percent(self, interval: float = 1.0) -> float:
        return psutil.cpu_percent(interval=interval)

    def cpu_count(self) -> int:
        return psutil.cpu_count(logical=True)

    def ram(self) -> Dict:
        mem = psutil.virtual_memory()
        return {
            "total": mem.total,
            "available": mem.available,
            "used": mem.used,
            "percent": mem.percent,
        }

    def disk(self, path: str = "C:\\") -> Dict:
        usage = psutil.disk_usage(path)
        return {
            "path": path,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
        }

    def disks(self) -> List[Dict]:
        out = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                out.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent,
                })
            except OSError:
                continue
        return out

    def network(self) -> Dict:
        net = psutil.net_io_counters()
        return {
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
            "packets_sent": net.packets_sent,
            "packets_recv": net.packets_recv,
        }

    def network_connections(self) -> List[Dict]:
        out = []
        for c in psutil.net_connections(kind="inet"):
            out.append({
                "fd": c.fd,
                "family": str(c.family),
                "type": str(c.type),
                "local_address": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None,
                "remote_address": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None,
                "status": c.status,
                "pid": c.pid,
            })
        return out

    def processes(self) -> List[Dict]:
        out = []
        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "status"]):
            try:
                info = p.info
                info["memory_mb"] = round((info["memory_info"].rss or 0) / 1024 / 1024, 2) if info.get("memory_info") else 0
                info.pop("memory_info", None)
                out.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def kill_process(self, pid: int) -> bool:
        try:
            psutil.Process(pid).kill()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def services(self) -> List[Dict]:
        out = []
        for s in psutil.win_service_iter():
            try:
                info = s.as_dict()
                out.append({
                    "name": info.get("name"),
                    "display_name": info.get("display_name"),
                    "status": info.get("status"),
                    "start_type": info.get("start_type"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def start_service(self, name: str) -> bool:
        try:
            psutil.WinService(name).start()
            return True
        except Exception:
            return False

    def stop_service(self, name: str) -> bool:
        try:
            psutil.WinService(name).stop()
            return True
        except Exception:
            return False

    def environment_variables(self) -> Dict:
        return dict(os.environ)

    def set_environment_variable(self, name: str, value: str, persistent: bool = False) -> None:
        os.environ[name] = value
        if persistent:
            import winreg  # Windows only
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
            except Exception:
                pass

    def device_info(self) -> Dict:
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "node": platform.node(),
            "python_version": platform.python_version(),
        }

    def battery(self) -> Optional[Dict]:
        try:
            b = psutil.sensors_battery()
            if b is None:
                return None
            return {
                "percent": b.percent,
                "plugged": b.power_plugged,
                "secs_left": b.secs_left,
            }
        except Exception:
            return None


system = SystemControl()
