# Spend Stamina for Character Growth (module version 1.5.8.8)

Automatically spend stamina on the materials missing from your current character growth goals, as listed in the in-game Cultivation Guide. It is a **standalone task** in the task list, on the same level as Anomaly Zone / Anomaly Hunts / Anomaly Pilgrimage, and does not interfere with them. The module version is independent of the ok-nte host version; until it is merged into a host release, availability follows the standalone plugin release.

## What it does

At start it opens the character's Cultivation Guide and reads all four target rows (owned/required) once (ledger mode), then picks dungeons by gap in this fixed priority:

1. Read current stamina and the four material rows, identify which dungeon each row belongs to.
2. Fill rows in order: **Weekly (Anomaly Pilgrimage) > Level row 2 (World BOSS) > Ability row 1 > Arc row 2**.
3. Chaining is fully automatic: keep using Continue Challenge while the current dungeon's output does not yet cover the gap; switch to the next dungeon once the gap is filled.
4. After each claim the log prints a loot summary: how much of each material dropped, how much you have, and how much is still needed.
5. Move to the next row when one is full; when all four are full, switch to Console to spend the remaining stamina; stop when stamina cannot cover one more run.

### Material row to dungeon map

| Goal row | Source | Stamina | Note |
|---|---|---|---|
| Ability row 2 | Anomaly Pilgrimage (weekly) | 60 | 3/week; row 2 is weekly material, not World BOSS |
| Level row 2 | Anomaly Hunts (World BOSS) | 60 | Wild BOSS, walk up before fighting |
| Ability row 1 | Anomaly Zone dungeon | 40 | Double reward and chaining supported |
| Arc row 2 | Arc dungeon (Bubble Can) | 40 | Row 1 is generic upgrade material, not a dungeon drop |

Other rows (common drops, EXP cards, Level row 1, etc.) are out of scope.

## Installation

**Option 1: bundled with the host** (after the PR is merged): update ok-nte and the task appears built-in.

**Option 2: plugin** (recommended; survives host updates):

1. Open the Plugins page in ok-nte → **Add from GitHub** → enter `Camellune/ok-nte-cultivation-goal` and install the latest `.okscript` from the Release.
2. Or download the package from https://github.com/Camellune/ok-nte-cultivation-goal/releases and install it as a local file.
3. The task appears in the list and updates independently of the host.

Do not enable both at once; registering the feature twice interferes with itself.

## How to use

1. Set the game to **1920x1080 / 16:9**, language **Simplified Chinese** (page navigation matches Chinese UI text only; other languages are not supported yet).
2. In-game, open the target character's detail page and pick the character to grow (the guide goals read are that character's).
3. Find **"Spend Stamina for Character Growth"** in the task list, configure, and start.

## Configuration

| Option | Default | Description |
|---|---|---|
| Daily stamina target | 180 | Stop once today's cumulative stamina reaches this; 0 = no cap — farm by gap until all materials are complete, then it still switches to Console to spend the remaining stamina. **If today's target is already met at start, the run is treated as uncapped**: current stamina is still spent, it is not preserved |
| Weekly farming | on | Weekly switch: on = clear this week's 3 weekly runs first; off = go straight to other materials, weekly only when Ability row 2 is short |
| Loot statistics | on | After each claim, log per material: dropped this round, owned, still needed |

Chaining is automatic with no option: estimated from the previous round's output; it keeps chaining until the gap is covered or stamina runs out (internal safety cap of 20 rounds).

## Double stamina

- Dungeons support double reward: two stamina, two rewards, same yield as two runs but only one fight.
- Double is used when the row's gap is ≥ 2 and stamina covers two units; otherwise it falls back to single to avoid overflow.

## Recognition and loot tracking

- Row numbers are read by OCR; an occasional slash misread as `1` (e.g. `47/60` → `47160`) is repaired at parse time, with tiered retries as fallback.
- Material icons and reward-card loot use local templates; no game memory is read and no game files are modified.
- Loot from each claim list is accumulated locally (ledger mode); when loot is unreadable the guide is re-checked once instead of guessing.
- Each run writes a JSONL evidence chain to `logs/cultivation_goal.jsonl`; view it with `python -m src.tasks.cultivation_goal.report`.

## Notes / troubleshooting

- **Unreadable row**: confirm you are on the target character's Cultivation Guide at 1920x1080; a screenshot is saved only for an unreadable row with no previous reading (a row reusing last round's numbers is marked stale and is not captured again).
- **Rows skipped repeatedly**: a row with zero gain for two chained rounds is skipped this session.
- **Weekly not finished**: if Anomaly Pilgrimage fails midway, weekly is not retried this session; already-claimed runs still count.
- **Out of scope**: common drops / EXP cards / Level row 1.
- The task is independent of the three anomaly tasks; disabling it leaves them running as before.
