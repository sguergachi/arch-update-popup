# upkeep — agent notes

## UI law: no layout shifts

The window's layout is **fixed**. A state change may change what is *inside* a
slot; it may never move the slots.

- **Never show/hide a container to signal state.** The stat cells, the control
  row, the list frame and the footer controls are always present on a page that
  has them. Hiding one moves everything below it — that reads as a glitch and
  makes the app feel unstable. Disable a control instead of hiding it, and swap
  its *text*.
- **The filter field and the cards live in one control row.** `_ControlsRow` is
  a window-level widget that serves the pages which have something to filter:
  the field on the left at its fixed width, then the page's stat cells — the
  numbers that double as the source filters — then Select all at the far right.
  Nothing in the row may change size: the field is fixed, and "N of M" has a
  reserved slot so the cards beside it never resize when a filter starts
  hiding rows. Obsolete and Security have nothing to filter and nothing to
  tick, so they hide the *whole row* — a page difference, not a state change;
  within a page the row is never hidden. The **view switcher is not in here**:
  it stays in the window header, where no page can move it.
- **Reserve space for anything conditional.** A line that only appears during
  progress keeps its height whether or not it has text. Give the status line one
  line of fixed height, elide it, and put the full text in a tooltip — never let
  a message wrap and push the content down.
- **Rows are all the same height**, and the list uses `ResizeMode.Fixed`.
  Variable row heights make the view re-anchor itself as rows are measured,
  which looks exactly like the list scrolling on its own. A row's own bottom
  line carries whatever it has to say — a stray dependency's applications, a
  tracked package's requirers, a status hint — always on that one line, never
  on extra ones. So a row that names eight requirers is exactly as tall as one
  that names none.
- **A long list opens under the list, never inside a row.** A package can be
  needed by dozens of others: the row shows the names that fit, a count for the
  rest and a "Show more" control, and the whole list opens in `_DetailPanel` —
  the one strip that sits between the list and the footer, closing on "Hide",
  on a page change and on a reload. The list gives up exactly that height; the
  footer and everything above the list stay put, and the strip is one fixed
  size whatever it is showing. The one thing that may grow a row is release
  notes: "Show more" unfolds *that* row to hold them and folds back on "Show
  less" — see `_relayout_after_row_event`, which holds the scroll offset so the
  clicked row never moves.
- **An empty list still looks like a list.** Keep its frame, its header and its
  stat cards; the placeholder lives *inside* the frame and only swaps an icon
  and two lines of text.
- **Lists show "Loading…" while they load** — inside that same frame, in that
  same placeholder, with a line saying what is being loaded. Never a blank box.
- **The only allowed shifts are deliberate, rare and worth it:** the security
  warning banner, the AUR→official banner, opening the log panel, unfolding a
  row's release notes, and opening the requirer panel under the list. Nothing
  else may move.

## Where information lives

One place per fact, and no page owns a line of its own:

- **Numbers live in the stat cells** — the cards in the control row, beside the
  filter field. How many packages, from where, and each card is one click from
  filtering the list down to what it counted. A page never repeats them in a
  paragraph.
- **Loading info lives on the status line, top left** (with the header progress
  track under it). Not beside the list, not in a footer: a list in flight
  reports itself there (`_set_load_status`), and only while its own page is the
  one on screen — which is exactly where the removed per-page line used to sit.
  A running job (check, install, removal, scan) always keeps that line.
  A list load also **fills the track**, stage by stage, and says the stage and
  the percent (`Reading the package database… · 34%`): `_LoadSteps` turns the
  loader's own stages — weighted by how long each really takes — into one
  0..1000 bar, so a two-second read fills as it goes instead of spinning. The
  track has **one owner at a time** — a job, a load, or the release notes
  arriving — and a load takes it only while its page is on screen; a load that
  ends (or dies) hands it straight back (`_render_load_track`,
  `_on_load_progress`).
- **The list's own placeholder carries the verdict** ("Nothing tracked",
  "Not scanned yet" — and "No matches" when a filter is hiding everything) and
  the list footer the count (`5 tracked`). Explanations that are the same for
  every row — like what "obsolete" means — go once, in a tooltip on the control
  that acts on them.
- **How much is selected lives in the footer**, next to that count:
  `390 tracked · All 390 packages selected` (see `_selection_text`). Not beside
  Select all — that row belongs to the filter field and the cards.
- **What needs a package lives on that row's own bottom line** — a stray
  dependency's applications (Untracked), the packages that would go with a
  tracked one (Tracked) — with the whole list in the panel under the list.
  One fact, one line, and every row keeps its height whether it names one
  requirer or none.

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
