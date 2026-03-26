#!/usr/bin/env bash
# ============================================================
# Dify 企业版工单工作区一键初始化脚本
# 将所有源码仓库克隆到标准目录结构中
# ============================================================

set -euo pipefail

# ── 颜色输出工具 ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

# ── 定位工作区根目录（脚本所在目录的上一级）──────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo -e "${BOLD}================================================${RESET}"
echo -e "${BOLD}  Dify 工单工作区初始化脚本${RESET}"
echo -e "${BOLD}  工作区根目录: ${WORKSPACE_ROOT}${RESET}"
echo -e "${BOLD}================================================${RESET}"
echo ""

# ── 克隆函数：已存在则跳过，不存在则克隆 ────────────────────
clone_repo() {
  local target_dir="$1"   # 目标目录（绝对路径）
  local repo_url="$2"     # 仓库地址
  local label="$3"        # 显示名称

  if [ -d "${target_dir}/.git" ]; then
    warn "${label} 已存在，跳过克隆（如需更新请手动执行 git pull）"
    return 0
  fi

  if [ -d "${target_dir}" ] && [ "$(ls -A "${target_dir}")" ]; then
    warn "${label} 目录非空但不是 git 仓库，跳过"
    return 0
  fi

  info "正在克隆 ${label} ..."
  if git clone "${repo_url}" "${target_dir}" 2>&1; then
    success "${label} 克隆完成"
  else
    error "${label} 克隆失败，请检查网络或 SSH Key 配置"
    return 1
  fi
}

# ── 创建父目录 ────────────────────────────────────────────────
mkdir -p "${WORKSPACE_ROOT}/dify-community-and-enterpise-base-source"
mkdir -p "${WORKSPACE_ROOT}/dify-enterprise-source"
mkdir -p "${WORKSPACE_ROOT}/dify-plugin-base-source"

FAILED=0

echo -e "\n${BOLD}── 1. 社区版基础源码 ─────────────────────────────${RESET}"

clone_repo \
  "${WORKSPACE_ROOT}/dify-community-and-enterpise-base-source/dify" \
  "https://github.com/langgenius/dify.git" \
  "dify（社区版核心）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-community-and-enterpise-base-source/dify-sandbox" \
  "https://github.com/langgenius/dify-sandbox.git" \
  "dify-sandbox（代码执行沙箱）" || FAILED=$((FAILED + 1))

echo -e "\n${BOLD}── 2. 企业版组件 ─────────────────────────────────${RESET}"

clone_repo \
  "${WORKSPACE_ROOT}/dify-enterprise-source/dify-enterprise" \
  "https://github.com/langgenius/dify-enterprise.git" \
  "dify-enterprise（Go 企业版后端）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-enterprise-source/dify-enterprise-frontend" \
  "git@github.com:langgenius/dify-enterprise-frontend.git" \
  "dify-enterprise-frontend（企业版前端）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-enterprise-source/dify-helm" \
  "https://github.com/langgenius/dify-helm.git" \
  "dify-helm（Helm Chart 部署）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-enterprise-source/dify-monitor" \
  "git@github.com:ZhouhaoJiang/dify-monitor.git" \
  "dify-monitor（监控系统）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-enterprise-source/aws-cdk-for-dify" \
  "git@github.com:langgenius/aws-cdk-for-dify.git" \
  "aws-cdk-for-dify（AWS CDK 部署）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-enterprise-source/dify-ee-terraform" \
  "git@github.com:langgenius/dify-ee-terraform.git" \
  "dify-ee-terraform（Terraform 部署）" || FAILED=$((FAILED + 1))

echo -e "\n${BOLD}── 3. 插件系统 ───────────────────────────────────${RESET}"

mkdir -p "${WORKSPACE_ROOT}/dify-plugin-base-source"

clone_repo \
  "${WORKSPACE_ROOT}/dify-plugin-base-source/dify-plugin-daemon" \
  "https://github.com/langgenius/dify-plugin-daemon.git" \
  "dify-plugin-daemon（插件守护进程）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-plugin-base-source/dify-plugin-sdks" \
  "git@github.com:langgenius/dify-plugin-sdks.git" \
  "dify-plugin-sdks（插件 SDK）" || FAILED=$((FAILED + 1))

