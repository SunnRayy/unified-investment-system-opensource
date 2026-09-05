# Huinsight Cloud Run Deployment Guide

# Huinsight 云端部署指南

---

## English Instructions

### What this does

After completing these steps, your Huinsight dashboard will be accessible from any device (phone, tablet, browser) at a permanent web address — without needing to keep your laptop on.

Your data syncs to Google Cloud. When you have new investment files (Schwab CSV, CN Fund Excel, etc.), you upload them through the dashboard and click Sync — the server handles the rest.

**Estimated time**: 45–60 minutes for first-time setup.

**Cost**: Google Cloud free tier covers most personal usage. Expect ~$0–5/month depending on how often you visit.

---

### Before You Start

You will need:

1. **A Google account** (Gmail is fine)
2. **A GitHub account** (free at github.com)
3. **Google Cloud CLI (`gcloud`)** installed on your laptop
   - Install: <https://cloud.google.com/sdk/docs/install>
   - After installing, open Terminal and run: `gcloud auth login`
4. **This project** checked out on your laptop (you already have this)

---

### Step 1 — Create a Google Cloud Project

1. Go to <https://console.cloud.google.com>
2. Click the project dropdown at the top → **New Project**
3. Name it something like `uis-dashboard`
4. Note the **Project ID** shown below the name (e.g. `uis-dashboard-123456`) — you'll need this

---

### Step 2 — Enable Billing

Cloud Run requires a billing account even for free-tier usage.

