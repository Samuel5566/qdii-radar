# QDII Radar

纳斯达克100 + 标普500 QDII 场外申购额度监控。

- GitHub Actions 自动抓取公开 QDII 额度日报
- 自动区分纳斯达克100 / 标普500
- 自动区分场外 / 场内ETF
- 自动记录代销额度 / 基金公司直销额度
- 与上一次成功数据比较：额度放宽 / 收紧 / 状态变化
- GitHub Pages 展示最新数据

数据源：安鑫乐量化实验室 QDII 基金申购限额日报。最终申购状态与额度以基金管理人公告及实际下单页面为准。

## 自动更新

工作流默认：

- 每小时自动执行一次
- 代码更新后自动执行一次
- 可在 GitHub Actions 页面手动运行

更新结果写入：

- `data/current.json`：当前最新数据
- `data/history.json`：最近 90 次成功快照

## GitHub Pages

仓库文件提交完成后，在 GitHub 仓库：

`Settings → Pages → Build and deployment → Source → GitHub Actions`

选择 GitHub Actions 后，页面将通过 Pages 工作流自动发布。
