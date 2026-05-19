using BizHealthApi.Data;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;

var builder = WebApplication.CreateBuilder(args);

var host     = Environment.GetEnvironmentVariable("POSTGRES_HOST")     ?? "db";
var port     = Environment.GetEnvironmentVariable("POSTGRES_PORT")     ?? "5432";
var database = Environment.GetEnvironmentVariable("POSTGRES_DB")       ?? "bizhealth";
var user     = Environment.GetEnvironmentVariable("POSTGRES_USER")     ?? "admin";
var password = Environment.GetEnvironmentVariable("POSTGRES_PASSWORD") ?? "";

var connectionString = $"Host={host};Port={port};Database={database};Username={user};Password={password}";

builder.Services.AddDbContext<BizHealthDbContext>(options =>
    options.UseNpgsql(connectionString));

builder.Services.AddProblemDetails();

// Allow any origin so /demo/index.html can call the API when opened directly from
// the filesystem (file://). Browsers send Origin: null for file:// requests;
// AllowAnyOrigin() responds with Access-Control-Allow-Origin: * which modern
// browsers accept for null origins. Scope this to specific hosts in production.
builder.Services.AddCors(options =>
    options.AddDefaultPolicy(p =>
        p.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));

builder.Services.AddHttpClient();

var app = builder.Build();

app.UseCors();
app.UseExceptionHandler();
app.UseStatusCodePages();

app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));

// GET /api/v1/restaurants — paginated list with latest overall_score per restaurant.
app.MapGet("/api/v1/restaurants", async (BizHealthDbContext db, int page = 1, int pageSize = 20) =>
{
    pageSize = Math.Clamp(pageSize, 1, 100);

    var total = await db.Restaurants.CountAsync();

    var items = await db.Restaurants
        .OrderBy(r => r.Name)
        .Skip((page - 1) * pageSize)
        .Take(pageSize)
        .Select(r => new
        {
            id                 = r.Id,
            name               = r.Name,
            address            = r.Address,
            city               = r.City,
            state              = r.State,
            zip                = r.Zip,
            latestOverallScore = db.HealthScores
                .Where(h => h.RestaurantId == r.Id)
                .OrderByDescending(h => h.ScoredAt)
                .Select(h => (int?)h.OverallScore)
                .FirstOrDefault(),
        })
        .ToListAsync();

    return Results.Ok(new { page, pageSize, total, items });
});

// GET /api/v1/restaurants/{id} — full restaurant record plus latest score breakdown.
app.MapGet("/api/v1/restaurants/{id:guid}", async (Guid id, BizHealthDbContext db) =>
{
    var restaurant = await db.Restaurants
        .Where(r => r.Id == id)
        .Select(r => new
        {
            id            = r.Id,
            name          = r.Name,
            address       = r.Address,
            city          = r.City,
            state         = r.State,
            zip           = r.Zip,
            googlePlaceId = r.GooglePlaceId,
            website       = r.Website,
            createdAt     = r.CreatedAt,
            latestScore   = db.HealthScores
                .Where(h => h.RestaurantId == r.Id)
                .OrderByDescending(h => h.ScoredAt)
                .Select(h => new
                {
                    overallScore        = h.OverallScore,
                    reviewVelocityScore = h.ReviewVelocityScore,
                    ratingTrendScore    = h.RatingTrendScore,
                    operationalScore    = h.OperationalScore,
                    staffingScore       = h.StaffingScore,
                    scoredAt            = h.ScoredAt,
                })
                .FirstOrDefault(),
        })
        .FirstOrDefaultAsync();

    return restaurant is null ? Results.NotFound() : Results.Ok(restaurant);
});

