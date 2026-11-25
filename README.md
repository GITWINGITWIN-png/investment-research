# investment-research

A personal system for researching US and Thai stocks without fooling myself. Code collects prices, news and SEC filings as point-in-time snapshots. An LLM writes a weekly plan, and code checks the plan against fixed rules and runs it on a paper portfolio next to a passive control. A whole-market scanner books its ideas against a random control drawn from the same candidates, so the question it answers is whether the LLM picks better than chance.

`finlab` is the research harness: rank IC, walk-forward splits, purged cross-validation with an embargo, the Deflated Sharpe ratio and PBO. Its first gate checks the harness itself. Twenty random factors must come out as noise, and a factor that secretly uses future returns must be caught.

## What this copy leaves out

This is the public copy of a private project.

- No price source. robots.txt on Yahoo Finance (query1 and query2.finance.yahoo.com, which yfinance uses), api.nasdaq.com and Google News RSS search disallows bots (checked 25 November 2026), so the code that read them is gone. Every price call goes through `finbg/datasource.py`, which stops with an explanation until you plug in a source you are allowed to use. Without one, the modules import and the CLIs run, but the daily pipeline cannot fetch prices.
- No generated output or private notes. The daily briefs, weekly plans, predictions, scan results and design notes stay on my machine, so some commit messages mention files that are not here.
- Thai stays where the tools need it: the text they write for a Thai reader, the LLM prompts and the Thai keyword lists.

The sources still in use follow each site's rules: SEC EDGAR (official API), the GDELT DOC API, RSS from CNBC, MarketWatch, the Fed and four Thai stock-news sites. The Thai article reader checks robots.txt before every page.

## Setup

```bash
uv sync
export FINBG_SEC_USER_AGENT="your-project you@your-domain.com"   # the SEC requires a name and a reachable email
export FINBG_BACKUP_DIR=/mnt/external/finbg                       # optional: where scripts/backup.sh writes
```

The interpretation steps call `claude -p` headless, so they need a logged-in Claude Code CLI. Phone alerts are optional: `uv run finbg notify setup` creates an ntfy topic. The scheduling files (`scripts/crontab.txt`, `scripts/anacrontab`, `scripts/label-loop-settings.json`) use absolute paths; replace `/home/you/investment-research` with your clone before installing them.

## How it is organised

The work is split into two layers. Code gathers and computes the facts, which is free and exactly repeatable. The LLM only interprets, so tokens go where a model does better than pandas.

| Part | What it does |
|---|---|
| `finbg/collect` | Prices, news from several sources, SEC XBRL financials and the earnings calendar, all stored with `fetched_at` |
| `finbg/pm` | The weekly plan: the LLM chooses the satellite part of a 70/30 core-satellite portfolio, and code checks caps, turnover and reasons before paper trading it against a control |
| `finbg/pm/volatility.py` | Every day, a violent move in a watched stock triggers an LLM investigation of the cause (0 tokens on quiet days) |
| `finbg/scan` | About 3,000 US stocks and every SEC filing are scored in code, Sonnet screens the top 30, Opus digs into up to 5, and ideas are tracked against a random control |
| `finbg/th` | A separate Thai track: point-in-time stock list, prices and news for SET/mai, and its own scanner and idea portfolio |
| `finlab` | The evaluation harness, plus slow factors on tradable Thai stocks net of SET tick costs |

## Rules the code keeps

1. Everything written to disk carries `fetched_at`, the knowledge date that prevents look-ahead bias.
2. Raw data is never overwritten; a repeated file name gets a `__v2` suffix, because stored data is evidence.
3. Every command is safe to run twice, because cron may do exactly that.
4. The ledger is recomputed from scratch each time, so a missed day repairs itself.
5. A step that depends on an outside service may fail without taking the others down; missing news never costs the prices.
6. The model writes plans and the code holds the rules. Every limit lives in code, where a model cannot argue with it.

## Commands

```bash
uv run finbg daily              # the daily job cron runs: collect, ledger, brief
uv run finbg status             # how much data the store holds
uv run finbg plan status        # the current plan and its performance against the control
uv run finbg plan run --dry-run # show the prompt the PM would read, without calling the model
uv run finbg scan --dry-run     # whole-market scan without calling the model
uv run finbg ideas              # idea portfolio against its random control
uv run finbg th status          # what the Thai store holds
uv run finlab gate              # check that the harness rejects fake factors
uv run finlab eval momentum_12_1
```

Run `uv run finbg --help` and `uv run finlab --help` for the rest.

## Unattended labelling

`scripts/morning.sh` runs once a day through anacron, at the machine's first start-up, and calls the other jobs in order. News headlines are third-party text fed to a model that can run commands, so `scripts/label-auto.sh` fences it three ways: a deny list in `scripts/label-loop-settings.json`, a hook (`scripts/guard-queue-write.py`) that allows writes only inside `data/queue/`, and an audit of `permission_denials` after every run. It also checks the model the CLI actually used from the harness's own record.

## Backups

`scripts/backup.sh` runs every 7 days and copies only what cannot be rebuilt: raw data, labels, plans with their prompts, alerts, investigations, idea results and the journal. It never uses `--delete`. The default target is on the same disk, which guards against mistakes but not against a disk failure, so point `FINBG_BACKUP_DIR` somewhere else.
