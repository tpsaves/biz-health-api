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

app.Run();
