# npm 下载统计（独立公开数据采集模块）

此模块在公开 GitHub Actions Runner 上，按周只读查询 GitHub 账号 Tests4gptplay 的以下六个公开插件仓库的根目录 `package.json`，获取实际声明的 npm 包名；经 npm 官方 Registry 核实已发布包后，查询 `api.npmjs.org/downloads` 的 last-day、last-week、last-month 和最近30天逐日序列。

- 范围固定：`dsh-compaction-selector`、`deepseek-servant`、`dsh-codex-native`、`dsh-kujira-persona`、`dsh-video-vision`、`dsh-dots`。
- 来源：GitHub 公开仓库 `package.json`、npm 官方 Registry、npm 官方 Downloads API。通过真实包名匹配，不猜包名；无数据或失败状态单独保留，不作为零下载。
- 数据：`data/npm_downloads/latest.json` 和 `data/npm_downloads/history/*.json`。JSON 内含 UTC 采样时刻及官方返回的 UTC 统计区间；历史采样独立存档。
- 定时：每周一 **03:15 日本时间**（前一天 18:15 UTC），使每周一 08:00 JST 的开源周报可以读取最新已取得的官方数据；GitHub Actions 定时可能存在排队延迟，周报以文件实际采样时刻判断新鲜度。
- 工作流：`.github/workflows/npm-downloads-weekly.yml`；同时支持手动运行。首次部署时该工作流文件提交会触发一次真实采集。
- 实现：仅 Python 标准库，无额外依赖；工作流运行单元测试后采集并保存数据。使用公开仓库的 GitHub 托管 Ubuntu Runner，不修改任何插件仓库、Release、市场数据或金融采集模块。
- `deepseek-servant` 当前在 `package.json` 声明无 scope 的 npm 名称；只有 Registry 明确发布且 repository URL 可对应本仓库时才计入。未发布状态按实际读取结果记录。
- npm 的 7/30 日数据是对应 UTC 区间内的滚动计数，不能直接当累计增长；GitHub Release 与 npm 按各自来源、区间分别统计。

本模块与 `collectors/china_financial/`、现有金融采集工作流和调度完全独立。其他金融采集任务的暂停状态保持不变。
