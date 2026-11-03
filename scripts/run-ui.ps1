# Starts the workshop showcase UI on http://localhost:8501
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root "demo\.venv\Scripts\python.exe"
& $py -c "import streamlit, plotly, pandas" 2>$null
if ($LASTEXITCODE -ne 0) { & $py -m pip install streamlit plotly pandas }
Push-Location (Join-Path $root "ui")

& $py -m streamlit run app.py --server.port 8501
Pop-Location
