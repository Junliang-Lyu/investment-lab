# Investment Lab 上线手册

本手册部署 `invest.jun-liang-lyu.com`：财报快照页和交易前闸口页，全部使用虚构组合和 SEC 公开数据。和 self_web 的 `docs/DEPLOYMENT.md` 一样，**所有构建都在本地 Windows 完成，服务器只接收构建好的产物。**

不需要新的 AWS 资源，不连 PostgreSQL，也不读取 `private-data/`。AI 反方默认关闭，开启步骤见下文。

## 架构和边界

- Caddy（self_web 已有）新增一个 site block：`invest.{$DOMAIN}`。静态页面来自 `/srv/invest`，`/api/*` 转发到 `investment-api:8081`，`/api/docs` 和 `/api/openapi.json` 返回 404。
- `investment-api` 容器：不对主机开放端口；只在自己的 `invest` 网络上（和 Caddy 共享，可以访问外网取 SEC 数据）；不在 self_web 的 `backend` 内部网络上，所以访问不到 PostgreSQL；只读根文件系统、非 root 用户、去掉全部 capabilities、内存上限 256M。
- 镜像只包含公开代码和演示用 fixture（`fixtures/rules/demo.yaml`、三个虚构组合、示例论点），由 `backend/tests/test_deploy.py` 和发布脚本里的镜像检查保证。
- 实测：10 家公司的快照首次加载 3–4 秒，之后 6 小时内走内存缓存（约 20 ms）；单进程峰值内存约 104 MB；SEC 缓存卷约 150 MB。冷加载串行执行，避免并发时超出内存上限。
- SEC 暂时不可用时：有旧快照就返回旧快照，没有就返回 503 和说明文字，不会出现 500。

## 一次性准备（需要本人操作）

1. **DNS**：在域名的 DNS 服务商处添加 A 记录：主机名 `invest`，值为 Lightsail 实例的静态 IPv4（和主站同一个 IP）。生效后 `nslookup invest.jun-liang-lyu.com` 能查到这个 IP。
2. **Docker Desktop**：Windows 上安装并启动，用于本地构建镜像。
3. **self_web 的 Caddyfile**：仓库里已经在末尾加了 `import /etc/caddy/sites/*.caddy`。服务器上的 `/opt/portfolio/current/deploy/Caddyfile` 需要同样加上（或者做一次 self_web 发布）。没有 Lab 时这一行只会记一条 warning，不影响主站。
4. **服务器目录**：

   ```sh
   sudo install -d -m 0755 /opt/investment/releases
   sudo chown -R "$USER":"$USER" /opt/investment
   ```

5. **服务器环境变量**：在 `/opt/portfolio/current/deploy/.env.production` 末尾追加（模板见 `deploy/invest.env.example`；不要把真实值提交到仓库或发到聊天里）：

   ```sh
   INVEST_API_IMAGE=investment-api:<RELEASE_ID>
   SEC_USER_AGENT=Your Name your-email@example.com
   INVEST_RATE_PER_MINUTE=60
   ```

6. **命令别名**（避免以后只用 `compose.yaml` 启动时把 Lab 漏掉）：在服务器的 `~/.bashrc` 加

   ```sh
   alias dc='docker compose --env-file .env.production -f compose.yaml -f /opt/investment/current/compose.invest.yaml'
   ```

   之后在 `/opt/portfolio/current/deploy` 里用 `dc ps`、`dc up -d`、`dc logs -f investment-api`。
   别名只在交互式登录的 shell 里有效；从本地用 `ssh host '命令'` 远程执行时不会加载，要写完整命令：
   `docker compose --env-file .env.production -f compose.yaml -f /opt/investment/current/compose.invest.yaml ...`

## 每次发布

**1. 本地构建（Windows PowerShell，在 `investment-lab` 目录）**

```powershell
powershell -ExecutionPolicy Bypass -File deploy\build-release.ps1
```

脚本依次执行：后端测试、`npm ci` + 前端构建、`docker build`、镜像内容检查（不能有 `private-data`、`.env`、非 demo 规则文件）、打包到 `deploy\release\<RELEASE_ID>\`（`site\`、`invest.caddy`、`compose.invest.yaml`、`investment-api-<RELEASE_ID>.tar`）。`deploy/release/` 已被 git 忽略。

**2. 上传**

```powershell
scp -r deploy\release\<RELEASE_ID> ubuntu@<SERVER_IP>:/opt/investment/releases/
```

**3. 服务器上切换**

```sh
cd /opt/investment/releases/<RELEASE_ID>
docker load -i investment-api-<RELEASE_ID>.tar && rm investment-api-<RELEASE_ID>.tar
ln -sfn /opt/investment/releases/<RELEASE_ID> /opt/investment/current
cd /opt/portfolio/current/deploy
nano .env.production            # 把 INVEST_API_IMAGE 改成新的 <RELEASE_ID>
dc config > /dev/null && dc up -d
dc up -d --force-recreate caddy # 必须：Caddy 挂载的是 current/site，Docker 在创建容器时就解析了这个软链接，
                                # 不重建就一直是旧版本的前端（2026-09-29 第二次发布时踩过）。主站会中断一两秒。
