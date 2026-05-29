using BizHealthApi.Models;
using Microsoft.EntityFrameworkCore;

namespace BizHealthApi.Data;

public class BizHealthDbContext(DbContextOptions<BizHealthDbContext> options) : DbContext(options)
{
    public DbSet<Restaurant>      Restaurants      => Set<Restaurant>();
    public DbSet<RawSignal>       RawSignals       => Set<RawSignal>();
    public DbSet<HealthScore>     HealthScores     => Set<HealthScore>();
    public DbSet<ClosedRestaurant> ClosedRestaurants => Set<ClosedRestaurant>();
    public DbSet<BacktestCohort>  BacktestCohorts  => Set<BacktestCohort>();
    public DbSet<OutscraperUsage>  OutscraperUsages  => Set<OutscraperUsage>();
    public DbSet<OutscraperRunLog> OutscraperRunLogs => Set<OutscraperRunLog>();
}