// GET /api/v1/restaurants/{id}/score — latest score record with restaurant name.
app.MapGet("/api/v1/restaurants/{id:guid}/score", async (Guid id, BizHealthDbContext db) =>
{
    var score = await db.HealthScores
        .Where(h => h.RestaurantId == id)
        .OrderByDescending(h => h.ScoredAt)
        .Select(h => new
        {
            restaurantId        = h.RestaurantId,
            restaurantName      = h.Restaurant!.Name,
            overallScore        = h.OverallScore,
            reviewVelocityScore = h.ReviewVelocityScore,
            ratingTrendScore    = h.RatingTrendScore,
            operationalScore    = h.OperationalScore,
            staffingScore       = h.StaffingScore,
            scoredAt            = h.ScoredAt,
        })
        .FirstOrDefaultAsync();

    return score is null ? Results.NotFound() : Results.Ok(score);
});

// POST /api/v1/restaurants/search-and-score
// Accepts name+address+city or restaurantId directly.
// Returns disambiguation list when name matches multiple restaurants.
// Returns cached score with score_factors and full raw evidence for the demo UI.
app.MapPost("/api/v1/restaurants/search-and-score", async (SearchRequest req, BizHealthDbContext db, IHttpClientFactory httpFactory) =>
{
    // When name+address are both provided, call findplacefromtext to resolve to a single
    // place_id and skip the disambiguation step entirely. Falls back to DB name search
    // when address is omitted or Google returns no result.
    async Task<string?> FindPlaceIdAsync(string name, string address, string? city)
    {
        var apiKey = Environment.GetEnvironmentVariable("GOOGLE_PLACES_API_KEY") ?? "";
        if (string.IsNullOrEmpty(apiKey)) return null;

        var parts = new[] { name, address, city, "TX" }.Where(s => !string.IsNullOrWhiteSpace(s));
        var query = string.Join(" ", parts);

        using var http = httpFactory.CreateClient();
        var url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json" +
                  $"?input={Uri.EscapeDataString(query)}&inputtype=textquery&fields=place_id&key={Uri.EscapeDataString(apiKey)}";

        using var resp = await http.GetAsync(url);
        if (!resp.IsSuccessStatusCode) return null;

        using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
        var root = doc.RootElement;

        // ZERO_RESULTS or request_denied — fall back to DB name search.
        if (!root.TryGetProperty("status", out var statusEl) || statusEl.GetString() == "ZERO_RESULTS")
            return null;

        if (!root.TryGetProperty("candidates", out var candidates) || candidates.GetArrayLength() == 0)
            return null;

        // findplacefromtext orders by relevance. A single result from a name+address query
        // is unambiguous (confidence ≥ 0.8). Multiple results indicate ambiguity — fall back.
        if (candidates.GetArrayLength() > 1) return null;

        return candidates[0].TryGetProperty("place_id", out var pid) ? pid.GetString() : null;
    }

    BizHealthApi.Models.Restaurant? restaurant = null;

    if (req.RestaurantId.HasValue)
    {
        // Direct lookup — used when the UI selects from the disambiguation list.
        restaurant = await db.Restaurants.FindAsync(req.RestaurantId.Value);
        if (restaurant is null)
            return Results.NotFound(new { message = "Restaurant not found." });
    }
    else
    {
        if (string.IsNullOrWhiteSpace(req.Name))
            return Results.BadRequest(new { message = "Name is required." });

        // Name + address → resolve via Google Places for a precise single match.
        if (!string.IsNullOrWhiteSpace(req.Address))
        {
            var placeId = await FindPlaceIdAsync(req.Name, req.Address, req.City);
            if (placeId is not null)
            {
                restaurant = await db.Restaurants.FirstOrDefaultAsync(r => r.GooglePlaceId == placeId);
                if (restaurant is null)
                    return Results.NotFound(new { message = $"\"{req.Name}\" was identified on Google Maps but is not yet tracked in this system." });
            }
        }

        // Name only, or Google returned no confident match — fall back to DB name search.
        if (restaurant is null)
        {
            var candidates = await db.Restaurants
                .Where(r => EF.Functions.ILike(r.Name, $"%{req.Name}%"))
                .ToListAsync();

            if (!candidates.Any())
                return Results.NotFound(new { message = $"No restaurant found matching \"{req.Name}\". Check the name and try again." });

            // Multiple matches → return disambiguation list so the UI can let the user pick.
            if (candidates.Count > 1)
            {
                return Results.Ok(new
                {
                    disambiguation = true,
                    candidates = candidates.Select(c => new
                    {
                        id      = c.Id,
                        name    = c.Name,
                        address = c.Address,
                        city    = c.City,
                        state   = c.State,
                        zip     = c.Zip,
                    }).ToList(),
                });
            }

            restaurant = candidates[0];
        }
    }

    var score = await db.HealthScores
        .Where(h => h.RestaurantId == restaurant.Id)
        .OrderByDescending(h => h.ScoredAt)
        .FirstOrDefaultAsync();

    if (score is null)
        return Results.NotFound(new { message = $"\"{restaurant.Name}\" is in the system but has not been scored yet." });

    // Parse score_factors from JSONB column.
    JsonElement? scoreFactors        = null;
    JsonElement? monthlyReviewCounts = null;
    if (score.ScoreFactors is not null)
    {
        using var sfDoc = JsonDocument.Parse(score.ScoreFactors);
        var root = sfDoc.RootElement;
        scoreFactors = root.Clone();
        if (root.TryGetProperty("monthlyReviewCounts", out var mrc))
            monthlyReviewCounts = mrc.Clone();
    }

    // Fetch latest raw signal payloads + scraped timestamps for all sources.
    var gpInfo  = await db.RawSignals.Where(s => s.RestaurantId == restaurant.Id && s.Source == "google_places")
                    .OrderByDescending(s => s.ScrapedAt).Select(s => new { s.Payload, s.ScrapedAt }).FirstOrDefaultAsync();
    var fsqInfo = await db.RawSignals.Where(s => s.RestaurantId == restaurant.Id && s.Source == "foursquare")
                    .OrderByDescending(s => s.ScrapedAt).Select(s => new { s.Payload, s.ScrapedAt }).FirstOrDefaultAsync();
    var hiInfo  = await db.RawSignals.Where(s => s.RestaurantId == restaurant.Id && s.Source == "health_inspections")
                    .OrderByDescending(s => s.ScrapedAt).Select(s => new { s.Payload, s.ScrapedAt }).FirstOrDefaultAsync();
    var tlInfo  = await db.RawSignals.Where(s => s.RestaurantId == restaurant.Id && s.Source == "tabc_license")
                    .OrderByDescending(s => s.ScrapedAt).Select(s => new { s.Payload, s.ScrapedAt }).FirstOrDefaultAsync();
    var hmInfo  = await db.RawSignals.Where(s => s.RestaurantId == restaurant.Id && s.Source == "hours_monitor")
                    .OrderByDescending(s => s.ScrapedAt).Select(s => new { s.Payload, s.ScrapedAt }).FirstOrDefaultAsync();

    // Earliest outscraper_reviews scraped_at — used by the UI to distinguish
    // "zero reviews this month" from "before data collection started".
    var dataCollectionStart = await db.RawSignals
                    .Where(s => s.RestaurantId == restaurant.Id && s.Source == "outscraper_reviews")
                    .OrderBy(s => s.ScrapedAt)
                    .Select(s => (DateTime?)s.ScrapedAt)
                    .FirstOrDefaultAsync();

    // ── Level 1 details (summary values shown without expansion) ──────────────
    double? googleRating  = null;
    int?    googleReviews = null;
    if (gpInfo?.Payload is not null)
    {
        using var doc = JsonDocument.Parse(gpInfo.Payload);
        var result = doc.RootElement.GetProperty("result");
        if (result.TryGetProperty("rating",             out var rv)) googleRating  = rv.GetDouble();
        if (result.TryGetProperty("user_ratings_total", out var rc)) googleReviews = rc.GetInt32();
    }

    int?    inspectionScore = null;
    string? inspectionDate  = null;
    if (hiInfo?.Payload is not null)
    {
        using var doc     = JsonDocument.Parse(hiInfo.Payload);
        var       records = doc.RootElement.GetProperty("records");
        if (records.GetArrayLength() > 0)
        {
            var first = records[0];
            if (first.TryGetProperty("score",     out var sv)) inspectionScore = (int)double.Parse(sv.GetString() ?? "0");
            if (first.TryGetProperty("insp_date", out var dv)) inspectionDate  = dv.GetString()?[..10];
        }
    }

    string? tabcLicenseType = null;
    string? tabcOwner       = null;
    if (tlInfo?.Payload is not null)
    {
        using var doc     = JsonDocument.Parse(tlInfo.Payload);
        var       records = doc.RootElement.GetProperty("records");
        if (records.GetArrayLength() > 0)
        {
            var first = records[0];
            if (first.TryGetProperty("aimslicensetype", out var tv)) tabcLicenseType = tv.GetString();
            if (first.TryGetProperty("aimsownername",   out var ov)) tabcOwner       = ov.GetString();
        }
    }

    int? hoursCompleteness = null;
    if (hmInfo?.Payload is not null)
    {
        using var doc = JsonDocument.Parse(hmInfo.Payload);
        if (doc.RootElement.TryGetProperty("hours_completeness", out var hv))
            hoursCompleteness = hv.GetInt32();
    }

    // ── Level 3 evidence (raw data for drill-down) ────────────────────────────

    // Inspection records: all historical records (date, score, violation count).
    var inspectionRecords = new List<object>();
    if (hiInfo?.Payload is not null)
    {
        using var doc = JsonDocument.Parse(hiInfo.Payload);
        if (doc.RootElement.TryGetProperty("records", out var recs))
        {
            foreach (var rec in recs.EnumerateArray().Take(10))
            {
                string iDate = "";
                if (rec.TryGetProperty("insp_date", out var dv)) iDate = dv.GetString()?[..10] ?? "";

                int iScore = 0;
                if (rec.TryGetProperty("score", out var sv))
                {
                    var raw = sv.GetString() ?? "0";
                    iScore = (int)double.Parse(raw);
                }

                int violations = rec.EnumerateObject()
                    .Count(p => p.Name.StartsWith("violation") && p.Name.EndsWith("_text"));

                inspectionRecords.Add(new { date = iDate, score = iScore, violations });
            }
        }
    }

    // TABC evidence: license number, entity, trade name, type.
    object? tabcEvidence = null;
    if (tlInfo?.Payload is not null)
    {
        using var doc = JsonDocument.Parse(tlInfo.Payload);
        if (doc.RootElement.TryGetProperty("records", out var recs) && recs.GetArrayLength() > 0)
        {
            var first = recs[0];
            string? licNum = null, entityName = null, tradeName = null, licType = null;
            if (first.TryGetProperty("legacylicensenumber", out var ln)) licNum     = ln.GetString();
            if (first.TryGetProperty("aimsownername",       out var en)) entityName = en.GetString();
            if (first.TryGetProperty("aimstradename",       out var tn)) tradeName  = tn.GetString();
            if (first.TryGetProperty("aimslicensetype",     out var lt)) licType    = lt.GetString();
            tabcEvidence = new { licenseNumber = licNum, entityName, tradeName, licenseType = licType, status = "Active" };
        }
    }

    // Foursquare evidence: rating + scraped date.
    double? fsqRating = null;
    if (fsqInfo?.Payload is not null)
    {
        using var doc = JsonDocument.Parse(fsqInfo.Payload);
        if (doc.RootElement.TryGetProperty("details", out var details) &&
            details.TryGetProperty("rating", out var fr))
            fsqRating = fr.GetDouble();
    }

    // Hours evidence: weekday text lines.
    List<string>? weekdayText  = null;
    int?          daysWithHours = null;
    if (hmInfo?.Payload is not null)
    {
        using var doc = JsonDocument.Parse(hmInfo.Payload);
        if (doc.RootElement.TryGetProperty("weekday_text", out var wt))
            weekdayText = wt.EnumerateArray().Select(e => e.GetString() ?? "").ToList();
        if (doc.RootElement.TryGetProperty("days_with_hours", out var dwh))
            daysWithHours = dwh.GetInt32();
    }

    return Results.Ok(new
    {
        id      = restaurant.Id,
        name    = restaurant.Name,
        address = restaurant.Address,
        city    = restaurant.City,
        state   = restaurant.State,
        zip     = restaurant.Zip,
        score = new
        {
            overallScore           = score.OverallScore,
            reviewVelocityScore    = score.ReviewVelocityScore,
            ratingTrendScore       = score.RatingTrendScore,
            operationalScore       = score.OperationalScore,
            financialRiskScore     = score.FinancialRiskScore,
            staffingScore          = score.StaffingScore,
            scoredAt               = score.ScoredAt,
            scoreFactors,
            // Phase 6b flags and enhanced fields
            reviewGapAlert         = score.ReviewGapAlert,
            oneStarSpike           = score.OneStarSpike,
            ratingDeterioration    = score.RatingDeterioration,
            sourceDivergence       = score.SourceDivergence,
            ninetyDaySlope         = score.NinetyDaySlope,
            daysSinceLastReview    = score.DaysSinceLastReview,
            ownerResponseRate      = score.OwnerResponseRate,
            monthlyVolumeTrend     = score.MonthlyVolumeTrend,
            reviewCountConfidence  = score.ReviewCountConfidence,
            seasonalityAdjusted    = score.SeasonalityAdjusted,
            comparisonMethod       = score.ComparisonMethod,
            // Phase 8: financial risk signals
            sbaDefault             = score.SbaDefault,
            repeatedSbaBorrowing   = score.RepeatedSbaBorrowing,
            taxDelinquent          = score.TaxDelinquent,
            sbaLoanCount           = score.SbaLoanCount,
            sbaLatestStatus        = score.SbaLatestStatus,
            sbaLatestAmount        = score.SbaLatestAmount,
            taxDelinquencyYears    = score.TaxDelinquencyYears,
            // Phase 9: delivery platform listing status
            doordashListed         = score.DoordashListed,
            ubereatsListed         = score.UbereatsListed,
            deliveryPlatformCount  = score.DeliveryPlatformCount,
            deliveryStatus         = score.DeliveryStatus,
            deliveryPlatformLoss   = score.DeliveryPlatformLoss,
            // Phase 10: composite risk cap
            compositeRiskCap       = score.CompositeRiskCap,
        },
        details = new
        {
            googleRating,
            googleReviews,
            inspectionScore,
            inspectionDate,
            tabcLicenseType,
            tabcOwner,
            hoursCompleteness,
        },
        evidence = new
        {
            inspectionRecords,
            tabc = tabcEvidence,
            google = gpInfo is null ? null : new
            {
                rating      = googleRating,
                reviewCount = googleReviews,
                scrapedAt   = gpInfo.ScrapedAt,
            },
            foursquare = fsqInfo is null ? null : new
            {
                rating    = fsqRating,
                scrapedAt = fsqInfo.ScrapedAt,
            },
            hours = hmInfo is null ? null : new
            {
                weekdayText,
                daysWithHours,
                scrapedAt = hmInfo.ScrapedAt,
            },
            monthlyReviewCounts,
            dataCollectionStart,
        },
    });
});

