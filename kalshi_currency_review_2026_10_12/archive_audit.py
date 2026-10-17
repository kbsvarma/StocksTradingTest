"""Read-only production preservation check; writes receipts inside this study only."""
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

P = Path(__file__).resolve().parent
L = P.parent / "kalshi_revalidation_2026_09_12/live_v1"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


before = json.loads((P / "preservation_before.json").read_text())
sources = {p.name: digest(p) for p in L.glob("*.py")}
flags = {p.name: digest(p) for p in (L / "state").iterdir()
         if p.name.startswith(("ARM", "HALT"))}
services = {
    n: dict(line.split("=", 1) for line in subprocess.check_output(
        ["systemctl", "--user", "show", n, "-p", "MainPID", "-p", "ActiveState", "-p", "NRestarts"],
        text=True).splitlines())
    for n in before["services"]
}
baseline = P.parent / "research_checkpoints/kalshi_stable_baseline_2026_09_28"
baseline_hashes = json.loads((baseline / "manifest.json").read_text())["sha256"]
checks = {
    "production_source_unchanged": sources == before["sources"],
    "arm_halt_flags_unchanged": flags == before["flags"],
    "services_and_pids_unchanged": services == before["services"],
    "stable_checkpoint_verified": all(digest(baseline / "source" / n) == h
                                      for n, h in baseline_hashes.items()),
}
receipt = {"checked_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
           "checks": checks, "passed": all(checks.values()),
           "source_count": len(sources), "flag_count": len(flags),
           "stable_checkpoint_files": len(baseline_hashes), "services": services}
(P / "preservation_final.json").write_text(json.dumps(receipt, indent=2) + "\n")
assert receipt["passed"], receipt

files = {
    str(p.relative_to(P)): {"sha256": digest(p), "bytes": p.stat().st_size}
    for p in sorted(P.rglob("*"))
    if p.is_file() and "__pycache__" not in p.parts and
    p.name not in {"manifest.json", "local_mirror_verification.json"}
}
manifest = {"created_utc": receipt["checked_utc"], "root": str(P),
            "file_count": len(files), "total_bytes": sum(f["bytes"] for f in files.values()),
            "files": files}
(P / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"preservation": receipt, "manifest_files": len(files),
                  "manifest_bytes": manifest["total_bytes"]}))
