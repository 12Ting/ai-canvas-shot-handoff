"""Prepare the source checkout and start its loopback release server on Windows."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"
PYTHON = API / ".venv" / "Scripts" / "python.exe"
LOCK = API / "requirements.lock"


def tool(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"找不到 {name}。请按 README 安装依赖，并重新打开启动窗口。")
    return path


def execute(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def prepare_python():
    for line in LOCK.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, version = line.strip().split("==", 1)
        try:
            matches = metadata.version(name) == version
        except metadata.PackageNotFoundError:
            matches = False
        if not matches:
            print("正在按锁文件安装 Python 依赖，首次安装需要网络。", flush=True)
            execute([sys.executable, "-m", "pip", "install", "-r", str(LOCK)])
            break


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def node_packages_ready():
    lock = read_json(WEB / "package-lock.json")["packages"]
    hidden_lock = WEB / "node_modules" / ".package-lock.json"
    if not hidden_lock.exists():
        return False
    try:
        installed = read_json(hidden_lock)["packages"]
    except (OSError, KeyError, ValueError):
        return False
    for key, spec in lock.items():
        if not key:
            continue
        if key not in installed:
            if spec.get("optional"):
                continue
            return False
        for field in ("version", "integrity"):
            if installed[key].get(field) != spec.get(field):
                return False
        if not (WEB / key / "package.json").is_file():
            return False
    return (WEB / "node_modules" / ".bin" / "vite.cmd").is_file()


def web_signature(node_version):
    digest = hashlib.sha256(node_version.encode("utf-8"))
    files = [*WEB.glob("*.json"), *WEB.glob("*.ts"), WEB / "index.html"]
    for directory in ("src", "public", "e2e"):
        files.extend(f for f in (WEB / directory).rglob("*") if f.is_file())
    for file in sorted(files):
        digest.update(file.relative_to(WEB).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def prepare_web():
    node = tool("node.exe")
    npm = tool("npm.cmd")
    version = execute([node, "--version"], capture_output=True, text=True).stdout.strip().removeprefix("v")
    major, minor, _ = map(int, version.split("."))
    if major != 22 or minor < 12:
        raise RuntimeError("请安装 Node.js 22.12 或更高的 22.x 版本。")
    if not node_packages_ready():
        print("正在按锁文件安装前端依赖，首次安装需要网络。", flush=True)
        execute([npm, "ci", "--no-fund", "--no-audit"], cwd=WEB)
    stamp = WEB / "dist" / ".source-sha256"
    signature = web_signature(version)
    if not (WEB / "dist/index.html").is_file() or not stamp.is_file() or stamp.read_text() != signature:
        print("正在构建本地界面。", flush=True)
        execute([npm, "run", "build"], cwd=WEB)
        stamp.write_text(signature, encoding="ascii")


def demo_command(env):
    output = ROOT / ".local" / "paper-boat-selection"
    command = [sys.executable, "-X", "utf8", str(ROOT / "scripts/handoff_demo.py"), "--out", str(output)]
    if not output.exists():
        print("正在创建并验证纸船样例。", flush=True)
        execute(command, cwd=ROOT, env=env)
    database = output / "database/canvas.sqlite3"
    try:
        report = read_json(output / "verification.json")
        valid = report.get("mode") == "可再分发的程序合成样例" and bool(report.get("packages"))
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            project = connection.execute("SELECT 1 FROM handoff_projects WHERE project_id = ?",
                                         (report["project_id"],)).fetchone()
    except (OSError, KeyError, ValueError, AttributeError, sqlite3.Error):
        valid, project = False, None
    if not valid or not project:
        raise RuntimeError(f"已有样例目录未通过完整性核对，已保留原文件。请先将该目录另存或移走再试：{output}")
    return [*command, "--open"]


def main():
    parser = argparse.ArgumentParser(description="准备并启动本地短片选片台。")
    parser.add_argument("--prepare-only", action="store_true", help="验证依赖并构建界面后退出")
    parser.add_argument("--demo", action="store_true", help="创建或打开随附的纸船样例")
    parser.add_argument("parent", nargs="?", type=Path, help="新项目存放位置，也可直接把父文件夹拖到 start.cmd")
    args = parser.parse_args()
    if args.parent is not None and (not args.parent.is_dir() or args.demo):
        parser.error("请提供一个现有的项目父文件夹；纸船演示使用自己的目录。")
    if sys.version_info[:2] != (3, 10):
        raise RuntimeError("请安装 Python 3.10 并启用 Windows Python Launcher，或将 Python 3.10 加入 PATH。")
    if Path(sys.executable).resolve() != PYTHON.resolve():
        if not PYTHON.is_file():
            print("正在为此项目创建 Python 虚拟环境。", flush=True)
            execute([sys.executable, "-m", "venv", str(PYTHON.parents[1])])
        execute([str(PYTHON), "-X", "utf8", __file__, *sys.argv[1:]])
        return
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not shutil.which(name):
            raise RuntimeError(f"未找到 {name}。请安装 FFmpeg，并将其 bin 目录加入 PATH。")
    prepare_python()
    prepare_web()
    env = {**os.environ, "PYTHONPATH": str(API / "src"), "AI_CANVAS_RUNTIME_PROFILE": "release",
           "AI_CANVAS_AUTH_MODE": "session"}
    default_data = Path(os.environ["LOCALAPPDATA"]) / "AI_Canvas" / "ShotHandoff"
    env.setdefault("AI_CANVAS_DATA_DIR", str(default_data))
    if args.parent is not None:
        parent = os.path.abspath(args.parent)
        env["AI_CANVAS_HANDOFF_PARENT"] = parent
        print(f"本次新项目会建在：{parent}", flush=True)
    command = demo_command(env) if args.demo else [sys.executable, "-m", "ai_canvas.launcher"]
    if args.prepare_only:
        print("选片台已就绪。" if not args.demo else "纸船样例已通过验证。")
        return
    print("正在打开本地选片台。保留此窗口，按 Ctrl+C 结束。", flush=True)
    execute(command, env=env, cwd=ROOT)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"启动未完成：{error}", file=sys.stderr)
        sys.exit(1)
