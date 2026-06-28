# 台股研究與自動報告

這個專案使用公開 API 產生可解釋的台股與 ETF 量化投資建議。

## 報告內容

- TWSE、TPEx 全市場流動性與最新月營收初篩
- FinMind 20／60 日趨勢、量能、波動與回撤
- 國際市場、匯率、利率與商品價格對台股的可能影響
- 台股大盤資金健康度、三大法人、期權籌碼與信用交易動態
- 次一交易日大盤上漲、平盤、下跌情境機率與反證條件
- 股票：趨勢、營收、估值、風險綜合評分
- ETF：趨勢、波動與流動性評分
- 每個建議標示投資原因與主要風險
- 同時輸出 Markdown 與 HTML

報告是依公開資料產生的一般性量化建議，不考慮個人財務狀況，
也不保證未來績效。

## 手動執行

```powershell
.\run_report.ps1 -Mode daily
.\run_report.ps1 -Mode weekly
```

報告輸出在 `reports`：

- `reports\daily`
- `reports\weekly`
- `reports\latest.html`

背景執行紀錄在 `logs\scheduler-*.log`。

## Discord 自動傳送

Discord webhook 可以直接附加 HTML 報告，不需要建立 Discord Bot。

1. 在 Discord 頻道設定中建立 Webhook。
2. 執行以下命令，網址不會寫入程式碼：

```powershell
.\setup_notifications.ps1 -DiscordWebhookUrl "你的 Discord Webhook URL"
```

之後每次排程產生報告，都會傳送摘要與 HTML 附件。

若已設定 GitHub Pages，Discord 訊息也會附上可直接在瀏覽器開啟的報告網址：

```text
https://c20021013.github.io/taiwan-stock-reports/
```

報告會由 `publish_report.py` 自動更新到公開儲存庫
`c20021013/taiwan-stock-reports`。GitHub Token 與 Discord Webhook 僅保存在
Windows 使用者環境變數，不會上傳到公開儲存庫。

## LINE 自動傳送

LINE Notify 已於 2025 年 3 月 31 日終止，現在需使用 LINE Messaging API。
LINE 不支援直接附加任意 HTML 檔，因此會傳送摘要與公開網址。

需要準備：

- LINE Official Account 的 Channel access token
- 接收者的 user ID、group ID 或 room ID
- 可公開存取報告的 HTTPS 基底網址

```powershell
.\setup_notifications.ps1 `
  -LineChannelAccessToken "你的 Channel access token" `
  -LineTargetId "Uxxxxxxxxxxxxxxxx" `
  -ReportPublicBaseUrl "https://example.com/taiwan-stock-research"
```

若未設定公開網址，LINE 仍可傳摘要，但手機無法開啟本機 `E:\` 內的 HTML。

移除所有通知設定：

```powershell
.\setup_notifications.ps1 -Remove
```

測試摘要與設定狀態，不會真的傳送：

```powershell
python .\notify_report.py --mode daily --dry-run
```

## Windows 背景排程

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_tasks.ps1
```

排程會在 Windows 工作排程器中建立：

- 週一至週五 08:00 每日報告
- 週日 21:00 每週彙整（包含週末與當週整理）

每日報告的「建議原因」優先使用公司重大訊息、月營收與產業事件。
均線、短期報酬及量能只用於排序，不會被寫成股票上漲原因。若個股
只有技術分數，卻沒有可驗證的公司事件或明顯營收成長，最高只列為
「建議觀察」。

報告也會加入「國際情勢與台股影響」，追蹤美股大盤、Nasdaq、費城
半導體、美國 10 年債殖利率、美元指數、美元／台幣、WTI 原油與黃金，
並把變化轉成對台股電子、金融、高股息、能源成本與外資資金流的可能影響。

「次一交易日大盤方向推估」整合國際盤、台股市場廣度、外資現貨、
外資台指期與匯率，輸出上漲／平盤／下跌三種機率。平盤定義為加權指數
收盤漲跌介於 ±0.3%，單一方向機率最高 60%；這是可檢查的情境猜測，
不預測點位，也不保證報酬。

移除排程：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_tasks.ps1 -Remove
```

Codex 不必保持開啟。電腦仍需開機、能連上網路；若排程時間處於睡眠，
工作排程器會嘗試喚醒，或在下次可執行時補跑。

## GitHub Actions 雲端排程

若要完全不依賴本機電腦，可以把專案放到 GitHub repository，使用
`.github/workflows/taiwan-stock-reports.yml` 在 GitHub 雲端自動執行。

雲端排程：

- 週一至週五 08:00（Asia/Taipei）：每日報告
- 週日 21:00（Asia/Taipei）：每週彙整（包含週末與當週整理）

到 GitHub repository 的 `Settings` → `Secrets and variables` → `Actions`
設定：

Secrets：

- `DISCORD_WEBHOOK_URL`：Discord webhook
- `GITHUB_REPORT_TOKEN`：可寫入報告 repository 的 GitHub token。如果 workflow
  和報告放在同一個 repository，可先省略，改用 GitHub 內建 token。
- `FINMIND_TOKEN`：選填；未設定也能跑，但可能遇到頻率限制。
- `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_TARGET_ID`：選填，只有要傳 LINE 才需要。

Variables：

- `GITHUB_REPORT_REPOSITORY`：預設 `c20021013/taiwan-stock-reports`
- `REPORT_PUBLIC_BASE_URL`：預設 `https://c20021013.github.io/taiwan-stock-reports`

第一次測試：

1. 到 GitHub repository 的 `Actions`。
2. 選 `Taiwan Stock Reports`。
3. 按 `Run workflow`。
4. `mode` 選 `daily`。
5. 執行成功後，確認 GitHub Pages 與 Discord 是否出現新報告。

GitHub Actions 排程可能因 GitHub 負載延遲幾分鐘，但不需要 Codex 或本機電腦開著。

## FinMind Token

未設定 token 也能執行。若遇到免費 API 頻率限制，可在環境變數設定：

```powershell
$env:FINMIND_TOKEN = "你的 token"
```

## 資料限制

- 公開資料可能延遲、缺漏或事後修正。
- 休市日使用最近一個交易日資料。
- 國際市場資料來自 Yahoo Finance Chart；若連線失敗會使用快取或略過。
- 籌碼、新聞與財報欄位部分來自 FinMind；免費權限不足的資料會在報告標示資料暫缺。
- 評分不含未公開資訊，也無法預測突發事件。
- 交易前應核對公開資訊觀測站、交易所公告及正式財報。
