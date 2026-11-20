from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Dict, Optional

from astrbot.api import logger
from astrbot.api.star import StarTools

from .course_types import UserBinding


class CourseStorage:
    def __init__(self, plugin_name: str):
        self._plugin_name = plugin_name
        self._base_dir = StarTools.get_data_dir(plugin_name)
        self._ics_dir = self._base_dir / "ics"
        self._bindings_file = self._base_dir / "bindings.json"

        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._ics_dir.mkdir(parents=True, exist_ok=True)

    def load_bindings(self) -> Dict[str, UserBinding]:
        if not self._bindings_file.exists():
            return {}
        try:
            raw = json.loads(self._bindings_file.read_text(encoding="utf-8"))
            bindings: Dict[str, UserBinding] = {}
            for user_id, item in raw["bindings"].items():
                if not isinstance(item, dict):
                    raise ValueError("invalid binding entry")
                overrides = item.get("group_reminders")
                overrides = overrides if isinstance(overrides, dict) else {}
                bindings[user_id] = UserBinding(
                    user_id=user_id,
                    unified_msg_origin=str(item.get("unified_msg_origin", "")),
                    nickname=str(item.get("nickname", "")),
                    ics_file=str(item.get("ics_file", "")),
                    updated_at_ts=float(item.get("updated_at_ts", 0.0)),
                    enable_daily_push=bool(item.get("enable_daily_push", False)),
                    daily_push_time=str(item.get("daily_push_time", "07:00")),
                    reminder_advance_minutes=int(
                        item.get("reminder_advance_minutes", 15)
                    ),
                    daily_push_job_id=str(item.get("daily_push_job_id", "")),
                    group_reminders={k: v for k, v in overrides.items() if isinstance(v, bool)},
                )
            return bindings
        except Exception as e:
            logger.error(f"[course] Failed to load bindings.json ({type(e).__name__})")
            raise

    def save_bindings(self, bindings: Dict[str, UserBinding]) -> None:
        payload = {
            "version": 1,
            "updated_at_ts": time.time(),
            "bindings": {uid: asdict(b) for uid, b in bindings.items()},
        }
        try:
            fd, temp = tempfile.mkstemp(dir=self._base_dir, prefix="bindings-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self._bindings_file)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        except Exception as e:
            logger.error(f"[course] Failed to save bindings.json: {e}")
            raise

    def delete_binding(self, user_id: str) -> bool:
        bindings = self.load_bindings()
        binding = bindings.pop(user_id, None)
        if not binding:
            return False
        self.save_bindings(bindings)
        self._cleanup_calendar(binding.ics_file)
        return True

    def _cleanup_calendar(self, relative_path: str) -> None:
        try:
            path = self._base_dir / relative_path
            if not path.is_symlink() and path.resolve().parent == self._ics_dir.resolve():
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"[course] old calendar cleanup failed ({type(exc).__name__})")

    def store_calendar(
        self, *, user_id: str, origin: str, nickname: str, content: bytes,
    ) -> UserBinding:
        bindings = self.load_bindings()
        previous = bindings.get(user_id)
        if previous and previous.unified_msg_origin.split(":", 1)[0] != origin.split(":", 1)[0]:
            raise ValueError("binding belongs to a different platform")
        fd, filename = tempfile.mkstemp(dir=self._ics_dir, prefix="course-", suffix=".ics")
        calendar = Path(filename)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            relative_path = calendar.resolve().relative_to(self._base_dir.resolve()).as_posix()
            updated = time.time()
            if previous:
                binding = replace(previous, nickname=nickname, ics_file=relative_path, updated_at_ts=updated)
            else:
                binding = UserBinding(user_id, origin, nickname, relative_path, updated)
            bindings[user_id] = binding
            self.save_bindings(bindings)
        except BaseException:
            self._cleanup_calendar(str(calendar))
            raise
        if previous:
            self._cleanup_calendar(previous.ics_file)
        return binding

    def set_group_reminder(self, user_id: str, origin: str, enabled: bool) -> bool:
        bindings = self.load_bindings()
        binding = bindings.get(user_id)
        if not binding:
            return False
        binding.group_reminders[origin] = enabled
        self.save_bindings(bindings)
        return True

    def get_binding(self, user_id: str) -> Optional[UserBinding]:
        return self.load_bindings().get(user_id)
