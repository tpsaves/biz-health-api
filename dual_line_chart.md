Read CLAUDE.md for full project context before doing anything.
Replace the sparkline bar chart in /demo/index.html with a dual line chart showing current year vs prior year monthly review volume. This is the final version of the review trend visualization.
1. Replace renderSparkline() with renderReviewTrendChart() that:

Draws an SVG line chart directly in the Review Velocity drill-down section
X axis: 12 months Jan through Dec
Y axis: review count, auto-scaled to the maximum value across both years
Two lines:

Current year (2026): solid blue line, filled area below with 15% opacity
Prior year (2025): dashed gray line, no fill


Chart dimensions: 100% container width, 120px height
Clean minimal style — no heavy borders, no grid lines except light horizontal guides at 25/50/75/100% of max

2. Labels and legend:

X axis: 3-character month abbreviations (Jan-Dec) evenly spaced
Y axis: no labels — keep it clean, values show on hover only
Legend: small inline legend above the chart showing:

Blue solid line — "2026"
Gray dashed line — "2025"


Chart title: "Monthly Review Volume — Year over Year"

3. Data points:

Plot a dot at each data point on both lines
Current year dots: filled blue circle, 4px radius
Prior year dots: filled gray circle, 3px radius
Months with zero reviews: plot at y=0, dot still visible as hollow circle
Months with no data (before collection started): gap in the line, no dot

4. Hover interaction:

Hovering anywhere on the chart shows a vertical crosshair line at the nearest month
Tooltip appears showing:
March 2026: 41 reviews
March 2025: 38 reviews
YoY change: +8% ↑
Seasonally adjusted: +3%

YoY change shown in green if positive, red if negative, gray if within 5%
Seasonally adjusted figure shown below using the DFW seasonal factors from seasonality.py

5. Year-over-year summary line
Below the chart add a single summary line:

Compute average YoY change across all months with data in both years
Display as: "Average year-over-year: +12% ↑" in green or "Average year-over-year: -8% ↓" in red
If fewer than 3 months have data in both years show: "Insufficient data for year-over-year comparison"

6. Seasonal adjustment note
Below the summary line keep the existing note:
"Volumes adjusted for DFW seasonal patterns"
7. Update all call sites that previously called renderSparkline() to call
renderReviewTrendChart() instead — including both the single result view and
the side-by-side comparison view
8. Verify by searching for Pecan Lodge:

Both lines render correctly with Jan-Dec on x axis
2026 line shows March (41), April (32), May (28) correctly
2025 line shows prior year data where available
Hovering March shows tooltip with both years and YoY change
Average YoY summary line appears below chart
Side-by-side comparison view also shows dual line chart for both restaurants