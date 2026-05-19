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

    // Phase 6b: enhanced velocity and rating fields
    [Column("review_gap_alert")]
    public bool? ReviewGapAlert { get; set; }

    [Column("one_star_spike")]
    public bool? OneStarSpike { get; set; }

    [Column("rating_deterioration")]
    public bool? RatingDeterioration { get; set; }

    [Column("source_divergence")]
    public bool? SourceDivergence { get; set; }

    [Column("ninety_day_slope")]
    public string? NinetyDaySlope { get; set; }

    [Column("days_since_last_review")]
    public int? DaysSinceLastReview { get; set; }

    [Column("owner_response_rate")]
    public int? OwnerResponseRate { get; set; }

    [Column("monthly_volume_trend")]
    public string? MonthlyVolumeTrend { get; set; }

    [Column("review_count_confidence")]
    public string? ReviewCountConfidence { get; set; }

    [Column("seasonality_adjusted")]
    public bool? SeasonalityAdjusted { get; set; }

    [Column("comparison_method")]
    public string? ComparisonMethod { get; set; }

    // Phase 8: financial risk signals
    [Column("financial_risk_score")]
    public int? FinancialRiskScore { get; set; }

    [Column("sba_default")]
    public bool? SbaDefault { get; set; }

    [Column("repeated_sba_borrowing")]
    public bool? RepeatedSbaBorrowing { get; set; }

    [Column("tax_delinquent")]
    public bool? TaxDelinquent { get; set; }

    [Column("sba_loan_count")]
    public int? SbaLoanCount { get; set; }

    [Column("sba_latest_status")]
    public string? SbaLatestStatus { get; set; }

    [Column("sba_latest_amount")]
    public decimal? SbaLatestAmount { get; set; }

    [Column("tax_delinquency_years")]
    public int? TaxDelinquencyYears { get; set; }

    // Phase 9: delivery platform listing status
    [Column("doordash_listed")]
    public bool? DoordashListed { get; set; }

    [Column("ubereats_listed")]
    public bool? UbereatsListed { get; set; }

    [Column("delivery_platform_count")]
    public int? DeliveryPlatformCount { get; set; }

    [Column("delivery_status")]
    public string? DeliveryStatus { get; set; }

    [Column("delivery_platform_loss")]
    public bool? DeliveryPlatformLoss { get; set; }

    // Phase 10: composite risk cap
    [Column("composite_risk_cap")]
    public bool? CompositeRiskCap { get; set; }

    public Restaurant? Restaurant { get; set; }
}
