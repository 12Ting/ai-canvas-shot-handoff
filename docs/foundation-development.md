# 基础画布开发说明

同一个项目 ID 同时用于镜头工作台和语义画布。目录型项目从首页进入镜头；画布中继续使用节点、图版本、分支和素材历史。纯画布项目可从最近项目打开，也可选择为其连接本地工作目录。镜头排序和显式采用共用图修订，未保存的画布编辑仍使用原有冲突处理。旧画布的视频槽显示为「已有绑定」，要纳入项目选片包时，可将该源片登记到生成组并确认采用。

## 开发环境

先运行 `start.cmd --prepare-only`。从项目根目录运行 `scripts/dev.ps1` 可启动 Vite 和本机 API，按 Ctrl+C 退出。`scripts/dev.ps1 -NoAuth` 只用于本机调试，使用独立测试数据和进程内临时会话。正式入口使用本机单次引导令牌。

```powershell
.\scripts\test-dev-runtime.ps1
.\scripts\check.ps1
npm.cmd exec --prefix apps/web -- playwright install chromium
npm.cmd run e2e --prefix apps/web
npm.cmd run e2e:noauth --prefix apps/web
npm.cmd run e2e:release --prefix apps/web
```

`check.ps1` 在 Git 工作区内检查 Python、前端测试、提交的 OpenAPI 契约和生产构建。浏览器测试使用单独的数据目录与端口。若要在终端直接运行 Python 服务，可安装本地开发包：

```powershell
.\apps\api\.venv\Scripts\python.exe -m pip install -e apps/api --no-deps
```

## 数据边界

SQLite 与本机内容寻址库一起保存。正式服务由数据目录锁保证单进程写入。画布素材通过已有的版本与回收机制管理，目录工作台另固定下载结果、原参考和声音的字节，供补镜意见和历史条件长期引用。浏览器原生播放和大文件下载使用短期、限定资源的本机会话。还原整个项目时，先关闭应用，再还原整份数据库及其同级媒体目录。

`apps/api/migrations` 保存迁移，`apps/api/src/ai_canvas/application` 保存业务规则，`apps/web/src/features/canvas` 和 `handoff` 分别包含两个界面。新增 API 后用 `scripts/generate-api-client.ps1` 更新并一起提交 OpenAPI 与 TypeScript 契约。
