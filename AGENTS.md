# upkeep — agent notes

## UI law: no layout shifts

The window's layout is **fixed**. A state change may change what is *inside* a
slot; it may never move the slots.

- **Never show/hide a container to signal state.** The stat strip, the list
  header, the list frame and the footer controls are always present. Hiding one
  moves everything below it — that reads as a glitch and makes the app feel
  unstable. Disable a control instead of hiding it, and swap its *text*.
- **Reserve space for anything conditional.** A line that only appears during
  progress keeps its height whether or not it has text. Give the status line one
  line of fixed height, elide it, and put the full text in a tooltip — never let
  a message wrap and push the content down.
- **Rows are all the same height**, and the list uses `ResizeMode.Fixed`.
  Variable row heights make the view re-anchor itself as rows are measured,
  which looks exactly like the list scrolling on its own. The one exception is
  the row the user just unfolded by hand: "Show more" grows *that* row to hold
  what it was hiding (full release notes, the whole requirer list) and folds
  back on "Show less" — see `_relayout_after_row_event`, which holds the scroll
  offset so the clicked row never moves. Nothing else may resize a row.
- **An empty list still looks like a list.** Keep its frame, its header and its
  stat cards; the placeholder lives *inside* the frame and only swaps an icon
  and two lines of text.
- **Lists show "Loading…" while they load** — inside that same frame, in that
  same placeholder, with a line saying what is being loaded. Never a blank box.
- **The only allowed shifts are deliberate, rare and worth it:** the security
  warning banner, the AUR→official banner, opening the log panel, and unfolding
  a row with "Show more". Nothing else may move.

## Where information lives

One place per fact, and no page owns a line of its own:

- **Numbers live in the stat strip.** How many packages, from where — the
  cards above the list say it. A page never repeats them in a paragraph.
- **Loading info lives on the status line, top left** (with the header progress
  track under it). Not beside the list, not in a footer: a list in flight
  reports itself there (`_set_load_status`), and only while its own page is the
  one on screen — which is exactly where the removed per-page line used to sit.
  A running job (check, install, removal, scan) always keeps that line.
- **The list's own placeholder carries the verdict** ("Nothing tracked",
  "Not scanned yet") and the list footer the count (`5 tracked`). Explanations
  that are the same for every row — like what "obsolete" means — go once, in a
  tooltip on the control that acts on them.

Why: pressing Refresh with 13 updates and getting 0 must not make the window
jump. Movement is read as a bug even when the new state is correct.

## Design system

Everything visual comes from the design system at the top of the script:
the 4px grid, the type scale (which follows the desktop font), and the semantic
colors read from the active KDE color scheme. Do not hardcode a color, a font
size or a spacing value — add a token. Icons take a color *role*; an icon on an
accent-filled button uses the `on_accent` role, not its own semantic color.

## Testing

`python3 -m pytest tests -q`. The suite is hermetic (worker threads are stubbed
in `tests/conftest.py`), so it never forks pacman or hits the network. Keep it
that way: a test that needs real work patches the function it calls.
