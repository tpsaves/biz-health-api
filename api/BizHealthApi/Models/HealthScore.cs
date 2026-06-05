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

    // Phase 11: rating distribution
    [Column("pct_5star_recent")]
    public decimal? Pct5StarRecent { get; set; }

    [Column("pct_1star_recent")]
    public decimal? Pct1StarRecent { get; set; }

    [Column("high_negative_rate")]
    public bool? HighNegativeRate { get; set; }

    [Column("negative_rate_rising")]
    public bool? NegativeRateRising { get; set; }

    [Column("bimodal_distribution")]
    public bool? BimodalDistribution { get; set; }

    // Phase 11: keyword flags
    [Column("sanitation_flag")]
    public bool? SanitationFlag { get; set; }

    [Column("operational_instability_flag")]
    public bool? OperationalInstabilityFlag { get; set; }

    [Column("ownership_change_flag")]
    public bool? OwnershipChangeFlag { get; set; }

    [Column("quality_decline_flag")]
    public bool? QualityDeclineFlag { get; set; }

    [Column("financial_stress_flag")]
    public bool? FinancialStressFlag { get; set; }

    [Column("keyword_findings", TypeName = "jsonb")]
    public string? KeywordFindings { get; set; }

    // Phase 11: response rate trend
    [Column("response_rate_declining")]
    public bool? ResponseRateDeclining { get; set; }

    [Column("owner_disengaged")]
    public bool? OwnerDisengaged { get; set; }

    [Column("response_rate_recent")]
    public int? ResponseRateRecent { get; set; }

    [Column("response_rate_prior")]
    public int? ResponseRatePrior { get; set; }

    // Signal confidence
    [Column("tabc_confidence")]
    public string? TabcConfidence { get; set; }

    [Column("tabc_confidence_reason")]
    public string? TabcConfidenceReason { get; set; }

    [Column("inspection_confidence")]
    public string? InspectionConfidence { get; set; }

    [Column("inspection_confidence_reason")]
    public string? InspectionConfidenceReason { get; set; }

    [Column("tabc_expected_missing")]
    public bool? TabcExpectedMissing { get; set; }

    [Column("inspection_expected_missing")]
    public bool? InspectionExpectedMissing { get; set; }

    [Column("inspection_data_unavailable")]
    public bool? InspectionDataUnavailable { get; set; }

    public Restaurant? Restaurant { get; set; }

    // Phase 14: business status and hours reduction
    [Column("business_status")]
    public string? BusinessStatus { get; set; }

    [Column("temporarily_closed")]
    public bool? TemporarilyClosed { get; set; }

    [Column("permanently_closed")]
    public bool? PermanentlyClosed { get; set; }

    [Column("total_weekly_hours")]
    public decimal? TotalWeeklyHours { get; set; }

    [Column("hours_reduction_pct")]
    public decimal? HoursReductionPct { get; set; }

    [Column("hours_reduction")]
    public bool? HoursReduction { get; set; }
}