1. In the Cloud Console, go to **Billing** in the left menu
2. Link your credit card (you won't be charged for typical personal use)

---

### Step 3 — Run the One-Time Setup Script

Open Terminal, go to the project worktree, and run:

```bash
cd /path/to/huinsight

GCP_PROJECT=your-project-id-here \
BUCKET=uis-data-yourname \
bash deploy/setup-gcs.sh
```

Replace:

- `your-project-id-here` → your actual Project ID from Step 1 (e.g. `uis-dashboard-123456`)
- `uis-data-yourname` → any unique bucket name (e.g. `uis-data-yourname`) — bucket names are globally unique on Google Cloud

The script will:

- Enable the required Google Cloud services
- Create a storage bucket for your database and source files
- Upload your current database to Google Cloud (this is your data seed)
- Ask you to enter 6 secret values one by one (input is hidden, like a password prompt)

**When prompted for each secret, enter:**

| Secret name | What to enter |
|---|---|
| `FRED_API_KEY` | Your FRED API key (from your `.env` file) |
| `GEMINI_API_KEY` | Your Gemini API key (from your `.env` file) |
| `DEEPSEEK_API_KEY` | Your DeepSeek API key (from your `.env` file) |
| `UIS_AUTH_TOKEN` | Make up a strong password (e.g. 32 random characters) — this protects your dashboard |
| `UIS_GCS_BUCKET` | The same bucket name you used above (e.g. `uis-data-yourname`) |
| `UIS_ALLOWED_ORIGIN` | Leave blank for now, just press Enter (you can restrict later) |

> **Tip**: To generate a strong auth token, run: `openssl rand -hex 32`

---

### Step 4 — Connect GitHub to Google Cloud (Workload Identity)

This lets GitHub Actions deploy to Cloud Run without storing long-lived credentials.

> **Important**: Use your **GitHub username** (not your git config name) for `GITHUB_REPO`. Check at github.com — it's in your profile URL.
> Also use your **Project ID** (the string slug, e.g. `my-app-name`), NOT the project number.

1. In Terminal, run **each command one at a time** (do not use backslash line continuations for the `--member` flag — they cause line-break bugs):

```bash
# Set your values
PROJECT_ID=your-project-id-here
GITHUB_REPO=YourGitHubUsername/huinsight

# Enable required APIs (iamcredentials is required for Workload Identity)
gcloud services enable iamcredentials.googleapis.com --project $PROJECT_ID

# Create a service account
gcloud iam service-accounts create github-deploy --project $PROJECT_ID --display-name "GitHub Actions Deploy"

# Grant deploy permissions
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/run.admin"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/storage.admin"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/iam.serviceAccountTokenCreator"

# Allow deploy SA to act as the default compute SA (required for Cloud Run)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
gcloud iam service-accounts add-iam-policy-binding ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com --project $PROJECT_ID --role="roles/iam.serviceAccountUser" --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com"

# Grant runtime SA (compute SA) access to secrets
gcloud projects add-iam-policy-binding $PROJECT_ID --role="roles/secretmanager.secretAccessor" --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Create Workload Identity pool
gcloud iam workload-identity-pools create github-pool --project $PROJECT_ID --location global --display-name "GitHub Actions Pool"

# Create OIDC provider (attribute-condition is now required by GCP)
gcloud iam workload-identity-pools providers create-oidc github-provider --project $PROJECT_ID --location global --workload-identity-pool github-pool --display-name "GitHub Provider" --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition="assertion.repository == '${GITHUB_REPO}'" --issuer-uri="https://token.actions.githubusercontent.com"

# Get the pool resource name
POOL_ID=$(gcloud iam workload-identity-pools describe github-pool --project $PROJECT_ID --location global --format="value(name)")

# Allow GitHub repo to impersonate the service account
# IMPORTANT: run this as a single line — do not wrap with backslash
gcloud iam service-accounts add-iam-policy-binding "github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" --project $PROJECT_ID --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GITHUB_REPO}"

# Print GitHub Secrets values
echo "=== Add these to GitHub Secrets ==="
echo "GCP_PROJECT_ID: $PROJECT_ID"
echo "GCP_SERVICE_ACCOUNT: github-deploy@${PROJECT_ID}.iam.gserviceaccount.com"
echo "GCP_WORKLOAD_IDENTITY_PROVIDER: ${POOL_ID}/providers/github-provider"
```

2. Copy the 3 values printed at the end.

---

### Step 5 — Add GitHub Secrets

1. Push the branch to GitHub (if you haven't already):

```bash
cd /path/to/huinsight
git push origin main
```

1. Go to your GitHub repository → **Settings** → **Secrets and variables** → **Actions**

2. Click **New repository secret** and add these 3 secrets (using values from Step 4):

| Secret name | Value |
|---|---|
| `GCP_PROJECT_ID` | Your project ID (e.g. `uis-dashboard-123456`) |
| `GCP_SERVICE_ACCOUNT` | `github-deploy@your-project-id.iam.gserviceaccount.com` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | The long string printed above |

---

### Step 6 — Deploy

Push the branch to the `deploy` branch to trigger automatic deployment:

```bash
cd /path/to/huinsight
git push origin main:deploy
```

GitHub Actions will:

1. Run all tests (takes ~3 minutes)
2. Build the Docker container
3. Push it to Google Cloud
4. Deploy to Cloud Run
5. Verify the service is healthy

You can watch the progress at: `https://github.com/your-username/huinsight/actions`

---

### Step 7 — Get Your Dashboard URL

After deployment succeeds:

```bash
gcloud run services describe uis-dashboard \
  --region us-central1 \
  --project your-project-id-here \
  --format="value(status.url)"
```

This prints something like `https://uis-dashboard-<your-hash>-uc.a.run.app` — that's your dashboard URL.

---

### Step 8 — Log In to Your Dashboard

Your dashboard is protected by the auth token you created in Step 3.

Open the URL in a browser and you get a login screen. Enter that token as the password — no
browser extension, no header configuration.

The token seeds the credential on **first boot only**. After that the database holds the
password, and changing the Secret Manager value does not change how you log in; use the in-app
password change instead. To reset a forgotten password, clear the `auth_credentials` table and
restart, which re-seeds from `UIS_AUTH_TOKEN` and touches no portfolio data.

To check the service is up without logging in:

```bash
curl https://your-dashboard-url.run.app/health
# Should return: {"status": "ok", "version": "...", "sha": "..."}
```

`/health` is deliberately unauthenticated so uptime checks and the deploy smoke test can reach
it; every data endpoint sits behind the login.

---

### Ongoing Usage

**To sync new data**: Open the dashboard → Settings → upload your new CSV/Excel files → click Sync. The server handles it and saves the updated database automatically.

**To re-deploy after code changes**: Push to the `deploy` branch and GitHub Actions redeploys automatically.

**If the dashboard is slow to load** (first visit after being idle): Cloud Run scales to zero when not in use. The first load takes ~5–10 seconds as it wakes up. Subsequent loads are fast.

---

### Troubleshooting

**"No database found" error on startup**: Run setup-gcs.sh again (it will re-upload your database).

**Dashboard loads but shows no data**: The database downloaded from GCS successfully. Try triggering a sync from Settings.

**GitHub Actions fails on "test" step**: Run `python3 -m pytest tests/ -q` locally to check for test failures before pushing.

**Forgot your auth token**: Update the secret in Google Cloud Console → Secret Manager → `UIS_AUTH_TOKEN` → add new version. Then redeploy.

---
---

## 中文说明

### 这个部署做什么

完成以下步骤后，你的 Huinsight 投资仪表盘将可以从任何设备（手机、平板、浏览器）通过一个固定网址访问，**不需要保持笔记本电脑开启**。

你的数据存储在 Google Cloud 上。当你有新的投资文件（嘉信理财 CSV、基金 Excel 等），直接在仪表盘上传并点击同步，服务器会自动处理。

**预计时间**：首次配置约 45–60 分钟。

**费用**：Google Cloud 免费额度基本覆盖个人使用。预计每月 $0–5 美元。

---

### 准备工作

你需要以下条件：

1. **一个 Google 账号**（Gmail 即可）
2. **一个 GitHub 账号**（在 github.com 免费注册）
3. **Google Cloud CLI（`gcloud`）** 已安装在笔记本上
   - 安装地址：<https://cloud.google.com/sdk/docs/install>
   - 安装完成后，打开终端运行：`gcloud auth login`
4. **本项目**已在笔记本上（你已经有了）

---

### 第一步 — 创建 Google Cloud 项目

1. 访问 <https://console.cloud.google.com>
2. 点击顶部的项目下拉菜单 → **新建项目**
3. 项目名称随意，比如 `uis-dashboard`
4. 记下名称下方显示的**项目 ID**（例如 `uis-dashboard-123456`），后面会用到

---

### 第二步 — 开启结算账号

Cloud Run 即使在免费额度内也需要绑定结算账号。

1. 在 Cloud Console 左侧菜单点击 **结算**
2. 绑定信用卡（正常个人使用不会产生费用）

---

### 第三步 — 运行一次性配置脚本

打开终端，进入项目工作目录，执行：

```bash
cd /path/to/huinsight

GCP_PROJECT=your-project-id-here \
BUCKET=uis-data-yourname \
bash deploy/setup-gcs.sh
```

替换说明：

- `你的项目ID` → 第一步记下的项目 ID（例如 `uis-dashboard-123456`）
- `uis-data-你的名字` → 任意唯一名称（例如 `uis-data-yourname`）——Google Cloud 的存储桶名称全球唯一

脚本会自动完成：

- 开启所需的 Google Cloud 服务
- 创建存储数据库和源文件的存储桶
- 将你当前的数据库上传到 Google Cloud（作为初始数据）
- 逐一提示你输入 6 个密钥（输入时不显示字符，像密码一样）

**各密钥输入内容：**

| 密钥名称 | 输入内容 |
|---|---|
| `FRED_API_KEY` | 你的 FRED API 密钥（在 `.env` 文件里） |
| `GEMINI_API_KEY` | 你的 Gemini API 密钥（在 `.env` 文件里） |
| `DEEPSEEK_API_KEY` | 你的 DeepSeek API 密钥（在 `.env` 文件里） |
| `UIS_AUTH_TOKEN` | 自己设定一个强密码（建议 32 位随机字符）——用于保护仪表盘 |
| `UIS_GCS_BUCKET` | 上面使用的存储桶名称（例如 `uis-data-yourname`） |
| `UIS_ALLOWED_ORIGIN` | 直接按回车跳过（以后可以限制） |

> **提示**：生成强密码可以运行：`openssl rand -hex 32`

---

### 第四步 — 连接 GitHub 与 Google Cloud（Workload Identity）

这一步让 GitHub Actions 能够自动部署到 Cloud Run，无需在 GitHub 上保存长期有效的密钥。

1. 在终端运行（替换变量）：

```bash
# 替换为你的值
PROJECT_ID=你的项目ID
GITHUB_REPO=你的GitHub用户名/huinsight

# 创建服务账号
gcloud iam service-accounts create github-deploy \
  --project $PROJECT_ID \
  --display-name "GitHub Actions Deploy"

# 授予权限
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"

# 创建 Workload Identity 联合
gcloud iam workload-identity-pools create github-pool \
  --project $PROJECT_ID \
  --location global \
  --display-name "GitHub Actions Pool"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project $PROJECT_ID \
  --location global \
  --workload-identity-pool github-pool \
  --display-name "GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# 允许 GitHub 使用服务账号
POOL_ID=$(gcloud iam workload-identity-pools describe github-pool \
  --project $PROJECT_ID --location global --format="value(name)")

gcloud iam service-accounts add-iam-policy-binding \
  "github-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project $PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GITHUB_REPO}"

# 打印需要添加到 GitHub 的值
echo ""
echo "=== 将以下内容添加到 GitHub Secrets ==="
echo "GCP_PROJECT_ID: $PROJECT_ID"
echo "GCP_SERVICE_ACCOUNT: github-deploy@${PROJECT_ID}.iam.gserviceaccount.com"
echo "GCP_WORKLOAD_IDENTITY_PROVIDER: ${POOL_ID}/providers/github-provider"
```

1. 复制最后打印出来的 3 个值。

---

### 第五步 — 添加 GitHub Secrets

1. 先将分支推送到 GitHub（如果还没推）：

```bash
cd /path/to/huinsight
git push origin main
```

1. 打开你的 GitHub 仓库 → **Settings**（设置）→ **Secrets and variables**（密钥与变量）→ **Actions**

2. 点击 **New repository secret**（新建仓库密钥），依次添加以下 3 个（值来自第四步）：

| 密钥名称 | 值 |
|---|---|
| `GCP_PROJECT_ID` | 你的项目 ID（例如 `uis-dashboard-123456`） |
| `GCP_SERVICE_ACCOUNT` | `github-deploy@你的项目ID.iam.gserviceaccount.com` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | 第四步末尾打印的那个长字符串 |

---

### 第六步 — 部署

将分支推送到 `deploy` 分支，自动触发部署：

```bash
cd /path/to/huinsight
git push origin main:deploy
```

GitHub Actions 会自动执行：

1. 运行所有测试（约 3 分钟）
2. 构建 Docker 容器镜像
3. 推送到 Google Cloud
4. 部署到 Cloud Run
5. 验证服务正常运行

可以在这里查看进度：`https://github.com/你的用户名/huinsight/actions`

---

### 第七步 — 获取仪表盘网址

部署成功后，运行：

```bash
gcloud run services describe uis-dashboard \
  --region us-central1 \
  --project 你的项目ID \
  --format="value(status.url)"
```

会打印出类似 `https://uis-dashboard-<your-hash>-uc.a.run.app` 的网址，这就是你的仪表盘地址。

---

### 第八步 — 登录仪表盘

仪表盘由你在第三步设置的认证令牌保护。

用浏览器打开网址会看到登录界面，把这个令牌当作密码输入即可——不需要浏览器插件，也不需要
手动配置请求头。

该令牌**只在首次启动时**写入凭据。之后密码由数据库保管，再改 Secret Manager 里的值不会影响
登录方式；要改密码请用应用内的修改密码功能。如果忘记了密码，清空 `auth_credentials` 表并重启
即可从 `UIS_AUTH_TOKEN` 重新写入，这个操作不会动到任何持仓数据。

如果只想确认服务是否正常，不必登录：

```bash
curl https://你的网址.run.app/health
# 应返回：{"status": "ok", "version": "...", "sha": "..."}
```

`/health` 是刻意不做鉴权的，好让健康检查和部署冒烟测试能访问；所有数据接口都在登录之后。

---

### 日常使用

**同步新数据**：打开仪表盘 → 设置 → 上传新的 CSV/Excel 文件 → 点击同步。服务器自动处理并保存更新后的数据库。

**代码更新后重新部署**：推送到 `deploy` 分支，GitHub Actions 自动重新部署。

**首次访问加载慢**：Cloud Run 在长时间无人访问后会自动休眠。第一次唤醒需要约 5–10 秒，之后访问速度正常。

---

### 常见问题

**启动时提示"未找到数据库"**：重新运行 setup-gcs.sh（会重新上传数据库）。

**仪表盘加载但没有数据**：数据库已成功下载，在设置页面手动触发一次同步即可。

**GitHub Actions 测试步骤失败**：先在本地运行 `python3 -m pytest tests/ -q` 检查测试是否通过，再推送。

**忘记认证令牌**：在 Google Cloud Console → Secret Manager → `UIS_AUTH_TOKEN` → 添加新版本。然后重新部署。

## Deployment Lessons Learned (2026-04-10)

### Gotchas encountered during first real deployment

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| `Missing data/unified.duckdb` in setup-gcs.sh | Script uses a relative path; must be run from the repository root | `cd /path/to/huinsight && bash deploy/setup-gcs.sh` |
| Artifact Registry `--project` error | gcloud Artifact Registry requires project ID string, not project number | `gcloud projects describe PROJECT_NUMBER --format='value(projectId)'` |
| OIDC provider creation failed | GCP now requires `--attribute-condition` on all OIDC providers | Add `--attribute-condition="assertion.repository == 'ORG/REPO'"` |
| IAM binding `unknown type` error | Backslash line continuation broke the `--member` URL across two lines | Always run `--member=` commands as a single unbroken line |
| `iamcredentials.googleapis.com` not enabled | Workload Identity requires this API; not in the standard enable list | `gcloud services enable iamcredentials.googleapis.com` |
| `Permission 'iam.serviceaccounts.actAs' denied` | Deploy SA needs `serviceAccountUser` on the default compute SA | Grant `roles/iam.serviceAccountUser` from github-deploy → compute SA |
| Secrets `Permission denied` at runtime | Cloud Run container runs as compute SA, not deploy SA; compute SA needs secret access | Grant `roles/secretmanager.secretAccessor` to compute SA |
| `UIS_ALLOWED_ORIGIN` secret not found | Script stored empty value when user pressed Enter; GCP creates secret but no version | `printf "*" \| gcloud secrets versions add UIS_ALLOWED_ORIGIN --data-file=-` |
| GitHub username mismatch | git config `user.name` ≠ GitHub username; attribute condition used wrong value | Always use actual GitHub profile username in `GITHUB_REPO` |

### Known issues after first deployment

**"0 sources OK · 6 need attention" in Settings**
This is expected on first cloud deployment. The source file paths reference your local Mac filesystem, which doesn't exist on Cloud Run. To sync data: go to Settings → upload each CSV/Excel file manually → click Sync All.

**AI Advisor timeout / page stops loading**
The AI Advisor makes LLM calls that can exceed the 900s Cloud Run timeout under load. After a timeout, the container may restart and re-download the DB from GCS (takes ~30s). This is a known issue for the first deployment — investigate LLM timeout handling and streaming response behaviour in a future session.