# Blackout Bakery

A 2–4 player co-op Roblox game about **asymmetric information**. Nobody can do
the whole job alone:

- **Reader** sees the order ticket — and *only* the Reader. They can't cook.
- **Cook** has full run of the bins, oven, and serve counter — but never sees
  the recipe. They cook by ear, off the Reader's shouting.
- **Taster** can taste the in-progress dish and gets a fuzzy hint back
  ("needs something sweeter") — but never sees the ticket either.

Roles rotate every round so everyone plays all three across a session.

---

## The security model (the part that actually matters)

The recipe is a **server secret**. It is never sent to clients who shouldn't
have it, so it can't be pulled out of the dev console — there is nothing to pull.

- `src/serverstorage/RecipeConfig.luau` lives in **ServerStorage**. Roblox never
  replicates ServerStorage to any client. The Cook's machine has *zero* recipe
  data on it.
- `OrderManager` fires the ticket at the Reader alone:

  ```lua
  ticketEvent:FireClient(readerPlayer, {
      recipeId = currentRecipe.id,
      name = currentRecipe.name,
      lines = RecipeConfig.toTicketLines(currentRecipe),
      timeLeft = ...,
  })
  ```

  Cook and Taster clients simply never have this event fired at them.
- `ClientTicketUI` renders the ticket **only when that event actually fires**.
  It is not a frame everyone receives with a client-side `if isReader` hiding
  it — that would be UI theater a Cook bypasses in ten seconds. Same idea for
  the Taster's hints (`TasteEvent` → Tasters only) — and the hints are *fuzzy*
  (a direction, never a number), so they can't be reverse-engineered into the
  exact recipe.
- Every station validates the player's **role on the server** on each
  `ProximityPrompt.Triggered`. The client disables prompts locally for roles
  that shouldn't use them, but that's purely for feel — a hacker who flips
  `.Enabled` back on and triggers a bin still gets rejected server-side, because
  the server never trusts the client's claimed role.

Same principle as keeping rarity rolls server-side: the authoritative data and
the authoritative checks never leave the server.

---

## Systems

| Script | Where | Responsibility |
| --- | --- | --- |
| `Config` | ReplicatedStorage | Client-visible constants only (event names, timings). **No secrets.** |
| `RecipeConfig` | **ServerStorage** | Dish definitions — ingredients + quantities. The secret. |
| `Net` | Server module | Creates the RemoteEvents at boot. |
| `RoleManager` | Server module | Assigns/rotates Reader, Cook, Taster each round. |
| `OrderManager` | Server module | Picks a recipe, tracks patience, fires the ticket to the Reader only. |
| `DishState` | Server module | Tracks the in-progress dish's ingredients. |
| `TasteService` | Server module | Diffs DishState vs. target, sends a fuzzy hint to Tasters only. |
| `WorldBuilder` | Server module | Builds the kitchen (bins, oven, counter, trash, ready pad) from code. |
| `IngredientStation` | Server | Handles bin/oven/trash prompts, checks role, updates DishState. |
| `SubmitStation` | Server | Compares the final dish to the recipe, scores, queues the next order. |
| `Main` | Server | Orchestrates lobby → shift → summary, reputation, role rotation. |
| `ClientTicketUI` | Client | Renders the ticket — only if the ticket event fires for you. |
| `ClientTasteUI` | Client | Renders taste hints — only if the taste event fires for you. |
| `ClientHUD` | Client | Reputation, timer, score, role banner, bowl panel, local prompt gating. |

---

## How to open it in Roblox Studio

### Option A — Rojo (recommended)

[Rojo](https://rojo.space) syncs these source files into Studio.

1. Install Rojo (`aftman add rojo-rbx/rojo` or the VS Code extension).
2. From this folder: `rojo serve`
3. In Studio, install the Rojo plugin, click **Connect**.
4. Press **Play**. The kitchen builds itself; step on the **Ready Pad** to start.

To get a `.rbxl` without Studio syncing: `rojo build -o BlackoutBakery.rbxl`.

### Option B — paste by hand

If you're not using Rojo, recreate this tree in Studio and paste each file in:

```
ReplicatedStorage/BlackoutBakery/Config            (ModuleScript)  <- src/shared/Config.luau
ServerStorage/BlackoutBakery/RecipeConfig          (ModuleScript)  <- src/serverstorage/RecipeConfig.luau
ServerScriptService/BlackoutBakery/Main            (Script)        <- src/server/Main.server.luau
ServerScriptService/BlackoutBakery/modules/*       (ModuleScripts) <- src/server/modules/*.luau
ServerScriptService/BlackoutBakery/stations/*      (ModuleScripts) <- src/server/stations/*.luau
StarterPlayer/StarterPlayerScripts/Client*         (LocalScripts)  <- src/client/*.client.luau
```

The `.server.luau` suffix → `Script`, `.client.luau` → `LocalScript`, plain
`.luau` → `ModuleScript`. Nothing needs to be built by hand in the world —
`WorldBuilder` spawns the whole kitchen at runtime.

### Testing with multiple players

Studio: **Test → Clients and Servers → Start** with 2–4 players. Each window is
a player; assign yourselves to bins and try to fill an order without letting the
Cook see the ticket.

---

## How a round plays

1. **Lobby** — everyone steps on the Ready Pad. Shift starts when all present
   players (2–4) are ready.
2. Server assigns **Reader / Cook / Taster** (4th player = second Cook).
3. Orders spawn one at a time, each with a **patience timer**. The Reader reads;
   the Cook adds ingredients at the bins, bakes at the oven, serves at the
   counter; the Taster tastes the bowl for hints.
4. A **wrong** dish or an **expired** order costs a point of reputation.
5. The shift ends when the timer runs out or reputation hits zero. Roles rotate,
   and the next round is a little harder (higher-tier recipes, shorter patience).
6. Session ends → day-end summary → back to the lobby.

---

## Decisions made (from the spec's "things to decide")

- **Fuzzy taste hints**, not exact diffs — funnier, and can't be cheesed into
  the recipe. The hint names the single biggest problem as flavor text.
- **Soft fail via reset**, not instant hard-fail. Adding a wrong ingredient
  doesn't nuke the dish, but the only fix is the **Trash** can (dump and
  restart), and the cost is the time you burn — which keeps the panic up.
- **Bins are labeled** by default (`Config.LabelBins = true`). Flip it to
  `false` for a "learn-the-layout-from-memory" hard mode.

These are all one-line changes in `Config` / the relevant module — tune away.

---

## Tuning

Everything numeric lives in `src/shared/Config.luau` (`Timing`, `Scoring`,
`LabelBins`) and `src/serverstorage/RecipeConfig.luau` (recipes and their
difficulty `tier`). Add a recipe by dropping another entry in the `RECIPES`
list; the difficulty ramp and taste hints pick it up automatically.
