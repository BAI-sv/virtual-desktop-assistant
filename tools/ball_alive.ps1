# ball_alive.ps1 —— 判断桌面球（fairy_ball.py）在不在跑
#
# 为什么不用 fairy.py status：
#   实测 python.exe 启动 + 跑完要 5 秒，而且 stdout 是进程退出那一刻才一次性给，
#   在 Node 里用 spawn 收很容易超时，不稳定。
#
# 这个脚本直接查进程命令行，1-2 秒返回。
# 输出：1 = 在跑，0 = 没在跑

$found = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -and ($_.CommandLine -like '*fairy_ball.py*') }

if ($found) { '1' } else { '0' }
