using System.ComponentModel.DataAnnotations.Schema;

namespace BizHealthApi.Models;

[Table("health_scores")]
public class HealthScore
{
    [Column("id")]
    public Guid Id { get; set; }

    [Column("restaurant_id")]
    public Guid RestaurantId { get; set; }

    [Column("review_velocity_score")]
    public int? ReviewVelocityScore { get; set; }

    [Column("rating_trend_score")]
    public int? RatingTrendScore { get; set; }

    [Column("operational_score")]
    public int? OperationalScore { get; set; }

    [Column("staffing_score")]
    public int? StaffingScore { get; set; }

    [Column("overall_score")]
    public int? OverallScore { get; set; }

    [Column("score_factors", TypeName = "jsonb")]
    public string? ScoreFactors { get; set; }

    [Column("scored_at")]
    public DateTime ScoredAt { get; set; }

    public Restaurant? Restaurant { get; set; }
}
