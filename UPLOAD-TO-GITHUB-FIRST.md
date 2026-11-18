# Upload this project to GitHub

This ZIP is arranged so its contents go directly into the repository root.

1. Extract this ZIP.
2. Open the extracted folder. You should see `app`, `dashboard`, `data`, `docs`, `n8n`, `scripts`, `tests`, `requirements.txt`, and `packages.txt` at the same level.
3. Upload/copy ALL contents of this folder into the root of your GitHub repository `bizpilot-ai-demo` (not into an extra nested `bizpilot-ai` folder).
4. Commit the files to branch `main`.
5. In Streamlit Community Cloud choose repository `HARIGANESH0508/bizpilot-ai-demo`, branch `main`, and Main file path `dashboard/streamlit_app.py`.
6. Before launching, open App settings → Secrets and set a private demo password:

```toml
DEMO_PASSWORD = "choose-a-unique-long-password"
BIZPILOT_AUTO_SEED = "true"
```

Do not commit `.env`, `.streamlit/secrets.toml`, databases, or real invoices. The PDFs under `data/samples/invoices/` are fictional demo files and are intentionally included for the sample workflow.

## Cloud limitations

Streamlit Community Cloud is suitable for a portfolio demo only. Its local SQLite/filesystem storage is not durable across all restarts/redeployments. Ollama and n8n in Docker Compose are not hosted by this Streamlit deployment. Tesseract is declared in `packages.txt` for the cloud build.