// GET /api/v1/backtesting/summary — returns the latest accuracy report from disk.
// Falls back to a live computation from DB data when no file is present.
app.MapGet("/api/v1/backtesting/summary", async (BizHealthDbContext db) =>
{
    // Try reading the most-recent saved report first.
    var reportDir = new System.IO.DirectoryInfo("/backtesting_results");
    if (reportDir.Exists)
    {
        var latest = reportDir
            .GetFiles("report_*.json")
            .OrderByDescending(f => f.Name)
            .FirstOrDefault();

        if (latest is not null)
        {
            var json = await System.IO.File.ReadAllTextAsync(latest.FullName);
            using var doc = JsonDocument.Parse(json);
            return Results.Ok(doc.RootElement.Clone());
        }
    }

    // No saved report — compute live summary from DB.
    var totalCohort = await db.BacktestCohorts.CountAsync(b => b.CohortType == "prospective");
    var totalRetro  = await db.BacktestCohorts.CountAsync(b => b.CohortType == "retrospective");

    var bandBreakdown = await db.BacktestCohorts
        .Where(b => b.CohortType == "retrospective")
        .GroupBy(b => b.BaselineRiskBand)
        .Select(g => new
        {
            band             = g.Key,
            count            = g.Count(),
            avgScore         = g.Average(b => (double?)b.BaselineScore) ?? 0,
            closed180dCount  = g.Count(b => b.Outcome180d == "closed_permanently" || b.Outcome180d == "closed_temporarily"),
        })
        .ToListAsync();

    return Results.Ok(new
    {
        generatedAt         = DateTime.UtcNow,
        totalProspective    = totalCohort,
        totalRetrospective  = totalRetro,
        bandBreakdown,
        note = "Run test_accuracy_report.py inside the scrapers container to generate a full report.",
    });
});

