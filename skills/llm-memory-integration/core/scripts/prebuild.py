#!/usr/bin/env python3
"""
预编译构建脚本

在有编译能力的环境中运行，将 hnswlib 和 C++ 工具链预编译打包，
生成 prebuilt/ 目录，供沙箱环境直接使用（无需 root/编译器）。

用法:
    # 预编译所有（hnswlib + 工具链）
    python scripts/prebuild.py --all

    # 只预编译 hnswlib
    python scripts/prebuild.py --hnswlib

    # 只预编译 C++ 工具链
    python scripts/prebuild.py --toolchain

    # 指定输出目录
    python scripts/prebuild.py --all --output /path/to/prebuilt

    # 指定 Python 版本（用于 wheel 命名）
    python scripts/prebuild.py --hnswlib --python-version 3.10

构建产物:
    prebuilt/
    ├── extensions/
    │   └── hnswlib/
    │       ├── hnswlib-*.whl          # 预编译 wheel
    │       └── METADATA.json
    ├── toolchain/
    │   ├── gcc_toolchain.tar.bz2      # gcc+binutils+libs 归档
    │   ├── cmake_toolchain.tar.bz2    # cmake 归档
    │   └── METADATA.json
    └── wheels/
        └── *.whl                       # 预缓存 wheel
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from datetime import datetime


def _get_build_env() -> dict:
    """
    获取构建环境变量，检测并配置本地工具链路径

    查找 conda-forge 工具链或已解压的 prebuilt 工具链，
    设置 CC/CXX/CFLAGS/CXXFLAGS/LDFLAGS 以确保 pip 编译时使用正确的编译器和 sysroot。
    """
    env = os.environ.copy()

    # 搜索工具链路径
    toolchain_candidates = [
        # prebuilt 工具链默认安装位置
        Path.home() / '.sandbox' / 'cpp_toolchain',
        # conda 环境
        Path(os.environ.get('CONDA_PREFIX', '')) if os.environ.get('CONDA_PREFIX') else None,
    ]

    for tc_dir in toolchain_candidates:
        if tc_dir is None or not tc_dir.exists():
            continue

        bin_dir = tc_dir / 'bin'
        if not bin_dir.exists():
            # 也搜索子目录
            for bd in tc_dir.rglob('bin'):
                bin_dir = bd
                break

        # 检测 g++
        gxx_path = None
        for name in ['g++', 'x86_64-conda-linux-gnu-g++']:
            candidate = bin_dir / name
            if candidate.exists():
                gxx_path = candidate
                break

        # 检测 gcc
        gcc_path = None
        for name in ['gcc', 'x86_64-conda-linux-gnu-gcc']:
            candidate = bin_dir / name
            if candidate.exists():
                gcc_path = candidate
                break

        if gxx_path:
            env['CXX'] = str(gxx_path)
            env.setdefault('CC', str(gcc_path) if gcc_path else str(gxx_path))
            # 将 bin 目录加入 PATH
            env['PATH'] = f"{bin_dir}:{env.get('PATH', '')}"

            # 检测 sysroot 路径
            # conda-forge 的 sysroot 通常在 x86_64-conda-linux-gnu/sysroot
            for sysroot in tc_dir.rglob('sysroot'):
                if sysroot.is_dir():
                    sysroot_str = str(sysroot)
                    env['CFLAGS'] = f"--sysroot={sysroot_str} {env.get('CFLAGS', '')}"
                    env['CXXFLAGS'] = f"--sysroot={sysroot_str} {env.get('CXXFLAGS', '')}"
                    env['LDFLAGS'] = f"--sysroot={sysroot_str} {env.get('LDFLAGS', '')}"
                    # 也设置常用的 conda 编译器规格变量
                    env['CONDA_BUILD_SYSROOT'] = sysroot_str
                    print(f"  检测到 sysroot: {sysroot_str}")
                    break

            print(f"  使用工具链: CXX={env.get('CXX')}, CC={env.get('CC')}")
            break

    return env


def get_output_dir(args) -> Path:
    """获取输出目录"""
    if args.output:
        return Path(args.output)
    return Path(__file__).parent.parent / 'prebuilt'


def build_hnswlib(output_dir: Path, python_version: str = None) -> dict:
    """
    从源码编译 hnswlib Python 包

    生成预编译 wheel 文件，放入 prebuilt/extensions/hnswlib/
    """
    print("=" * 60)
    print("预编译 hnswlib")
    print("=" * 60)

    ext_dir = output_dir / 'extensions' / 'hnswlib'
    ext_dir.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    results = {'status': 'unknown', 'artifacts': []}

    # 检测本地工具链环境（conda-forge 或已解压的 prebuilt 工具链）
    build_env = _get_build_env()

    # 策略1: 直接 pip wheel 编译
    print("\n[策略1] pip wheel 编译 hnswlib...")
    wheel_dir = tempfile.mkdtemp(prefix='hnswlib_wheel_')
    try:
        # 先尝试带构建隔离（更可靠，自动安装 pybind11 等构建依赖）
        cmd = [python, '-m', 'pip', 'wheel', 'hnswlib', '--no-deps',
               '-w', wheel_dir]
        print(f"  执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                env=build_env)

        if result.returncode != 0:
            # 回退: 尝试 --no-build-isolation（已安装构建依赖时更快）
            cmd_fallback = [python, '-m', 'pip', 'wheel', 'hnswlib', '--no-deps',
                            '-w', wheel_dir, '--no-build-isolation']
            print(f"  回退: {' '.join(cmd_fallback)}")
            result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=600,
                                    env=build_env)

        if result.returncode == 0:
            # 找到生成的 wheel
            wheels = list(Path(wheel_dir).glob('hnswlib*.whl'))
            if wheels:
                whl = wheels[0]
                dst = ext_dir / whl.name
                shutil.copy2(str(whl), str(dst))
                print(f"  成功: {whl.name}")
                results['status'] = 'success'
                results['artifacts'].append(str(dst))
            else:
                print("  pip wheel 成功但未找到 .whl 文件")
        else:
            print(f"  pip wheel 失败: {result.stderr[:200]}")
    finally:
        shutil.rmtree(wheel_dir, ignore_errors=True)

    # 策略2: 从 GitHub 源码编译
    if results['status'] != 'success':
        print("\n[策略2] 从 GitHub 源码编译 hnswlib...")
        src_dir = tempfile.mkdtemp(prefix='hnswlib_src_')
        try:
            # 克隆源码
            clone_cmd = ['git', 'clone', '--depth', '1',
                         'https://github.com/nmslib/hnswlib.git', src_dir]
            print(f"  克隆: {clone_cmd}")
            subprocess.run(clone_cmd, capture_output=True, text=True, timeout=120)

            # 编译 Python 绑定
            py_bindings = Path(src_dir) / 'python'
            if py_bindings.exists():
                build_cmd = [python, '-m', 'pip', 'wheel', '.', '--no-deps',
                             '-w', str(ext_dir)]
                print(f"  编译: {' '.join(build_cmd)}")
                result = subprocess.run(
                    build_cmd, capture_output=True, text=True, timeout=600,
                    cwd=str(py_bindings), env=build_env
                )
                if result.returncode == 0:
                    wheels = list(ext_dir.glob('hnswlib*.whl'))
                    if wheels:
                        print(f"  成功: {wheels[0].name}")
                        results['status'] = 'success'
                        results['artifacts'].append(str(wheels[0]))
                    else:
                        print("  编译成功但未找到 wheel")
                else:
                    print(f"  编译失败: {result.stderr[:300]}")
        finally:
            shutil.rmtree(src_dir, ignore_errors=True)

    # 策略3: 尝试下载预编译 wheel（从 PyPI）
    if results['status'] != 'success':
        print("\n[策略3] 从 PyPI 下载预编译 wheel...")
        try:
            import urllib.request
            py_ver = f"{sys.version_info.major}{sys.version_info.minor}"
            machine = platform.machine()

            # 查询 PyPI API
            url = 'https://pypi.org/pypi/hnswlib/json'
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            latest_ver = data.get('info', {}).get('version', '')
            releases = data.get('releases', {}).get(latest_ver, [])

            # 查找匹配的 wheel
            for release in releases:
                fname = release.get('filename', '')
                whl_url = release.get('url', '')
                if not fname.endswith('.whl'):
                    continue
                # 匹配 Python 版本和平台
                if f'cp{py_ver}' in fname and machine in fname:
                    print(f"  下载: {fname}")
                    dst = ext_dir / fname
                    urllib.request.urlretrieve(whl_url, str(dst))
                    results['status'] = 'success'
                    results['artifacts'].append(str(dst))
                    print(f"  成功: {fname}")
                    break
            else:
                print(f"  未找到匹配的 wheel (cp{py_ver}, {machine})")
        except Exception as e:
            print(f"  下载失败: {e}")

    # 写入元数据
    metadata = {
        'name': 'hnswlib',
        'build_time': datetime.now().isoformat(),
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}",
        'platform': platform.machine(),
        'system': platform.system(),
        'status': results['status'],
    }
    (ext_dir / 'METADATA.json').write_text(json.dumps(metadata, indent=2))

    print(f"\nhnswlib 预编译结果: {results['status']}")
    return results


def build_toolchain(output_dir: Path) -> dict:
    """
    从 conda-forge 下载并打包 C++ 编译工具链

    将 gcc_impl, binutils_impl, cmake, make 及其依赖
    下载、解压、合并为单个归档文件，放入 prebuilt/toolchain/
    """
    print("=" * 60)
    print("预编译 C++ 工具链")
    print("=" * 60)

    toolchain_dir = output_dir / 'toolchain'
    toolchain_dir.mkdir(parents=True, exist_ok=True)

    machine = platform.machine()
    if machine != 'x86_64':
        print(f"  警告: 当前平台 {machine}，工具链下载仅支持 x86_64")

    conda_base_url = 'https://conda.anaconda.org/conda-forge/linux-64/'

    # 需要下载的包列表（安装顺序）
    packages = [
        # 核心编译器（_impl 版本，非元包）
        'binutils_impl_linux-64',
        'gcc_impl_linux-64',
        'gxx_impl_linux-64',
        # 构建工具
        'cmake',
        'make',
        # 运行时库依赖
        'libgcc-ng',
        'libstdcxx-ng',
        # C++ 标准库头文件（编译 C++ 代码必须）
        'libstdcxx-devel_linux-64',
    ]

    # 某些包在 defaults 频道而非 conda-forge（如 sysroot）
    # 必须包含 sysroot_linux-64，否则 g++ 找不到 stdlib.h/features.h 等系统头文件，
    # 导致 Python C 扩展编译失败（RuntimeError: Unsupported compiler）
    EXTRA_CHANNEL_PACKAGES = {
        'sysroot_linux-64': 'https://repo.anaconda.com/pkgs/main/linux-64/',
    }

    results = {'status': 'unknown', 'packages': {}, 'artifacts': []}

    # 创建临时目录用于解压合并
    merged_dir = tempfile.mkdtemp(prefix='toolchain_merge_')

    try:
        # 获取 repodata
        print("\n获取 conda-forge repodata...")
        import urllib.request
        repodata_url = f"{conda_base_url}repodata.json"
        req = urllib.request.Request(repodata_url, headers={'Accept': 'application/json'})

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                repodata = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f"  获取 repodata 失败: {e}")
            results['status'] = 'failed'
            return results

        packages_meta = {**repodata.get('packages', {}),
                         **repodata.get('packages.conda', {})}

        # 下载并解压每个包
        for pkg_name in packages:
            print(f"\n处理: {pkg_name}")

            # 查找最新版本
            best_pkg = None
            best_ver = ''
            for pkg_filename, pkg_meta in packages_meta.items():
                if not pkg_filename.startswith(pkg_name + '-'):
                    continue
                if pkg_meta.get('subdir') != 'linux-64':
                    continue
                # 排除元包（只下载 _impl 或非编译器包）
                ver = pkg_meta.get('version', '0')
                if ver > best_ver:
                    best_ver = ver
                    best_pkg = (pkg_filename, pkg_meta)

            if not best_pkg:
                print(f"  未找到 {pkg_name}")
                results['packages'][pkg_name] = {'status': 'not_found'}
                continue

            pkg_filename, pkg_meta = best_pkg
            download_url = f"{conda_base_url}{pkg_filename}"

            # 下载
            cache_path = Path(tempfile.gettempdir()) / 'conda_cache' / pkg_filename
            cache_path.parent.mkdir(exist_ok=True)

            if not cache_path.exists():
                print(f"  下载: {pkg_filename}...")
                try:
                    urllib.request.urlretrieve(download_url, str(cache_path))
                    print(f"  下载完成")
                except Exception as e:
                    print(f"  下载失败: {e}")
                    results['packages'][pkg_name] = {'status': 'download_failed'}
                    continue
            else:
                print(f"  使用缓存: {pkg_filename}")

            # 解压到合并目录
            try:
                if pkg_filename.endswith('.tar.bz2'):
                    with tarfile.open(str(cache_path), 'r:bz2') as tar:
                        tar.extractall(path=merged_dir)
                elif pkg_filename.endswith('.conda'):
                    # .conda 格式: zip 包含 tar.zst
                    import zipfile
                    with zipfile.ZipFile(str(cache_path), 'r') as zf:
                        zf.extractall(str(cache_path.parent / f'{pkg_filename}_extract'))
                    # 尝试找 tar.zst
                    extract_dir = cache_path.parent / f'{pkg_filename}_extract'
                    for zst_file in extract_dir.rglob('*.tar.zst'):
                        try:
                            with tarfile.open(str(zst_file), 'r:zst') as tar:
                                tar.extractall(path=merged_dir)
                        except Exception:
                            # 回退：用 zstd 命令
                            zstd = shutil.which('zstd') or shutil.which('zstdmt')
                            if zstd:
                                tar_path = tempfile.mktemp(suffix='.tar')
                                subprocess.run(
                                    [zstd, '-d', str(zst_file), '-o', tar_path],
                                    check=True, timeout=60,
                                )
                                with tarfile.open(tar_path, 'r:') as tar:
                                    tar.extractall(path=merged_dir)
                                os.unlink(tar_path)
                    shutil.rmtree(extract_dir, ignore_errors=True)

                print(f"  解压成功")
                results['packages'][pkg_name] = {
                    'status': 'success',
                    'version': best_ver,
                    'filename': pkg_filename,
                }
            except Exception as e:
                print(f"  解压失败: {e}")
                results['packages'][pkg_name] = {'status': 'extract_failed'}

        # 下载并解压额外频道的包（如 sysroot_linux-64 来自 defaults 频道）
        # sysroot_linux-64 不在 conda-forge 公开仓库，需要从 Anaconda 商业仓库获取
        # 如果不可用，回退到从系统 /usr/include 打包最小化 sysroot
        for pkg_name, channel_url in EXTRA_CHANNEL_PACKAGES.items():
            print(f"\n处理（额外频道）: {pkg_name}")
            print(f"  频道: {channel_url}")

            pkg_downloaded = False
            try:
                # 获取该频道的 repodata
                extra_repodata_url = f"{channel_url}repodata.json"
                extra_req = urllib.request.Request(
                    extra_repodata_url, headers={'Accept': 'application/json'})
                with urllib.request.urlopen(extra_req, timeout=60) as resp:
                    extra_repodata = json.loads(resp.read().decode('utf-8'))

                extra_packages_meta = {**extra_repodata.get('packages', {}),
                                       **extra_repodata.get('packages.conda', {})}

                # 查找最新版本
                best_pkg = None
                best_ver = ''
                for pkg_filename, pkg_meta in extra_packages_meta.items():
                    if not pkg_filename.startswith(pkg_name + '-'):
                        continue
                    if pkg_meta.get('subdir') != 'linux-64':
                        continue
                    ver = pkg_meta.get('version', '0')
                    if ver > best_ver:
                        best_ver = ver
                        best_pkg = (pkg_filename, pkg_meta)

                if best_pkg:
                    pkg_filename, pkg_meta = best_pkg
                    download_url = f"{channel_url}{pkg_filename}"

                    # 下载
                    cache_path = Path(tempfile.gettempdir()) / 'conda_cache' / pkg_filename
                    cache_path.parent.mkdir(exist_ok=True)

                    if not cache_path.exists():
                        print(f"  下载: {pkg_filename}...")
                        urllib.request.urlretrieve(download_url, str(cache_path))
                        print(f"  下载完成")
                    else:
                        print(f"  使用缓存: {pkg_filename}")

                    # 解压到合并目录
                    if pkg_filename.endswith('.tar.bz2'):
                        with tarfile.open(str(cache_path), 'r:bz2') as tar:
                            tar.extractall(path=merged_dir)
                    elif pkg_filename.endswith('.conda'):
                        import zipfile
                        with zipfile.ZipFile(str(cache_path), 'r') as zf:
                            zf.extractall(str(cache_path.parent / f'{pkg_filename}_extract'))
                        extract_dir = cache_path.parent / f'{pkg_filename}_extract'
                        for zst_file in extract_dir.rglob('*.tar.zst'):
                            try:
                                with tarfile.open(str(zst_file), 'r:zst') as tar:
                                    tar.extractall(path=merged_dir)
                            except Exception:
                                zstd = shutil.which('zstd') or shutil.which('zstdmt')
                                if zstd:
                                    tar_path = tempfile.mktemp(suffix='.tar')
                                    subprocess.run(
                                        [zstd, '-d', str(zst_file), '-o', tar_path],
                                        check=True, timeout=60,
                                    )
                                    with tarfile.open(tar_path, 'r:') as tar:
                                        tar.extractall(path=merged_dir)
                                    os.unlink(tar_path)
                        shutil.rmtree(extract_dir, ignore_errors=True)

                    print(f"  解压成功")
                    results['packages'][pkg_name] = {
                        'status': 'success',
                        'version': best_ver,
                        'filename': pkg_filename,
                    }
                    pkg_downloaded = True
                else:
                    print(f"  未找到 {pkg_name}（在 {channel_url}）")
            except Exception as e:
                print(f"  下载失败: {e}")

            # 回退: 从系统 /usr/include 创建最小化 sysroot
            if not pkg_downloaded and pkg_name == 'sysroot_linux-64':
                print(f"  回退: 从系统 /usr/include 创建最小化 sysroot...")
                sysroot_dir = Path(merged_dir) / 'x86_64-conda-linux-gnu' / 'sysroot'
                sysroot_usr_include = sysroot_dir / 'usr' / 'include'
                sysroot_usr_include.mkdir(parents=True, exist_ok=True)

                system_include = Path('/usr/include')
                if system_include.exists():
                    # 复制编译 C/C++ 扩展所需的最小头文件集
                    # 这些是 glibc 的核心头文件，缺了就会报 "Unsupported compiler"
                    essential_patterns = [
                        '*.h',           # 所有 .h 头文件
                    ]
                    copied = 0
                    for pattern in essential_patterns:
                        for src_file in system_include.glob(pattern):
                            if src_file.is_file():
                                try:
                                    shutil.copy2(str(src_file), str(sysroot_usr_include / src_file.name))
                                    copied += 1
                                except Exception:
                                    pass
                    # 也复制关键子目录
                    essential_subdirs = ['asm', 'asm-generic', 'bits', 'linux', 'sys', 'gnu']
                    for subdir in essential_subdirs:
                        src = system_include / subdir
                        if src.is_dir():
                            try:
                                shutil.copytree(str(src), str(sysroot_usr_include / subdir))
                                for f in (sysroot_usr_include / subdir).rglob('*'):
                                    if f.is_file():
                                        copied += 1
                            except Exception:
                                pass
                    print(f"  从系统复制了 {copied} 个头文件到 sysroot")
                    results['packages'][pkg_name] = {
                        'status': 'success',
                        'version': 'system',
                        'source': '/usr/include',
                        'files_copied': copied,
                    }
                else:
                    print(f"  系统 /usr/include 不存在，无法创建 sysroot")
                    results['packages'][pkg_name] = {'status': 'not_found'}

        # 修复可执行权限
        print("\n修复可执行权限...")
        for bindir in Path(merged_dir).rglob('bin'):
            if bindir.is_dir():
                for f in bindir.iterdir():
                    try:
                        if f.is_file() and not os.access(f, os.X_OK):
                            os.chmod(f, 0o755)
                    except Exception:
                        pass

        # 创建常用符号链接
        print("创建编译器符号链接...")
        bin_dir = Path(merged_dir) / 'bin'
        bin_dir.mkdir(exist_ok=True)

        short_links = {
            'gcc': 'x86_64-conda-linux-gnu-gcc',
            'g++': 'x86_64-conda-linux-gnu-g++',
            'cc': 'x86_64-conda-linux-gnu-gcc',
            'c++': 'x86_64-conda-linux-gnu-g++',
            'ld': 'x86_64-conda-linux-gnu-ld',
            'as': 'x86_64-conda-linux-gnu-as',
            'nm': 'x86_64-conda-linux-gnu-nm',
            'ar': 'x86_64-conda-linux-gnu-ar',
            'ranlib': 'x86_64-conda-linux-gnu-ranlib',
            'strip': 'x86_64-conda-linux-gnu-strip',
            'objcopy': 'x86_64-conda-linux-gnu-objcopy',
        }
        for link_name, target_name in short_links.items():
            target_path = None
            for bindir in Path(merged_dir).rglob('bin'):
                candidate = bindir / target_name
                if candidate.exists():
                    target_path = candidate
                    break
            if target_path:
                link_path = bin_dir / link_name
                try:
                    if link_path.exists() or link_path.is_symlink():
                        link_path.unlink()
                    # 使用相对路径，确保解压后仍然有效
                    rel_target = os.path.relpath(str(target_path), str(bin_dir))
                    os.symlink(rel_target, str(link_path))
                except Exception:
                    pass

        # 检查是否编译器已就位
        gcc_found = (bin_dir / 'gcc').exists() or (bin_dir / 'x86_64-conda-linux-gnu-gcc').exists()
        gxx_found = (bin_dir / 'g++').exists() or (bin_dir / 'x86_64-conda-linux-gnu-g++').exists()
        cmake_found = (bin_dir / 'cmake').exists()
        make_found = (bin_dir / 'make').exists()

        print(f"\n工具链检查:")
        print(f"  gcc:   {'✓' if gcc_found else '✗'}")
        print(f"  g++:   {'✓' if gxx_found else '✗'}")
        print(f"  cmake: {'✓' if cmake_found else '✗'}")
        print(f"  make:  {'✓' if make_found else '✗'}")

        # 分包打包
        # gcc 工具链归档（gcc + g++ + binutils + libs）
        print("\n打包 gcc 工具链归档...")
        gcc_archive = toolchain_dir / 'gcc_toolchain.tar.bz2'
        with tarfile.open(str(gcc_archive), 'w:bz2') as tar:
            tar.add(merged_dir, arcname='.')
        size_mb = gcc_archive.stat().st_size / (1024 * 1024)
        print(f"  生成: {gcc_archive.name} ({size_mb:.1f} MB)")
        results['artifacts'].append(str(gcc_archive))

        # cmake 单独归档（如果存在）
        if cmake_found:
            print("打包 cmake 归档...")
            cmake_tmp = tempfile.mkdtemp(prefix='cmake_pack_')
            # 复制 cmake 相关文件
            for bindir in Path(merged_dir).rglob('bin'):
                for exe in bindir.iterdir():
                    if 'cmake' in exe.name.lower() and exe.is_file():
                        dst = Path(cmake_tmp) / 'cpp_toolchain' / 'bin'
                        dst.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(exe), str(dst / exe.name))
            # 复制 cmake 的 share 和 lib
            for share_dir in Path(merged_dir).rglob('share'):
                if 'cmake' in str(share_dir).lower():
                    dst = Path(cmake_tmp) / 'cpp_toolchain' / 'share'
                    if share_dir.exists():
                        shutil.copytree(str(share_dir), str(dst / share_dir.name),
                                        dirs_exist_ok=True)
            cmake_archive = toolchain_dir / 'cmake_toolchain.tar.bz2'
            with tarfile.open(str(cmake_archive), 'w:bz2') as tar:
                tar.add(cmake_tmp, arcname='.')
            cmake_size = cmake_archive.stat().st_size / (1024 * 1024)
            print(f"  生成: {cmake_archive.name} ({cmake_size:.1f} MB)")
            results['artifacts'].append(str(cmake_archive))
            shutil.rmtree(cmake_tmp, ignore_errors=True)

        results['status'] = 'success' if gcc_found else 'partial'

    finally:
        shutil.rmtree(merged_dir, ignore_errors=True)

    # 写入元数据
    metadata = {
        'build_time': datetime.now().isoformat(),
        'platform': platform.machine(),
        'system': platform.system(),
        'packages': list(packages) + list(EXTRA_CHANNEL_PACKAGES.keys()),
        'status': results['status'],
    }
    (toolchain_dir / 'METADATA.json').write_text(json.dumps(metadata, indent=2))

    print(f"\n工具链预编译结果: {results['status']}")
    return results


def build_all(output_dir: Path, python_version: str = None) -> dict:
    """预编译所有"""
    print("预编译全部（hnswlib + C++ 工具链）")
    print(f"输出目录: {output_dir}")
    print()

    results = {
        'hnswlib': build_hnswlib(output_dir, python_version),
        'toolchain': build_toolchain(output_dir),
    }

    # 汇总
    print("\n" + "=" * 60)
    print("预编译汇总")
    print("=" * 60)
    for name, result in results.items():
        status = result.get('status', 'unknown')
        print(f"  {name}: {status}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description='预编译 hnswlib 和 C++ 工具链，生成沙箱可直接使用的预编译产物'
    )
    parser.add_argument('--all', action='store_true',
                        help='预编译所有（hnswlib + 工具链）')
    parser.add_argument('--hnswlib', action='store_true',
                        help='只预编译 hnswlib')
    parser.add_argument('--toolchain', action='store_true',
                        help='只预编译 C++ 工具链')
    parser.add_argument('--output', type=str, default=None,
                        help='输出目录（默认: 项目根目录/prebuilt）')
    parser.add_argument('--python-version', type=str, default=None,
                        help='目标 Python 版本（如 3.10）')

    args = parser.parse_args()
    output_dir = get_output_dir(args)

    if not any([args.all, args.hnswlib, args.toolchain]):
        args.all = True  # 默认全部

    if args.all:
        build_all(output_dir, args.python_version)
    elif args.hnswlib:
        build_hnswlib(output_dir, args.python_version)
    elif args.toolchain:
        build_toolchain(output_dir)


if __name__ == '__main__':
    main()
