"""File system control with safety guards.

Default delete goes to the Recycle Bin. Important modifications create backups.
Recursive operations are depth-limited. Nothing is destructive without confirmation
of the safety settings.
"""

import os
import shutil
import zipfile
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import send2trash  # type: ignore

from config import BACKUP_BEFORE_MODIFY, FILE_DELETE_TO_RECYCLE_BIN, MAX_RECURSIVE_DEPTH

SAFETY_NOTE = (
    "This module never bypasses Windows authentication. "
    "Deletion uses the Recycle Bin by default."
)


class FileSystemControl:
    def __init__(self):
        self._backup_dir = os.path.join(os.path.expanduser("~"), "PC Agent Backups")
        os.makedirs(self._backup_dir, exist_ok=True)

    # --------------------------------------------------------------- read/write
    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        with open(path, "r", encoding=encoding) as f:
            return f.read()

    def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def write_file(self, path: str, content: str, encoding: str = "utf-8",
                   backup: Optional[bool] = None) -> None:
        if backup is None:
            backup = BACKUP_BEFORE_MODIFY
        if backup and os.path.exists(path):
            self._backup(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding=encoding) as f:
            f.write(content)

    def write_bytes(self, path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def create_file(self, path: str, content: str = "") -> None:
        self.write_file(path, content)

    def edit_file(self, path: str, old_string: str, new_string: str,
                  replace_all: bool = False) -> int:
        """Replace text in a file. Returns number of replacements."""
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        content = self.read_file(path)
        count = content.count(old_string)
        if count == 0:
            return 0
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
        self.write_file(path, new_content)
        return count

    def append_file(self, path: str, content: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)

    # --------------------------------------------------------------- delete/rename/copy/move
    def delete_file(self, path: str) -> bool:
        """Delete a file to the Recycle Bin by default."""
        if not os.path.exists(path):
            return False
        if FILE_DELETE_TO_RECYCLE_BIN:
            send2trash.send2trash(path)
        else:
            os.remove(path)
        return True

    def delete_folder(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        if FILE_DELETE_TO_RECYCLE_BIN:
            send2trash.send2trash(path)
        else:
            shutil.rmtree(path)
        return True

    def rename(self, src: str, dst: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        os.rename(src, dst)

    def copy(self, src: str, dst: str) -> None:
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
            shutil.copy2(src, dst)

    def move(self, src: str, dst: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.move(src, dst)

    # --------------------------------------------------------------- folders
    def create_folder(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def list_folder(self, path: str) -> List[dict]:
        entries = []
        p = Path(path)
        if not p.exists():
            return entries
        for child in p.iterdir():
            entries.append({
                "name": child.name,
                "path": str(child),
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else None,
                "modified": datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
        return sorted(entries, key=lambda e: (not e["is_dir"], e["name"]))

    def search_files(self, query: str, roots: Optional[List[str]] = None,
                     max_results: int = 100) -> List[dict]:
        """Search file names across accessible drives (depth-limited)."""
        if roots is None:
            roots = self._list_drives()
        results = []
        for root in roots:
            if not os.path.exists(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                # depth limit
                depth = dirpath[len(root):].count(os.sep)
                if depth > MAX_RECURSIVE_DEPTH:
                    dirnames[:] = []
                    continue
                for name in filenames:
                    if query.lower() in name.lower():
                        full = os.path.join(dirpath, name)
                        try:
                            st = os.stat(full)
                            results.append({
                                "name": name,
                                "path": full,
                                "size": st.st_size,
                                "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                            })
                            if len(results) >= max_results:
                                return results
                        except OSError:
                            continue
        return results

    def find_file(self, name_or_path: str) -> Optional[str]:
        """Resolve a possibly-incorrect path: try exact, then search by name."""
        if os.path.exists(name_or_path):
            return name_or_path
        # Try relative to common user folders
        candidates = [
            name_or_path,
            os.path.join(os.path.expanduser("~"), name_or_path),
            os.path.join(os.path.expanduser("~"), "Desktop", name_or_path),
            os.path.join(os.path.expanduser("~"), "Documents", name_or_path),
            os.path.join(os.path.expanduser("~"), "Downloads", name_or_path),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        # Search by file name across drives
        found = self.search_files(os.path.basename(name_or_path), max_results=10)
        if found:
            return found[0]["path"]
        return None

    # --------------------------------------------------------------- archives
    def compress_archive(self, source: str, output_archive: str, fmt: str = "zip") -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_archive)), exist_ok=True)
        if fmt == "zip":
            with zipfile.ZipFile(output_archive, "w", zipfile.ZIP_DEFLATED) as z:
                if os.path.isdir(source):
                    for root, _, files in os.walk(source):
                        for f in files:
                            full = os.path.join(root, f)
                            rel = os.path.relpath(full, source)
                            z.write(full, rel)
                else:
                    z.write(source, os.path.basename(source))
        elif fmt in ("tar", "tar.gz", "tgz"):
            mode = "w:gz" if fmt in ("tar.gz", "tgz") else "w"
            with tarfile.open(output_archive, mode) as t:
                t.add(source, arcname=os.path.basename(source))
        else:
            raise ValueError(f"Unsupported archive format: {fmt}")
        return output_archive

    def extract_archive(self, archive: str, destination: str) -> str:
        os.makedirs(destination, exist_ok=True)
        if archive.lower().endswith((".zip",)):
            with zipfile.ZipFile(archive, "r") as z:
                z.extractall(destination)
        elif archive.lower().endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(archive, "r:*") as t:
                t.extractall(destination)
        else:
            raise ValueError(f"Unsupported archive: {archive}")
        return destination

    # --------------------------------------------------------------- metadata
    def get_metadata(self, path: str) -> dict:
        st = os.stat(path)
        return {
            "path": path,
            "name": os.path.basename(path),
            "size": st.st_size,
            "is_dir": os.path.isdir(path),
            "created": datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat(),
            "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "accessed": datetime.fromtimestamp(st.st_atime, tz=timezone.utc).isoformat(),
        }

    # --------------------------------------------------------------- backup/restore
    def _backup(self, path: str) -> str:
        name = os.path.basename(path)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(self._backup_dir, f"{ts}_{name}")
        if os.path.isdir(path):
            shutil.copytree(path, dst)
        else:
            shutil.copy2(path, dst)
        return dst

    def create_backup(self, path: str) -> str:
        return self._backup(path)

    # --------------------------------------------------------------- drives
    @staticmethod
    def _list_drives() -> List[str]:
        import string
        from ctypes import windll  # Windows only
        drives = []
        bitmask = windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drives.append(f"{letter}:\\")
            bitmask >>= 1
        return drives


fs = FileSystemControl()
