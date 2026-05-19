using System.ComponentModel.DataAnnotations.Schema;

namespace BizHealthApi.Models;

[Table("backtest_cohort")]
public class BacktestCohort
{
    [Column("id")]
    public Guid Id { get; set; }

    [Column("restaurant_id")]
    public Guid? RestaurantId { get; set; }

    [Column("cohort_type")]
    public string? CohortType { get; set; }

    [Column("baseline_score")]
    public int? BaselineScore { get; set; }

    [Column("baseline_risk_band")]
    public string? BaselineRiskBand { get; set; }

    [Column("baseline_date")]
    public DateOnly? BaselineDate { get; set; }

    [Column("baseline_factors", TypeName = "jsonb")]
    public string? BaselineFactors { get; set; }

    [Column("outcome_90d")]
    public string? Outcome90d { get; set; }

    [Column("outcome_180d")]
    public string? Outcome180d { get; set; }

    [Column("outcome_90d_date")]
    public DateOnly? Outcome90dDate { get; set; }

    [Column("outcome_180d_date")]
    public DateOnly? Outcome180dDate { get; set; }

    [Column("closure_date")]
    public DateOnly? ClosureDate { get; set; }

    [Column("closure_source")]
    public string? ClosureSource { get; set; }

    [Column("notes")]
    public string? Notes { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; }

    public Restaurant? Restaurant { get; set; }
}
