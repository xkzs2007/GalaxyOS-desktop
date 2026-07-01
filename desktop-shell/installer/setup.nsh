; setup.nsh — GalaxyOS Desktop NSIS 自定义安装脚本
; 在 electron-builder 生成的安装器中添加 Python 环境检测 + 依赖安装阶段
;
; electron-builder 通过 package.json 的 nsis.include 引用此文件
; 会自动调用这里定义的自定义宏

!include "FileFunc.nsh"
!include "LogicLib.nsh"

; ── 查找系统 Python ───────────────────────────────────────────────────
; 优先级：注册表 Python 3.12 > 3.11 > 3.10 > PATH 中的 python3/python
Function FindPython
  ; 1. 尝试注册表查找（Python.org 安装器写入的键）
  ${ForEach} $1 12 10 - 1
    ReadRegStr $0 HKLM "SOFTWARE\Python\PythonCore\3.$1\InstallPath" ""
    ${If} $0 != ""
      StrCpy $R0 "$0python.exe"
      IfFileExists "$R0" python_found 0
    ${EndIf}
    ReadRegStr $0 HKCU "SOFTWARE\Python\PythonCore\3.$1\InstallPath" ""
    ${If} $0 != ""
      StrCpy $R0 "$0python.exe"
      IfFileExists "$R0" python_found 0
    ${EndIf}
  ${Next}

  ; 2. 尝试 PATH 中的 python3 / python
  SearchPath $R0 python3.exe
  ${IfNot} ${Errors}
    Goto python_found
  ${EndIf}
  SearchPath $R0 python.exe
  ${IfNot} ${Errors}
    Goto python_found
  ${EndIf}

  ; 3. 常见安装位置兜底
  StrCpy $R0 "$PROGRAMFILES\Python312\python.exe"
  IfFileExists "$R0" python_found 0
  StrCpy $R0 "$PROGRAMFILES\Python311\python.exe"
  IfFileExists "$R0" python_found 0
  StrCpy $R0 "$LOCALAPPDATA\Programs\Python\Python312\python.exe"
  IfFileExists "$R0" python_found 0
  StrCpy $R0 "$LOCALAPPDATA\Programs\Python\Python311\python.exe"
  IfFileExists "$R0" python_found 0

  ; 未找到
  StrCpy $R0 ""
  Return

python_found:
  ; 验证版本 >= 3.11
  nsExec::ExecToStack '"$R0" -c "import sys; sys.stdout.write(sys.version[:4])"'
  Pop $1  ; exit code
  Pop $2  ; output
  ${If} $1 == 0
    ${If} $2 >= "3.11"
      ; Python 版本合格
      Return
    ${EndIf}
  ${EndIf}

  ; 版本过低或不兼容
  StrCpy $R0 ""
  Return
FunctionEnd


; ── 安装依赖 ──────────────────────────────────────────────────────────
Function InstallPythonDeps
  Pop $R1  ; requirements 文件路径

  ${If} $R0 == ""
    MessageBox MB_OK|MB_ICONINFORMATION \
      "$\r$\n未检测到 Python 3.11+ 运行环境。$\r$\n$\r$\n\
       GalaxyOS 引擎需要 Python 才能运行全部功能。$\r$\n$\r$\n\
       请先安装 Python (https://python.org/downloads/)，$\r$\n\
       然后重新运行 GalaxyOS 安装程序。$\r$\n$\r$\n\
       您也可以跳过此步骤，使用内置的轻量引擎模式启动。"
    Return
  ${EndIf}

  ; 安装核心依赖（轻量，~200MB）
  DetailPrint "正在安装 GalaxyOS 核心依赖..."
  DetailPrint "  Python: $R0"
  DetailPrint "  依赖文件: $R1"
  DetailPrint "  这可能需要 2-5 分钟..."

  ; 先升级 pip
  nsExec::ExecToLog '"$R0" -m pip install --upgrade pip --quiet'

  ; 安装核心依赖（requirements-core.txt 已在 resources/ 中）
  nsExec::ExecToLog '"$R0" -m pip install -r "$R1"'

  ; 提示重型依赖（可选项）
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "$\r$\n核心依赖安装完成！$\r$\n$\r$\n\
     是否需要安装重型 AI 组件？$\r$\n\
     (torch ~2.5GB / faiss / transformers ~500MB)$\r$\n$\r$\n\
     这些组件启用完整的易液态神经记忆、$\r$\n\
     知识图谱 GNN 和 CfC 推理能力。$\r$\n$\r$\n\
     总下载量约 3GB，建议安装。" \
    IDNO skip_heavy_deps

  ; 安装重型依赖
  DetailPrint "正在安装 GalaxyOS 重型 AI 组件..."
  ${If} ${FileExists} "$INSTDIR\resources\requirements-heavy.txt"
    nsExec::ExecToLog '"$R0" -m pip install -r "$INSTDIR\resources\requirements-heavy.txt"'
  ${Else}
    ; fallback: strip "requirements-core.txt" and append "requirements-heavy.txt"
    StrCpy $2 "$R1" -23  ; remove last 23 chars ("requirements-core.txt")
    StrCpy $2 "$2requirements-heavy.txt"
    ${If} ${FileExists} "$2"
      nsExec::ExecToLog '"$R0" -m pip install -r "$2"'
    ${Else}
      DetailPrint "警告: requirements-heavy.txt 未找到，跳过重型组件安装"
    ${EndIf}
  ${EndIf}

skip_heavy_deps:
  DetailPrint "GalaxyOS 依赖安装完成！"
FunctionEnd


; ── 自定义安装步骤 ────────────────────────────────────────────────────
; electron-builder 会在安装过程中调用这些宏

!macro customInit
  ; 安装程序一开始就检测 Python 环境
  Call FindPython
!macroend


!macro customInstall
  ; 文件复制完成后安装 Python 依赖
  ; 优先使用安装目录中的 requirements 文件
  StrCpy $R1 "$INSTDIR\resources\requirements-core.txt"

  ${IfNot} ${FileExists} "$R1"
    ; 如果 resources 下没有，可能在当前目录（某些打包配置）
    StrCpy $R1 "$INSTDIR\requirements-core.txt"
  ${EndIf}

  ${IfNot} ${FileExists} "$R1"
    ; 都找不到就跳过
    DetailPrint "警告: requirements-core.txt 未找到，跳过依赖安装"
    Goto skip_deps_install
  ${EndIf}

  ; 显示确认对话框
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "$\r$\nGalaxyOS 需要在您的系统上安装 Python 依赖库。$\r$\n$\r$\n\
     Python 位置: $R0$\r$\n\
     依赖文件: $R1$\r$\n$\r$\n\
     是否现在安装？$\r$\n\
     (约 200MB 核心依赖 + 可选 3GB AI 组件)$\r$\n$\r$\n\
     选择「否」将跳过安装，使用轻量引擎模式启动。" \
    IDNO skip_deps_install

  ; 推入 requirements 路径然后调用安装函数
  Push $R1
  Call InstallPythonDeps

skip_deps_install:
  DetailPrint "GalaxyOS 安装完成！"
!macroend


!macro customUnInstall
  ; 卸载时提示用户 pip 安装的包不会自动删除
  ; 不做自动清理，因为用户可能其他程序也在用这些包
  DetailPrint "注意: GalaxyOS 安装的 Python 依赖包不会自动删除。"
  DetailPrint "如需清理，请手动运行: pip uninstall -r requirements-core.txt"
!macroend
