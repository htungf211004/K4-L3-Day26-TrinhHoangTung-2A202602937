"""Read-only final audit; run with the project's Python 3.12 interpreter."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEAM = "TrinhHoangTung-2A202602937"
OWNED = ("agent", "deck", "eval")
DENIED = (
    "socket", "ssl", "http.client", "urllib.request", "urllib3", "requests",
    "httpx", "subprocess", "multiprocessing", "ctypes", "cffi", "importlib.util",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def audit_imports():
    count = 0
    for directory in OWNED:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            count += 1
            tree = ast.parse(path.read_text(encoding="utf-8"))
            aliases = {}
            for node in ast.walk(tree):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [a.name for a in node.names]
                    for a in node.names:
                        aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports = [node.module] + [node.module + "." + a.name for a in node.names]
                    for a in node.names:
                        aliases[a.asname or a.name] = node.module + "." + a.name
                for name in imports:
                    assert not any(name == d or name.startswith(d + ".") for d in DENIED), (path, node.lineno, name)
                    root = name.split(".")[0]
                    assert root in sys.stdlib_module_names or root in OWNED or root == "kit", (path, node.lineno, name)

            def qualified(node):
                if isinstance(node, ast.Name):
                    return aliases.get(node.id, node.id)
                if isinstance(node, ast.Attribute):
                    return qualified(node.value) + "." + node.attr
                return ""

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = qualified(node.func)
                    assert name not in {"os.system", "asyncio.open_connection", "eval", "exec", "compile", "__import__", "importlib.import_module"}, (path, node.lineno, name)
                    assert not name.startswith(("os.exec", "os.spawn")), (path, node.lineno, name)
    print(f"IMPORT AUDIT: PASS ({count} Python files; stdlib and project modules only)")


def audit_protected():
    # Match the Windows checkout's Git settings without changing configuration.
    result = subprocess.run(["git", "-c", "core.autocrlf=true", "-c", "core.filemode=false", "status", "--porcelain", "--", "kit/", "bots/", "fixtures/"], cwd=ROOT, check=True, capture_output=True, text=True)
    assert not result.stdout.strip(), result.stdout
    print("PROTECTED DIRECTORIES: PASS (git status empty)")
    archive = ROOT / "colosseum-world-df8c55dabb35.zip"
    expected = archive.with_suffix(".zip.sha256").read_text().split()[0]
    assert sha(archive.read_bytes()) == expected, "World ZIP checksum mismatch"
    count = 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = Path(info.filename)
            if rel.parts[:2] != ("kit", "world"):
                rel = Path("kit/world") / rel
            assert ".." not in rel.parts and not rel.is_absolute(), info.filename
            assert (ROOT / rel).read_bytes() == z.read(info), info.filename
            count += 1
    assert count > 0
    assert not list((ROOT / "kit/world").glob("*/truth.json"))
    print(f"WORLD INTEGRITY: PASS ({count} exported files match original ZIP; SHA256 verified; no truth.json)")


def audit_bundle():
    bundle = ROOT / "submissions" / f"{TEAM}.bundle"
    with zipfile.ZipFile(bundle) as z:
        assert z.testzip() is None
        manifest = json.loads(z.read("MANIFEST.json"))
        assert manifest["team"] == TEAM
        expected_files = {p.relative_to(ROOT).as_posix() for d in OWNED for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        required_payload = {
            "agent/__init__.py", "agent/gateway.py", "agent/guardrails.py",
            "agent/strategy.py", "agent/telemetry.py", "agent/prompt.md",
            "deck/deck.json", "deck/lineup.json", "eval/__init__.py", "eval/prosecute.py",
        }
        assert expected_files == required_payload, "Unexpected/missing submission file"
        entries = {f["path"]: f["sha256"] for f in manifest["files"]}
        assert set(entries) == expected_files
        assert set(z.namelist()) == expected_files | {"MANIFEST.json"}
        assert len(z.namelist()) == len(set(z.namelist()))
        for name, digest in entries.items():
            assert sha(z.read(name)) == digest == sha((ROOT / name).read_bytes()), name
        kit_files = {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in (ROOT / "kit").rglob("*.py") if "__pycache__" not in p.parts}
        assert manifest["kit_hashes"] == kit_files
    print(f"BUNDLE AUDIT: PASS ({len(entries)} payload files, {len(kit_files)} kit Python hashes, valid ZIP)")
    print(f"BUNDLE SHA256: {sha(bundle.read_bytes())}")
    print("NOTE: shipped kit.submit records kit/*.py hashes; exported world checked separately above.")


if __name__ == "__main__":
    audit_imports()
    audit_protected()
    if "--bundle" in sys.argv:
        audit_bundle()
