#!/bin/bash
# 停止 AI 面试系统（后端 + 前端）
# 使用方式：bash stop.sh

echo "=== 停止后端 ==="
launchctl stop com.benerniang.interview-backend 2>/dev/null

echo "=== 停止前端 ==="
launchctl stop com.benerniang.interview-frontend 2>/dev/null

sleep 2

echo ""
echo "服务已停止"
launchctl list | grep benerniang || echo "已全部停止"
