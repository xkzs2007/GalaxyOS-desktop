; setup.nsh — GalaxyOS Desktop NSIS minimal installer
;
; v10: 极简版 — 移除所有 Python 自动检测 / 依赖安装逻辑。
; 原因：这些逻辑在 GitHub Actions 的 windows-latest (NSIS 3.10) 下
; 产生 ENOENT/解析错误导致 electron-builder 找不到产物。
;
; 用户如果需要 Python 依赖，可：
;   1) 用应用内首启向导 (install-wizard.js)
;   2) 手动 pip install -r requirements-core.txt
;
; electron-builder 通过 package.json 的 nsis.include 引用此文件。
; 只保留: customInit / customInstall / customUnInstall 三个空宏，
; 让 electron-builder 用默认 NSIS 模板生成安装器。

!macro customInit
!macroend

!macro customInstall
  ; 仅打印安装完成日志，不做任何自动操作
  DetailPrint "GalaxyOS 桌面应用已安装。"
  DetailPrint "首次启动后请按提示安装 Python 依赖，或使用应用内向导。"
!macroend

!macro customUnInstall
!macroend
