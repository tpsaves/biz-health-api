using System.ComponentModel.DataAnnotations.Schema;

namespace BizHealthApi.Models;

[Table("outscraper_usage")]
public class OutscraperUsage
{
    [Column("id")]
    public Guid Id { get; set; }

    [Column("restaurant_id")]
    public Guid? RestaurantId { get; set; }

    [Column("records_fetched")]
    public int RecordsFetched { get; set; }

    [Column("month")]
    public required string Month { get; set; }

    [Column("scraped_at")]
    public DateTime ScrapedAt { get; set; }

    [Column("is_backfill")]
    public bool IsBackfill { get; set; }
}