dc ps
dc logs --tail=50 caddy investment-api
```

Caddy 会自动为 `invest.` 子域名申请证书（DNS 已生效、80/443 可达时）。

## 发布检查

**推荐：在服务器上运行检查脚本**（逐项打印 PASS/FAIL，不经过本地 PowerShell 的引号转义）：

```sh
scp deploy/check-release.sh ubuntu@<SERVER_IP>:/opt/investment/current/   # 在本地执行（以后的发布包会自带）
bash /opt/investment/current/check-release.sh                              # 在服务器上执行
```

证书刚开始申请时 HTTPS 检查可能失败，等一两分钟再跑一次。下面是脚本里各项检查对应的手动命令，供排查用：

```sh
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://invest.jun-liang-lyu.com/     # 308 .../lab
curl --fail https://invest.jun-liang-lyu.com/lab > /dev/null && echo page ok
curl --fail https://invest.jun-liang-lyu.com/api/health                                          # {"status":"ok"}
curl --fail -s https://invest.jun-liang-lyu.com/api/lab/companies/GOOG/snapshot | head -c 120; echo
curl -s -o /dev/null -w '%{http_code}\n' https://invest.jun-liang-lyu.com/api/docs              # 404
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"portfolio_id":"concentrated-tech","symbol":"AMZN","side":"buy","amount_usd":800}' \
  https://invest.jun-liang-lyu.com/api/lab/gate | head -c 120; echo                              # "overall":"rule_breaks"
docker stats --no-stream
```

self_web `docs/DEPLOYMENT.md` 里原有的检查必须全部仍然通过。然后用手机打开 `/lab/company` 和 `/lab/gate` 看一遍。

## 开启 AI 反方（第二版起）

AI 反方默认关闭。顺序不能反：先在本地跑 eval 并达标，再发布，最后在服务器上打开开关。

1. **本地跑 eval，先小后大**（Windows，在 `investment-lab\\backend`，用本地 `.env` 里的 key）：

   ```powershell
   # 第一步：8 条最容易出问题的用例，约 $0.4，结果在 fixtures\evals\results\smoke\（不公开）
   .venv\Scripts\python.exe -m investment_ai eval-skeptic --provider anthropic --smoke --max-usd 0.8
   # 第二步：冒烟测试的安全项全为 0 且至少 7/8 通过，才跑完整 32 条（约 $1.5–2）
   .venv\Scripts\python.exe -m investment_ai eval-skeptic --provider anthropic --max-usd 2.5
   # 第三步：memo 工作流的 AI 审查，10 条，约 $0.1–0.2，结果在 fixtures\evals\results\review\
   .venv\Scripts\python.exe -m investment_ai eval-memo-review --provider anthropic --max-usd 0.5
   # 任何一步打印 INCOMPLETE（返回码 4），用同样的参数加 --resume 继续
   ```

   memo 工作流（DESIGN §11.5）和 AI 反方用同一个开关：反方开启时，memo 的保存、审查、定稿接口也一起开启。审查 eval 的放行标准：显示出来的审查中建议为 0、注入暗号为 0、宽松判定为 0（把空回应、"同意"或回避的回答判为驳倒或接受风险），通过率 ≥ 90%。

   `--max-usd` 是这次运行的花费上限（包括续跑前已花的部分），到了就不再开始新用例。完整运行结束时打印 `PASSED` 或 `FAILED`，结果写入 `fixtures\evals\results\latest.json`（会随发布包进入镜像并在页面公开）。`FAILED` 就停下，不要开启。
   以后根据反馈修改 prompt 或校验规则时：先用已有的调用记录离线重放（不花钱），再用 `--only <用例>` 或 `--smoke` 验证，发布前才跑完整 32 条。
2. **发布**：照常运行 `build-release.ps1` 并按上文切换版本。
3. **服务器环境变量**（`.env.production`，模板见 `deploy/invest.env.example`）：

   ```sh
   INVEST_ANTHROPIC_API_KEY=<单独给 Lab 用的 key>
   LAB_SKEPTIC_ENABLED=1
   LAB_DAILY_BUDGET_USD=0.5
   LLM_MONTHLY_BUDGET_USD=5
   LAB_SKEPTIC_PER_IP_DAILY=3
   ```

   然后 `dc up -d investment-api`，再跑 `check-release.sh`，应看到 `AI skeptic status: ENABLED`。
   服务器和本地各有自己的花费记录，但扣的是同一个 Anthropic 账户。最可靠的兜底是在 Anthropic 控制台给账户设每月用量上限。
4. **紧急关闭**：把 `LAB_SKEPTIC_ENABLED` 改成 `0` 后 `dc up -d investment-api`。页面会显示"尚未开启"，其他功能不受影响。
5. 访客输入保存在命名卷 `invest_lab_data` 中 30 天后自动删除；花费记录不含访客数据。
6. memo 工作流（DESIGN §11.5）和 AI 反方同一个开关。访客的 memo 也在 `invest_lab_data`（`lab.sqlite3` 的 `memos` 表），最后修改 180 天后自动删除。**这个卷现在有访客数据，不要随手删除**；需要备份时在服务器上 `docker run --rm -v invest_lab_data:/d -v "$PWD":/b alpine tar czf /b/lab-data.tgz -C /d .`。

## 自动发布（推荐）

不再需要 SSH 和手工粘贴。流程：GitHub Actions 构建并发布一个 Release，服务器上的定时器每 5 分钟拉取、校验、部署、检查，失败自动回滚。服务器不开放任何新入口，也不保存任何 GitHub 令牌（仓库公开，只读下载）。

**一次性安装**（需要一次 SSH）：

1. 把 `.github/workflows/` 和 `deploy/server/` 提交并推送到 `main`。
2. 把 `deploy/server/` 整个目录复制到服务器（例如 `scp -r deploy/server ubuntu@<服务器>:/tmp/investment-server`），然后 `cd /tmp/investment-server && sudo bash install.sh ubuntu`。脚本会检查 docker/curl/python3/flock/sha256sum/tar，创建 `/opt/investment/{bin,releases,state}`，安装脚本和 systemd 单元并启用定时器。
3. （可选，推荐）GitHub → Settings → Environments → `production` → 勾选 Required reviewers 并选自己：这样每次发布都要你点一次批准。
4. 确认 GitHub 账号开启了两步验证。能触发发布的人就等于能改线上，所以这就是最后一道门。

**每次发布**：GitHub → Actions → Release → Run workflow（选 main）。工作流会跑测试、构建前端和镜像、做镜像边界检查、打包并创建 `release-<UTC时间>` Release，然后每 15 秒轮询 `https://invest.jun-liang-lyu.com/release.txt`，直到它变成新的发布号（最多约 20 分钟），否则标红并发邮件。`ci.yml` 在每次推送和 PR 时单独跑测试和前端构建。

