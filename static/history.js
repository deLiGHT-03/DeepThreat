/* The History tables sort in the browser. Each column head that carries a sort button re-orders
   its own table's rows on a click, and the arrow beside the label says which way it is read. The
   server hands the rows over already ordered -- the standings by record, each leaderboard by the
   number it is named for -- so a page left alone reads the way it always did.

   A cell is compared by its own text unless the text on screen is not the value: data-sort-value
   carries the number behind "1,234.56", the amount behind "$45" and the zero behind an em dash.
   A record like 12-3-1 is read win by win rather than as a string, so 9-8 never beats 10-7. */

const collator = new Intl.Collator(undefined, { sensitivity: "base" });

function cellText(cell) {
  return (cell.dataset.sortValue ?? cell.textContent).trim();
}

function cellNumber(cell) {
  const value = Number(cellText(cell).replace(/[,\s$%]/g, ""));
  return Number.isFinite(value) ? value : null;
}

function cellRecord(cell) {
  const [wins = 0, losses = 0, ties = 0] = cellText(cell)
    .split("-")
    .map((part) => Number(part) || 0);
  return { wins, losses, ties };
}

function compareCells(left, right, type) {
  if (type === "record") {
    const a = cellRecord(left);
    const b = cellRecord(right);
    return (a.wins - b.wins) || (b.losses - a.losses) || (a.ties - b.ties);
  }
  if (type === "number") {
    const a = cellNumber(left);
    const b = cellNumber(right);
    // A cell with no number to read is not a zero: it sorts past every real value.
    if (a !== null && b !== null) return a - b;
    if (a !== b) return a === null ? 1 : -1;
  }
  return collator.compare(cellText(left), cellText(right));
}

function prepareTable(table) {
  const buttons = [...table.querySelectorAll("thead .sort-button")];
  if (!buttons.length) return;

  let sortedButton = null;
  let descending = false;

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const column = button.closest("th").cellIndex;
      const type = button.dataset.sortType || "text";
      if (sortedButton === button) {
        descending = !descending;
      } else {
        sortedButton = button;
        descending = (button.dataset.sortDirection || (type === "text" ? "asc" : "desc")) === "desc";
      }

      const rows = [...table.tBodies[0].rows];
      rows.sort((rowA, rowB) => {
        const comparison = compareCells(rowA.cells[column], rowB.cells[column], type);
        return descending ? -comparison : comparison;
      });
      rows.forEach((row) => table.tBodies[0].append(row));

      [...table.tHead.rows[0].cells].forEach((head, index) => {
        head.setAttribute("aria-sort", index === column
          ? (descending ? "descending" : "ascending")
          : "none");
        const indicator = head.querySelector(".sort-button span");
        if (indicator) indicator.textContent = index === column ? (descending ? "↓" : "↑") : "↕";
      });
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("table.season-table").forEach(prepareTable);
});
