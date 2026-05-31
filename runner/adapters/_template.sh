#!/bin/bash
# Adapter template - 复制此文件并改名为 <your_agent>.sh，按下方 TODO 填写
# 用法: bash <your_agent>.sh <workspace_dir> <output_dir>
#
# Adapter 的职责（无论你接入哪个 agent CLI 都需要做完）：
# 1. cd 到 workspace_dir（agent 看到的输入只有这个目录里的 task_prompt.md）
# 2. 把 task_prompt.md 喂给 CLI
# 3. 把完整轨迹（thinking / tool_use / tool_result / 最终回复）写到 trajectory.log
# 4. 推荐用 stream-json 格式，verifier 解析它来评 trajectory_layer 分数
#
# 跑 run_eval.sh --agents your_agent ... 即可调用本 adapter

WORKSPACE_DIR=$1
OUTPUT_DIR=${2:-"$WORKSPACE_DIR"}

WORKSPACE_DIR=$(cd "$WORKSPACE_DIR" && pwd)
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)

cd "$WORKSPACE_DIR"
PROMPT=$(cat task_prompt.md)

echo "============================================" > "$OUTPUT_DIR/trajectory.log"
echo "AGENT: <your_agent_name>" >> "$OUTPUT_DIR/trajectory.log"
echo "TIMESTAMP: $(date -Iseconds)" >> "$OUTPUT_DIR/trajectory.log"
echo "WORKSPACE: $WORKSPACE_DIR" >> "$OUTPUT_DIR/trajectory.log"
echo "============================================" >> "$OUTPUT_DIR/trajectory.log"
echo "" >> "$OUTPUT_DIR/trajectory.log"
echo "========== PROMPT ==========" >> "$OUTPUT_DIR/trajectory.log"
echo "$PROMPT" >> "$OUTPUT_DIR/trajectory.log"
echo "" >> "$OUTPUT_DIR/trajectory.log"
echo "========== EXECUTION ==========" >> "$OUTPUT_DIR/trajectory.log"

# ============================================================
# TODO: 把下面这一行替换成你的 CLI 真实调用命令
# ============================================================
# 几个参考样例（见同目录 *.sh.example）：
#
#   Claude Code:
#     timeout 300 claude -p "$PROMPT" --dangerously-skip-permissions \
#         --verbose --output-format stream-json 2>&1 | tee -a "$OUTPUT_DIR/trajectory.log"
#
#   CodeBuddy CLI:
#     codebuddy -p "$PROMPT" -y --output-format stream-json 2>&1 | tee -a "$OUTPUT_DIR/trajectory.log"
#
#   Qoder CN:
#     qoderclicn -p "$PROMPT" --dangerously-skip-permissions \
#         -w "$WORKSPACE_DIR" --output-format stream-json 2>&1 | tee -a "$OUTPUT_DIR/trajectory.log"
#
your_agent_cli -p "$PROMPT" 2>&1 | tee -a "$OUTPUT_DIR/trajectory.log"

echo "" >> "$OUTPUT_DIR/trajectory.log"
echo "========== END ==========" >> "$OUTPUT_DIR/trajectory.log"

# 如果你的 agent 把最终结果写到了 workspace/output.json，runner 会自动收集
# 否则 verifier 会从 server 端 call_log 里反推（适用于 finalize 走 HTTP 的方式）
if [ -f "$WORKSPACE_DIR/output.json" ]; then
    cp "$WORKSPACE_DIR/output.json" "$OUTPUT_DIR/output.json"
fi
