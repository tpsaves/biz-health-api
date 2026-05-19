using System.ComponentModel.DataAnnotations.Schema;

namespace BizHealthApi.Models;

[Table("closed_restaurants")]
public class ClosedRestaurant
{
    [Column("id")]
    public Guid Id { get; set; }

    [Column("name")]
    public string Name { get; set; } = "";

    [Column("address")]
    public string? Address { get; set; }

    [Column("city")]
    public string? City { get; set; }

    [Column("zip")]
    public string? Zip { get; set; }

    [Column("google_place_id")]
    public string? GooglePlaceId { get; set; }

    [Column("yelp_id")]
    public string? YelpId { get; set; }

    [Column("closure_date")]
    public DateOnly? ClosureDate { get; set; }

    [Column("closure_date_estimated")]
    public bool? ClosureDateEstimated { get; set; }

    [Column("closure_source")]
    public string? ClosureSource { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; }
}
