/* Pace: reading the board by any column, and keeping what was read in the address.

   The board arrives already sorted: the server puts the rows in the order the address asked for,
   so a pasted board is read the same way in the first frame it is drawn in and reads the same
   with no script at all. Every column head is a button, and a click reorders the rows here rather
   than asking the server again -- which is what turns "who is on pace for the most rushing yards"
   into a click instead of another question -- and then writes the view back into the address. So
   the board a reader is looking at is the board he can paste into a league chat: the column and
   the way round travel in its address, beside the position chip that is already there. The chips
   are given the sort in force too, because a chip is the same board narrowed and a narrowed board
   that forgot its column would not be the one that was shared.

   The address is the state here, not a copy of it: it is read once on arrival, so the page a
   reader opens is the page the server drew for that address, and every change is written back
   with the page's own history left alone -- a sort is how the board under the reader is being
   read, not somewhere else he went, so the back button does not walk him through every column he
   tried on the way to the one he wanted.

   The board's own opening -- its first stat column, highest first -- is left unsaid in the address,
   the way every page of this app spells a default, and the table carries those two words for the
   browser so it can tell when a board is back in its opening order. It carries the sentence's own
   words as well, and so does every column head: what the sentence above the table says the board
   is read by is the same words the server would have drawn, so the sentence stays true after a
   click instead of describing the board the reader arrived with.

   A name is capped at the width the column keeps on every board, so the longest names in a league
   are clipped rather than holding a column open for themselves, and the part of one that is not
   shown is left on it as the element's title: a pointer resting on a clipped name says the whole
   of it. Which names those are is measured rather than assumed, and measured again when the
   window changes, because whether a name fits depends on the font and on the room it is given.

   A figure a player's position is not measured by is a dash, and a dash is not a zero: it sorts
   after every player who has the stat, whichever way the column is read, because he has none of
   it rather than none of it so far. Two players level on a column are read by name, in either
   direction -- the same order the server lays a shared board out in -- so a board pasted into a
   chat is the board the sender was reading, to the row. A name is compared by its own characters
   with the case taken out, which is how the server compares one, rather than by the browser's
   idea of an alphabet: the two have to agree about where "A.J. Brown" sits, and only a rule
   written down in both places can promise that. */

