using System.ComponentModel.DataAnnotations.Schema;

namespace BizHealthApi.Models;

[Table("outscraper_run_log")]
public class OutscraperRunLog
{
    [Column("id")]
    public Guid Id { get; set; }

    [Column("run_date")]
    public DateOnly RunDate { get; set; }

    [Column("status")]
    public required string Status { get; set; }

    [Column("restaurants_completed")]
    public int RestaurantsCompleted { get; set; }

    [Column("restaurants_skipped")]
    public int RestaurantsSkipped { get; set; }

    [Column("restaurants_no_reviews")]
    public int? RestaurantsNoReviews { get; set; }

    [Column("restaurants_failed")]
    public int? RestaurantsFailed { get; set; }

    [Column("records_used_before")]
    public int RecordsUsedBefore { get; set; }

    [Column("records_used_after")]
    public int RecordsUsedAfter { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; }
}
