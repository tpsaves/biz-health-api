Read CLAUDE.md for full project context before doing anything.
Update the sparkline in /demo/index.html to show 12 months instead of 6.
1. Update the month generation logic in renderSparkline():

Change the loop from 6 months to 12 months working backwards from current month
Update the label: "Monthly review trend — last 6 months" → "Monthly review trend — last 12 months"

2. Adjust the sparkline width to accommodate 12 bars without crowding:

Each bar and label should remain readable at 12 months
If needed reduce individual bar width slightly to fit all 12 in the same container
Month labels should still show 3-character abbreviations (Jan-Dec) without overlapping

3. Update the seasonal adjustment note below the sparkline to reference 12 months:

"Volumes adjusted for DFW seasonal patterns (12-month view)"

4. Verify by opening the demo and searching for Pecan Lodge:

All 12 month labels visible without overlap
Bars proportional and readable
June 2025 through May 2026 all showing with correct counts
Hover tooltips showing correct month, year, and review count for all 12 bars