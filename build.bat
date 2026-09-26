@echo off
REM 从源码打包 exe
REM 需要：Python 3.9+（64位）
cd /d "%~dp0"

python -m pip install --upgrade pyinstaller edge-tts pystray pillow
python make_icon.py

pyinstaller --onefile --windowed ^
  --name "MHW竞速语音计时器" ^
  --icon app.ico ^
  --add-data "app.ico;." ^
  --collect-all edge_tts ^
  --collect-all pystray ^
  --noconfirm --clean mhw_voice_timer.py

echo.
echo 完成！输出在 dist\MHW竞速语音计时器.exe
echo 使用时请把 config.json 和 exe 放在同一文件夹。
pause
