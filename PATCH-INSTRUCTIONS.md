# Apply reliability patch in VS Code / Windows

1. Make a backup of your current project folder and database.
2. Extract this patch ZIP. It contains the complete patched `bizpilot-ai` project.
3. Copy the patched files into your current project, allowing replacement only for the listed changed files.
4. Activate your existing virtual environment and run the test commands below.

Changed files:
- `app/services/invoices.py`
- `app/services/matching.py`
- `dashboard/streamlit_app.py`
- `tests/test_pipeline.py`
- `tests/test_matching_regressions.py` (new)
- `docs/FIXES-2026-11.md` (new)

PowerShell from the project root:
```powershell
.\.venv\Scripts\Activate.ps1
python -m compileall app dashboard tests
pytest -q
```

If your virtual environment has a different name, activate that environment instead.

Important: this patch does not modify database columns, so it does not require a schema migration. The existing SQLite database should still be backed up before applying code changes.
