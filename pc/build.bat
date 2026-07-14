@echo off
REM 一键打包 NoticeFloat PC 版为单文件 exe
setlocal
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat

pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
pip install -i https://mirrors.aliyun.com/pypi/simple/ pyinstaller

pyinstaller --noconfirm --clean ^
    --name NoticeFloat ^
    --onefile --noconsole ^
    --hidden-import PIL._tkinter_finder ^
    main.py

echo.
echo === 打包完成: dist\NoticeFloat.exe ===
pause
