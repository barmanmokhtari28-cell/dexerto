# Dexerto → Telegram (free, no X API)

Every 4 hours a GitHub Action reads @Dexerto's latest posts from free public sources
(Nitter RSS, then X's public syndication timeline) and sends the new ones to your Telegram channel, oldest first.

## Setup
1. Create a bot with @BotFather, copy the token.
2. Add the bot to your channel as an admin (can post).
3. Repo → Settings → Secrets and variables → Actions → add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` (`@yourchannel`, or `-100...` for private channels)
4. Actions tab → "Dexerto to Telegram" → Run workflow to test.

If every free source is blocked in a run, the job fails (GitHub emails you) and nothing is lost:
the next successful run sends everything missed, as long as the posts are still in the source feed (~20 latest).

## Options (env in the workflow)
- `NITTER_INSTANCES`: comma-separated instance URLs
- `EXTRA_FEEDS`: your own RSS URL(s), tried first
- `TG_LINK_PREVIEW=1`: link preview cards
- `FIRST_RUN_HOURS`: first-run lookback (default 4)

Retweets and replies are skipped.