// GET /api/v1/backtesting/cohort — paginated list of cohort members with baseline scores and outcomes.
app.MapGet("/api/v1/backtesting/cohort", async (
    BizHealthDbContext db,
    string? cohortType,
    string? band,
    int page = 1,
    int pageSize = 50) =>
{
    pageSize = Math.Clamp(pageSize, 1, 200);
    cohortType ??= "prospective";

    var query = db.BacktestCohorts
        .Where(b => b.CohortType == cohortType);

    if (!string.IsNullOrEmpty(band))
        query = query.Where(b => b.BaselineRiskBand == band);

    var total = await query.CountAsync();

    var items = await query
        .OrderByDescending(b => b.CreatedAt)
        .Skip((page - 1) * pageSize)
        .Take(pageSize)
        .Select(b => new
        {
            id                = b.Id,
            restaurantId      = b.RestaurantId,
            restaurantName    = b.Restaurant != null ? b.Restaurant.Name : null,
            restaurantCity    = b.Restaurant != null ? b.Restaurant.City : null,
            cohortType        = b.CohortType,
            baselineScore     = b.BaselineScore,
            baselineRiskBand  = b.BaselineRiskBand,
            baselineDate      = b.BaselineDate,
            outcome90d        = b.Outcome90d,
            outcome180d       = b.Outcome180d,
            outcome90dDate    = b.Outcome90dDate,
            outcome180dDate   = b.Outcome180dDate,
            closureDate       = b.ClosureDate,
            notes             = b.Notes,
        })
        .ToListAsync();

    var dayUntil90d = items
        .Where(i => i.baselineDate.HasValue && i.outcome90d == null)
        .Select(i => 90 - (DateTime.Today - i.baselineDate!.Value.ToDateTime(TimeOnly.MinValue)).Days)
        .Where(d => d > 0)
        .DefaultIfEmpty(0)
        .Min();

    return Results.Ok(new { page, pageSize, total, cohortType, items, daysUntilNext90dOutcome = dayUntil90d });
});

// GET /api/v1/admin/outscraper-quota — current month Outscraper usage summary.
app.MapGet("/api/v1/admin/outscraper-quota", async (BizHealthDbContext db) =>
{
    const int monthlyCap    = 10_000;
    const double costPer1k  = 3.00;

    var currentMonth = DateTime.UtcNow.ToString("yyyy-MM");

    var used = await db.OutscraperUsages
        .Where(u => u.Month == currentMonth)
        .SumAsync(u => (int?)u.RecordsFetched) ?? 0;

    var remaining = Math.Max(0, monthlyCap - used);
    var cost      = used / 1000.0 * costPer1k;
    var pctUsed   = (int)Math.Round((double)used / monthlyCap * 100);

    return Results.Ok(new
    {
        month              = currentMonth,
        records_used       = used,
        records_remaining  = remaining,
        cap                = monthlyCap,
        estimated_cost     = $"${cost:F2}",
        pct_used           = pctUsed,
        projected_monthly  = 9844,
        schedule           = "biweekly (1st and 15th)",
    });
});

app.Run();

record SearchRequest(string? Name, string? Address, string? City, Guid? RestaurantId);
