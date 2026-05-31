#!/bin/bash
# 准备 workspace（沙箱隔离版）：
# 在 /tmp 下创建独立 workspace，与评测仓库物理断开。
# Agent 的 cwd 落在 /tmp/his-cpoe-<ts>-<rand>/，向上 ls 看不到 server/ rubrics/ verifier/。
#
# 用法: ./prepare_workspace.sh <case_id> [<workspace_path_out>]
#   - case_id: 如 case_01（仅用于内部跑评编排，不会出现在 workspace 路径）
#   - workspace_path_out: 可选，把生成的 workspace 路径写到该文件（供 run_eval.sh 读取）
#
# 输出: 实际 workspace 绝对路径，回显到 stdout 末行。

set -e

CASE_ID=$1
PATH_OUT=$2

if [ -z "$CASE_ID" ]; then
    echo "用法: $0 <case_id> [<path_out_file>]" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 在创建新 sandbox 之前，清理所有旧 sandbox（避免跨次跑评污染——
# 旧 sandbox 的 prompt/api_docs 可能版本不一致，agent 能 ls /tmp 看到并误读）
# 注意：用 || true 兜底，因为 Windows 下被某些进程占用的旧 sandbox 删不掉时不应让本次跑评失败
for old in /tmp/his-cpoe-* /tmp/eval-his-*; do
    if [ -d "$old" ]; then
        rm -rf "$old" 2>/dev/null || true
    fi
done

# 在 /tmp 下创建唯一目录（跨平台：Linux/Mac/Windows Git Bash 均映射到系统 temp）
# 路径里不带 case_id / eval 字样，避免 agent 通过 pwd 推断"我在评测里"
TS=$(date +%Y%m%d-%H%M%S)
WORKSPACE=$(mktemp -d "/tmp/his-cpoe-${TS}-XXXXXX")

# 复制模板（仅 agent 该看到的文件：task_prompt.md + output_schema.json）
cp -r "$SCRIPT_DIR/workspace_template/." "$WORKSPACE/"

# 把绝对路径写到调用者指定的文件（便于 run_eval.sh 拿到）
if [ -n "$PATH_OUT" ]; then
    echo "$WORKSPACE" > "$PATH_OUT"
fi

echo "[prepare] sandbox workspace ready: <system tmp>/$(basename "$WORKSPACE")" >&2
echo "$WORKSPACE"
