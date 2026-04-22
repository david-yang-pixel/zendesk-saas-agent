#!/usr/bin/env bash
# scripts/init-resources.sh — 在服务器上首次初始化资源目录骨架
set -euo pipefail

RESOURCES_DIR="${1:-/opt/zd-agent/resources}"

echo "Initializing resources at: $RESOURCES_DIR"

mkdir -p "$RESOURCES_DIR"/{saas源代码,saas版知识库,工单附件}
mkdir -p "$RESOURCES_DIR/zendesk-cli/工单记录"
mkdir -p "$RESOURCES_DIR/.state/logs"

chmod 755 "$RESOURCES_DIR"
chmod 700 "$RESOURCES_DIR/.state"

echo "Directory structure created:"
find "$RESOURCES_DIR" -maxdepth 2 -type d

echo ""
echo "Next steps:"
echo "  1. rsync saas源代码/* into $RESOURCES_DIR/saas源代码/"
echo "  2. rsync saas版知识库/* into $RESOURCES_DIR/saas版知识库/"
echo "  3. rsync zendesk-cli/工单记录/* into $RESOURCES_DIR/zendesk-cli/工单记录/"