document.addEventListener("DOMContentLoaded", () => {
  const table = document.getElementById("pace-table");
  if (!table) return;
  const body = table.tBodies[0];
  const buttons = [...table.querySelectorAll("thead .sort-button")];

  // A name is capped at a width the column keeps whatever names it holds, so a long one ends in an
  // ellipsis rather than the whole column being widened for it. The whole name is handed back on
  // hover -- as the element's own title, which is where a browser looks for the rest of something
  // it was only shown part of -- and only on the rows where the browser is really clipping it.
  // That is measured here rather than guessed: whether a name fits depends on the webfont having
  // arrived, and on the window it is read in, so the answer is taken again whenever either
  // changes. A name with no box of its own is left alone: an inline span is never clipped, and
  // reading its scroll width would call every name on the board a clipped one.
  const names = [...table.querySelectorAll("tbody td:first-child .player-card-target")];
  const revealClippedNames = () => {
    names.forEach((name) => {
      if (name.clientWidth > 0 && name.scrollWidth > name.clientWidth) {
        name.title = name.textContent.trim();
      } else {
        name.removeAttribute("title");
      }
    });
  };
  if (names.length) {
    document.fonts.ready.then(revealClippedNames);
    window.addEventListener("resize", revealClippedNames);
  }

  if (!buttons.length) return;

  const openKey = table.dataset.openSort || "";
  const openDirection = table.dataset.openDirection || "desc";
  const buttonFor = (key) => buttons.find((button) => button.dataset.sortKey === key) || null;

  // What the address asked for, resolved the way the server resolved it so that this page agrees
  // with the rows it was handed: the column it names if this board draws that column, and this
  // board's own opening column otherwise; and, for a column, the way round it asks for or the way
  // that kind of column is read first -- a figure from its highest, a name from its first letter.
  const params = new URLSearchParams(window.location.search);
  const named = buttonFor((params.get("sort") || "").trim().toLowerCase());
  const opening = buttonFor(openKey) || buttons[0];
  let active = named || opening;
  const asked = (params.get("dir") || "").trim().toLowerCase();
  let descending = asked === "asc" || asked === "desc"
    ? asked === "desc"
    : active.dataset.sortType !== "text";

  const inOpening = () => active.dataset.sortKey === openKey
    && (descending ? "desc" : "asc") === openDirection;

  // A name, as both this page and the server compare one: the case taken out and the characters
  // left as they are, so the two orders are one order rather than two that nearly agree.
  const compareNames = (left, right) => {
    const one = left.toLowerCase();
    const other = right.toLowerCase();
    return one < other ? -1 : one > other ? 1 : 0;
  };

  const nameOf = (row) => (row.cells[0] ? row.cells[0].textContent.trim() : "");

  // A cell's own figure: the number the page printed it from where there is one, and the cell's
  // own text where the column is a name or a position. A stat this player's position is not
  // measured by is null -- the page writes that as a dash and leaves the value empty -- because
  // it is not a figure of his and must not sort as though a low one were.
  const figureOf = (row, column) => {
    const cell = row.cells[column];
    if (!cell) return null;
    const value = cell.dataset.sortValue;
    if (value === undefined) return cell.textContent.trim() || null;
    return value === "" ? null : Number(value);
  };

  // What the sentence above the table says the board is read by: the words the server drew for
  // this column, or the board's own opening words where the board is back in its opening order.
  const say = () => {
    const line = document.querySelector(".pace-read-by");
    if (!line) return;
    line.textContent = inOpening()
      ? (table.dataset.openPhrase || "")
      : (active.dataset.sortPhrase || "");
  };

  const mark = () => {
    buttons.forEach((button) => {
      const here = button === active;
      button.closest("th").setAttribute(
        "aria-sort", here ? (descending ? "descending" : "ascending") : "none");
      const indicator = button.querySelector("span");
      if (indicator) indicator.textContent = here ? (descending ? "↓" : "↑") : "↕";
    });
  };

  const sortRows = () => {
    const column = active.closest("th").cellIndex;
    const type = active.dataset.sortType;
    const rows = [...body.rows];
    rows.sort((rowA, rowB) => {
      const left = figureOf(rowA, column);
      const right = figureOf(rowB, column);
      // A dash is absent rather than smallest, so it is placed before the direction is applied:
      // it stays at the far end of the column read either way up, its own players by name.
      if (left === null || right === null) {
        if (left === right) return compareNames(nameOf(rowA), nameOf(rowB));
        return left === null ? 1 : -1;
      }
      if (type === "text") return (descending ? -1 : 1) * compareNames(left, right);
      if (left === right) return compareNames(nameOf(rowA), nameOf(rowB));
      return (descending ? -1 : 1) * (left < right ? -1 : 1);
    });
    rows.forEach((row) => body.append(row));
  };

  // The sort written into a set of address parameters: the column and the way round, or nothing
  // at all where the board is back in its own opening order.
  const write = (into) => {
    if (inOpening()) {
      into.delete("sort");
      into.delete("dir");
    } else {
      into.set("sort", active.dataset.sortKey);
      into.set("dir", descending ? "desc" : "asc");
    }
  };

  // The board in the address, and the chips beside it. Everything else the address carries -- the
  // league, the account, the season chain, the position chip -- is left as it is: this page owns
  // the sort and nothing else.
  const remember = () => {
    const query = new URLSearchParams(window.location.search);
    write(query);
    const search = query.toString();
    window.history.replaceState(null, "", search ? `?${search}` : window.location.pathname);
    document.querySelectorAll(".pace-filter").forEach((chip) => {
      const href = chip.getAttribute("href");
      if (!href) return;
      const url = new URL(href, window.location.origin);
      write(url.searchParams);
      chip.setAttribute("href", url.pathname + url.search);
    });
  };

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      if (button === active) {
        descending = !descending;
      } else {
        active = button;
        descending = button.dataset.sortType !== "text";
      }
      sortRows();
      mark();
      say();
      remember();
    });
  });

  // An address the server could not answer as written -- a column from another board, or one of
  // this board's own columns written down as its opening -- is put back into the words this page
  // would have written for the board it is showing, so the address a reader copies is the view he
  // is looking at and not the one he arrived by.
  say();
  remember();
});