clone_repo \
  "${WORKSPACE_ROOT}/dify-plugin-base-source/dify-official-plugins" \
  "https://github.com/langgenius/dify-official-plugins.git" \
  "dify-official-plugins（官方插件源码）" || FAILED=$((FAILED + 1))

echo -e "\n${BOLD}── 4. 工单交互工具 ───────────────────────────────${RESET}"

clone_repo \
  "${WORKSPACE_ROOT}/zendesk-cli" \
  "git@github.com:ZhouhaoJiang/zendesk-cli.git" \
  "zendesk-cli（Zendesk 工单 CLI）" || FAILED=$((FAILED + 1))

# zendesk-cli 克隆成功后，检查 .env 是否需要配置
if [ -d "${WORKSPACE_ROOT}/zendesk-cli/.git" ]; then
  if [ ! -f "${WORKSPACE_ROOT}/zendesk-cli/.env" ]; then
    cp "${WORKSPACE_ROOT}/zendesk-cli/.env.example" "${WORKSPACE_ROOT}/zendesk-cli/.env"
    warn "zendesk-cli/.env 已从模板创建，请填入 Zendesk 凭据后再使用："
    echo -e "  编辑文件: ${WORKSPACE_ROOT}/zendesk-cli/.env"
    echo -e "  需填写:   ZENDESK_SUBDOMAIN / ZENDESK_EMAIL / ZENDESK_API_TOKEN"
  else
    success "zendesk-cli/.env 已存在，跳过初始化"
  fi

  # 安装 Python 依赖（需要 uv）
  if command -v uv &>/dev/null; then
    info "正在安装 zendesk-cli 依赖（uv sync）..."
    if (cd "${WORKSPACE_ROOT}/zendesk-cli" && uv sync 2>&1); then
      success "zendesk-cli 依赖安装完成"
    else
      warn "zendesk-cli 依赖安装失败，请手动执行: cd zendesk-cli && uv sync"
    fi
  else
    warn "未检测到 uv，请手动安装依赖: cd zendesk-cli && uv sync"
    echo -e "  安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
  fi
fi

# ── 汇总结果 ──────────────────────────────────────────────────
echo ""
echo -e "${BOLD}================================================${RESET}"
if [ "${FAILED}" -eq 0 ]; then
  success "所有仓库初始化完成！"
  echo ""
  echo -e "  工作区目录结构："
  echo -e "  ${WORKSPACE_ROOT}/"
  echo -e "  ├── dify-community-and-enterpise-base-source/"
  echo -e "  │   ├── dify"
  echo -e "  │   └── dify-sandbox"
  echo -e "  ├── dify-enterprise-source/"
  echo -e "  │   ├── dify-enterprise"
  echo -e "  │   ├── dify-enterprise-frontend"
  echo -e "  │   ├── dify-helm"
  echo -e "  │   ├── dify-monitor"
  echo -e "  │   ├── aws-cdk-for-dify"
  echo -e "  │   └── dify-ee-terraform"
  echo -e "  ├── dify-plugin-base-source/"
  echo -e "  │   ├── dify-plugin-daemon"
  echo -e "  │   ├── dify-plugin-sdks"
  echo -e "  │   └── dify-official-plugins"
  echo -e "  └── zendesk-cli/               ← 工单 CLI 工具"
  echo ""
  echo -e "  ${YELLOW}下一步：${RESET}编辑 zendesk-cli/.env 填入 Zendesk 凭据"
  echo -e "  验证：  cd zendesk-cli && uv run zd me"
else
  warn "初始化完成，但有 ${FAILED} 个仓库克隆失败，请检查："
  echo -e "  1. 网络连接是否正常"
  echo -e "  2. SSH Key 是否已添加到 GitHub（用于 git@ 开头的仓库）"
  echo -e "  3. 是否有对应仓库的访问权限"
fi
echo -e "${BOLD}================================================${RESET}"
