"""Approval gates, persisted in episodes/<ep>/status.json."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GATES = ["outline", "script", "audio", "cut", "package"]

# Artifact each gate approves (relative to the episode dir). Approval records its hash;
# if the file later changes, the approval is treated as stale.
ARTIFACTS = {
    "outline": "outline.md",
    "script": "script.md",
    "audio": "mix.mp3",
    "cut": "rough_cut.mp4",
    "package": "package.md",
}

# Stage command -> gate that must be approved before it can run.
STAGE_PREREQ = {"audio": "script", "visuals": "audio"}


class GateError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_hash(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Status:
    def __init__(self, ep_dir: Path):
        self.ep_dir = ep_dir
        self.path = ep_dir / "status.json"
        if self.path.is_file():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {"episode": ep_dir.name, "gates": {}, "builds": {}}
        for g in GATES:
            self.data["gates"].setdefault(g, {"state": "pending", "history": []})
        self.data.setdefault("builds", {})

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")

    def gate(self, g: str) -> dict:
        return self.data["gates"][g]

    def effective_state(self, g: str) -> str:
        """approved | approved-stale | rejected | pending"""
        info = self.gate(g)
        if info["state"] != "approved":
            return info["state"]
        if file_hash(self.ep_dir / ARTIFACTS[g]) != info.get("hash"):
            return "approved-stale"
        return "approved"

    def is_approved(self, g: str) -> bool:
        return self.effective_state(g) == "approved"

    def require(self, g: str) -> None:
        """Gate g and every gate before it must be approved and unchanged."""
        for up in GATES[: GATES.index(g) + 1]:
            state = self.effective_state(up)
            if state == "approved-stale":
                raise GateError(f"gate '{up}' was approved, but {ARTIFACTS[up]} has changed since — needs re-approval")
            if state != "approved":
                raise GateError(f"gate '{up}' is {state}; it must be approved first")

    def record_build(self, stage: str, info: dict) -> None:
        self.data["builds"][stage] = {**info, "at": now()}

    def approve(self, g: str) -> None:
        idx = GATES.index(g)
        if idx > 0:
            self.require(GATES[idx - 1])
        artifact = self.ep_dir / ARTIFACTS[g]
        if not artifact.is_file():
            raise GateError(f"nothing to approve: {artifact} does not exist")
        info = self.gate(g)
        info.update(state="approved", hash=file_hash(artifact), at=now())
        info["history"].append({"action": "approve", "at": now()})
        self._invalidate_after(g)

    def reject(self, g: str, notes: str) -> None:
        info = self.gate(g)
        info.update(state="rejected", hash=None, at=now(), notes=notes)
        info["history"].append({"action": "reject", "at": now(), "notes": notes})
        self._invalidate_after(g)

    def _invalidate_after(self, g: str) -> None:
        # Downstream approvals built on an older version are no longer valid.
        for later in GATES[GATES.index(g) + 1:]:
            info = self.gate(later)
            if info["state"] == "approved" and info.get("at", "") < self.gate(g)["at"]:
                info.update(state="pending", hash=None)
                info["history"].append({"action": "reset", "at": now(), "notes": f"upstream gate '{g}' changed"})

    def next_step(self) -> str:
        for g in GATES:
            state = self.effective_state(g)
            if state == "approved":
                continue
            artifact = self.ep_dir / ARTIFACTS[g]
            if state == "rejected":
                return f"revise for gate '{g}' (notes: {self.gate(g).get('notes')!r}), then ask again"
            if state == "approved-stale":
                return f"{ARTIFACTS[g]} changed after approval — ask Phillip to re-approve '{g}'"
            if artifact.is_file():
                return f"{ARTIFACTS[g]} is ready — waiting on Phillip to approve '{g}'"
            return {
                "outline": "write outline.md",
                "script": "write script.md",
                "audio": "run: python run.py <ep> audio",
                "cut": "run: python run.py <ep> visuals",
                "package": "write package.md (title / thumbnail / description, AI-voice disclosure)",
            }[g]
        return "all gates approved — ready for Phillip to publish"
