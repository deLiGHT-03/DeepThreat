document.addEventListener("DOMContentLoaded", () => {
  const table = document.querySelector("#season-team-table");
  if (!table) return;
  const viewport = table.closest(".season-table-wrap");
  viewport.addEventListener("wheel", (event) => {
    const maxScroll = viewport.scrollWidth - viewport.clientWidth;
    if (maxScroll <= 1 || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    event.preventDefault();
    viewport.scrollLeft += event.deltaY;
  }, { passive: false });

  table.tBodies[0].addEventListener("click", (event) => {
    const row = event.target.closest("tr");
    if (!row || event.target.closest("button")) return;
    table.querySelectorAll("tbody tr").forEach((teamRow) => {
      teamRow.classList.remove("season-my-team", "season-selected-team");
    });
    row.classList.add("season-selected-team");
  });

  let activeColumn = null;
  let descending = true;
  table.querySelectorAll(".sort-button").forEach((button) => {
    button.addEventListener("click", () => {
      const column = Number(button.dataset.sortColumn);
      const type = button.dataset.sortType;
      if (activeColumn === column) {
        descending = !descending;
      } else {
        activeColumn = column;
        descending = type === "number" || type === "record";
      }

      const rows = [...table.tBodies[0].rows];
      rows.sort((a, b) => {
        const aValue = a.cells[column].dataset.sortValue ?? a.cells[column].textContent.trim();
        const bValue = b.cells[column].dataset.sortValue ?? b.cells[column].textContent.trim();
        let comparison;
        if (type === "number") {
          comparison = Number(aValue) - Number(bValue);
        } else if (type === "record") {
          const parseRecord = (record) => record.split("-").map((part) => Number(part) || 0);
          const [aWins, aLosses, aTies = 0] = parseRecord(aValue);
          const [bWins, bLosses, bTies = 0] = parseRecord(bValue);
          comparison = (aWins - bWins) || (bLosses - aLosses) || (aTies - bTies);
        } else {
          comparison = aValue.localeCompare(bValue);
        }
        return (descending ? -1 : 1) * comparison;
      });
      rows.forEach((row) => table.tBodies[0].append(row));

      table.querySelectorAll("thead th").forEach((header, index) => {
        header.setAttribute("aria-sort", index === column
          ? (descending ? "descending" : "ascending")
          : "none");
        const indicator = header.querySelector(".sort-button span");
        if (indicator) indicator.textContent = index === column ? (descending ? "↓" : "↑") : "↕";
      });
    });
  });

  if (table.querySelector('.true-record-toggle input[name="true_record"]:checked')) {
    table.querySelector('.sort-button[data-sort-type="record"]')?.click();
  }
});
