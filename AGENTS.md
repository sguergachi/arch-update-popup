# arch-update-popup — agent notes

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
  which looks exactly like the list scrolling on its own.
- **An empty list still looks like a list.** Keep its frame, its header and its
  stat cards; the placeholder lives *inside* the frame and only swaps an icon
  and two lines of text.
- **Lists show "Loading…" while they load** — inside that same frame, in that
  same placeholder, with a line saying what is being loaded. Never a blank box.
- **The only allowed shifts are deliberate, rare and worth it:** the security
  warning banner, the AUR→official banner, and opening the log panel. Nothing
  else may move.

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
