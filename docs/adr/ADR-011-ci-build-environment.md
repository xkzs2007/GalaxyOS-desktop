# ADR-011: CI 构建环境策略

## 状态

已采纳

## 背景

此前 GitHub Actions 工作流每次构建都从头安装 Python 依赖（requirements-core.txt + openjiuwen + pymilvus 等），耗时 2-5 分钟。GHCR 上已有预装依赖的 Docker 镜像但未被使用。

## 决策

统一使用 GHCR 容器作为 CI 构建环境。Linux 和 Windows runner 都通过 `container: ghcr.io/xkzs2007/galaxyos-desktop:latest` 使用预装依赖镜像，容器内只需 `pip install .` + PyInstaller 打包。

## 替代方案

1. **裸机 + pip cache**：使用 `actions/setup-python` 的 pip cache → 缓存命中率不稳定，仍需部分安装
2. **混合模式**：Linux 用容器，Windows 用裸机 → 不一致，维护两套流程
3. **beforeBuildCommand**：Tauri 内置构建前命令 → 不够灵活，无法处理复杂依赖

## 理由

- 容器内依赖预装，构建步骤从 7 步简化为 3 步
- 双平台一致体验
- Docker 镜像版本化，可回滚
- 消除 pip install 网络不稳定风险

## 后果

- 需要 Docker 镜像先行构建（build-docker → build-tauri 串行）
- 镜像更新需重新构建推送
- 容器内 Rust 工具链需额外安装（不在镜像中）