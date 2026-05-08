using BizHealthApi.Data;
using Microsoft.EntityFrameworkCore;

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

var app = builder.Build();

app.UseExceptionHandler();
app.UseStatusCodePages();

app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));

// GET /api/v1/restaurants — paginated list of all tracked restaurants with their latest score.
// The inner db.HealthScores subquery is a correlated subquery in SQL — equivalent to
// a LATERAL join or a SELECT TOP 1 per restaurant_id. EF Core + Npgsql translates this
// automatically; no raw SQL needed.
app.MapGet("/api/v1/restaurants", async (BizHealthDbContext db, int page = 1, int pageSize = 20) =>
{
    pageSize = Math.Clamp(pageSize, 1, 100);
    var offset = (page - 1) * pageSize;

    var total = await db.Restaurants.CountAsync();

    var items = await db.Restaurants
        .OrderBy(r => r.Name)
        .Skip(offset)
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

    return score is null
        ? Results.NotFound()
        : Results.Ok(score);
});

app.Run();
