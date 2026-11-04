#!/bin/bash
# 双击启动佳境产线排障情景训练系统。启动后浏览器会自动打开首页；关闭这个窗口即停止系统。
cd "$(dirname "$0")"
OPEN_URL="http://localhost:8000"
echo "== 佳境 · 产线排障情景训练系统 =="
if [ ! -x .venv/bin/python ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "没有找到 Python。请先安装 Python 3.13：https://www.python.org/downloads/"
    read -r
    exit 1
  fi
  echo "首次运行：正在安装运行环境（需要联网，约 1–2 分钟）…"
  if ! { python3 -m venv .venv && .venv/bin/python -m pip install -q -r backend/requirements.txt; }; then
    rm -rf .venv
    echo "安装失败。请确认已安装 Python 3.13、网络正常，然后再双击一次。"
    read -r
    exit 1
  fi
fi
if [ ! -f data/app.db ]; then
  echo "首次运行：正在准备关卡与演示数据…"
  .venv/bin/python scripts/seed.py || { echo "准备数据失败，请把这个窗口里的内容发到项目的 Issues。"; read -r; exit 1; }
fi
if lsof -i :8000 >/dev/null 2>&1; then
  echo "8000 端口已经有程序在运行，直接打开浏览器。如果打开的不是这个系统，请先关掉占用端口的程序。"
  open "$OPEN_URL"
  exit 0
fi
(sleep 3; open "$OPEN_URL") &
echo "系统启动中……浏览器会自动打开。要停止系统，直接关闭这个窗口。"
.venv/bin/python -m uvicorn app.api.main:app --app-dir backend --host 127.0.0.1 --port 8000 --log-level warning
