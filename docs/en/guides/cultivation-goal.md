# Spend Stamina for Character Growth (module version 1.5.10.2)

Automatically spend stamina on the materials missing from your current character growth goals, as listed in the in-game Cultivation Guide. It is a **standalone task** in the task list, on the same level as Anomaly Zone / Anomaly Hunts / Anomaly Pilgrimage, and does not interfere with them. The module version is independent of the ok-nte host version; until it is merged into a host release, availability follows the standalone plugin release.

## What it does

At start it opens the character's Cultivation Guide and reads all four target rows (owned/required) once (ledger mode), then picks dungeons by gap in this fixed priority:

1. Read current stamina and the four material rows, identify which dungeon each row belongs to.
2. Fill rows in order: **Weekly (Anomaly Pilgrimage) > Level row 2 (World BOSS) > Ability row 1 > Arc row 2**.
3. Chaining is fully automatic: keep using Continue Challenge while the current dungeon's output does not yet cover the gap; switch to the next dungeon once the gap is filled.
4. After each claim the log prints a loot summary: how much of each material dropped, how much you have, and how much is still needed.
   **All tiers of a row are counted in lowest-tier units** (3 low combine into 1 high, and 1 high can be
   dismantled back into 3 low, so tiers are just forms of the same material): `48/8 · 32/40 · 16/13` folds
   into 288 / 245 units and is already enough, so no further runs; conversely `0/15 · 60/0` is also enough
   because the spare high tier can be dismantled to cover the low gap. The log prints both the raw readings
   and the folded `owned/required` so the numbers reconcile.
5. Move to the next row when one is full; stop when stamina cannot cover one more run.
6. **After every material row is full, coin still has to be checked**: scroll to the bottom of each of
   the Level / Arc / Ability tabs; their last "required materials" row is always coin (Armor Coin). The
   three requirements differ and **must be added up**; the owned number on the left is the same inventory
   on all three tabs, so **count it once** (normally identical; if they differ the majority wins, and if
   all three differ the check is skipped for this round). If coin is short it runs the Coin Saga dungeon
   (Anomaly Zone, 40 stamina, drops Magic Thread and coin); Console only comes after coin is covered.
7. **Console has the lowest farming priority, but no run limit**: once every other row is stuck (materials
   complete, or weekly runs used up while still short), Console is what turns the remaining stamina into
   materials instead of letting it overflow. Which dungeon it runs is decided by the **recommended set**:
   the Console page only shows set names (e.g. "Speedy Hedgehog" x4), which are looked up in a
   set → Rabbit Hole node table (Clockwork Trick / Sculpture Hall / Loom of Latitudes / Guard Radish /
   Mental Atlas / Night of the Track); the run then uses the stock F1 dungeon-select flow. If the set name
   cannot be read or is not listed, it deliberately does nothing — farming the wrong dungeon just burns 40 stamina.
   Console runs are **logged but not booked** (the drops are nearly all the same gold disc, so icons cannot tell
   nodes apart); confirmation only checks that the settlement confirm button appears. Nodes 5/6 (Mental Atlas /
   Night of the Track) need hunter level to unlock; if locked the entry simply fails and **no stamina is spent**.

### Material row to dungeon map

| Goal row | Source | Stamina | Note |
|---|---|---|---|
| Ability row 2 | Anomaly Pilgrimage (weekly) | 60 | 3/week; row 2 is weekly material, not World BOSS |
| Level row 2 | Anomaly Hunts (World BOSS) | 60 | Wild BOSS, walk up before fighting |
| Ability row 1 | Anomaly Zone dungeon | 40 | Double reward and chaining supported |
| Arc row 2 | Arc dungeon (Bubble Can) | 40 | Row 1 is generic upgrade material, not a dungeon drop |
| Coin (last row of all three tabs) | Anomaly Zone - Coin Saga | 40 | Requirements added across tabs, owned counted once; handled only after materials are full |
| Console (lowest priority) | Anomaly Zone · Console (Rabbit Hole) | 40 | No run limit; dungeon comes from the recommended set, refuses to run when unreadable |

Other rows (common drops, EXP cards, Level row 1, etc.) are out of scope.

## Installation

**Option 1: bundled with the host** (after the PR is merged): update ok-nte and the task appears built-in.

**Option 2: plugin** (recommended; survives host updates):

1. Open the Plugins page in ok-nte → **Add from GitHub** → enter `Camellune/ok-nte-cultivation-goal` and install the latest `.okscript` from the Release.
2. Or download the package from https://github.com/Camellune/ok-nte-cultivation-goal/releases and install it as a local file.
3. The task appears under the existing "Stamina" group next to Anomaly Zone, not in a tab of its own, and updates independently of the host.

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
  Stamina works the same way: **right before deciding "not enough stamina for one more run", the panel is read again**,
  and that measured value is what the decision and the reported remainder use — the opening reading is never reported as the remainder.
  Console is the exception: it has no run limit and any material is useful, so its loot is **logged only, never booked** —
  whether a run counted is decided solely by the settlement screen appearing.
- Each run writes a JSONL evidence chain to `logs/cultivation_goal.jsonl`; view it with `python -m src.tasks.cultivation_goal.report`.

## Notes / troubleshooting

- **Unreadable row**: confirm you are on the target character's Cultivation Guide at 1920x1080; a screenshot is saved only for an unreadable row with no previous reading (a row reusing last round's numbers is marked stale and is not captured again).
- **Rows skipped repeatedly**: a row with zero gain for two chained rounds is skipped this session.
- **Weekly dungeon not unlocked**: the bottom-right button of the map detail panel reads "追踪" (Track) instead of "传送" (Teleport). Before queueing, the plugin opens each weekly target and reads that button; on "追踪" it logs and skips that target instead of waiting 10 minutes for a teleport button that never shows. Unreadable buttons count as unlocked — a wasted attempt is cheaper than dropping a playable dungeon.
- **Weekly not finished**: if Anomaly Pilgrimage fails midway, weekly is not retried this session; already-claimed runs still count.
- **Remaining stamina looks off**: only the opening reading comes from the panel; in between the ledger drives it. The panel value is refreshed from the ledger, and one real measurement is taken when the run stops, so the final number is measured. Look for `收工前复核体力: 账本 X → 实测 Y` in the log if you suspect ledger drift.
- **Out of scope**: common drops / EXP cards / Level row 1.
- **Coin not handled**: it only takes effect when all three tabs read successfully. If any tab fails
  (page not scrolled to the bottom, or the slash was not recognised) the log shows
  `甲硬币 X 页读不出, 本轮不检查甲硬币` and this round goes straight to Console. Under-reading one tab
  would look like "already enough", which is worse than not farming at all. The
  `甲硬币 拥有 X, 三页需求 Y` line reconciles the numbers directly.
- The task is independent of the three anomaly tasks; disabling it leaves them running as before.
