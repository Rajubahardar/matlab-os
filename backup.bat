@echo off
set timestamp=%date:~-4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%
set backupPath=E:\matlab os\backup_V1_%timestamp%
mkdir "%backupPath%"
xcopy "E:\matlab os\*.*" "%backupPath%" /E /I /EXCLUDE:.git\exclude.txt /Y
echo Backup completed: %backupPath%