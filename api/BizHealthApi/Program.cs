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

var app = builder.Build();

// UseCors before UseExceptionHandler so CORS headers are present on error responses too.
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
// Fuzzy name search with address as the primary disambiguation field.
// Returns the cached score and signal details for display in the demo UI.
// If the restaurant is not in the system, returns 404 — onboarding is a separate pipeline.
app.MapPost("/api/v1/restaurants/search-and-score", async (SearchRequest req, BizHealthDbContext db) =>
{
    if (string.IsNullOrWhiteSpace(req.Name))
        return Results.BadRequest(new { message = "Name is required." });

    var candidates = await db.Restaurants
        .Where(r => EF.Functions.ILike(r.Name, $"%{req.Name}%"))
        .ToListAsync();

    if (!candidates.Any())
        return Results.NotFound(new { message = $"No restaurant found matching \"{req.Name}\". Check the name and try again." });

    // Use address as the primary disambiguation signal when multiple candidates share a name.
    // Score each candidate by how many meaningful words from the input address it contains.
    var restaurant = candidates
        .OrderByDescending(r =>
        {
            if (string.IsNullOrEmpty(req.Address) || r.Address is null) return 0;
            return req.Address
                .Split(' ', StringSplitOptions.RemoveEmptyEntries)
                .Where(w => w.Length > 2)
                .Count(w => r.Address.Contains(w, StringComparison.OrdinalIgnoreCase));
        })
        .ThenByDescending(r =>
            !string.IsNullOrEmpty(req.City) && r.City is not null &&
            r.City.Equals(req.City, StringComparison.OrdinalIgnoreCase) ? 1 : 0)
        .First();

    var score = await db.HealthScores
        .Where(h => h.RestaurantId == restaurant.Id)
        .OrderByDescending(h => h.ScoredAt)
        .FirstOrDefaultAsync();

    if (score is null)
        return Results.NotFound(new { message = $"\"{restaurant.Name}\" is in the system but has not been scored yet." });

    // Fetch the most recent raw signal payload for each source shown in the demo UI.
    var gpRaw = await db.RawSignals
        .Where(s => s.RestaurantId == restaurant.Id && s.Source == "google_places")
        .OrderByDescending(s => s.ScrapedAt).Select(s => s.Payload).FirstOrDefaultAsync();

    var hiRaw = await db.RawSignals
        .Where(s => s.RestaurantId == restaurant.Id && s.Source == "health_inspections")
        .OrderByDescending(s => s.ScrapedAt).Select(s => s.Payload).FirstOrDefaultAsync();

    var tlRaw = await db.RawSignals
        .Where(s => s.RestaurantId == restaurant.Id && s.Source == "tabc_license")
        .OrderByDescending(s => s.ScrapedAt).Select(s => s.Payload).FirstOrDefaultAsync();

    var hmRaw = await db.RawSignals
        .Where(s => s.RestaurantId == restaurant.Id && s.Source == "hours_monitor")
        .OrderByDescending(s => s.ScrapedAt).Select(s => s.Payload).FirstOrDefaultAsync();

    // Parse JSONB payloads — stored as raw JSON strings in the RawSignal.Payload column.
    // JsonDocument.Parse is used here instead of a typed model because the JSONB schema
    // varies per source and we only need a handful of leaf values.
    double? googleRating  = null;
    int?    googleReviews = null;
    if (gpRaw is not null)
    {
        using var doc = JsonDocument.Parse(gpRaw);
        var result = doc.RootElement.GetProperty("result");
        if (result.TryGetProperty("rating",             out var rv)) googleRating  = rv.GetDouble();
        if (result.TryGetProperty("user_ratings_total", out var rc)) googleReviews = rc.GetInt32();
    }

    int?    inspectionScore = null;
    string? inspectionDate  = null;
    if (hiRaw is not null)
    {
        using var doc     = JsonDocument.Parse(hiRaw);
        var       records = doc.RootElement.GetProperty("records");
        if (records.GetArrayLength() > 0)
        {
            var first = records[0];
            // Socrata returns inspection score as a numeric string — parse defensively.
            if (first.TryGetProperty("score",     out var sv)) inspectionScore = (int)double.Parse(sv.GetString() ?? "0");
            if (first.TryGetProperty("insp_date", out var dv)) inspectionDate  = dv.GetString()?[..10];
        }
    }

    string? tabcLicenseType = null;
    string? tabcOwner       = null;
    if (tlRaw is not null)
    {
        using var doc     = JsonDocument.Parse(tlRaw);
        var       records = doc.RootElement.GetProperty("records");
        if (records.GetArrayLength() > 0)
        {
            var first = records[0];
            if (first.TryGetProperty("aimslicensetype", out var tv)) tabcLicenseType = tv.GetString();
            if (first.TryGetProperty("aimsownername",   out var ov)) tabcOwner       = ov.GetString();
        }
    }

    int? hoursCompleteness = null;
    if (hmRaw is not null)
    {
        using var doc = JsonDocument.Parse(hmRaw);
        if (doc.RootElement.TryGetProperty("hours_completeness", out var hv))
            hoursCompleteness = hv.GetInt32();
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
            overallScore        = score.OverallScore,
            reviewVelocityScore = score.ReviewVelocityScore,
            ratingTrendScore    = score.RatingTrendScore,
            operationalScore    = score.OperationalScore,
            staffingScore       = score.StaffingScore,
            scoredAt            = score.ScoredAt,
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
    });
});

app.Run();

record SearchRequest(string Name, string? Address, string? City);
