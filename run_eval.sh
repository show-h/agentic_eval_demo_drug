#!/bin/bash
# run_eval.sh - 一键运行全部药房 demo 评测（沙箱隔离版）
# 用法: bash run_eval.sh [--agents "claude_code codex"] [--cases "01 02 03 04 05"]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 默认配置
# 仓库带了 4 个示范 adapter（runner/adapters/*.sh.example），但默认不自动跑：
# 强制用户用 --agents 指定，避免误以为这是"4 个 agent 现成结果"。
# 接入新 agent: 复制 runner/adapters/_template.sh 改名为 <your_agent>.sh，按 TODO 填命令。
AGENTS=${AGENTS:-""}
CASES=${CASES:-"01 02 03 04 05"}
PORT=${PORT:-5000}

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --agents) AGENTS="$2"; shift 2;;
        --cases) CASES="$2"; shift 2;;
        --port) PORT="$2"; shift 2;;
        *) echo "Unknown option: $1"; exit 1;;
    esac
done

# 必须指定 --agents（避免误以为有"开箱即用的 4 agent"）
if [ -z "$AGENTS" ]; then
    echo "ERROR: 请用 --agents 指定要跑的 agent。例如："
    echo "  bash run_eval.sh --agents \"claude_code codebuddy\" --cases \"01 02\""
    echo ""
    echo "可用 adapter（runner/adapters/）："
    ls runner/adapters/ 2>/dev/null | sed 's/^/  /'
    echo ""
    echo "如何接入新 agent：复制 runner/adapters/_template.sh 改名为 <your_agent>.sh，按 TODO 填 CLI 命令。"
    exit 1
fi

# 自动 fallback：如果只有 .sh.example，复制一份为 .sh 供 adapter 调用
for ag in $AGENTS; do
    if [ ! -f "runner/adapters/${ag}.sh" ] && [ -f "runner/adapters/${ag}.sh.example" ]; then
        cp "runner/adapters/${ag}.sh.example" "runner/adapters/${ag}.sh"
        echo "[adapter] 已激活示范 adapter: ${ag}.sh.example -> ${ag}.sh"
    fi
done

# === 跨平台杀 server 进程 ===
# 在 Git Bash for Windows 下，bash $! 拿到的是 cygwin PID（如 2027），不是 Windows native PID（如 32760）。
# 用 cygwin PID 跑 kill / taskkill 都找不到进程 → trap cleanup 完全失效，僵尸 server 占着 5000 端口。
# 修复方案（参考姊妹项目）：用 wmic / Get-WmiObject 按命令行匹配找 Windows 真实 PID。
kill_server_by_cmdline() {
    # 根据命令行子串杀掉所有匹配的 Windows python 进程
    local pattern=$1
    if [ -z "$pattern" ]; then return 0; fi
    if command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -Command "
            Get-WmiObject Win32_Process -Filter 'Name=\"python.exe\"' |
            Where-Object { \$_.CommandLine -like '*$pattern*' } |
            ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }
        " 2>/dev/null || true
    fi
}

# 兜底：扫端口找占用者杀掉，并 verify-and-retry 确认端口真空
kill_port_holder() {
    local port=$1
    if ! command -v powershell.exe >/dev/null 2>&1; then
        return 0
    fi
    local attempt
    for attempt in 1 2 3; do
        local stuck_pid
        stuck_pid=$(powershell.exe -Command "(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess" 2>/dev/null | tr -d '\r\n')
        if [ -z "$stuck_pid" ] || [ "$stuck_pid" = "0" ]; then
            return 0  # 端口已空
        fi
        powershell.exe -Command "Stop-Process -Id $stuck_pid -Force -ErrorAction SilentlyContinue" 2>/dev/null || true
        sleep 1
    done
    # 3 次后仍占用 → 警告但不阻塞
    if netstat -ano 2>/dev/null | grep -q ":$port.*LISTENING"; then
        echo "  [WARN] port $port still occupied after 3 kill attempts" >&2
    fi
}

