using System.ComponentModel.DataAnnotations.Schema;

namespace BizHealthApi.Models;

[Table("raw_signals")]
public class RawSignal
{
    [Column("id")]
    public Guid Id { get; set; }

    [Column("restaurant_id")]
    public Guid RestaurantId { get; set; }

    [Column("source")]
    public required string Source { get; set; }

    // Stored as JSONB in Postgres; read/written as raw JSON string
    [Column("payload", TypeName = "jsonb")]
    public required string Payload { get; set; }

    [Column("scraped_at")]
    public DateTime ScrapedAt { get; set; }

    public Restaurant? Restaurant { get; set; }
}
