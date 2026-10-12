#!/bin/bash
# 启动 AI 面试系统（后端 + 前端）
# 使用方式：bash start.sh

echo "=== 启动后端 ==="
launchctl start com.benerniang.interview-backend 2>/dev/null

echo "=== 启动前端 ==="
launchctl start com.benerniang.interview-frontend 2>/dev/null

sleep 3

echo ""
echo "=== 服务状态 ==="
launchctl list | grep benerniang || echo "启动失败，请检查日志"

echo ""
echo "=== 访问地址（内网）==="
IP=$(ipconfig getifaddr en0 2>/dev/null || echo "localhost")
echo "管理员后台：http://${IP}:3000/console"
echo "候选人面试：http://${IP}:3000/i/{面试码}"
echo "后端 API：  http://${IP}:8000"