# 验证 server 是否真的健康可用：
#   1. 连续 3 次 health 200（基础连通性）
#   2. 然后调一次业务 API（GET /encounters/current）确认 call_log 真的被写入
#      —— 这是 fingerprint 兜底，防止 zombie server 假回 200 但路由不到我们的 Flask routes
verify_server_alive() {
    local port=$1
    local call_log_path=$2
    local i ok=0
    sleep 2  # 给 server 一点启动时间
    for i in 1 2 3; do
        local resp
        resp=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${port}/api/v1/health" 2>/dev/null)
        if [ "$resp" = "200" ]; then
            ok=$((ok + 1))
        fi
        sleep 1
    done
    if [ $ok -lt 3 ]; then
        echo "  [WARN] health check failed ($ok/3 ok)" >&2
        return 1
    fi
    # Fingerprint：调一次业务 API 让它进 call_log（health 不进 log，不能用作 fingerprint）
    curl -s -o /dev/null --max-time 3 "http://127.0.0.1:${port}/api/v1/encounters/current" 2>/dev/null
    sleep 1
    if [ -f "$call_log_path" ]; then
        local n_biz
        n_biz=$(python -c "
import json
calls = json.load(open('$call_log_path', encoding='utf-8'))
# encounters/current 是评测期间真正会进 log 的端点
print(sum(1 for c in calls if '/encounters/current' in c.get('endpoint', '')))
" 2>/dev/null || echo 0)
        if [ "${n_biz:-0}" -lt 1 ]; then
            echo "  [WARN] fingerprint failed: encounters/current call not in call_log — zombie server suspected" >&2
            return 1
        fi
    fi
    return 0
}

# 全局清理：脚本退出时（含 Ctrl-C）杀掉所有还活着的 server
cleanup_all() {
    kill_server_by_cmdline "server/app.py"
    kill_port_holder "$PORT"
}
trap cleanup_all EXIT INT TERM

echo "=========================================="
echo " HIS Drug Eval - Automated Runner (sandboxed)"
echo "=========================================="
echo " Agents: $AGENTS"
echo " Cases:  $CASES"
echo " Port:   $PORT"
echo "=========================================="

# 加固 #1：run_eval 启动第一行就清，不依赖 trap
echo "[startup] verifying port $PORT is free..."
kill_port_holder "$PORT"
kill_server_by_cmdline "server/app.py"

# 确保依赖
pip install flask -q 2>/dev/null || true

# === 清理 Claude Code CLI 项目级 memory（防跨项目污染）===
# Claude Code CLI 把每个 cwd 的会话存在 ~/.claude-internal/projects/<encoded-cwd>/*.jsonl
# 跑评 sandbox 路径含 his-cpoe / eval-runtime / agent-workspace 等关键词
# 不清的话上次跑评的"有 POST /v1/cases/finalize 这种 endpoint"会被这次 agent 当 prior 复用
# 详见 LEARNINGS.md §0
CLAUDE_PROJECTS_DIR="$HOME/.claude-internal/projects"
if [ -d "$CLAUDE_PROJECTS_DIR" ]; then
    echo "[cleanup] clearing claude_code memory for sandbox paths..."
    for d in "$CLAUDE_PROJECTS_DIR"/*Temp-his-cpoe* \
             "$CLAUDE_PROJECTS_DIR"/*Temp-eval-his* \
             "$CLAUDE_PROJECTS_DIR"/*Temp-claude-eval-runtime* \
             "$CLAUDE_PROJECTS_DIR"/*agent-workspace*; do
        if [ -e "$d" ]; then rm -rf "$d"; fi
    done
fi

for agent in $AGENTS; do
    echo ""
    echo ">>> Running agent: $agent"
    echo "-------------------------------------------"
    for case_id in $CASES; do
        echo "  [Case $case_id] Preparing sandbox..."

        # 启动新 server 前先清掉 5000 端口的僵尸（方案 B）
        kill_port_holder "$PORT"
        kill_server_by_cmdline "server/app.py"
        sleep 1

        # 在 /tmp 下创建独立 workspace（沙箱隔离）
        # prepare_workspace.sh 会把绝对路径回显到 stdout 末行
        WORKSPACE=$(bash runner/prepare_workspace.sh "case_${case_id}" | tail -1)

        if [ -z "$WORKSPACE" ] || [ ! -d "$WORKSPACE" ]; then
            echo "  [ERROR] Failed to prepare sandbox workspace"
            continue
        fi
        echo "  [Case $case_id] Sandbox: <system tmp>/$(basename "$WORKSPACE")"

        # 启动 server（server 端持有 case 信息，agent 看不到）
        # 删除旧 call_log 让 fingerprint 验证从 0 开始
        rm -f server/call_log.json
        python server/app.py --scenario "case_${case_id}" --port $PORT &

        # 加固 #2：fingerprint 验证 server 真的活着
        # 连续 3 次 health 200 + call_log 有 3 条记录（防分流到僵尸）
        if ! verify_server_alive "$PORT" "server/call_log.json"; then
            echo "  [ERROR] Server fingerprint verification failed for case $case_id"
            kill_server_by_cmdline "server/app.py"
            kill_port_holder "$PORT"
            sleep 2
            continue
        fi
        echo "  [Case $case_id] Server alive (3x health verified)"

        echo "  [Case $case_id] Running $agent..."
        RESPONSE_DIR="responses/${agent}/case_${case_id}"
        mkdir -p "$RESPONSE_DIR"

        # 加固 #3：单 case 5 分钟 timeout（normal 1-3 min, 异常 case_03/05 多轮反问 4 min, buffer 1 min）
        # adapter 收 workspace 绝对路径作为 cwd，agent 物理上看不到评测仓库
        AGENT_TIMEOUT=${AGENT_TIMEOUT:-300}
        if command -v timeout >/dev/null 2>&1; then
            timeout "${AGENT_TIMEOUT}s" bash "runner/adapters/${agent}.sh" "$WORKSPACE" "$RESPONSE_DIR"
            adapter_rc=$?
            if [ $adapter_rc -eq 124 ]; then
                echo "  [WARN] Case $case_id agent timed out after ${AGENT_TIMEOUT}s"
            fi
        else
            bash "runner/adapters/${agent}.sh" "$WORKSPACE" "$RESPONSE_DIR"
        fi

        # 收集服务端轨迹（agent 所有 API 调用历史，含最终的 POST /prescriptions）
        if [ -f "server/call_log.json" ]; then
            cp "server/call_log.json" "$RESPONSE_DIR/api_calls.json"
            echo "  [Case $case_id] [OK] api_calls.json collected"
        else
            echo "  [Case $case_id] [FAIL] No API calls recorded"
            echo '[]' > "$RESPONSE_DIR/api_calls.json"
        fi

        # 从 call_log 抽出最后一次成功提交（POST /prescriptions 201）作为 agent 最终交付物
        python -c "
import sys, json, os
# Windows 控制台默认 GBK，强制 UTF-8 避免 ✓/✗ 等字符崩溃
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
calls = json.load(open('$RESPONSE_DIR/api_calls.json', encoding='utf-8'))
success = [c for c in calls if c.get('endpoint') == '/api/v1/prescriptions'
           and c.get('method') == 'POST' and c.get('status_code') == 201]
out = '$RESPONSE_DIR/output.json'
if success:
    json.dump(success[-1].get('request', {}), open(out, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    print('  [Case $case_id] [OK] output.json derived from final 201 prescription')
else:
    json.dump({'error': 'Agent did not successfully POST /prescriptions'},
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('  [Case $case_id] [FAIL] No successful prescription submission')
"

        # 跨平台杀 server（按命令行匹配 Windows 真实 PID）
        kill_server_by_cmdline "server/app.py --scenario case_${case_id}"
        sleep 1

        # 默认保留沙箱目录便于事后审计；如需清理设置 KEEP_SANDBOX=0
        if [ "${KEEP_SANDBOX:-1}" = "0" ]; then
            rm -rf "$WORKSPACE"
        else
            echo "  [Case $case_id] Sandbox kept at: <system tmp>/$(basename "$WORKSPACE")"
        fi

        echo "  [Case $case_id] Done."
    done
done

echo ""
echo "=========================================="
echo " Running Verifier..."
echo "=========================================="
mkdir -p results
# 如果设置了 DEEPSEEK_API_KEY 环境变量，verifier 会自动启用 LLM judge 维度
python verifier/verify.py --responses responses/ --rubrics rubrics/rubrics.json --output results/scores.json

echo ""
echo "=========================================="
echo " EVALUATION COMPLETE"
echo " Results: results/scores.json"
echo "=========================================="
