# ADR-010: BuildKit 多平台构建

## 状态

已采纳

## 背景

GalaxyOS 的 CI/CD 工作流需要在 Windows 和 Linux 双平台上构建 Tauri 桌面应用。此前每次 CI 都从头安装 Python 依赖（pip install），耗时 2-5 分钟，且 GHCR 上已有预装依赖的 Docker 镜像未被利用。

## 决策

使用 Docker BuildKit 多平台构建，通过 `Dockerfile.buildkit` 同时构建 Linux (amd64) 和 Windows (amd64) 镜像，推送到 GHCR。CI 工作流通过 `container:` 指令使用 GHCR 镜像作为构建环境。

## 替代方案

1. **独立 Dockerfile**：为 Linux 和 Windows 分别维护 Dockerfile → 维护成本高
2. **仅 Linux 容器**：Windows 保留裸机 pip install → 不一致，Windows 构建慢
3. **不用容器**：全部裸机安装 → 每次构建 5+ 分钟依赖安装

## 理由

- 双平台统一构建环境，消除依赖安装时间
- BuildKit `--mount=type=cache` 缓存 pip/apt 下载
- 多阶段构建减小最终镜像体积
- GHCR 镜像可复用，避免重复工作

## 后果

- 首次构建 Docker 镜像需 10-15 分钟
- Windows 容器基于 Server Core，镜像较大（~3GB）
- build-tauri Job 需要 `needs: build-docker` 串行依赖
- macOS 不支持容器，不在构建矩阵中