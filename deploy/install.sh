#!/usr/bin/env bash
#
# Synetra_DataFlow Linux 一键部署脚本（幂等可重跑）。
#
# 覆盖范围（tasks.md 1.1）：Web 后端 (FastAPI)、前端构建产物 (React)、
# 元数据库 (MySQL，探测并提示装配)、systemd 服务。
#
# 设计为幂等：在已部署主机上重复执行，或中途失败后重跑，
# 均跳过/修复已满足步骤，不破坏既有部署与数据（不会清空数据库）。
#
# 用法:
#   sudo bash deploy/install.sh [--app-user <user>] [--web-user <user>] [--skip-frontend]
#
# 说明：本脚本依赖 root 权限安装系统服务；在无 sudo 的沙箱中请仅做审阅，
# 并在目标 Linux 主机上以 root 执行验证。

set -euo pipefail

# ---------- 可配置 ----------
APP_USER="${APP_USER:-synetra}"
WEB_USER="${WEB_USER:-synetra}"
INSTALL_BASE="${INSTALL_BASE:-/opt/synetra}"
RUN_USER_ID="$(id -u)"
IS_ROOT=0
[ "${RUN_USER_ID}" -eq 0 ] && IS_ROOT=1

SKIP_FRONTEND="false"
for arg in "$@"; do
  case "${arg}" in
    --skip-frontend) SKIP_FRONTEND="true" ;;
    --app-user=*) APP_USER="${arg#*=}" ;;
    --web-user=*) WEB_USER="${arg#*=}" ;;
  esac
done

log() { echo "[synetra:install] $*"; }

# ---------- 权限与用户（幂等） ----------
prepare_users() {
  if [ "${IS_ROOT}" -ne 1 ]; then
    log "WARN: 非 root 运行，跳过用户创建与 systemd 装配；建议在目标主机以 root 执行。"
    return
  fi
  for u in "${APP_USER}" "${WEB_USER}"; do
    if ! id "${u}" >/dev/null 2>&1; then
      log "创建系统用户: ${u}"
      useradd --system --create-home --home-dir "/var/lib/${u}" --shell /usr/sbin/nologin "${u}"
    else
      log "用户已存在，跳过: ${u}"
    fi
  done
  if command -v usermod >/dev/null 2>&1; then
    usermod -a -G "${APP_USER}" "${WEB_USER}"
  fi
}

# ---------- 依赖检查（幂等） ----------
check_deps() {
  log "检查运行时依赖..."
  command -v python3 >/dev/null 2>&1 || { log "缺少 python3"; exit 2; }
  command -v curl >/dev/null 2>&1 || { log "缺少 curl"; exit 2; }
  command -v mysql >/dev/null 2>&1 || log "提示: 未发现 mysql 客户端。请确认已部署 MySQL 元数据库（本脚本不自动安装数据库）。"
  command -v node >/dev/null 2>&1 || { [ "${SKIP_FRONTEND}" = "true" ] || log "提示: 未发现 node；将跳过前端构建，请以 --skip-frontend 或稍后手动构建。"; }
}

# ---------- Python 依赖（幂等：已装则跳过） ----------
install_backend() {
  local backend_dir="${INSTALL_BASE}/backend"
  [ -d "${backend_dir}" ] || { log "源码目录不存在: ${backend_dir}（请先将源码部署到该路径）"; return 3; }
  if [ -x "${backend_dir}/.venv/bin/python" ] && "${backend_dir}/.venv/bin/python" -c "import fastapi" >/dev/null 2>&1; then
    log "后端依赖已安装，跳过：${backend_dir}/.venv"
    return
  fi
  log "创建虚拟环境并安装后端依赖..."
  python3 -m venv "${backend_dir}/.venv"
  "${backend_dir}/.venv/bin/python" -m pip install --upgrade pip
  "${backend_dir}/.venv/bin/python" -m pip install -e "${backend_dir}[dev]"
  log "后端依赖安装完成"
}

