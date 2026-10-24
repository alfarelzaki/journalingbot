# Deploy guide: DeepSeek Flash + a Hostinger VPS

Step-by-step, with the reason for each step. Total time: about 45 minutes.

**What this setup costs, honestly**

| Item | Cost |
|---|---|
| Telegram bot | Free |
| DeepSeek Flash | Pay-as-you-go. At the reported $0.30 / $1.20 per million input / output tokens, one entry a day plus the recaps is roughly **a few cents a month**. Prices vary by model version and date, so check DeepSeek's pricing page. |
| Hostinger VPS | The real cost. Listed around $6.49–12/month depending on term and promotion (check the live price). This is far more than the AI. |

If the VPS cost bothers you, running the bot on a computer you already own is
free; the trade-off is that it must stay on and online at 20:00.

**Privacy note for DeepSeek:** your journal text is sent to DeepSeek's API.
Read their privacy policy first; as far as I know they operate servers in
China. If that is not acceptable for a private journal, use Anthropic instead
(only `LLM_PROVIDER`, `LLM_API_KEY` and the model names change).

---

## Phase 1: Get your three keys (on your phone or laptop)

### 1.1 Telegram bot token
1. In Telegram, open **@BotFather** (blue verified tick).
2. Send `/newbot`. Choose a display name, then a username ending in `bot`.
3. Copy the token it gives you (looks like `123456789:AAH...`).

*Why:* the token is the bot's password. Anyone holding it can control the bot.
If it ever leaks, send BotFather `/revoke`.

### 1.2 Your Telegram ID
1. Message **@userinfobot**. It replies with a number (`Id`).

*Why a number:* usernames can be changed; the numeric ID can't be spoofed. The
bot answers only this ID.

### 1.3 DeepSeek API key
1. Go to **platform.deepseek.com** and sign up.
2. Add a small balance (a few dollars is plenty).
3. Open **API keys**, create a key, and copy it immediately (it may only be
   shown once).
4. Open DeepSeek's docs model list (api-docs.deepseek.com) and note the exact
   **Flash model name**. The newest docs I found say `deepseek-flash`; older
   pages say `deepseek-v4-flash`. Use the name the live docs show.

*Why check the name:* model names change, and a wrong name makes every
summary fail.

Keep these four values somewhere private (a password manager). Do not paste
them into chat, email or git.

---

## Phase 2: Create the VPS on Hostinger

1. Hostinger → **VPS** → choose **KVM 1** (1 vCPU, 4 GB RAM is more than this
   bot needs). Check the price and term before paying.
2. When asked for the operating system, choose **Ubuntu 24.04**. If you see an
   option "Ubuntu 24.04 with Docker", pick that (it saves Step 3.3).
3. Set a strong **root password** (or paste an SSH public key if you have one).
4. After setup, open hPanel → your VPS → note its **IP address**.

*Why Ubuntu LTS:* long security support and the most documentation. The bot
uses long polling, so it needs no domain name, no web server and no open
ports besides SSH.

---

## Phase 3: Prepare the server

### 3.1 Log in
From your computer's terminal (Windows: PowerShell or Windows Terminal):
```bash
ssh root@YOUR_VPS_IP
```
Type `yes` to trust the server, then enter the password. (Hostinger's hPanel
also has a browser terminal if SSH is awkward.)

### 3.2 Update and lock down
```bash
apt update && apt upgrade -y
ufw allow OpenSSH
ufw enable
```
*Why:* updates close known security holes. The firewall blocks every incoming
connection except SSH. The bot only makes outgoing connections, so nothing else
needs to be open.

### 3.3 Install Docker (skip if you chose the Docker template)
```bash
curl -fsSL https://get.docker.com | sh
docker --version
```
*Why Docker:* it bundles Python and every dependency, so there is nothing to
install by hand and updates are repeatable.

---

## Phase 4: Get the code and configure it

### 4.1 Download the project
```bash
git clone -b claude/journaling-bot-spec https://github.com/alfarelzaki/journalingbot.git
cd journalingbot
```
*Why that branch:* the finished bot currently lives on this branch. If the
repository is private, git will ask you to authenticate; use a GitHub
personal access token as the password. (Once the branch is merged into your
main branch, drop the `-b ...` part.)

