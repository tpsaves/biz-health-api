Read CLAUDE.md for full project context before doing anything.
Make the Recently Scored list items in /demo/index.html clickable so they auto-populate the score results section without re-running the search pipeline.
1. Update the Recently Scored list rendering to make each item a clickable card:

Wrap each recently scored item in a clickable div or button element
On click: load the cached score for that restaurant directly from the API
using GET /api/v1/restaurants/{id}/score — do not re-trigger search-and-score
Show a brief loading indicator while fetching
Populate the full score results section with the cached data exactly as if
the user had searched for it manually

2. Visual treatment for clickable items:

Cursor changes to pointer on hover
Subtle hover state — light background color change
Active/selected state — highlight the currently displayed restaurant in the
recently scored list with a left border in the risk band color
(green/yellow/orange/red matching their overall score)
Smooth transition on hover and selection

3. Update the recently scored list to store restaurant ID alongside name,
address, and score so the click handler knows which ID to fetch:

If recently scored items are currently stored without ID add the restaurant
ID to the stored object
For existing items without ID fall back to re-running search-and-score

4. Add a subtle "click to view" hint on first render:

Small gray text below the recently scored header: "Click any result to view score"
Hide the hint after the first click

5. Verify:

Search for Pecan Lodge — it appears in recently scored list
Search for Torchy's Tacos — it appears, Pecan Lodge still in list
Click Pecan Lodge in the recently scored list — score populates instantly
without re-running the pipeline
The clicked item shows the active highlight state
Works in both single result view and does not interfere with compare feature