# ---------- 前端构建（幂等：产物存在则跳过） ----------
install_frontend() {
  [ "${SKIP_FRONTEND}" = "true" ] && { log "跳过前端构建（--skip-frontend）"; return; }
  local app_dir="${INSTALL_BASE}/frontend"
  [ -d "${app_dir}" ] || { log "前端目录不存在: ${app_dir}"; return 3; }
  if [ -d "${app_dir}/dist" ] && [ -f "${app_dir}/dist/index.html" ]; then
    log "前端产物已存在，跳过构建：${app_dir}/dist"
    return
  fi
  log "前端 npm install + build..."
  ( cd "${app_dir}" && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi && npm run build )
  log "前端构建完成"
}

# ---------- systemd 服务（幂等） ----------
install_systemd() {
  [ "${IS_ROOT}" -ne 1 ] && return
  local unit="/etc/systemd/system/synetra-web.service"
  cat > "${unit}" <<EOF
[Unit]
Description=Synetra_DataFlow Web backend
After=network.target mysql.service

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${INSTALL_BASE}/backend
ExecStart=${INSTALL_BASE}/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
EnvironmentFile=${INSTALL_BASE}/backend/.env

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable synetra-web.service
  log "systemd 服务已装配并 enable（启动: systemctl start synetra-web）"
}

# ---------- MySQL 元数据库（幂等，可选：仅在提供凭据时执行） ----------
# 通过环境变量启用，避免在未装配 MySQL 的主机上阻塞部署:
#   MYSQL_ADMIN_USER / MYSQL_ADMIN_PASS / MYSQL_HOST / MYSQL_META_DB
init_mysql() {
  if [ -z "${MYSQL_ADMIN_USER:-}" ] || [ -z "${MYSQL_META_DB:-}" ]; then
    log "跳过我元建库：未提供 MYSQL_ADMIN_USER/MYSQL_META_DB（幂等部署不阻塞）。"
    return
  fi
  local host="${MYSQL_HOST:-127.0.0.1}"
  log "校验/创建元数据库 ${MYSQL_META_DB}（幂等）..."
  local args=(-h "${host}" -u "${MYSQL_ADMIN_USER}" -p"${MYSQL_ADMIN_PASS:-}")
  mysql "${args[@]}" -e "CREATE DATABASE IF NOT EXISTS \`${MYSQL_META_DB}\` DEFAULT CHARACTER SET utf8mb4;" || {
    log "WARN: 元库创建失败（若为权限/连接问题，请先装配 MySQL 后再重跑本脚本）"
    return
  }
  log "元数据库就绪: ${MYSQL_META_DB}"
}

# ---------- 后端运行环境 .env（幂等：仅当缺失时写入，不回写已存在值） ----------
write_env() {
  local backend_dir="${INSTALL_BASE}/backend"
  local env="${backend_dir}/.env"
  [ -e "${env}" ] && { log "后端 .env 已存在，跳过写入: ${env}"; return; }
  cat > "${env}" <<EOF
SYNETRA_DATA_DIR=${backend_dir}/data
SYNETRA_SCRIPTS_ROOT=${backend_dir}/data
SYNETRA_METADATA_DB_DSN=mysql://${MYSQL_META_DB_USER:-synetra}:${MYSQL_META_DB_PASS:-}@${MYSQL_HOST:-127.0.0.1}/${MYSQL_META_DB:-synetra_meta}
EOF
  chmod 600 "${env}"
  log "已生成后端 .env: ${env}"
}

# ---------- 部署自检 ----------
verify() {
  local port="${WEB_PORT:-8000}"
  log "自检: 等待后端 ${INSTALL_BASE}/backend @127.0.0.1:${port} 健康检查..."
  local i
  for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      log "自检通过: http://127.0.0.1:${port}/health -> ok"
      return 0
    fi
    sleep 2
  done
  log "WARN: 后端健康检查未在 60s 内通过（服务可能尚未随本脚本启动）。请 systemctl start synetra-web 后重试。"
}

main() {
  log "开始 Synetra_DataFlow 部署（幂等）..."
  prepare_users
  check_deps
  install_backend
  install_frontend
  init_mysql
  write_env
  install_systemd
  verify
  log "完成。验证: curl http://<host>:8000/health 应返回 {\"status\":\"ok\",...}"
}

main "$@"