**服务器行为**（`/opt/investment/bin/auto-deploy.sh`）：只接受 `release-YYYYMMDDTHHMMSSZ` 格式的 tag，且发布者在 `ALLOWED_PUBLISHERS`（默认 `github-actions[bot],Junliang-Lyu`）之内；校验 sha256 和压缩包路径；`docker load` 镜像；切换 `current` 软链接和 `.env.production` 里的 `INVEST_API_IMAGE`；`up -d` 并重建 caddy；最多 3 次运行 `check-release.sh`；成功记入 `/opt/investment/state/last_good`，失败记入 `last_failed` 并回滚到上一个版本（同一个失败的版本不会反复重试）。

**排查**：`sudo systemctl status investment-deploy.timer`、`sudo journalctl -u investment-deploy -n 100`、`cat /opt/investment/state/last-deploy.txt`。立刻拉取：`sudo systemctl start investment-deploy.service`。暂停自动发布：`sudo systemctl disable --now investment-deploy.timer`。

以后 SSH 规则可以收回到只允许你本人常用 IP，甚至关闭 22 端口；旧的手工路径（`build-release.ps1`）仍可作为备用。

## 回滚和下线

- 回滚：`ln -sfn /opt/investment/releases/<上一个ID> /opt/investment/current`，把 `INVEST_API_IMAGE` 改回上一个镜像，`dc up -d`。
- 暂时下线 Lab（主站不受影响）：`dc rm -sf investment-api`，然后不带 override 启动：`docker compose --env-file .env.production -f compose.yaml up -d`。Caddy 找不到 `invest.caddy`，子域名就不再提供服务。
- 没有 PostgreSQL。开启 AI 反方后，`invest_lab_data` 卷里有访客的 memo（见上文第 6 条），回滚镜像不影响它；SEC 缓存卷 `invest_edgar_cache` 删掉后会自动重新下载。

## 以后（不在这一版）

私有 dashboard、LLM 反方的公开版本（每 IP 限流、日/月预算、30 天清理）上线时才需要数据库和密钥，方案见 DESIGN §14.2。上线 LLM 之前先完成 ≥ 30 条的 eval 集。
