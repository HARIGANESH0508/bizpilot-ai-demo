# Publish BizPilot AI with Streamlit Community Cloud

## Before publishing
- This is a portfolio/demo deployment, not production finance software.
- Use synthetic sample invoices only. Never upload real supplier, customer, bank, or tax documents.
- The default database is SQLite on the app container. Streamlit Community Cloud local storage is not durable; demo data may reset after a restart/redeploy.
- The login is a shared demo login, not individual hiring-team accounts.

## 1. Push this project to GitHub
1. Create a new GitHub repository named `bizpilot-ai-demo`.
2. Upload the contents of this project folder (the `app`, `dashboard`, `data`, `scripts`, `tests` folders, `requirements.txt`, `packages.txt`, and `.streamlit` folder).
3. Do NOT upload `.env`, `data/bizpilot.db`, `data/uploads`, or real invoices. `.gitignore` excludes these.
4. Make the repository public if using the free Streamlit Community Cloud workflow. The source code will be public.

## 2. Deploy
1. Open https://share.streamlit.io/ and sign in with GitHub.
2. Choose **Create app** and select your `bizpilot-ai-demo` repository.
3. Set branch to `main`.
4. Set **Main file path** to `dashboard/streamlit_app.py`.
5. Deploy.

## 3. Configure the demo password
In the deployed app's **App settings → Secrets**, add TOML like this (replace the example password with a unique long password):

```toml
DEMO_PASSWORD = "replace-with-a-long-random-demo-password"
BIZPILOT_AUTO_SEED = "true"
```

Save and reboot the app. The app bootstrap creates the four demo accounts and sample data on a fresh database. Usernames are `admin`, `fin.manager`, `ap.clerk`, and `viewer`; all share the password you set above.

## 4. Verify before sharing
- Open the public URL in a private/incognito browser window.
- Sign in using the `viewer` account first.
- Test sample invoice review and navigation.
- Test upload with a synthetic PDF/image only.
- Confirm the app does not expose any real files or secrets.

## Important limitations
- Community Cloud's local filesystem/database is not a persistent production database. Uploaded files and demo data can disappear after a restart/redeploy.
- The shared demo password can be forwarded. Do not use it to protect confidential data.
- No payment execution is implemented.
- For a real multi-user deployment, move to PostgreSQL plus persistent object storage, individual accounts, rate limiting, monitoring, and a security review.