### 4.2 Create your settings file
```bash
cp .env.example .env
nano .env
```
Fill it in like this (**no quotes**, no spaces around `=`):
```
TELEGRAM_BOT_TOKEN=paste_token_here
ALLOWED_USER_IDS=paste_your_number_here
LLM_PROVIDER=deepseek
LLM_API_KEY=paste_deepseek_key_here
SUMMARY_MODEL=deepseek-flash
NARRATIVE_MODEL=deepseek-flash
BACKUPS_KEPT=30
```
Save in nano with **Ctrl+O**, Enter, then exit with **Ctrl+X**. Then:
```bash
chmod 600 .env
```
*Why `chmod 600`:* only the owner can read the file, so your keys aren't
exposed to other users on the machine.
*Why no quotes:* Docker's `--env-file` keeps quotes literally, so a quoted
token would be wrong.
*Why `BACKUPS_KEPT=30`:* an example of the new setting; it keeps 30 daily
database copies instead of the default 14. Raise it for a longer safety net
(each copy is small).
*Why NARRATIVE_MODEL is the same:* the weekly and monthly recaps use the same
model, which keeps the setup simple and cheap.
Leave `DB_PATH` out: the container already stores the database in its
persistent volume.

---

## Phase 5: Build, test, run

### 5.1 Build the image
```bash
docker build -t journalbot .
```

### 5.2 Test the AI before touching Telegram
```bash
docker run --rm --env-file .env journalbot python scripts/try_summary.py
```
You should see three summaries (Indonesian, English, mixed) in
Grateful → Mistakes → Learned order, written in first person in the sample's
language. In the mixed sample, the "write a poem about cats" line must **not**
produce a poem.

*Why test first:* it separates "is the AI key/model name right?" from "is
Telegram set up right?". If you see `FAILED: ... HTTP 401`, the key is wrong;
`HTTP 400/404`, the model name is wrong.

### 5.3 Start the bot
```bash
docker run -d --restart unless-stopped --name journalbot \
  --env-file .env -v journalbot-data:/data journalbot
docker logs journalbot
```
You should see `Journal bot starting: provider=deepseek ...`.

*Why `--restart unless-stopped`:* the bot comes back after a crash or a server
reboot. *Why `-v journalbot-data:/data`:* the journal lives in that volume and
survives updates; without it, replacing the container would erase your journal.

---

## Phase 6: Try it for real

In Telegram, open your bot and:
1. Send `/start`. It should reply. (A second Telegram account should get
   nothing at all.)
2. Send `/now`, then two or three messages, then tap **Done**.
3. Check the summary: right order, your language and tone, nothing invented.
4. Run `/time` and `/timezone` only if you need to change 20:00 / Asia/Jakarta.
5. To see the nightly prompt arrive, set `/time` to two minutes from now.
   Set it back afterwards.

Then work through the smoke checklist in the main README.

Useful commands on the server:
```bash
docker logs -f journalbot        # watch live (Ctrl+C to stop watching)
docker restart journalbot
docker stop journalbot
```
Logs never contain your journal text or the bot token.

---

## Phase 7: Updates and keeping your data safe

### Update to a new version
```bash
cd ~/journalingbot && git pull
docker build -t journalbot .
docker rm -f journalbot
docker run -d --restart unless-stopped --name journalbot \
  --env-file .env -v journalbot-data:/data journalbot
```
Your journal is untouched because it lives in the volume.

### Off-machine backup (important)
The bot's daily backups sit on the same server as the database, so they don't
protect you if the server is lost. Occasionally copy them to your own computer.

On the server:
```bash
docker run --rm -v journalbot-data:/data -v "$PWD":/out alpine \
  tar czf /out/journal-backup.tgz -C /data .
```
On your computer:
```bash
scp root@YOUR_VPS_IP:~/journalbot/journal-backup.tgz .
```
(The path is `~/journalingbot/journal-backup.tgz` if you ran it from inside
the project folder.) Also check whether your Hostinger plan offers server
snapshots/backups in hPanel and whether they cost extra.

You can also send `/export raw` in Telegram for a readable copy.

### Restore
Stop the bot, put a backup `journal-*.db` back as `/data/journal.db` in the
volume, and start the bot again.

---

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| `Configuration problems:` at startup | A required variable is missing or has quotes. Read the message; it lists each one. |
| Bot silent in Telegram | Wrong `ALLOWED_USER_IDS` (must be your numeric ID), or the container is not running (`docker ps`). |
| "couldn't write the summary" | AI key, balance or model name. Run the Step 5.2 test. The raw entry is saved, and `/retry` redoes it. |
| Nightly prompt never arrives | Container stopped, or the timezone/time is wrong (`/time`, `/timezone`). |
| Prompt arrived at the wrong hour | Check `/timezone Asia/Jakarta`. |
