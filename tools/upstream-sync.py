#!/usr/bin/env python3
"""Update Odysseus-Edit from a newer Odysseus checkout.

Read docs/UPSTREAM-SYNC.md first. In short:

    python3 tools/upstream-sync.py /path/to/odysseus --check   # look, change nothing
    python3 tools/upstream-sync.py /path/to/odysseus           # vendor update + merge

How it works (vendor-branch pattern):

1. The `upstream` branch holds the Odysseus files this project mirrors, exactly
   as upstream ships them except that `?v=...` cache-busting query strings are
   stripped from JS imports (Odysseus-Edit has no use for them, and keeping
   them would make every version bump a merge conflict).
2. This script copies the mirrored files from the given checkout onto the
   `upstream` branch (in a temporary worktree) and commits them.
3. It then runs `git merge --no-ff --no-commit upstream` on the current
   branch, so git performs a real 3-way merge: upstream's changes are applied
   on top of Odysseus-Edit's own changes, and only genuine overlaps conflict.
4. It records the new upstream commit in upstream.json, regenerates the CSS
   extracted from Odysseus' app stylesheets, and writes a report of what needs
   a human (or agent) look: conflicts, imports that point at files we do not
   have, API endpoints the server does not serve, shim functions that are
   missing, and the upstream backend diff to port by hand.

Nothing is committed on the current branch: review, fix, test, then commit.
Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "upstream.json"
VENDOR_BRANCH = "upstream"
REPORT_DIR = ROOT / ".git" / "upstream-sync"

# (upstream path, local path, kind). "dir" mirrors a whole directory tree.
MAPPING = [
    ("static/js/editor", "web/js/editor", "dir"),
    ("static/js/galleryEditor.js", "web/js/galleryEditor.js", "file"),
    ("static/js/spinner.js", "web/js/spinner.js", "file"),
    ("static/js/colorPicker.js", "web/js/colorPicker.js", "file"),
    ("static/js/dragSort.js", "web/js/dragSort.js", "file"),
    ("static/js/storage.js", "web/js/storage.js", "file"),
    ("static/js/platform.js", "web/js/platform.js", "file"),
    ("static/js/modelSort.js", "web/js/modelSort.js", "file"),
    ("static/js/toolWindowZOrder.js", "web/js/toolWindowZOrder.js", "file"),
    ("static/css/00-tokens.css", "web/css/00-tokens.css", "file"),
    ("static/css/13-image-editor.css", "web/css/13-image-editor.css", "file"),
    ("static/fonts/FiraCode-Light.woff2", "web/fonts/FiraCode-Light.woff2", "file"),
    ("static/fonts/FiraCode-Regular.woff2", "web/fonts/FiraCode-Regular.woff2", "file"),
    ("static/fonts/FiraCode-SemiBold.woff2", "web/fonts/FiraCode-SemiBold.woff2", "file"),
    ("licenses/FiraCode-OFL-1.1.txt", "licenses/FiraCode-OFL-1.1.txt", "file"),
]

# Upstream modules replaced by small shims; their callers must still find
# every function they use.
SHIMS = {
    "static/js/ui.js": "web/js/ui.js",
    "static/js/modalManager.js": "web/js/modalManager.js",
}

# Upstream backend code that server/ was adapted from (ported by hand).
BACKEND_WATCH = [
    "routes/gallery/gallery_routes.py",
    "routes/gallery/gallery_helpers.py",
    "src/url_safety.py",
    "src/optional_deps.py",
    "src/upload_limits.py",
    "src/model_capability_readers/base.py",
]

QUERY_RE = re.compile(r"""(\.js)\?v=[A-Za-z0-9_.-]+(['"])""")


# ── helpers ──────────────────────────────────────────────────────────────────

def run(cmd: list[str], cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    return run(["git", *args], cwd=cwd, check=check).stdout.strip()


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def normalize(rel: str, data: bytes) -> bytes:
    """What the vendor branch stores for an upstream file."""
    if rel.endswith(".js"):
        return QUERY_RE.sub(r"\1\2", data.decode("utf-8")).encode("utf-8")
    return data


def upstream_info(up: Path) -> dict:
    info = {"repository": "https://github.com/odysseus-dev/odysseus", "branch": None,
            "commit": None, "committed_at": None}
    if (up / ".git").exists():
        info["commit"] = git("rev-parse", "HEAD", cwd=up)
        info["branch"] = git("rev-parse", "--abbrev-ref", "HEAD", cwd=up) or None
        info["committed_at"] = git("log", "-1", "--format=%cI", cwd=up)
        remote = git("remote", "get-url", "origin", cwd=up, check=False)
        if remote:
            info["repository"] = remote
    return info


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def expected_files(up: Path) -> tuple[dict[str, Path], list[str]]:
    """Map local path -> upstream file for everything mirrored; list missing singles."""
    files: dict[str, Path] = {}
    missing: list[str] = []
    for src, dst, kind in MAPPING:
        source = up / src
        if kind == "dir":
            if not source.is_dir():
                missing.append(src + "/")
                continue
            for path in sorted(source.rglob("*")):
                if path.is_file() and not any(part.startswith(".") for part in path.relative_to(source).parts):
                    files[f"{dst}/{path.relative_to(source).as_posix()}"] = path
        elif source.is_file():
            files[dst] = source
        else:
            missing.append(src)
    return files, missing


def vendor_tree() -> set[str]:
    """Files under mirrored paths on the vendor branch."""
    roots = [dst for _, dst, _ in MAPPING]
    out = git("ls-tree", "-r", "--name-only", VENDOR_BRANCH, "--", *roots)
    return set(out.splitlines()) if out else set()


def ensure_vendor_branch() -> None:
    if git("branch", "--list", VENDOR_BRANCH):
        return
    root_commit = git("rev-list", "--max-parents=0", "HEAD").splitlines()[-1]
    git("branch", VENDOR_BRANCH, root_commit)
    print(f"Created branch '{VENDOR_BRANCH}' at the pristine import commit {root_commit[:9]}.")


def planned_changes(up: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    """(added, modified, deleted, missing-upstream) relative to the vendor branch."""
    files, missing = expected_files(up)
    current = vendor_tree()
    added, modified = [], []
    for rel, source in files.items():
        data = normalize(rel, source.read_bytes())
        if rel not in current:
            added.append(rel)
            continue
        old = subprocess.run(["git", "show", f"{VENDOR_BRANCH}:{rel}"], cwd=ROOT, capture_output=True)
        if old.returncode != 0:
            added.append(rel)
        elif old.stdout != data:
            modified.append(rel)
    dir_roots = tuple(dst + "/" for _, dst, kind in MAPPING if kind == "dir")
    deleted = sorted(rel for rel in current if rel not in files and rel.startswith(dir_roots))
    single_dsts = {dst: src for src, dst, kind in MAPPING if kind == "file"}
    deleted += sorted(dst for dst, src in single_dsts.items() if src in missing and dst in current)
    return sorted(added), sorted(modified), deleted, missing


def update_vendor_branch(up: Path, info: dict) -> list[str]:
    """Commit the normalized upstream files onto the vendor branch. Returns changed paths."""
    added, modified, deleted, _ = planned_changes(up)
    if not (added or modified or deleted):
        return []
    files, _ = expected_files(up)
    worktree = Path(tempfile.mkdtemp(prefix="oe-upstream-"))
    worktree.rmdir()
    git("worktree", "add", "--quiet", str(worktree), VENDOR_BRANCH)
    try:
        for rel in deleted:
            (worktree / rel).unlink(missing_ok=True)
        for rel in added + modified:
            target = worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(normalize(rel, files[rel].read_bytes()))
        # The vendor branch holds nothing but mirrored files (plus the import
        # commit's LICENSE/NOTICE/.gitignore, never touched here).
        git("add", "-A", cwd=worktree)
        short = (info.get("commit") or "unknown")[:9]
        message = (
            f"Upstream: odysseus {info.get('branch') or ''} @{short}\n\n"
            f"{info.get('repository')} {info.get('commit') or '(no git metadata)'}\n"
            "Mirrored files only; ?v= query strings stripped from JS imports.\n"
            "Updated by tools/upstream-sync.py."
        )
        git("commit", "--quiet", "-m", message, cwd=worktree)
    finally:
        run(["git", "worktree", "remove", "--force", str(worktree)], check=False)
        shutil.rmtree(worktree, ignore_errors=True)
    return sorted(added + modified + deleted)


# ── checks on the merged tree ────────────────────────────────────────────────

IMPORT_RE = re.compile(
    r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|import\s+|export\s[^'"]*?from\s*|new\s+URL\(\s*)(['"])(\.{1,2}/[^'"]+)\1""")
COMMENT_RE = re.compile(r"/\*.*?\*/|(^|\s)//[^\n]*", re.S)


def code_only(text: str) -> str:
    """Drop JS comments (roughly) so documentation does not count as usage."""
    return COMMENT_RE.sub(lambda m: m.group(1) or "", text)
API_RE = re.compile(r"""['"`](/api/[A-Za-z0-9_\-/.]*)""")
ROUTE_RE = re.compile(r"""@router\.(?:get|post|put|patch|delete|api_route)\(\s*['"](/api/[^'"]+)['"]""")


def check_imports() -> list[str]:
    """Relative imports in web/ that point at files we do not have."""
    problems = []
    for path in sorted((ROOT / "web").rglob("*.js")):
        text = code_only(path.read_text(encoding="utf-8", errors="replace"))
        for match in IMPORT_RE.finditer(text):
            spec = match.group(2).split("?")[0]
            target = (path.parent / spec).resolve()
            if not target.exists():
                problems.append(f"{path.relative_to(ROOT)} imports {match.group(2)} (missing)")
            if "?v=" in match.group(2):
                problems.append(f"{path.relative_to(ROOT)} imports {match.group(2)} (has ?v=, strip it)")
    return problems


def check_api_endpoints() -> list[str]:
    """/api/ paths the frontend uses that no server route matches."""
    routes = []
    for path in (ROOT / "server" / "app").rglob("*.py"):
        routes += ROUTE_RE.findall(path.read_text(encoding="utf-8"))
    patterns = [re.compile("^" + re.sub(r"\\\{[^/]+\\\}", "[^/]+", re.escape(r)) + "$") for r in routes]
    used = {}
    for path in sorted((ROOT / "web").rglob("*.js")):
        for match in API_RE.finditer(code_only(path.read_text(encoding="utf-8", errors="replace"))):
            used.setdefault(match.group(1).rstrip("/"), set()).add(str(path.relative_to(ROOT)))
    problems = []
    for api, where in sorted(used.items()):
        prefix_ok = any(p.match(api) for p in patterns) or any(r.startswith(api + "/") or r == api for r in routes)
        if not prefix_ok:
            problems.append(f"{api}  (used in {', '.join(sorted(where))})")
    return problems


def check_shims() -> list[str]:
    """Functions callers use on the shimmed modules that the shims do not export."""
    problems = []
    for upstream_rel, local in SHIMS.items():
        name = Path(local).name
        shim = (ROOT / local).read_text(encoding="utf-8")
        exported = set(re.findall(r"export\s+(?:async\s+)?function\s+(\w+)", shim))
        default = re.search(r"export default \{([^}]*)\}", shim)
        if default:
            exported |= {n.strip().split(":")[0] for n in default.group(1).split(",") if n.strip()}
        used: dict[str, set[str]] = {}
        for path in sorted((ROOT / "web" / "js").rglob("*.js")):
            if path.name == name:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if f"{name}'" not in text and f'{name}"' not in text:
                continue
            for alias in re.findall(rf"import\s+(\w+)\s+from\s+['\"][./]*{re.escape(name)}['\"]", text):
                for fn in re.findall(rf"\b{alias}\??\.(\w+)", text):
                    used.setdefault(fn, set()).add(str(path.relative_to(ROOT)))
            for group in re.findall(rf"import\s*\{{([^}}]*)\}}\s*from\s+['\"][./]*{re.escape(name)}['\"]", text):
                for fn in group.split(","):
                    fn = fn.strip().split(" as ")[0].strip()
                    if fn:
                        used.setdefault(fn, set()).add(str(path.relative_to(ROOT)))
        for fn, where in sorted(used.items()):
            if fn not in exported and not fn.startswith("_"):
                problems.append(f"{local}: missing `{fn}` (used in {', '.join(sorted(where))}); "
                                f"compare upstream {upstream_rel}")
    return problems


def upstream_diff(up: Path, old: str | None, new: str | None, paths: list[str], out_name: str) -> str:
    if not old:
        return "(no previous sync recorded in upstream.json; nothing to compare against)"
    if not (new and (up / ".git").exists()):
        return "(the checkout has no git history; compare the files by hand)"
    if run(["git", "cat-file", "-e", f"{old}^{{commit}}"], cwd=up, check=False).returncode != 0:
        return f"(old commit {old[:9]} is not in the checkout's history; fetch it or compare by hand)"
    stat = git("diff", "--stat", f"{old}..{new}", "--", *paths, cwd=up, check=False)
    full = git("diff", f"{old}..{new}", "--", *paths, cwd=up, check=False)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / out_name).write_text(full + "\n")
    if not stat:
        return "no changes"
    return stat + f"\nfull diff: {(REPORT_DIR / out_name).relative_to(ROOT)}"


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("odysseus", type=Path, help="path to an Odysseus checkout (ideally a git clone)")
    parser.add_argument("--check", action="store_true", help="report what would change; modify nothing")
    args = parser.parse_args()

    up = args.odysseus.resolve()
    if not (up / "static" / "js" / "galleryEditor.js").is_file():
        die(f"{up} does not look like an Odysseus checkout (static/js/galleryEditor.js missing)")
    if not (ROOT / ".git").exists():
        die("run this inside the Odysseus-Edit git repository")

    state = load_state()
    info = upstream_info(up)
    old_commit = state.get("commit")
    print(f"Odysseus-Edit last synced: {(old_commit or 'unknown')[:9]}   checkout: {(info['commit'] or 'unknown')[:9]}")

    ensure_vendor_branch()
    added, modified, deleted, missing = planned_changes(up)
    print(f"Mirrored files: {len(added)} added, {len(modified)} modified, {len(deleted)} deleted upstream")
    for label, items in (("added", added), ("modified", modified), ("deleted", deleted)):
        for rel in items:
            print(f"  {label:8} {rel}")
    for rel in missing:
        print(f"  MISSING upstream: {rel}  (update MAPPING in tools/upstream-sync.py)")
    print("\nBackend files to port by hand:")
    print("  " + upstream_diff(up, old_commit, info["commit"], BACKEND_WATCH, "backend.diff").replace("\n", "\n  "))
    print("\nShimmed upstream modules (keep the shims' APIs in step):")
    print("  " + upstream_diff(up, old_commit, info["commit"], list(SHIMS), "shims.diff").replace("\n", "\n  "))

    if args.check:
        print("\n--check: nothing was changed.")
        return

    if git("status", "--porcelain"):
        die("the working tree has uncommitted changes; commit or stash them first")
    if git("rev-parse", "--abbrev-ref", "HEAD") == VENDOR_BRANCH:
        die(f"switch away from the '{VENDOR_BRANCH}' branch first (e.g. git switch main)")

    changed = update_vendor_branch(up, info)
    print(f"\nVendor branch '{VENDOR_BRANCH}': " + (f"{len(changed)} files committed" if changed else "no changes"))

    merge = run(["git", "merge", "--no-ff", "--no-commit", VENDOR_BRANCH], check=False)
    already = "Already up to date" in (merge.stdout + merge.stderr)
    conflicts = git("diff", "--name-only", "--diff-filter=U").splitlines()

    new_state = {
        "repository": info["repository"],
        "branch": info["branch"],
        "commit": info["commit"],
        "committed_at": info["committed_at"],
        "synced_at": dt.date.today().isoformat(),
        "previous_commit": old_commit,
    }
    STATE_FILE.write_text(json.dumps(new_state, indent=2) + "\n")
    git("add", str(STATE_FILE.relative_to(ROOT)))

    css = run([str(ROOT / "tools" / "sync-upstream-css.sh"), str(up)], check=False)
    if css.returncode == 0:
        git("add", "web/css/upstream-pre.css", "web/css/upstream-post.css")
    css_note = "regenerated" if css.returncode == 0 else f"FAILED: {css.stderr.strip()[:300]}"

    imports = check_imports()
    apis = check_api_endpoints()
    shims = check_shims()

    lines = [
        "# Upstream sync report",
        "",
        f"- Upstream: {info['repository']} {info['branch'] or ''} {info['commit'] or '(unknown)'}",
        f"- Previous: {old_commit or '(unknown)'}",
        f"- Mirrored files changed upstream: {len(changed)}",
        f"- Merge: {'nothing new to merge' if already else ('CONFLICTS' if conflicts else 'clean (not committed yet)')}",
        f"- CSS from app stylesheets: {css_note}",
        "",
        "## Conflicts to resolve",
        *(f"- {c}" for c in conflicts or ["none"]),
        "",
        "## Imports pointing at missing files",
        *(f"- {p}" for p in imports or ["none"]),
        "",
        "## /api paths the server does not serve",
        *(f"- {p}" for p in apis or ["none"]),
        "",
        "## Shim functions missing",
        *(f"- {p}" for p in shims or ["none"]),
        "",
        "## Backend (port by hand into server/)",
        "```",
        upstream_diff(up, old_commit, info["commit"], BACKEND_WATCH, "backend.diff"),
        "```",
        "",
        "Next: follow docs/UPSTREAM-SYNC.md, steps 4-8.",
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / "REPORT.md"
    report.write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    print(f"\nReport saved to {report.relative_to(ROOT)}. Nothing is committed yet.")
    sys.exit(1 if conflicts else 0)


if __name__ == "__main__":
    main()
