using System.ComponentModel.DataAnnotations.Schema;

namespace BizHealthApi.Models;

[Table("restaurants")]
public class Restaurant
{
    [Column("id")]
    public Guid Id { get; set; }

    [Column("name")]
    public required string Name { get; set; }

    [Column("address")]
    public string? Address { get; set; }

    [Column("city")]
    public string? City { get; set; }

    [Column("state")]
    public string? State { get; set; }

    [Column("zip")]
    public string? Zip { get; set; }

    [Column("google_place_id")]
    public string? GooglePlaceId { get; set; }

    [Column("yelp_id")]
    public string? YelpId { get; set; }

    [Column("website")]
    public string? Website { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; }

    [Column("updated_at")]
    public DateTime UpdatedAt { get; set; }
}
