# Daily routine: inbox, find leads, read sites, write drafts, search project boards, upload due videos.
# Sends email only if Autopilot is switched on in Settings.
#
# To run it automatically every day at 19:00 (morning in the US, afternoon in the UK),
# paste this ONE line into PowerShell, from inside this folder:
#
#   schtasks /Create /F /TN "Vintelligence daily" /SC DAILY /ST 19:00 /TR "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PWD\daily.ps1`""
#
# To stop it:   schtasks /Delete /F /TN "Vintelligence daily"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force data | Out-Null
python vi.py run | Tee-Object -FilePath "data\last-run.log"
